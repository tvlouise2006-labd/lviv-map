import asyncio
import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
from backend.models import Sighting, SightingStatus, SightingAction, ParsedMessage, TransportType
from backend.parser import parser, RE_MILITARY_FILTER
from backend.matcher import matcher
from backend.transit import transit_manager
from backend.config import settings

MAX_PROCESSED_IDS = 400
STATE_CACHE_FILE = os.path.join("data", "active_sightings.json")


class StateStore:
    def __init__(self):
        self.sightings: Dict[str, Sighting] = {}
        self.processed_msg_ids: Set[str] = set()
        self._processed_queue: deque = deque()
        self.cleared_msg_ids: Set[str] = set()
        self.subscribers: Set[asyncio.Queue] = set()
        self.voter_Registry: Dict[str, Set[str]] = {}  # sighting_id -> set of client_ips
        self._last_disk_mtime: float = 0.0
        self._load_from_disk()

    def _mark_processed(self, msg_id: str):
        if msg_id in self.processed_msg_ids:
            return
        self.processed_msg_ids.add(msg_id)
        self._processed_queue.append(msg_id)
        while len(self._processed_queue) > MAX_PROCESSED_IDS:
            oldest = self._processed_queue.popleft()
            self.processed_msg_ids.discard(oldest)
            self.cleared_msg_ids.discard(oldest)

    def _save_to_disk(self):
        """Persists only active (<20m) sightings & cleared IDs so multi-worker Azure instances stay in sync without disk bloat."""
        try:
            os.makedirs("data", exist_ok=True)
            ttl_sec = settings.SIGHTING_TTL_MINUTES * 60
            active_dict = {
                sid: s.model_dump()
                for sid, s in self.sightings.items()
                if s.status == SightingStatus.ACTIVE and self.compute_age_seconds(s.posted_at) <= ttl_sec
            }
            payload = {
                "updated_at": time.time(),
                "sightings": active_dict,
                "cleared_ids": list(self.cleared_msg_ids)[-200:],
            }
            tmp_file = f"{STATE_CACHE_FILE}.tmp.{os.getpid()}"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp_file, STATE_CACHE_FILE)
            self._last_disk_mtime = os.path.getmtime(STATE_CACHE_FILE)
        except Exception as e:
            print(f"[StateStore] Disk sync warning: {e}")

    def _load_from_disk(self):
        """Reloads active sightings if another worker process updated the state file."""
        try:
            if not os.path.exists(STATE_CACHE_FILE):
                return
            mtime = os.path.getmtime(STATE_CACHE_FILE)
            if mtime <= self._last_disk_mtime:
                return
            with open(STATE_CACHE_FILE, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self._last_disk_mtime = mtime

            for cid in payload.get("cleared_ids", []):
                self.cleared_msg_ids.add(cid)
                self._mark_processed(cid)
                sid_clean = cid.replace("/", "_")
                self.sightings.pop(sid_clean, None)
                self.sightings.pop(cid, None)

            ttl_sec = settings.SIGHTING_TTL_MINUTES * 60
            disk_sightings = payload.get("sightings", {})
            for sid, s_data in disk_sightings.items():
                age = self.compute_age_seconds(s_data.get("posted_at", ""))
                if age <= ttl_sec and s_data.get("raw_message_id") not in self.cleared_msg_ids:
                    s_obj = Sighting(**s_data)
                    s_obj.age_seconds = age
                    self.sightings[sid] = s_obj
                    self._mark_processed(s_obj.raw_message_id)
        except Exception as e:
            print(f"[StateStore] Disk load warning: {e}")

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self.subscribers.discard(q)

    async def broadcast(self, event_type: str, data: dict):
        dead = []
        payload = {"type": event_type, "data": data}
        for q in list(self.subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.subscribers.discard(q)

    @staticmethod
    def compute_age_seconds(posted_at_iso: str) -> int:
        """Computes exact age in seconds from when the message was SENT in the channel."""
        now = datetime.now(timezone.utc)
        try:
            posted_dt = datetime.fromisoformat(posted_at_iso.replace("Z", "+00:00"))
            return max(0, int((now - posted_dt).total_seconds()))
        except Exception:
            return 0

    def ingest_raw_message(
        self,
        msg_id: str,
        text: str,
        posted_at_iso: str,
        allow_historical: bool = False,
        use_llm: bool = True,
        is_manual: bool = False,
    ) -> Optional[Sighting]:
        """
        Parses a raw Telegram message or manual Admin warning, matches to GTFS/vehicles, and stores the sighting.
        Strictly ignores any message older than SIGHTING_TTL_MINUTES (20 minutes) based on posted_at_iso.
        """
        age_sec = self.compute_age_seconds(posted_at_iso)
        ttl_sec = settings.SIGHTING_TTL_MINUTES * 60

        # Strictly clear/ignore messages older than 20 minutes
        if age_sec > ttl_sec and not is_manual:
            self._mark_processed(msg_id)
            return None

        if msg_id in self.processed_msg_ids or msg_id in self.cleared_msg_ids:
            return None

        if RE_MILITARY_FILTER.search(text):
            self._mark_processed(msg_id)
            return None

        parsed: ParsedMessage = parser.parse(text, use_llm=use_llm)

        # Never drop an admin manual report as noise unless it matched military filter above
        if parsed.action == SightingAction.NOISE:
            if not is_manual:
                self._mark_processed(msg_id)
                return None
            parsed.action = (
                SightingAction.IN_VEHICLE
                if (parsed.route_id or parsed.route_raw)
                else SightingAction.STANDING
            )
            parsed.confidence = max(parsed.confidence, 0.90)

        self._mark_processed(msg_id)

        # If message says route is cleared, clear active sightings on that route
        if parsed.action == SightingAction.CLEARED and parsed.route_id:
            for sid_k, s in list(self.sightings.items()):
                if s.route_id == parsed.route_id and s.status == SightingStatus.ACTIVE:
                    s.status = SightingStatus.CLEARED
                    self.cleared_msg_ids.add(s.raw_message_id)
                    self.sightings.pop(sid_k, None)
                    self.voter_Registry.pop(sid_k, None)
            self._save_to_disk()
            if not is_manual:
                return None

        # Match to live vehicle if IN_VEHICLE
        matched_veh, conf_mult = matcher.match_sighting_to_vehicle(parsed)

        route_obj = transit_manager.routes.get(parsed.route_id) if parsed.route_id else None
        route_short = route_obj.short_name if route_obj else (parsed.route_raw.upper() if parsed.route_raw else None)

        lat = matched_veh.lat if matched_veh else (parsed.stop_lat or 49.8419)
        lon = matched_veh.lon if matched_veh else (parsed.stop_lon or 24.0315)

        clean_fallback_label = text.replace("❗️", "").strip()
        stop_label = (
            parsed.matched_stop_name
            or parsed.location_text
            or (clean_fallback_label[:60] if is_manual and clean_fallback_label else "Львів (Центр)")
        )

        sighting_id = msg_id.replace("/", "_")
        sighting = Sighting(
            id=sighting_id,
            raw_message_id=msg_id,
            raw_text=text,
            posted_at=posted_at_iso,
            seen_at=posted_at_iso,  # Always anchored to actual channel SENT timestamp
            action=parsed.action,
            transport_type=parsed.transport_type,
            route_short_name=route_short,
            route_id=parsed.route_id,
            stop_name=stop_label,
            stop_id=parsed.matched_stop_id,
            direction_hint=parsed.direction_hint,
            lat=lat,
            lon=lon,
            status=SightingStatus.CLEARED if parsed.action == SightingAction.CLEARED else SightingStatus.ACTIVE,
            confidence=round(min(0.99, max(0.85 if is_manual else 0.35, parsed.confidence * conf_mult)), 2),
            matched_vehicle_id=matched_veh.vehicle_id if matched_veh else None,
            vehicle_lat=matched_veh.lat if matched_veh else None,
            vehicle_lon=matched_veh.lon if matched_veh else None,
            age_seconds=age_sec
        )

        if sighting.status == SightingStatus.ACTIVE:
            self.sightings[sighting_id] = sighting
            self._save_to_disk()
        return sighting

    def sync_sightings_with_vehicles(self):
        """
        Updates positions of active sightings bound to live vehicles,
        and strictly removes/expires any sighting older than SIGHTING_TTL_MINUTES (20 min) from posted_at.
        """
        self._load_from_disk()
        ttl_sec = settings.SIGHTING_TTL_MINUTES * 60
        expired_ids = []

        for sid, s in list(self.sightings.items()):
            # Always calculate age from the time the message was SENT in the channel (posted_at)
            age = self.compute_age_seconds(s.posted_at)
            s.age_seconds = age

            if age > ttl_sec or s.status != SightingStatus.ACTIVE:
                s.status = SightingStatus.EXPIRED
                expired_ids.append(sid)
                continue

            # If bound to a vehicle, update coordinates so the marker rides with the bus/tram
            if s.status == SightingStatus.ACTIVE and s.matched_vehicle_id:
                v = transit_manager.get_vehicle(s.matched_vehicle_id)
                if v:
                    s.vehicle_lat = v.lat
                    s.vehicle_lon = v.lon
                    s.lat = v.lat
                    s.lon = v.lon
            elif s.status == SightingStatus.ACTIVE and s.action == SightingAction.IN_VEHICLE and s.route_id and not s.matched_vehicle_id:
                dummy_parsed = ParsedMessage(
                    action=s.action,
                    route_id=s.route_id,
                    stop_lat=s.lat,
                    stop_lon=s.lon
                )
                matched_veh, _ = matcher.match_sighting_to_vehicle(dummy_parsed)
                if matched_veh:
                    s.matched_vehicle_id = matched_veh.vehicle_id
                    s.vehicle_lat = matched_veh.lat
                    s.vehicle_lon = matched_veh.lon
                    s.lat = matched_veh.lat
                    s.lon = matched_veh.lon

        # Purge expired sightings older than 20 minutes and free voter registry memory
        if expired_ids:
            for sid in expired_ids:
                self.sightings.pop(sid, None)
                self.voter_Registry.pop(sid, None)
            self._save_to_disk()

    def get_active_sightings(self) -> List[Sighting]:
        self.sync_sightings_with_vehicles()
        ttl_sec = settings.SIGHTING_TTL_MINUTES * 60
        active = [
            s for s in self.sightings.values()
            if s.status == SightingStatus.ACTIVE and s.age_seconds <= ttl_sec
        ]
        # Sort newest first (smallest age_seconds first)
        active.sort(key=lambda x: x.age_seconds)
        return active

    def vote(self, sighting_id: str, direction: str, voter_key: str) -> Optional[Sighting]:
        self._load_from_disk()
        s = self.sightings.get(sighting_id)
        if not s:
            return None

        if direction == "down":
            s.votes_down += 1
            s.status = SightingStatus.CLEARED
            self.cleared_msg_ids.add(s.raw_message_id)
            # Immediately remove cleared sighting and free voter memory
            self.sightings.pop(sighting_id, None)
            self.voter_Registry.pop(sighting_id, None)
            self._save_to_disk()
            return s

        voters = self.voter_Registry.setdefault(sighting_id, set())
        vote_token = f"{voter_key}:{direction}"
        if vote_token in voters:
            return s
        voters.add(vote_token)

        if direction == "up":
            s.votes_up += 1
            s.confidence = min(0.99, round(s.confidence + 0.05, 2))
            if s.status != SightingStatus.ACTIVE:
                s.status = SightingStatus.ACTIVE
            self._save_to_disk()
        return s


state_store = StateStore()
