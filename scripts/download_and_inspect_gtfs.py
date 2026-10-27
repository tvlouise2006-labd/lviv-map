import os
import zipfile
import urllib.request
import ssl
from google.transit import gtfs_realtime_pb2
import csv

os.makedirs("data", exist_ok=True)

# 1. Download and inspect GTFS static
STATIC_URL = "http://track.ua-gis.com/gtfs/lviv/static.zip"
STATIC_ZIP = "data/static.zip"
STATIC_DIR = "data/gtfs_static"

if not os.path.exists(STATIC_ZIP):
    print(f"Downloading static GTFS from {STATIC_URL}...")
    urllib.request.urlretrieve(STATIC_URL, STATIC_ZIP)
    print("Downloaded static GTFS.")

if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)
    with zipfile.ZipFile(STATIC_ZIP, "r") as z:
        z.extractall(STATIC_DIR)
    print(f"Extracted to {STATIC_DIR}")

print("\nFiles in GTFS static:")
for fname in os.listdir(STATIC_DIR):
    fpath = os.path.join(STATIC_DIR, fname)
    size_kb = os.path.getsize(fpath) / 1024
    print(f"  {fname} ({size_kb:.1f} KB)")

# Sample stops
stops_file = os.path.join(STATIC_DIR, "stops.txt")
if os.path.exists(stops_file):
    with open(stops_file, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        stops = list(reader)
        print(f"\nTotal stops: {len(stops)}")
        print("Sample stops:")
        for s in stops[:5]:
            print(f"  ID: {s.get('stop_id')}, Name: {s.get('stop_name')}, Lat: {s.get('stop_lat')}, Lon: {s.get('stop_lon')}")

# Sample routes
routes_file = os.path.join(STATIC_DIR, "routes.txt")
if os.path.exists(routes_file):
    with open(routes_file, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        routes = list(reader)
        print(f"\nTotal routes: {len(routes)}")
        print("Sample routes:")
        for r in routes[:10]:
            print(f"  ID: {r.get('route_id')}, ShortName: {r.get('route_short_name')}, LongName: {r.get('route_long_name')}, Type: {r.get('route_type')}")

# 2. Inspect Live GTFS-RT
RT_URL = "https://track.ua-gis.com/gtfs/lviv/vehicle_position"
print(f"\nFetching live vehicles from {RT_URL}...")
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
req = urllib.request.Request(RT_URL, headers={"User-Agent": "Mozilla/5.0"})

try:
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        feed_bytes = resp.read()
    
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(feed_bytes)
    
    print(f"Header timestamp: {feed.header.timestamp}")
    print(f"Total live vehicle entities: {len(feed.entity)}")
    
    sample_entities = feed.entity[:5]
    for entity in sample_entities:
        v = entity.vehicle
        print(f"  Vehicle ID: {v.vehicle.id}, Route ID: {v.trip.route_id}, Trip ID: {v.trip.trip_id}, Lat: {v.position.latitude}, Lon: {v.position.longitude}, Speed: {v.position.speed}, Bearing: {v.position.bearing}")
except Exception as e:
    print(f"Error reading GTFS-RT: {e}")
