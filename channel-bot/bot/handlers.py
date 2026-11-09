"""پنل مدیریت: منوهای دکمه‌ای در چت خصوصی ربات."""
import asyncio
import html
import logging
import re
import time
from datetime import datetime

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M, ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, TypeHandler, filters)

from . import config as cfg
from . import imagegen, textrender
from .jobs import build_flights, next_run, post_job, schedule_job
from .persian import to_en, to_fa

log = logging.getLogger(__name__)

TITLES = {"dollar": "💵 قیمت دلار", "flights": "✈️ پروازهای خروجی IKA", "songs": "🎵 آهنگ روز"}
TIME_RE = re.compile(r"^\s*(\d{1,2})\s*[:.]\s*(\d{2})\s*$")
MENU_TEXT = "پنل مدیریت — یکی از گزینه‌ها را انتخاب کنید:"


def yes_no(v: bool) -> str:
    return "بله" if v else "خیر"


# ------------------------------------------------------------------ keyboards
def main_menu() -> M:
    return M([
        [B(TITLES["dollar"], callback_data="job:dollar"), B("✈️ پروازهای IKA", callback_data="job:flights")],
        [B("🎵 آهنگ‌ها", callback_data="job:songs"), B("🎨 ساخت تصویر با هوش مصنوعی", callback_data="ai:start")],
        [B("📊 وضعیت", callback_data="status")],
    ])


def job_menu(store, name: str) -> M:
    job = store.job(name)
    rows = [
        [B("🚀 ارسال همین حالا", callback_data=f"now:{name}"), B("⏰ تغییر ساعت", callback_data=f"time:{name}")],
        [B("✅ روشن" if job["enabled"] else "⛔ خاموش", callback_data=f"toggle:{name}"),
         B(f"🗑 حذف پست قبلی: {'روشن' if job['delete_prev'] else 'خاموش'}", callback_data=f"delprev:{name}")],
    ]
    if name == "songs":
        rows.insert(0, [B("➕ افزودن آهنگ", callback_data="songs:add"), B("📃 فهرست آهنگ‌ها", callback_data="songs:list")])
        rows.append([B("🧹 حذف همه آهنگ‌ها", callback_data="songs:clear")])
    rows.append([B("⬅️ بازگشت", callback_data="menu")])
    return M(rows)


def job_text(app: Application, name: str) -> str:
    store = app.bot_data["store"]
    job = store.job(name)
    last = job["last_sent_at"].replace("T", " ")[:16] if job["last_sent_at"] else "—"
    lines = [f"<b>{TITLES[name]}</b>",
             f"ساعت ارسال روزانه: <b>{to_fa(job['time'])}</b> (به وقت تهران)",
             f"وضعیت: {'✅ روشن' if job['enabled'] else '⛔ خاموش'} · اجرای بعدی: {next_run(app, name)}",
             f"حذف پست قبلی: {yes_no(job['delete_prev'])}",
             f"آخرین ارسال: {to_fa(last)}"]
    if name == "songs":
        lines.append(f"تعداد آهنگ‌های ذخیره‌شده: <b>{to_fa(store.song_count())}</b>")
    return "\n".join(lines)


# ------------------------------------------------------------------ guards
async def admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """قبل از همه هندلرها اجرا می‌شود. ادمین در چت خصوصی به پنل مدیریت می‌رود؛
    کاربر عادی در چت خصوصی منوی کاربران را می‌بیند؛ بقیه (گروه، کانال و ...) نادیده گرفته می‌شوند."""
    user, chat = update.effective_user, update.effective_chat
    if chat and chat.type == "private" and user and user.id in cfg.ADMIN_IDS:
        return
    if chat and chat.type == "private" and user:
        try:
            await user_router(update, context)
        except Exception:  # noqa: BLE001
            log.exception("user handler failed")
    raise ApplicationHandlerStop


# ------------------------------------------------------------------ normal users
USER_FLIGHTS_BTN = "✈️ پروازهای خروجی امروز IKA"


def user_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[USER_FLIGHTS_BTN]], resize_keyboard=True, is_persistent=True)


def welcome_text(user) -> str:
    return cfg.WELCOME_TEXT.replace("{name}", user.first_name or "")


MEMBER_STATUSES = {"member", "administrator", "creator", "owner"}


async def is_channel_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """عضویت کاربر در کانال را بررسی می‌کند (ربات باید ادمین کانال باشد).
    اگر بررسی ممکن نباشد (مثلاً ربات ادمین کانال نیست)، کاربر رد نمی‌شود تا ربات برای همه قفل نشود."""
    try:
        m = await context.application.bot.get_chat_member(cfg.CHANNEL_ID, user_id)
    except Exception:  # noqa: BLE001
        log.exception("could not check channel membership of %s", user_id)
        return True
    status = str(getattr(m.status, "value", m.status))
    return status in MEMBER_STATUSES or (status == "restricted" and bool(getattr(m, "is_member", False)))


async def channel_link(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    if cfg.CHANNEL_LINK:
        return cfg.CHANNEL_LINK
    if isinstance(cfg.CHANNEL_ID, str) and cfg.CHANNEL_ID.startswith("@"):
        return f"https://t.me/{cfg.CHANNEL_ID[1:]}"
    bd = context.application.bot_data
    if "channel_link" not in bd:       # numeric channel id → ask Telegram once
        try:
            chat = await context.application.bot.get_chat(cfg.CHANNEL_ID)
            bd["channel_link"] = (f"https://t.me/{chat.username}" if chat.username else chat.invite_link)
        except Exception:  # noqa: BLE001
            log.exception("could not get channel link")
            bd["channel_link"] = None
    return bd["channel_link"]


async def join_gate_markup(context: ContextTypes.DEFAULT_TYPE) -> M:
    rows = []
    link = await channel_link(context)
    if link:
        rows.append([B("📢 عضویت در کانال", url=link)])
    rows.append([B("✅ دنبال کردم", callback_data="u:joined")])
    return M(rows)


async def send_join_gate(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.application.bot.send_message(chat_id, cfg.JOIN_TEXT, reply_markup=await join_gate_markup(context))


async def user_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, user = update.message, update.effective_user
    if update.callback_query:
        q = update.callback_query
        if q.data != "u:joined":       # e.g. an old admin button pressed by someone who is no longer admin
            await q.answer()
            return
        if not await is_channel_member(context, user.id):
            await q.answer("هنوز عضو کانال نشده‌اید. اول عضو شوید، بعد «دنبال کردم» را بزنید.", show_alert=True)
            return
        await q.answer("✅ عضویت شما تأیید شد")
        await q.edit_message_text("✅ عضویت شما در کانال تأیید شد. ممنون!")
        await context.application.bot.send_message(q.message.chat_id, welcome_text(user),
                                                   reply_markup=user_keyboard())
        return
    if not msg:
        return
    text = (msg.text or "").strip()
    if text.startswith("/id"):
        await msg.reply_text(f"شناسه تلگرام شما: <code>{user.id}</code>", parse_mode=ParseMode.HTML)
    elif not await is_channel_member(context, user.id):
        await send_join_gate(msg.chat_id, context)
    elif text == USER_FLIGHTS_BTN or text.startswith("/flights"):
        await send_user_flights(msg.chat_id, context)
    else:                              # /start and anything else → welcome
        await msg.reply_text(welcome_text(user), reply_markup=user_keyboard())


async def _user_flights_post(app: Application) -> dict:
    """همان پست کانال را می‌سازد و چند دقیقه نگه می‌دارد تا هر کلیک کاربر به ikac.ir درخواست نفرستد."""
    lock = app.bot_data.setdefault("user_flights_lock", asyncio.Lock())
    async with lock:                   # many users clicking at once → only one fetch
        cached = app.bot_data.get("user_flights")
        if cached and time.monotonic() - cached["at"] < cfg.USER_FLIGHTS_CACHE_MIN * 60:
            return cached
        image, text, _ = await build_flights(app, datetime.now(cfg.TZ))
        cached = {"at": time.monotonic(), "image": image, "photo_id": None, "text": text}
        app.bot_data["user_flights"] = cached
        return cached


async def send_user_flights(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    app = context.application
    await app.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
    try:
        post = await _user_flights_post(app)
    except Exception:  # noqa: BLE001
        log.exception("user flights fetch failed")
        await app.bot.send_message(chat_id, "متأسفانه الان دریافت فهرست پروازها ممکن نشد. چند دقیقه دیگر دوباره امتحان کنید 🙏",
                                   reply_markup=user_keyboard())
        return
    if post["image"] or post["photo_id"]:
        sent = await app.bot.send_photo(chat_id, post["photo_id"] or post["image"], caption=post["text"],
                                        parse_mode=ParseMode.HTML, reply_markup=user_keyboard())
        post["photo_id"] = sent.photo[-1].file_id   # later clicks reuse the uploaded photo
    else:
        await app.bot.send_message(chat_id, post["text"], parse_mode=ParseMode.HTML, reply_markup=user_keyboard())


# ------------------------------------------------------------------ commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("await", None)
    await update.message.reply_text(MENU_TEXT, reply_markup=main_menu())


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("await", None)
    await update.message.reply_text("لغو شد.", reply_markup=main_menu())


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"شناسه تلگرام شما: <code>{update.effective_user.id}</code>",
                                    parse_mode=ParseMode.HTML)


# ------------------------------------------------------------------ button presses
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    app = context.application
    store = app.bot_data["store"]
    action, _, arg = q.data.partition(":")

    if action == "menu":
        context.user_data.pop("await", None)
        await q.edit_message_text(MENU_TEXT, reply_markup=main_menu())

    elif action == "job":
        await q.edit_message_text(job_text(app, arg), parse_mode=ParseMode.HTML, reply_markup=job_menu(store, arg))

    elif action == "now":
        await q.edit_message_text(f"⏳ در حال ارسال {TITLES[arg]}…")
        try:
            result = "✅ " + html.escape(await post_job(app, arg))
        except Exception as e:  # noqa: BLE001
            log.exception("manual %s failed", arg)
            result = f"❌ ارسال انجام نشد: {html.escape(str(e))}"
        await q.edit_message_text(f"{result}\n\n{job_text(app, arg)}", parse_mode=ParseMode.HTML,
                                  reply_markup=job_menu(store, arg))

    elif action == "time":
        context.user_data["await"] = ("time", arg)
        await q.edit_message_text(
            f"ساعت جدید ارسال روزانه برای {TITLES[arg]} را به شکل <b>ساعت:دقیقه</b> بفرستید "
            "(۲۴ ساعته، به وقت تهران). مثال: <code>09:30</code> یا <code>۲۱:۰۰</code>\n"
            "برای انصراف /cancel را بفرستید.", parse_mode=ParseMode.HTML)

    elif action == "toggle":
        store.update_job(arg, enabled=not store.job(arg)["enabled"])
        schedule_job(app, arg)
        await q.edit_message_text(job_text(app, arg), parse_mode=ParseMode.HTML, reply_markup=job_menu(store, arg))

    elif action == "delprev":
        store.update_job(arg, delete_prev=not store.job(arg)["delete_prev"])
        await q.edit_message_text(job_text(app, arg), parse_mode=ParseMode.HTML, reply_markup=job_menu(store, arg))

    elif action == "songs":
        await songs_button(q, context, arg)

    elif action == "ai":
        await ai_button(q, context, arg)

    elif action == "status":
        parts = [f"کانال: <code>{html.escape(str(cfg.CHANNEL_ID))}</code>"]
        parts += [job_text(app, n) for n in ("dollar", "flights", "songs")]
        parts.append(f"سرویس ساخت تصویر: {html.escape(cfg.IMAGE_API_BASE_URL)} · مدل {html.escape(cfg.IMAGE_MODEL)}")
        if cfg.CHAT_API_KEY:
            parts.append(f"نوشتن توضیح تصویر با مدل: {html.escape(cfg.CHAT_MODEL)}")
        await q.edit_message_text("\n\n".join(parts), parse_mode=ParseMode.HTML,
                                  reply_markup=M([[B("⬅️ بازگشت", callback_data="menu")]]))


# ------------------------------------------------------------------ songs
DONE_BTN = M([[B("✅ تمام شد", callback_data="songs:done")]])


async def songs_button(q, context, arg: str) -> None:
    store = context.application.bot_data["store"]
    if arg == "add":
        context.user_data["await"] = ("songs", 0)
        await q.edit_message_text(
            "🎵 هر تعداد آهنگ که می‌خواهید بفرستید یا فوروارد کنید (فایل صوتی یا فایل موسیقی).\n"
            "همه ذخیره می‌شوند و هر روز در ساعت تعیین‌شده یکی از آن‌ها در کانال ارسال می‌شود.\n\n"
            "وقتی تمام شد، دکمه <b>تمام شد</b> را بزنید.", parse_mode=ParseMode.HTML, reply_markup=DONE_BTN)
    elif arg == "done":
        added = context.user_data.pop("await", ("songs", 0))
        n = added[1] if added and added[0] == "songs" else 0
        await q.edit_message_text(f"✅ {to_fa(n)} آهنگ جدید اضافه شد. مجموع: {to_fa(store.song_count())}\n\n"
                                  + job_text(context.application, "songs"),
                                  parse_mode=ParseMode.HTML, reply_markup=job_menu(store, "songs"))
    elif arg == "list":
        songs = store.list_songs(40)
        if not songs:
            text = "هنوز آهنگی ذخیره نشده است."
        else:
            text = "\n".join(f"#{s['id']} · {html.escape(s['title'] or 'بدون نام')}"
                             f"{' — ' + html.escape(s['performer']) if s['performer'] else ''}"
                             f" · {to_fa(s['sent_count'])} بار ارسال شده" for s in songs)
            more = store.song_count() - len(songs)
            if more > 0:
                text += f"\n… و {to_fa(more)} آهنگ دیگر"
            text += "\n\nبرای حذف یک آهنگ بفرستید: <code>/delsong شماره</code>\nمثال: <code>/delsong 5</code>"
        await q.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=M([[B("⬅️ بازگشت", callback_data="job:songs")]]))
    elif arg == "clear":
        await q.edit_message_text("همه آهنگ‌های ذخیره‌شده حذف شوند؟ این کار برگشت‌پذیر نیست.",
                                  reply_markup=M([[B("🧹 بله، همه حذف شوند", callback_data="songs:clear_yes"),
                                                   B("انصراف", callback_data="job:songs")]]))
    elif arg == "clear_yes":
        n = store.clear_songs()
        await q.edit_message_text(f"{to_fa(n)} آهنگ حذف شد.\n\n" + job_text(context.application, "songs"),
                                  parse_mode=ParseMode.HTML, reply_markup=job_menu(store, "songs"))


async def on_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = context.application.bot_data["store"]
    msg = update.message
    if msg.audio:
        f, kind = msg.audio, "audio"
        title, performer = f.title or f.file_name or "", f.performer or ""
    else:
        f, kind = msg.document, "document"
        title, performer = f.file_name or "", ""
    is_new = store.add_song(f.file_id, f.file_unique_id, kind, title, performer)
    state = context.user_data.get("await")
    if is_new and state and state[0] == "songs":
        context.user_data["await"] = ("songs", state[1] + 1)
    note = "✅ ذخیره شد" if is_new else "ℹ️ این آهنگ قبلاً ذخیره شده"
    await msg.reply_text(f"{note}: {title or 'بدون نام'} (مجموع: {to_fa(store.song_count())})",
                         reply_markup=DONE_BTN)


async def del_song(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = context.application.bot_data["store"]
    arg = to_en(context.args[0]) if context.args else ""
    if not arg.isdigit():
        await update.message.reply_text("روش استفاده: /delsong شماره‌آهنگ (شماره را از 📃 فهرست آهنگ‌ها ببینید)")
        return
    ok = store.delete_song(int(arg))
    await update.message.reply_text("حذف شد." if ok else "آهنگی با این شماره پیدا نشد.")


# ------------------------------------------------------------------ AI image
async def ai_button(q, context, arg: str) -> None:
    app = context.application
    if arg == "start":
        context.user_data["await"] = ("ai_text",)
        await q.edit_message_text("🎨 متنی را که می‌خواهید روی تصویر نوشته شود بفرستید (فارسی یا انگلیسی).\n"
                                  "برای رفتن به خط بعد، Enter بزنید. برای انصراف /cancel را بفرستید.")
    elif arg == "skip":
        context.user_data.pop("await", None)
        await q.edit_message_text("⏳ در حال ساخت تصویر… (ممکن است تا یک دقیقه طول بکشد)")
        await make_and_preview(q.message.chat_id, context, None)
    elif arg == "retry":
        ai = context.user_data.get("ai")
        if not ai:
            await q.message.reply_text("چیزی برای ساخت دوباره نیست. از 🎨 ساخت تصویر دوباره شروع کنید.")
            return
        await q.message.reply_text("⏳ در حال ساخت یک نسخه دیگر…")
        await make_and_preview(q.message.chat_id, context, ai.get("description"))
    elif arg == "post":
        ai = context.user_data.get("ai")
        if not ai or not ai.get("file_id"):
            await q.message.reply_text("تصویری برای ارسال وجود ندارد.")
            return
        await app.bot.send_photo(cfg.CHANNEL_ID, ai["file_id"], caption=cfg.SIGNATURE or None)
        await q.edit_message_reply_markup(None)
        await q.message.reply_text("✅ در کانال ارسال شد.", reply_markup=main_menu())
    elif arg == "cancel":
        context.user_data.pop("ai", None)
        await q.edit_message_reply_markup(None)
        await q.message.reply_text("کنار گذاشته شد.", reply_markup=main_menu())


async def make_and_preview(chat_id: int, context: ContextTypes.DEFAULT_TYPE, description: str | None) -> None:
    app = context.application
    ai = context.user_data.setdefault("ai", {})
    ai["description"] = description
    http = app.bot_data["http"]
    try:
        better = await imagegen.write_prompt(http, cfg.CHAT_API_BASE_URL, cfg.CHAT_API_KEY, cfg.CHAT_MODEL,
                                             ai["text"], description)
        prompt = imagegen.build_prompt(ai["text"], better or description)
        bg = await imagegen.generate_background(http, prompt, cfg.IMAGE_API_BASE_URL, cfg.IMAGE_API_KEY,
                                                cfg.IMAGE_MODEL)
        font = await textrender.ensure_font(http, cfg.DATA_DIR, textrender.font_urls("Bold", cfg.FONT_URL))
        if not font:
            raise RuntimeError("فونت فارسی دانلود نشد؛ لطفاً بعداً دوباره امتحان کنید.")
        data = imagegen.render_text(bg, ai["text"], font)
    except Exception as e:  # noqa: BLE001
        log.exception("image generation failed")
        await app.bot.send_message(chat_id, f"❌ ساخت تصویر انجام نشد: {e}",
                                   reply_markup=M([[B("🔄 تلاش دوباره", callback_data="ai:retry")],
                                                   [B("⬅️ منو", callback_data="menu")]]))
        return
    sent = await app.bot.send_photo(
        chat_id, data, caption="پیش‌نمایش — در کانال ارسال شود؟",
        reply_markup=M([[B("📤 ارسال در کانال", callback_data="ai:post")],
                        [B("🔄 ساخت دوباره", callback_data="ai:retry"), B("❌ انصراف", callback_data="ai:cancel")]]))
    ai["file_id"] = sent.photo[-1].file_id


# ------------------------------------------------------------------ free text (time / AI input)
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get("await")
    text = update.message.text
    app = context.application
    store = app.bot_data["store"]

    if not state:
        await update.message.reply_text(MENU_TEXT, reply_markup=main_menu())
        return

    if state[0] == "time":
        name = state[1]
        m = TIME_RE.match(to_en(text))
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            await update.message.reply_text("لطفاً یک ساعت درست بفرستید، مثل 08:00 یا 21:30 (یا /cancel).")
            return
        value = f"{int(m.group(1)):02d}:{m.group(2)}"
        store.update_job(name, time=value, enabled=True)
        schedule_job(app, name)
        context.user_data.pop("await", None)
        await update.message.reply_text(f"✅ {TITLES[name]} هر روز ساعت {to_fa(value)} (به وقت تهران) ارسال می‌شود.\n\n"
                                         + job_text(app, name), parse_mode=ParseMode.HTML,
                                         reply_markup=job_menu(store, name))

    elif state[0] == "ai_text":
        context.user_data["ai"] = {"text": text[:400]}
        context.user_data["await"] = ("ai_prompt",)
        await update.message.reply_text(
            "حالا تصویر پس‌زمینه را توصیف کنید (توضیح به انگلیسی نتیجه بهتری می‌دهد)، مثلاً:\n"
            "<i>Tehran skyline at sunset, watercolor</i>\n"
            "یا دکمه «رد شدن» را بزنید تا هوش مصنوعی از روی متن شما تصویر بسازد.",
            parse_mode=ParseMode.HTML, reply_markup=M([[B("⏭ رد شدن", callback_data="ai:skip")]]))

    elif state[0] == "ai_prompt":
        context.user_data.pop("await", None)
        await update.message.reply_text("⏳ در حال ساخت تصویر… (ممکن است تا یک دقیقه طول بکشد)")
        await make_and_preview(update.effective_chat.id, context, text[:500])

    elif state[0] == "songs":
        await update.message.reply_text("فایل صوتی بفرستید، یا دکمه «تمام شد» را بزنید.", reply_markup=DONE_BTN)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error", exc_info=context.error)


def register(app: Application) -> None:
    app.add_handler(TypeHandler(Update, admin_only), group=-1)
    app.add_handler(CommandHandler(["start", "menu"], start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("delsong", del_song))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, on_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
