import urllib.request
import re
import html
import json
import os
import time

def fetch_channel_messages(channel: str = "kontrollviv", max_messages: int = 150):
    all_messages = {}
    before = None
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    pattern = re.compile(
        r'<div class="tgme_widget_message\b[^"]*"\s+data-post="([^"]+)".*?'
        r'<time\s+datetime="([^"]+)".*?'
        r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
        re.DOTALL
    )

    id_pattern = re.compile(rf'{channel}/(\d+)')

    while len(all_messages) < max_messages:
        url = f"https://t.me/s/{channel}"
        if before:
            url += f"?before={before}"
        print(f"Fetching {url}...")
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read().decode("utf-8")
        except Exception as e:
            print(f"Error: {e}")
            break

        matches = pattern.findall(content)
        if not matches:
            print("No more messages found.")
            break

        old_count = len(all_messages)
        min_id = None
        for post_id, dt, raw_text in matches:
            m_id_match = id_pattern.search(post_id)
            if m_id_match:
                num_id = int(m_id_match.group(1))
                if min_id is None or num_id < min_id:
                    min_id = num_id

            text = re.sub(r'<br\s*/?>', '\n', raw_text)
            text = re.sub(r'<[^>]+>', '', text)
            text = html.unescape(text).strip()
            
            all_messages[post_id] = {
                "post_id": post_id,
                "datetime": dt,
                "text": text
            }

        print(f"Total unique messages collected: {len(all_messages)} (min_id: {min_id})")
        if min_id is None or len(all_messages) == old_count:
            break
        before = min_id
        time.sleep(1.0) # polite rate limit

    os.makedirs("data", exist_ok=True)
    msg_list = sorted(all_messages.values(), key=lambda x: x["datetime"])
    with open("data/raw_messages.json", "w", encoding="utf-8") as f:
        json.dump(msg_list, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(msg_list)} messages to data/raw_messages.json")
    return msg_list

if __name__ == "__main__":
    fetch_channel_messages("kontrollviv", max_messages=150)
