import asyncio
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
from backend.models import Sighting, SightingStatus, SightingAction, ParsedMessage, TransportType
from backend.parser import parser
from backend.matcher import matcher
from backend.transit import transit_manager
from backend.config import settings

class StateStore:
    def __init__(self):
        self.sightings: Dict[str, Sighting] = {}
        self.processed_msg_ids: Set[str] = set()
        self.subscribers: Set[asyncio.Queue] = set()
        self.voter_Registry: Dict[str, Set[str]] = {}  # sighting_id -> set of client_ips

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self.subscribers.discard(q)

    async def broadcast(self, event_type: str, data: dict):
        dead = []
        payload = {"type": event_type, "data": data}
        for q in self.subscribers:
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
        use_llm: bool = True
    ) -> Optional[Sighting]:
        """
        Parses a raw Telegram message, matches to GTFS/vehicles, and stores the sighting.
        Strictly ignores any message older than SIGHTING_TTL_MINUTES (20 minutes) based on posted_at_iso.
        """
        age_sec = self.compute_age_seconds(posted_at_iso)
        ttl_sec = settings.SIGHTING_TTL_MINUTES * 60

        # Strictly clear/ignore messages older than 20 minutes
        if age_sec > ttl_sec:
            self.processed_msg_ids.add(msg_id)
            return None

        if msg_id in self.processed_msg_ids:
            return None
        self.processed_msg_ids.add(msg_id)

        parsed: ParsedMessage = parser.parse(text, use_llm=use_llm)
        if parsed.action == SightingAction.NOISE:
            return None

        # If message says route is cleared, clear active sightings on that route
        if parsed.action == SightingAction.CLEARED and parsed.route_id:
            for s in self.sightings.values():
                if s.route_id == parsed.route_id and s.status == SightingStatus.ACTIVE:
                    s.status = SightingStatus.CLEARED

        # Match to live vehicle if IN_VEHICLE
        matched_veh, conf_mult = matcher.match_sighting_to_vehicle(parsed)

        route_obj = transit_manager.routes.get(parsed.route_id) if parsed.route_id else None
        route_short = route_obj.short_name if route_obj else (parsed.route_raw.upper() if parsed.route_raw else None)

        lat = matched_veh.lat if matched_veh else (parsed.stop_lat or 49.8419)
        lon = matched_veh.lon if matched_veh else (parsed.stop_lon or 24.0315)

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
            stop_name=parsed.matched_stop_name or parsed.location_text or "Невідома зупинка",
            stop_id=parsed.matched_stop_id,
            direction_hint=parsed.direction_hint,
            lat=lat,
            lon=lon,
            status=SightingStatus.CLEARED if parsed.action == SightingAction.CLEARED else SightingStatus.ACTIVE,
            confidence=round(min(0.99, parsed.confidence * conf_mult), 2),
            matched_vehicle_id=matched_veh.vehicle_id if matched_veh else None,
            vehicle_lat=matched_veh.lat if matched_veh else None,
            vehicle_lon=matched_veh.lon if matched_veh else None,
            age_seconds=age_sec
        )

        self.sightings[sighting_id] = sighting
        return sighting

    def sync_sightings_with_vehicles(self):
        """
        Updates positions of active sightings bound to live vehicles,
        and strictly removes/expires any sighting older than SIGHTING_TTL_MINUTES (20 min) from posted_at.
        """
        ttl_sec = settings.SIGHTING_TTL_MINUTES * 60
        expired_ids = []

        for sid, s in list(self.sightings.items()):
            # Always calculate age from the time the message was SENT in the channel (posted_at)
            age = self.compute_age_seconds(s.posted_at)
            s.age_seconds = age

            if age > ttl_sec:
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

        # Purge expired sightings older than 20 minutes from state
        for sid in expired_ids:
            self.sightings.pop(sid, None)

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
        s = self.sightings.get(sighting_id)
        if not s:
            return None

        if direction == "down":
            s.votes_down += 1
            s.status = SightingStatus.CLEARED
            # Immediately remove cleared sighting so it disappears from map and feed
            self.sightings.pop(sighting_id, None)
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
        return s

state_store = StateStore()
