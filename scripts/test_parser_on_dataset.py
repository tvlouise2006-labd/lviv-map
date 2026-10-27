import sys
import os
sys.path.insert(0, os.path.abspath("."))
import json
from backend.parser import parser
from backend.models import SightingAction

with open("data/raw_messages.json", "r", encoding="utf-8") as f:
    messages = json.load(f)

print(f"Testing parser on {len(messages)} messages...\n")

total = len(messages)
sighting_count = 0
noise_count = 0
with_route = 0
with_location = 0

for i, m in enumerate(messages):
    text = m["text"]
    parsed = parser.parse(text)
    
    if parsed.action == SightingAction.NOISE:
        noise_count += 1
        print(f"[{i+1}] NOISE: {text[:60]}...")
    else:
        sighting_count += 1
        if parsed.route_raw:
            with_route += 1
        if parsed.matched_stop_name:
            with_location += 1
        print(f"[{i+1}] SIGHTING: action={parsed.action.value} | route={parsed.route_raw} ({parsed.transport_type.value}) | stop={parsed.matched_stop_name} | dir={parsed.direction_hint} | conf={parsed.confidence:.2f}")
        print(f"     Text: {text}")

print("\n--- RESULTS ---")
print(f"Total messages: {total}")
print(f"Sightings: {sighting_count}")
print(f"Noise/Junk filtered: {noise_count}")
print(f"With extracted route: {with_route} ({with_route/sighting_count*100:.1f}%)")
print(f"With extracted location/stop: {with_location} ({with_location/sighting_count*100:.1f}%)")
