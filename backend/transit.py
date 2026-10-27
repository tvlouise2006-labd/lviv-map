import csv
import os
import ssl
import time
import urllib.request
import http.client
import zipfile
import threading
from typing import Dict, List, Optional, Tuple
from collections import defaultdict, deque
from google.transit import gtfs_realtime_pb2
from rapidfuzz import process, fuzz

from backend.models import Stop, Route, VehiclePosition, TransportType
from backend.config import settings

class TransitManager:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.static_dir = os.path.join(data_dir, "gtfs_static")
        self.static_zip = os.path.join(data_dir, "static.zip")
        
        self.stops: Dict[str, Stop] = {}
        self.stop_names: List[str] = []
        self.stop_name_to_id: Dict[str, str] = {}
        
        self.routes: Dict[str, Route] = {}
        self.route_number_to_routes: Dict[str, List[Route]] = defaultdict(list)
        
        self.shapes: Dict[str, List[List[float]]] = {} # shape_id -> [[lon, lat], ...]
        self.route_shapes: Dict[str, List[List[float]]] = {} # route_id -> [[lon, lat], ...]
        self.route_headsigns: Dict[str, List[str]] = defaultdict(list)
        
        self.current_vehicles: Dict[str, VehiclePosition] = {}
        self.vehicle_history: deque = deque(maxlen=20000) # (timestamp, vehicle_id, route_id, lat, lon)
        self.last_rt_update: float = 0
        self._lock = threading.Lock()
        
        self._ensure_static_data()
        self._load_static_data()

    def _ensure_static_data(self):
        os.makedirs(self.data_dir, exist_ok=True)
        if not os.path.exists(self.static_zip) and not os.path.exists(self.static_dir):
            print(f"Downloading GTFS static from {settings.GTFS_STATIC_URL}...")
            try:
                urllib.request.urlretrieve(settings.GTFS_STATIC_URL, self.static_zip)
            except Exception as e:
                print(f"Failed to download GTFS static: {e}")
                return

        if not os.path.exists(self.static_dir) and os.path.exists(self.static_zip):
            print(f"Extracting {self.static_zip}...")
            os.makedirs(self.static_dir, exist_ok=True)
            with zipfile.ZipFile(self.static_zip, "r") as z:
                z.extractall(self.static_dir)

    def _load_static_data(self):
        print("Loading static GTFS data...")
        # 1. Stops
        stops_file = os.path.join(self.static_dir, "stops.txt")
        if os.path.exists(stops_file):
            with open(stops_file, "r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    sid = row["stop_id"]
                    # Clean up stop name from numbers like "ТРЦ Кінг Кросс (320)" -> "ТРЦ Кінг Кросс"
                    raw_name = row["stop_name"]
                    clean_name = raw_name.split("(")[0].strip()
                    stop = Stop(
                        id=sid,
                        name=clean_name,
                        lat=float(row["stop_lat"]),
                        lon=float(row["stop_lon"]),
                        code=row.get("stop_code")
                    )
                    self.stops[sid] = stop
                    if clean_name not in self.stop_name_to_id:
                        self.stop_name_to_id[clean_name] = sid
            self.stop_names = list(self.stop_name_to_id.keys())
            print(f"Loaded {len(self.stops)} stops, {len(self.stop_names)} unique stop names.")

        # 2. Shapes
        shapes_file = os.path.join(self.static_dir, "shapes.txt")
        raw_shapes = defaultdict(list)
        if os.path.exists(shapes_file):
            with open(shapes_file, "r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    raw_shapes[row["shape_id"]].append((
                        int(row["shape_pt_sequence"]),
                        float(row["shape_pt_lon"]),
                        float(row["shape_pt_lat"])
                    ))
            for sh_id, pts in raw_shapes.items():
                pts.sort(key=lambda x: x[0])
                self.shapes[sh_id] = [[p[1], p[2]] for p in pts]
            print(f"Loaded {len(self.shapes)} shapes.")

        # 3. Trips to link route to shape & headsigns
        trips_file = os.path.join(self.static_dir, "trips.txt")
        if os.path.exists(trips_file):
            with open(trips_file, "r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    rid = row["route_id"]
                    sh_id = row.get("shape_id")
                    if sh_id and sh_id in self.shapes and rid not in self.route_shapes:
                        self.route_shapes[rid] = self.shapes[sh_id]
                    headsign = row.get("trip_headsign", "")
                    if headsign and headsign not in self.route_headsigns[rid]:
                        self.route_headsigns[rid].append(headsign)

        # 4. Routes
        routes_file = os.path.join(self.static_dir, "routes.txt")
        if os.path.exists(routes_file):
            with open(routes_file, "r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    rid = row["route_id"]
                    short = row["route_short_name"].strip()
                    long_name = row["route_long_name"].strip()
                    rtype = int(row.get("route_type", 3))

                    # Classify transport type & normalize number
                    if short.startswith("Тр"):
                        ttype = TransportType.TROLLEYBUS
                        num = short[2:].lstrip("0")
                        color = "#0284c7" # Sky blue for trolleybus
                    elif short.startswith("Т"):
                        ttype = TransportType.TRAM
                        num = short[1:].lstrip("0")
                        color = "#dc2626" # Red for tram
                    elif short.startswith("А"):
                        ttype = TransportType.BUS
                        num = short[1:].lstrip("0")
                        color = "#16a34a" # Green for bus
                    else:
                        ttype = TransportType.BUS
                        num = short
                        color = "#ea580c"

                    route = Route(
                        id=rid,
                        short_name=short,
                        long_name=long_name,
                        route_type=rtype,
                        transport_type=ttype,
                        color=color,
                        coordinates=self.route_shapes.get(rid)
                    )
                    self.routes[rid] = route
                    self.route_number_to_routes[num.lower()].append(route)
                    self.route_number_to_routes[short.lower()].append(route)
            print(f"Loaded {len(self.routes)} routes.")

    def find_route(self, number: str, transport_type: Optional[TransportType] = None) -> Optional[Route]:
        """Finds matching Route given a number string like '46', '2', '8', '30' or '1а'."""
        key = number.strip().lower()
        candidates = self.route_number_to_routes.get(key, [])
        if not candidates:
            # Try stripping leading zeros or Ukrainian/Latin variants
            key_alt = key.replace('a', 'а').replace('i', 'і')
            candidates = self.route_number_to_routes.get(key_alt, [])
        if not candidates and key.endswith(('а', 'a')):
            # e.g. "1а" -> "1" (GTFS has А01 for 1А)
            candidates = self.route_number_to_routes.get(key[:-1], [])

        if not candidates:
            return None

        if transport_type and transport_type != TransportType.UNKNOWN:
            for r in candidates:
                if r.transport_type == transport_type:
                    return r
        # Return first candidate
        return candidates[0]

    def fuzzy_match_stop(self, query: str, score_cutoff: int = 70) -> Optional[Stop]:
        """Fuzzy matches location string to GTFS stops."""
        if not query or len(query.strip()) < 3:
            return None
        q = query.strip()
        match = process.extractOne(q, self.stop_names, scorer=fuzz.token_sort_ratio, score_cutoff=score_cutoff)
        if match:
            matched_name, score, _ = match
            sid = self.stop_name_to_id.get(matched_name)
            if sid and sid in self.stops:
                return self.stops[sid]
        return None

    def poll_gtfs_rt(self) -> int:
        """Polls GTFS-RT feed and updates vehicle positions."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(settings.GTFS_RT_URL, headers={"User-Agent": "Mozilla/5.0"})

        try:
            try:
                with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                    data = resp.read()
            except http.client.IncompleteRead as ie:
                data = ie.partial

            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(data)

            new_vehicles = {}
            now = int(time.time())

            for entity in feed.entity:
                if not entity.HasField("vehicle"):
                    continue
                v = entity.vehicle
                vid = str(v.vehicle.id) if v.vehicle.id else entity.id
                rid = str(v.trip.route_id) if v.trip.route_id else ""
                
                route = self.routes.get(rid)
                route_short = route.short_name if route else None
                ttype = route.transport_type if route else TransportType.UNKNOWN

                vp = VehiclePosition(
                    vehicle_id=vid,
                    route_id=rid,
                    trip_id=str(v.trip.trip_id) if v.trip.trip_id else None,
                    route_short_name=route_short,
                    transport_type=ttype,
                    lat=float(v.position.latitude),
                    lon=float(v.position.longitude),
                    speed=float(v.position.speed) if v.position.speed else 0.0,
                    bearing=float(v.position.bearing) if v.position.bearing else 0.0,
                    timestamp=int(v.timestamp) if v.timestamp else now
                )
                new_vehicles[vid] = vp

                with self._lock:
                    self.vehicle_history.append((vp.timestamp, vid, rid, vp.lat, vp.lon))

            with self._lock:
                self.current_vehicles = new_vehicles
                self.last_rt_update = time.time()

            return len(new_vehicles)
        except Exception as e:
            print(f"Error polling GTFS-RT: {e}")
            return 0

    def get_live_vehicles(self) -> List[VehiclePosition]:
        with self._lock:
            return list(self.current_vehicles.values())

    def get_vehicle(self, vehicle_id: str) -> Optional[VehiclePosition]:
        with self._lock:
            return self.current_vehicles.get(vehicle_id)

transit_manager = TransitManager()
