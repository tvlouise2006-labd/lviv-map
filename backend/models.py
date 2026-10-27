from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class TransportType(str, Enum):
    BUS = "bus"
    TRAM = "tram"
    TROLLEYBUS = "trolleybus"
    UNKNOWN = "unknown"

class SightingStatus(str, Enum):
    ACTIVE = "active"
    CLEARED = "cleared"
    EXPIRED = "expired"

class SightingAction(str, Enum):
    IN_VEHICLE = "in_vehicle"   # зайшли в автобус / їдуть
    STANDING = "standing"       # стоять на зупинці
    EXITED = "exited"           # вийшли
    CLEARED = "cleared"         # чисто
    NOISE = "noise"             # жарт, новина, питання

class Stop(BaseModel):
    id: str
    name: str
    lat: float
    lon: float
    code: Optional[str] = None

class Route(BaseModel):
    id: str
    short_name: str
    long_name: str
    route_type: int             # 0=tram, 3=bus, etc.
    transport_type: TransportType
    color: str = "#2563eb"
    shape_id: Optional[str] = None
    coordinates: Optional[List[List[float]]] = None # [[lon, lat], ...]

class VehiclePosition(BaseModel):
    vehicle_id: str
    route_id: str
    trip_id: Optional[str] = None
    route_short_name: Optional[str] = None
    transport_type: TransportType = TransportType.UNKNOWN
    lat: float
    lon: float
    speed: Optional[float] = 0.0
    bearing: Optional[float] = 0.0
    timestamp: int

class ParsedMessage(BaseModel):
    action: SightingAction = SightingAction.NOISE
    route_raw: Optional[str] = None           # e.g. "46", "2", "8"
    route_id: Optional[str] = None
    transport_type: TransportType = TransportType.UNKNOWN
    fleet_number: Optional[str] = None        # e.g. "1240" from "1240 борт"
    location_text: Optional[str] = None       # e.g. "Дорошенка", "ДБК"
    matched_stop_id: Optional[str] = None
    matched_stop_name: Optional[str] = None
    stop_lat: Optional[float] = None
    stop_lon: Optional[float] = None
    direction_hint: Optional[str] = None      # e.g. "в сторону сихова"
    confidence: float = 0.5
    count: Optional[int] = 2                  # default pair of inspectors

class Sighting(BaseModel):
    id: str
    raw_message_id: str
    raw_text: str
    posted_at: str                            # ISO timestamp
    seen_at: str                              # ISO timestamp
    action: SightingAction
    transport_type: TransportType
    route_short_name: Optional[str] = None
    route_id: Optional[str] = None
    stop_name: Optional[str] = None
    stop_id: Optional[str] = None
    direction_hint: Optional[str] = None
    lat: float
    lon: float
    status: SightingStatus = SightingStatus.ACTIVE
    confidence: float = 0.8
    matched_vehicle_id: Optional[str] = None  # If snapped to active bus
    vehicle_lat: Optional[float] = None
    vehicle_lon: Optional[float] = None
    votes_up: int = 0                         # "Still here"
    votes_down: int = 0                       # "Already gone"
    age_seconds: int = 0
