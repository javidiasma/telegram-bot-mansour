"""Entry point:  python -m bot"""
import logging

import httpx
from telegram import BotCommand, Update
from telegram.ext import Application, Defaults

from . import config as cfg
from . import handlers
from .jobs import notify_admins, schedule_all
from .storage import Storage

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


async def on_startup(app: Application) -> None:
    app.bot_data["store"] = Storage(cfg.DATA_DIR / "bot.sqlite3", cfg.DEFAULT_TIMES)
    app.bot_data["http"] = httpx.AsyncClient(
        timeout=30, follow_redirects=True, proxy=cfg.PROXY_URL,
        headers={"User-Agent": BROWSER_UA, "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8"},
    )
    schedule_all(app)
    await app.bot.set_my_commands([
        BotCommand("start", "Open the admin panel"),
        BotCommand("cancel", "Cancel the current step"),
        BotCommand("id", "Show your Telegram user id"),
    ])
    log.info("Bot started; channel=%s admins=%s", cfg.CHANNEL_ID, cfg.ADMIN_IDS)
    await notify_admins(app, "🤖 Bot started. Send /start for the admin panel.")


async def on_shutdown(app: Application) -> None:
    http = app.bot_data.get("http")
    if http:
        await http.aclose()


def main() -> None:
    cfg.validate()
    app = (Application.builder()
           .token(cfg.BOT_TOKEN)
           .defaults(Defaults(tzinfo=cfg.TZ))
           .post_init(on_startup)
           .post_shutdown(on_shutdown)
           .build())
    handlers.register(app)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
