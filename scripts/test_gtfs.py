import urllib.request
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

urls = [
    "https://track.ua-gis.com/gtfs/lviv/vehicle_position",
    "http://track.ua-gis.com/gtfs/lviv/static.zip",
    "http://track.ua-gis.com/gtgs/lviv/static.zip"
]

for url in urls:
    print(f"Testing {url}...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10, context=ctx if "https" in url else None) as resp:
            content_type = resp.headers.get("Content-Type")
            length = resp.headers.get("Content-Length")
            data = resp.read(500)
            print(f"  SUCCESS: Status {resp.status}, Content-Type: {content_type}, Content-Length: {length}, preview: {len(data)} bytes")
    except Exception as e:
        print(f"  FAILED: {e}")
