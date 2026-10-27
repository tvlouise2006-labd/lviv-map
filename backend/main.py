import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config import settings
from backend.transit import transit_manager
from backend.state import state_store
from backend.ingestor import ingestor

ROUTE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
SIGHTING_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# Rate limiting & DoS protection stores
_login_attempts: Dict[str, List[float]] = defaultdict(list)
_sse_connections_per_ip: Dict[str, int] = defaultdict(int)
MAX_LOGIN_ATTEMPTS_PER_MINUTE = 5
MAX_SSE_PER_IP = 6
MAX_SSE_TOTAL = 300
ADMIN_COOKIE_NAME = "lviv_admin_session"
ADMIN_TOKEN_TTL_SECONDS = 12 * 3600  # 12 hours


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def create_admin_token() -> str:
    expires = int(time.time()) + ADMIN_TOKEN_TTL_SECONDS
    payload = f"admin:{expires}"
    sig = hmac.new(
        settings.ADMIN_SECRET_KEY.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return f"{payload}:{sig}"


def verify_admin_token(token: Optional[str]) -> bool:
    if not token:
        return False
    parts = token.split(":")
    if len(parts) != 3 or parts[0] != "admin":
        return False
    try:
        expires = int(parts[1])
    except ValueError:
        return False
    if time.time() > expires:
        return False
    payload = f"admin:{expires}"
    expected_sig = hmac.new(
        settings.ADMIN_SECRET_KEY.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(parts[2], expected_sig)


def is_request_admin(request: Request) -> bool:
    cookie_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if verify_admin_token(cookie_token):
        return True
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return verify_admin_token(auth_header[7:].strip())
    return False


def require_admin(request: Request):
    if not is_request_admin(request):
        raise HTTPException(
            status_code=403,
            detail="Доступ заборонено: створення та видалення міток доступне лише адміністратору"
        )


async def vehicle_poll_loop():
    """Periodically polls GTFS-RT vehicle positions and updates matched sightings."""
    while True:
        try:
            count = await asyncio.to_thread(transit_manager.poll_gtfs_rt)
            if count > 0:
                state_store.sync_sightings_with_vehicles()
                vehicles_data = [v.model_dump() for v in transit_manager.get_live_vehicles()]
                sightings_data = [s.model_dump() for s in state_store.get_active_sightings()]
                await state_store.broadcast("live_tick", {
                    "vehicles": vehicles_data,
                    "sightings": sightings_data,
                    "timestamp": int(time.time())
                })
        except Exception as e:
            print(f"[VehiclePoller] Error: {e}")
        await asyncio.sleep(settings.VEHICLE_POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initial GTFS-RT poll so vehicles are ready before matching initial sightings
    print("[Startup] Polling initial GTFS-RT vehicle positions...")
    v_count = await asyncio.to_thread(transit_manager.poll_gtfs_rt)
    print(f"[Startup] Loaded {v_count} live vehicles on Lviv streets.")

    # 2. Seed initial Telegram sightings and snap to live vehicles
    await asyncio.to_thread(ingestor.seed_initial_messages)

    # 3. Start background polling tasks
    veh_task = asyncio.create_task(vehicle_poll_loop())
    tg_task = asyncio.create_task(ingestor.poll_loop())
    yield
    veh_task.cancel()
    tg_task.cancel()


app = FastAPI(
    title="Lviv Controllers Live Map API",
    description="Real-time transit & ticket inspector tracking for Lviv",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENABLE_SWAGGER_DOCS else None,
    redoc_url="/redoc" if settings.ENABLE_SWAGGER_DOCS else None,
    openapi_url="/openapi.json" if settings.ENABLE_SWAGGER_DOCS else None,
)

# Restrictive CORS policy (only explicitly configured origins if provided)
allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), payment=(), usb=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com blob:; "
        "worker-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob: https://*.basemaps.cartocdn.com; "
        "connect-src 'self' https://*.basemaps.cartocdn.com https://unpkg.com; "
        "frame-ancestors 'none'; "
        "base-uri 'self';"
    )
    return response


class AdminLoginRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=128)


class VoteRequest(BaseModel):
    direction: str = Field(..., pattern="^(up|down)$")


class ManualReportRequest(BaseModel):
    text: str = Field(..., min_length=4, max_length=300)


@app.post("/api/admin/login")
async def admin_login(req: AdminLoginRequest, request: Request, response: Response):
    client_ip = get_client_ip(request)
    now = time.time()

    # Prune login attempts older than 60s
    _login_attempts[client_ip] = [t for t in _login_attempts[client_ip] if now - t < 60.0]
    if len(_login_attempts[client_ip]) >= MAX_LOGIN_ATTEMPTS_PER_MINUTE:
        raise HTTPException(
            status_code=429,
            detail="Забагато спроб входу. Зачекайте 1 хвилину."
        )

    if not settings.ADMIN_PASSWORD or not hmac.compare_digest(req.password, settings.ADMIN_PASSWORD):
        _login_attempts[client_ip].append(now)
        raise HTTPException(status_code=401, detail="Невірний пароль адміністратора")

    _login_attempts.pop(client_ip, None)
    token = create_admin_token()
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=token,
        max_age=ADMIN_TOKEN_TTL_SECONDS,
        httponly=True,
        samesite="strict",
        secure=is_https,
        path="/"
    )
    return {"is_admin": True}


@app.post("/api/admin/logout")
async def admin_logout(response: Response):
    response.delete_cookie(key=ADMIN_COOKIE_NAME, path="/")
    return {"is_admin": False}


@app.get("/api/admin/me")
async def admin_me(request: Request):
    return {"is_admin": is_request_admin(request)}


@app.get("/api/init")
async def get_initial_state(request: Request):
    """Returns initial payload with active sightings, live vehicles, and route metadata."""
    sightings = [s.model_dump() for s in state_store.get_active_sightings()]
    vehicles = [v.model_dump() for v in transit_manager.get_live_vehicles()]
    routes = [
        {
            "id": r.id,
            "short_name": r.short_name,
            "long_name": r.long_name,
            "transport_type": r.transport_type,
            "color": r.color
        }
        for r in transit_manager.routes.values()
    ]
    return {
        "channel": settings.TELEGRAM_CHANNEL,
        "ttl_minutes": settings.SIGHTING_TTL_MINUTES,
        "carto_api_key": settings.CARTO_API_KEY,
        "is_admin": is_request_admin(request),
        "sightings": sightings,
        "vehicles": vehicles,
        "routes": routes,
        "server_time": int(time.time())
    }


@app.get("/api/routes/{route_id}/shape")
async def get_route_shape(route_id: str):
    """Returns GeoJSON LineString geometry for a specific route."""
    if not ROUTE_ID_PATTERN.match(route_id):
        raise HTTPException(status_code=400, detail="Invalid route ID")
    route = transit_manager.routes.get(route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    return {
        "id": route.id,
        "short_name": route.short_name,
        "long_name": route.long_name,
        "color": route.color,
        "coordinates": route.coordinates or []
    }


@app.get("/api/sightings")
async def get_sightings():
    return {"sightings": [s.model_dump() for s in state_store.get_active_sightings()]}


@app.get("/api/vehicles")
async def get_vehicles():
    return {"vehicles": [v.model_dump() for v in transit_manager.get_live_vehicles()]}


@app.post("/api/sightings/{sighting_id}/vote")
async def vote_sighting(
    sighting_id: str,
    req: VoteRequest,
    request: Request,
    _: None = Depends(require_admin)
):
    """Admin-only endpoint to confirm ('up') or delete/clear ('down') a warning."""
    if not SIGHTING_ID_PATTERN.match(sighting_id):
        raise HTTPException(status_code=400, detail="Invalid sighting ID")
    voter_ip = get_client_ip(request)
    updated = state_store.vote(sighting_id, req.direction, voter_ip)
    if not updated:
        raise HTTPException(status_code=404, detail="Sighting not found")

    await state_store.broadcast("sightings_update", {
        "sightings": [s.model_dump() for s in state_store.get_active_sightings()]
    })
    return {"sighting": updated.model_dump()}


@app.post("/api/report")
async def submit_manual_report(
    req: ManualReportRequest,
    _: None = Depends(require_admin)
):
    """Admin-only endpoint to create a new warning on the map."""
    text = req.text.strip()
    if len(text) < 4:
        raise HTTPException(status_code=400, detail="Повідомлення занадто коротке")
    now_iso = datetime.now(timezone.utc).isoformat()
    msg_id = f"user/{int(time.time() * 1000)}"
    s = await asyncio.to_thread(
        state_store.ingest_raw_message,
        msg_id,
        f"❗️ {text}",
        now_iso,
        True,
        True
    )
    if not s:
        raise HTTPException(status_code=400, detail="Не вдалося розпізнати локацію або маршрут у повідомленні")

    state_store.sync_sightings_with_vehicles()
    await state_store.broadcast("sightings_update", {
        "sightings": [item.model_dump() for item in state_store.get_active_sightings()]
    })
    return {"sighting": s.model_dump()}


@app.get("/api/stream")
async def sse_stream(request: Request):
    """Server-Sent Events stream for real-time sightings and vehicle positions."""
    client_ip = get_client_ip(request)
    if len(state_store.subscribers) >= MAX_SSE_TOTAL or _sse_connections_per_ip[client_ip] >= MAX_SSE_PER_IP:
        raise HTTPException(status_code=429, detail="Too many live stream connections")

    _sse_connections_per_ip[client_ip] += 1
    q = state_store.subscribe()

    async def event_generator():
        try:
            yield f"data: {json.dumps({'type': 'connected', 'ts': int(time.time())})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            state_store.unsubscribe(q)
            _sse_connections_per_ip[client_ip] = max(0, _sse_connections_per_ip[client_ip] - 1)
            if _sse_connections_per_ip[client_ip] == 0:
                _sse_connections_per_ip.pop(client_ip, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# Mount frontend static files
os.makedirs("frontend", exist_ok=True)
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
async def index():
    return FileResponse("frontend/index.html")
