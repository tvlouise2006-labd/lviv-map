import json

with open("data/raw_messages.json", encoding="utf-8") as f:
    msgs = json.load(f)

print(f"Total messages: {len(msgs)}")
for i, m in enumerate(msgs[-25:]):
    print(f"\n--- [{i+1}] {m['datetime']} ({m['post_id']}) ---")
    print(m['text'])
