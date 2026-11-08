"""Building channel posts, posting them, deleting the previous one, and daily scheduling."""
import html
import logging
from datetime import datetime, time, timedelta

from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from . import config as cfg
from .persian import fmt_num, jalali_date, to_fa
from . import textrender
from .flights_image import render_flights, status_fa
from .sources import ikac, navasan

log = logging.getLogger(__name__)
JOB_PREFIX = "daily:"
JOB_FA = {"dollar": "قیمت دلار", "flights": "پروازهای IKA", "songs": "آهنگ روز"}


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
    esc = html.escape
    head = [f"✈️ <b>پروازهای خروجی فرودگاه امام خمینی (IKA)</b>", f"📅 {jalali_date(now)}", ""]
    if not flights:
        return "\n".join(head) + "امروز پرواز خروجی ثبت نشده است." + _footer()
    body = []
    for f in flights[: cfg.FLIGHTS_MAX]:
        t, sched = f.get("time", ""), f.get("scheduled", "")
        when = f"<s>{esc(sched)}</s> {esc(t)}" if sched and t and sched != t else esc(t or sched)
        main = " | ".join(esc(p) for p in (f.get("flight"), f.get("airline"), f.get("dest")) if p)
        status = f.get("status", "")
        line = f"{_status_icon(status)} <b>{when}</b> | {main}"
        extras = []
        if status:
            extras.append(esc(status_fa(status)))
        if f.get("gate"):
            extras.append(f"گیت {to_fa(esc(f['gate']))}")
        if extras:
            line += "\n      " + " · ".join(extras)
        body.append(line)
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
        summary = f"قیمت دلار ارسال شد: {fmt_num(rate.price)} تومان"
    elif name == "flights":
        flights = await ikac.fetch_departures(http, cfg.FLIGHTS_URL)
        image = None
        if cfg.FLIGHTS_STYLE == "image":
            try:
                bold = await textrender.ensure_font(http, cfg.DATA_DIR, textrender.font_urls("Bold", cfg.FONT_URL))
                regular = await textrender.ensure_font(
                    http, cfg.DATA_DIR, textrender.font_urls("Regular", cfg.FONT_REGULAR_URL)) or bold
                if bold:
                    image = render_flights(flights, now, bold, regular, cfg.SIGNATURE, cfg.FLIGHTS_MAX)
                else:
                    log.warning("Persian font could not be downloaded; sending flights as text")
            except Exception:  # noqa: BLE001 – never lose the post because of a drawing problem
                log.exception("flight image rendering failed; sending text instead")
        if image:
            caption = f"✈️ <b>پروازهای خروجی فرودگاه امام خمینی</b>\n📅 {jalali_date(now)}" + _footer()
            msg = await bot.send_photo(cfg.CHANNEL_ID, image, caption=caption, parse_mode="HTML")
        else:
            msg = await bot.send_message(cfg.CHANNEL_ID, format_flights(flights, now), parse_mode="HTML")
        summary = f"فهرست پروازها ارسال شد: {to_fa(len(flights))} پرواز خروجی"
    elif name == "songs":
        song = store.next_song()
        if not song:
            raise JobError("هیچ آهنگی ذخیره نشده است. اول از 🎵 آهنگ‌ها ← ➕ افزودن آهنگ، آهنگ اضافه کنید.")
        caption = cfg.SIGNATURE or None
        if song["kind"] == "audio":
            msg = await bot.send_audio(cfg.CHANNEL_ID, song["file_id"], caption=caption)
        else:
            msg = await bot.send_document(cfg.CHANNEL_ID, song["file_id"], caption=caption)
        store.mark_song_sent(song["id"])
        summary = f"آهنگ ارسال شد: {song['title'] or 'بدون نام'}"
    else:
        raise JobError(f"کار ناشناخته: {name}")

    prev = job.get("last_msg_id")
    store.update_job(name, last_msg_id=msg.message_id, last_sent_at=now.isoformat(timespec="minutes"))
    if job.get("delete_prev") and prev:
        try:
            await bot.delete_message(cfg.CHANNEL_ID, prev)
            summary += " (پست قبلی حذف شد)"
        except TelegramError as e:
            summary += f" (حذف پست قبلی انجام نشد: {e.message})"
    if song is not None and store.songs_left_in_round() == store.song_count():
        summary += "\nℹ️ همه آهنگ‌ها یک‌بار ارسال شدند؛ از فردا فهرست از اول تکرار می‌شود."
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
            await notify_admins(context.application, f"⚠️ ارسال «{JOB_FA[name]}» انجام نشد: {e}\n۵ دقیقه دیگر دوباره تلاش می‌کنم.")
        else:
            await notify_admins(context.application, f"❌ ارسال «{JOB_FA[name]}» دوباره انجام نشد: {e}")


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
    t = jobs[0].next_t.astimezone(cfg.TZ)
    day = "امروز" if t.date() == datetime.now(cfg.TZ).date() else "فردا"
    return f"{day} ساعت {to_fa(t.strftime('%H:%M'))}"
