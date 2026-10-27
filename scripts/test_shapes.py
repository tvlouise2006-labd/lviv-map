import csv
import json
import os

shapes_file = "data/gtfs_static/shapes.txt"
trips_file = "data/gtfs_static/trips.txt"
routes_file = "data/gtfs_static/routes.txt"
stops_file = "data/gtfs_static/stops.txt"
stop_times_file = "data/gtfs_static/stop_times.txt"

print("Checking GTFS files exist...")
for f in [shapes_file, trips_file, routes_file, stops_file]:
    print(f"  {f}: {os.path.exists(f)}")

# Check shapes.txt sample
with open(shapes_file, "r", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    sample_shapes = [next(reader) for _ in range(5)]
    print("\nSample shapes:")
    for s in sample_shapes:
        print(" ", s)

# Check trips.txt to see how routes map to shape_id
with open(trips_file, "r", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    sample_trips = [next(reader) for _ in range(5)]
    print("\nSample trips:")
    for t in sample_trips:
        print(" ", t)
