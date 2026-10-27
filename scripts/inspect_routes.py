import csv
import json

with open("data/gtfs_static/routes.txt", mode="r", encoding="utf-8-sig") as f:
    routes = list(csv.DictReader(f))

print(f"Total routes: {len(routes)}")
for r in routes:
    # route_type: 0 = Tram, 3 = Bus, 11 = Trolleybus / 800
    print(f"ID={r['route_id']:<6} ShortName={r['route_short_name']:<8} Type={r['route_type']:<3} LongName={r['route_long_name']}")
