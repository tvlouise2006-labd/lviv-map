import asyncio
import html
import re
import time
import urllib.request
from typing import List, Dict
from backend.config import settings
from backend.state import state_store

POST_ID_RE = re.compile(r'^"([^"]+)"')
MAIN_TEXT_BLOCK_RE = re.compile(
    r'<div class="tgme_widget_message_text\s+js-message_text\b[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL
)
ANY_TEXT_BLOCK_RE = re.compile(
    r'<div class="tgme_widget_message_text\b(?![^"]*reply_text)[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL
)
TIME_RE = re.compile(r'<time\b[^>]*datetime="([^"]+)"')


def fetch_public_channel_web(channel: str = "kontrollviv") -> List[Dict[str, str]]:
    """
    Fetches the latest messages from public Telegram web preview t.me/s/<channel> with cache-busting.
    Parses each message block independently so `<div class="tgme_widget_message_text">` (above)
    and `<time datetime="...">` (below in footer) are always paired to their exact message.
    """
    url = f"https://t.me/s/{channel}?_={int(time.time() * 1000)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        }
    )

    content = ""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read().decode("utf-8")
            break
        except Exception as e:
            if attempt == 2:
                print(f"[Ingestor] Error fetching {url}: {e}")
                return []
            time.sleep(0.4)

    blocks = content.split("data-post=")
    results: List[Dict[str, str]] = []

    for block in blocks[1:]:
        id_match = POST_ID_RE.match(block)
        if not id_match:
            continue
        post_id = id_match.group(1)

        time_match = TIME_RE.search(block)
        text_match = MAIN_TEXT_BLOCK_RE.search(block) or ANY_TEXT_BLOCK_RE.search(block)
        if not time_match or not text_match:
            continue

        dt_str = time_match.group(1)
        raw_html = text_match.group(1)

        text = re.sub(r"<br\s*/?>", "\n", raw_html)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text).strip()
        if not text:
            continue

        results.append({
            "post_id": post_id,
            "datetime": dt_str,
            "text": text,
        })

    return results


class TelegramIngestor:
    def __init__(self):
        self.channel = settings.TELEGRAM_CHANNEL
        self.initialized = False

    def seed_initial_messages(self):
        """
        Loads latest channel messages on startup.
        Only imports messages sent within the last SIGHTING_TTL_MINUTES (20 minutes)
        using their exact channel sent timestamp.
        """
        msgs = fetch_public_channel_web(self.channel)
        if not msgs:
            self.initialized = True
            return

        ingested = 0
        for m in msgs:
            # Use fast rule-based extraction on initial startup seed so container boot never blocks on LLM API calls
            s = state_store.ingest_raw_message(
                msg_id=m["post_id"],
                text=m["text"],
                posted_at_iso=m["datetime"],
                use_llm=False
            )
            if s:
                ingested += 1

        state_store.sync_sightings_with_vehicles()
        self.initialized = True
        print(f"[Ingestor] Seeded {ingested} active sightings (< {settings.SIGHTING_TTL_MINUTES} min old) from @{self.channel}.")

    async def poll_loop(self):
        """Background loop polling public Telegram channel web preview & synchronizing active sightings."""
        while True:
            await asyncio.sleep(settings.TELEGRAM_POLL_INTERVAL)
            try:
                msgs = await asyncio.to_thread(fetch_public_channel_web, self.channel)
                changed = False
                for m in msgs:
                    if m["post_id"] not in state_store.processed_msg_ids:
                        s = await asyncio.to_thread(
                            state_store.ingest_raw_message,
                            m["post_id"],
                            m["text"],
                            m["datetime"],
                            False,
                            True
                        )
                        if s:
                            changed = True

                # Always sync & expire messages older than 20 minutes
                before_count = len(state_store.sightings)
                active_list = state_store.get_active_sightings()
                if changed or len(state_store.sightings) != before_count:
                    await state_store.broadcast(
                        "sightings_update",
                        {"sightings": [s.model_dump() for s in active_list]}
                    )
            except Exception as e:
                print(f"[Ingestor] Poll error: {e}")


ingestor = TelegramIngestor()
