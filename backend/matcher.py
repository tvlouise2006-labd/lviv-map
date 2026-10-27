import math
from typing import Optional, Tuple, List
from backend.models import ParsedMessage, VehiclePosition, SightingAction
from backend.transit import transit_manager

def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates distance in meters between two lat/lon points."""
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * R * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

class VehicleMatcher:
    def match_sighting_to_vehicle(
        self,
        parsed: ParsedMessage,
        max_distance_meters: float = 2500.0
    ) -> Tuple[Optional[VehiclePosition], float]:
        """
        Attempts to snap an IN_VEHICLE sighting to a live GTFS-RT vehicle on the same route.
        Returns (matched_vehicle, match_confidence_multiplier).
        """
        if parsed.action != SightingAction.IN_VEHICLE:
            return None, 1.0

        live_vehicles = transit_manager.get_live_vehicles()
        if not live_vehicles:
            return None, 0.9

        # 1. Explicit fleet number match (e.g. "1240 борт")
        if parsed.fleet_number:
            for v in live_vehicles:
                if parsed.fleet_number in v.vehicle_id:
                    return v, 1.0

        if not parsed.route_id or parsed.stop_lat is None or parsed.stop_lon is None:
            return None, 0.85

        # 2. Filter vehicles on the matched route
        candidates: List[Tuple[float, VehiclePosition]] = []
        for v in live_vehicles:
            if v.route_id == parsed.route_id:
                dist = haversine_meters(parsed.stop_lat, parsed.stop_lon, v.lat, v.lon)
                if dist <= max_distance_meters:
                    candidates.append((dist, v))

        if not candidates:
            return None, 0.85

        candidates.sort(key=lambda x: x[0])
        best_dist, best_vehicle = candidates[0]

        # Compute confidence boost/factor based on proximity
        if best_dist < 400:
            conf_mult = 1.0
        elif best_dist < 1000:
            conf_mult = 0.95
        else:
            conf_mult = 0.85

        return best_vehicle, conf_mult

matcher = VehicleMatcher()
