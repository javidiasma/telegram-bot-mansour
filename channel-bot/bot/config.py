"""All settings come from environment variables (set them in Railway → Variables)."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo


def _int_set(value: str) -> set[int]:
    return {int(x) for x in value.replace(" ", "").split(",") if x}


def _chat_id(value: str):
    value = value.strip()
    if value.lstrip("-").isdigit():
        return int(value)
    return value if value.startswith("@") or not value else "@" + value


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = _int_set(os.getenv("ADMIN_IDS", ""))
CHANNEL_ID = _chat_id(os.getenv("CHANNEL_ID", ""))

TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Tehran"))
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))

# Default daily times (HH:MM, Tehran time) – the admin can change them from the bot.
DEFAULT_TIMES = {
    "dollar": os.getenv("DEFAULT_DOLLAR_TIME", "10:00"),
    "flights": os.getenv("DEFAULT_FLIGHTS_TIME", "08:00"),
    "songs": os.getenv("DEFAULT_SONGS_TIME", "21:00"),
}

# Dollar rate source
NAVASAN_ITEM = os.getenv("NAVASAN_ITEM", "usd_sell")          # page: navasan.net/dayRates.php?item=usd_sell
NAVASAN_API_KEY = os.getenv("NAVASAN_API_KEY", "")            # optional official API key (navasan.net/api)
NAVASAN_API_URL = os.getenv("NAVASAN_API_URL", "http://api.navasan.tech/latest/")

# IKA flights source
FLIGHTS_URL = os.getenv("FLIGHTS_URL", "https://www.ikac.ir/en-US/airport.ikac/5257/page/Flights")
FLIGHTS_MAX = int(os.getenv("FLIGHTS_MAX", "40"))

# Optional HTTP proxy, e.g. http://user:pass@1.2.3.4:8080 (useful if an Iranian site blocks foreign servers)
PROXY_URL = os.getenv("PROXY_URL") or None

# AI image: uses OpenAI if OPENAI_API_KEY is set, otherwise the free Pollinations service
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
POLLINATIONS_URL = os.getenv("POLLINATIONS_URL", "https://image.pollinations.ai/prompt/")
FONT_URL = os.getenv(
    "FONT_URL",
    "https://raw.githubusercontent.com/rastikerdar/vazirmatn/master/fonts/ttf/Vazirmatn-Bold.ttf",
)

# Optional line added under every channel post, e.g. "@my_channel"
SIGNATURE = os.getenv("SIGNATURE", "")


def validate() -> None:
    missing = [n for n, v in (("BOT_TOKEN", BOT_TOKEN), ("ADMIN_IDS", ADMIN_IDS), ("CHANNEL_ID", CHANNEL_ID)) if not v]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
