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
FLIGHTS_MAX = int(os.getenv("FLIGHTS_MAX", "30"))

# Optional HTTP proxy, e.g. http://user:pass@1.2.3.4:8080 (useful if an Iranian site blocks foreign servers)
PROXY_URL = os.getenv("PROXY_URL") or None

# AI image: any OpenAI-compatible /images/generations API (e.g. GapGPT). Falls back to the chat settings.
IMAGE_API_BASE_URL = os.getenv("IMAGE_API_BASE_URL") or os.getenv("CHAT_API_BASE_URL") or "https://api.gapgpt.app/v1"
IMAGE_API_KEY = os.getenv("IMAGE_API_KEY") or os.getenv("CHAT_API_KEY") or os.getenv("OPENAI_API_KEY", "")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1")

# Optional OpenAI-compatible CHAT model that rewrites the admin's text into a better English image prompt.
# Image generation itself still uses Pollinations/OpenAI above. Leave empty to skip this step.
CHAT_API_BASE_URL = os.getenv("CHAT_API_BASE_URL", "")
CHAT_API_KEY = os.getenv("CHAT_API_KEY", "")
CHAT_MODEL = os.getenv("CHAT_MODEL", "")

# Optional custom font URLs (a Persian .ttf). By default Vazirmatn is downloaded automatically.
FONT_URL = os.getenv("FONT_URL", "")
FONT_REGULAR_URL = os.getenv("FONT_REGULAR_URL", "")
# "image" = designed flight-board picture, "text" = plain text message
FLIGHTS_STYLE = os.getenv("FLIGHTS_STYLE", "image").lower()

# Optional line added under every channel post, e.g. "@my_channel"
SIGNATURE = os.getenv("SIGNATURE", "")

# ---- normal (non-admin) users
# Welcome message shown to users on /start. Use \n for a new line.
WELCOME_TEXT = os.getenv("WELCOME_TEXT", "").replace("\\n", "\n") or (
    "سلام {name} 👋\n"
    "من کارمند هوشمند منصورتراولز هستم. چطور می‌تونم کمکت کنم؟\n\n"
    "برای دیدن پروازهای خروجی امروز فرودگاه امام خمینی، دکمه پایین را بزن 👇"
)
# Users must be members of the channel before using the bot. Message shown to non-members:
JOIN_TEXT = os.getenv("JOIN_TEXT", "").replace("\\n", "\n") or (
    "برای استفاده از خدمات ربات، اول عضو کانال ما شوید 🙏\n\n"
    "بعد از عضویت، دکمه «✅ دنبال کردم» را بزنید."
)
# Join link for the button. Not needed if CHANNEL_ID is @username; for a private/numeric channel set an invite link.
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "")
# How long (minutes) one fetch of the flights list is reused for users, so many clicks don't hit ikac.ir each time
USER_FLIGHTS_CACHE_MIN = int(os.getenv("USER_FLIGHTS_CACHE_MIN", "10"))


def validate() -> None:
    missing = [n for n, v in (("BOT_TOKEN", BOT_TOKEN), ("ADMIN_IDS", ADMIN_IDS), ("CHANNEL_ID", CHANNEL_ID)) if not v]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
