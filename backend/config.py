import os
import secrets
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseModel):
    # Telegram settings
    TELEGRAM_CHANNEL: str = os.getenv("TELEGRAM_CHANNEL", "kontrollviv")
    TELEGRAM_API_ID: int = int(os.getenv("TELEGRAM_API_ID", "0"))
    TELEGRAM_API_HASH: str = os.getenv("TELEGRAM_API_HASH", "")
    TELEGRAM_POLL_INTERVAL: int = int(os.getenv("TELEGRAM_POLL_INTERVAL", "10"))  # seconds for web poll

    # GTFS settings
    GTFS_STATIC_URL: str = os.getenv("GTFS_STATIC_URL", "http://track.ua-gis.com/gtfs/lviv/static.zip")
    GTFS_RT_URL: str = os.getenv("GTFS_RT_URL", "https://track.ua-gis.com/gtfs/lviv/vehicle_position")
    VEHICLE_POLL_INTERVAL: int = int(os.getenv("VEHICLE_POLL_INTERVAL", "7"))  # seconds

    # Sighting lifecycle (minutes) - strictly 20 minutes from channel message post time
    SIGHTING_TTL_MINUTES: int = int(os.getenv("SIGHTING_TTL_MINUTES", "20"))

    # Optional Gemini API key
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    # Optional CARTO API key
    CARTO_API_KEY: str = os.getenv("CARTO_API_KEY", "")

    # Security & Admin settings
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "lviv-admin-2026")
    ADMIN_SECRET_KEY: str = os.getenv("ADMIN_SECRET_KEY", secrets.token_hex(32))
    ALLOWED_ORIGINS: str = os.getenv("ALLOWED_ORIGINS", "")  # Comma-separated origins, empty = same-origin only
    ENABLE_SWAGGER_DOCS: bool = os.getenv("ENABLE_SWAGGER_DOCS", "false").lower() == "true"

    # Server settings
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

settings = Settings()
