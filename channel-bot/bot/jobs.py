"""Building channel posts, posting them, deleting the previous one, and daily scheduling."""
import logging
from datetime import datetime, time, timedelta

from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from . import config as cfg
from .persian import fmt_num, jalali_date, to_fa
from .sources import ikac, navasan

log = logging.getLogger(__name__)
JOB_PREFIX = "daily:"


class JobError(Exception):
    pass


# ------------------------------------------------------------------ message builders
def _footer() -> str:
    return f"\n\n{cfg.SIGNATURE}" if cfg.SIGNATURE else ""


def format_dollar(rate: navasan.Rate, now: datetime) -> str:
    lines = [f"💵 <b>قیمت {rate.title}</b>", f"📅 {jalali_date(now)}", "",
             f"💰 <b>{fmt_num(rate.price)}</b> تومان"]
    if rate.change_pct is not None:
        arrow = "🔺" if rate.change_pct > 0 else "🔻" if rate.change_pct < 0 else "⏺"
        lines.append(f"{arrow} تغییر: ‎{to_fa(f'{rate.change_pct:+.2f}')}٪")
    elif rate.change_abs is not None:
        arrow = "🔺" if rate.change_abs > 0 else "🔻" if rate.change_abs < 0 else "⏺"
        lines.append(f"{arrow} تغییر: ‎{to_fa(f'{rate.change_abs:+,.0f}')} تومان")
    if rate.high and rate.low:
        lines.append(f"📈 بیشترین امروز: {fmt_num(rate.high)}")
        lines.append(f"📉 کمترین امروز: {fmt_num(rate.low)}")
    if rate.updated:
        lines.append(f"🕒 آخرین بروزرسانی: {to_fa(rate.updated)}")
    lines.append(f"\nمنبع: {rate.source}")
    return "\n".join(lines) + _footer()


def _status_icon(status: str) -> str:
    s = status.lower()
    if any(w in s for w in ("cancel", "لغو", "باطل")):
        return "❌"
    if any(w in s for w in ("delay", "تاخیر", "تأخیر")):
        return "⏳"
    if any(w in s for w in ("depart", "پرید", "پرواز کرد", "airborne")):
        return "🛫"
    if any(w in s for w in ("board", "سوار", "gate", "گیت")):
        return "🚶"
    return "🕒"


def format_flights(flights: list[dict], now: datetime) -> str:
    head = [f"✈️ <b>پروازهای خروجی فرودگاه امام خمینی (IKA)</b>", f"📅 {jalali_date(now)}", ""]
    body = []
    for f in flights[: cfg.FLIGHTS_MAX]:
        parts = [f.get("time", ""), f.get("flight", ""), f.get("airline", ""), f.get("dest", "")]
        line = " | ".join(p for p in parts if p)
        status = f.get("status", "")
        icon = _status_icon(status)
        body.append(f"{icon} {line}" + (f" — {status}" if status else ""))
    text = "\n".join(head + body)
    extra = len(flights) - len(body)
    while len(text) > 3900 and body:          # Telegram limit is 4096 chars
        body.pop()
        extra += 1
        text = "\n".join(head + body)
    if extra > 0:
        text += f"\n\n… و {to_fa(extra)} پرواز دیگر"
    return text + "\n\nمنبع: ikac.ir" + _footer()


# ------------------------------------------------------------------ posting
async def post_job(app: Application, name: str) -> str:
    """Posts one job to the channel. Returns a short summary for the admin."""
    store = app.bot_data["store"]
    http = app.bot_data["http"]
    bot = app.bot
    now = datetime.now(cfg.TZ)
    job = store.job(name)
    song = None

    if name == "dollar":
        rate = await navasan.fetch_rate(http, cfg.NAVASAN_ITEM, cfg.NAVASAN_API_KEY, cfg.NAVASAN_API_URL)
        msg = await bot.send_message(cfg.CHANNEL_ID, format_dollar(rate, now), parse_mode="HTML")
        summary = f"Dollar rate posted: {rate.price:,.0f} toman"
    elif name == "flights":
        flights = await ikac.fetch_departures(http, cfg.FLIGHTS_URL)
        msg = await bot.send_message(cfg.CHANNEL_ID, format_flights(flights, now), parse_mode="HTML")
        summary = f"Flights posted: {len(flights)} departures"
    elif name == "songs":
        song = store.next_song()
        if not song:
            raise JobError("No songs cached. Use 🎵 Songs → ➕ Add songs first.")
        caption = cfg.SIGNATURE or None
        if song["kind"] == "audio":
            msg = await bot.send_audio(cfg.CHANNEL_ID, song["file_id"], caption=caption)
        else:
            msg = await bot.send_document(cfg.CHANNEL_ID, song["file_id"], caption=caption)
        store.mark_song_sent(song["id"])
        summary = f"Song posted: {song['title'] or 'untitled'}"
    else:
        raise JobError(f"Unknown job {name}")

    prev = job.get("last_msg_id")
    store.update_job(name, last_msg_id=msg.message_id, last_sent_at=now.isoformat(timespec="minutes"))
    if job.get("delete_prev") and prev:
        try:
            await bot.delete_message(cfg.CHANNEL_ID, prev)
            summary += " (previous post deleted)"
        except TelegramError as e:
            summary += f" (couldn't delete previous post: {e.message})"
    if song is not None and store.songs_left_in_round() == store.song_count():
        summary += "\nℹ️ Every cached song has now been played once; the list starts over tomorrow."
    return summary


async def notify_admins(app: Application, text: str) -> None:
    for admin in cfg.ADMIN_IDS:
        try:
            await app.bot.send_message(admin, text)
        except TelegramError:
            log.warning("Could not message admin %s", admin)


async def _scheduled(context: ContextTypes.DEFAULT_TYPE) -> None:
    name, is_retry = context.job.data
    try:
        summary = await post_job(context.application, name)
        log.info(summary)
    except Exception as e:  # noqa: BLE001 – report every failure to the admins
        log.exception("Job %s failed", name)
        if not is_retry:
            context.job_queue.run_once(_scheduled, when=timedelta(minutes=5), data=(name, True),
                                       name=f"retry:{name}")
            await notify_admins(context.application, f"⚠️ {name} failed: {e}\nRetrying in 5 minutes.")
        else:
            await notify_admins(context.application, f"❌ {name} failed again: {e}")


# ------------------------------------------------------------------ scheduling
def parse_hhmm(value: str) -> tuple[int, int]:
    h, m = value.split(":")
    return int(h), int(m)


def schedule_job(app: Application, name: str) -> None:
    for j in app.job_queue.get_jobs_by_name(JOB_PREFIX + name):
        j.schedule_removal()
    job = app.bot_data["store"].job(name)
    if not job["enabled"]:
        return
    h, m = parse_hhmm(job["time"])
    app.job_queue.run_daily(_scheduled, time=time(h, m, tzinfo=cfg.TZ), data=(name, False),
                            name=JOB_PREFIX + name)


def schedule_all(app: Application) -> None:
    for name in ("dollar", "flights", "songs"):
        schedule_job(app, name)


def next_run(app: Application, name: str) -> str:
    jobs = app.job_queue.get_jobs_by_name(JOB_PREFIX + name)
    if not jobs or not jobs[0].next_t:
        return "—"
    return jobs[0].next_t.astimezone(cfg.TZ).strftime("%a %H:%M")
