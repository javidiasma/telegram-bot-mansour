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
        [B("📝 پیام عضویت کانال", callback_data="jointext:show"), B("📊 وضعیت", callback_data="status")],
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
    text = current_join_text(context.application.bot_data["store"])
    await context.application.bot.send_message(chat_id, text, reply_markup=await join_gate_markup(context))


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
    """همان پست کانال را می‌سازد و چند دقیقه نگه می‌دارد تا هر کلیک کاربر به منبع پروازها درخواست نفرستد."""
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

    elif action == "jointext":
        await jointext_button(q, context, arg)

    elif action == "status":
        parts = [f"کانال: <code>{html.escape(str(cfg.CHANNEL_ID))}</code>"]
        parts += [job_text(app, n) for n in ("dollar", "flights", "songs")]
        parts.append(f"سرویس ساخت تصویر: {html.escape(cfg.IMAGE_API_BASE_URL)} · مدل {html.escape(cfg.IMAGE_MODEL)}")
        if cfg.CHAT_API_KEY:
            parts.append(f"نوشتن توضیح تصویر با مدل: {html.escape(cfg.CHAT_MODEL)}")
        await q.edit_message_text("\n\n".join(parts), parse_mode=ParseMode.HTML,
                                  reply_markup=M([[B("⬅️ بازگشت", callback_data="menu")]]))


# ------------------------------------------------------------------ join-channel message (admin)
def current_join_text(store) -> str:
    return store.get("join_text") or cfg.JOIN_TEXT


def jointext_screen(store) -> tuple[str, M]:
    custom = bool(store.get("join_text"))
    text = ("📝 <b>پیام عضویت کانال</b>\n"
            "این پیام به کاربرانی نشان داده می‌شود که هنوز عضو کانال نیستند "
            "(زیر آن دکمه‌های «عضویت در کانال» و «دنبال کردم» می‌آید).\n\n"
            f"متن فعلی{'' if custom else ' (پیش‌فرض)'}:\n"
            f"<blockquote>{html.escape(current_join_text(store))}</blockquote>")
    rows = [[B("✏️ تغییر متن", callback_data="jointext:edit")]]
    if custom:
        rows.append([B("↩️ برگشت به متن پیش‌فرض", callback_data="jointext:reset")])
    rows.append([B("⬅️ بازگشت", callback_data="menu")])
    return text, M(rows)


async def jointext_button(q, context, arg: str) -> None:
    store = context.application.bot_data["store"]
    if arg == "edit":
        context.user_data["await"] = ("join_text",)
        await q.edit_message_text("متن جدید پیام عضویت را بفرستید.\nبرای انصراف /cancel را بفرستید.")
        return
    if arg == "reset":
        store.set("join_text", None)
    text, kb = jointext_screen(store)
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


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
        context.user_data["await"] = ("ai_request",)
        await q.edit_message_text(
            "🎨 در یک پیام بنویسید پوستر درباره چیست و چه متن‌هایی روی آن بیاید. مثلاً:\n\n"
            "<i>پوستر تور کویر مصر، ۱۰ تا ۱۲ دی، فول برد، قیمت ۱ تومن، "
            "برای اطلاعات بیشتر با ۰۲۱۴۸۴۰۷ تماس بگیر. حال‌وهوای غروب کویر با شتر، چادر و فانوس.</i>\n\n"
            "متن‌ها را دقیقاً همان‌طور که باید روی تصویر بیاید بنویسید؛ هرچه کوتاه‌تر، دقیق‌تر نوشته می‌شود.\n"
            "برای انصراف /cancel را بفرستید.", parse_mode=ParseMode.HTML)
    elif arg == "retry":
        ai = context.user_data.get("ai")
        if not ai or not ai.get("request"):
            await q.message.reply_text("چیزی برای ساخت دوباره نیست. از 🎨 ساخت تصویر دوباره شروع کنید.")
            return
        await q.message.reply_text("⏳ در حال ساخت یک نسخه دیگر… (ممکن است یکی دو دقیقه طول بکشد)")
        await make_and_preview(q.message.chat_id, context)
    elif arg == "edit":
        ai = context.user_data.get("ai")
        if not ai or not ai.get("request"):
            await q.message.reply_text("تصویری برای ویرایش نیست. از 🎨 ساخت تصویر دوباره شروع کنید.")
            return
        context.user_data["await"] = ("ai_edit",)
        await q.message.reply_text(
            "✏️ بنویسید چه چیزی در همین تصویر عوض شود، مثلاً:\n\n"
            "<i>قیمت را ۲ میلیون تومان کن</i>\n<i>پس‌زمینه را شب پرستاره کن</i>\n<i>شتر را حذف کن</i>\n\n"
            "بقیه تصویر تا حد ممکن همان‌طور می‌ماند. برای انصراف /cancel را بفرستید.", parse_mode=ParseMode.HTML)
    elif arg == "redo":
        ai = context.user_data.get("ai")
        if not ai or not ai.get("edits"):
            await q.message.reply_text("چیزی برای ساخت دوباره نیست. از 🎨 ساخت تصویر دوباره شروع کنید.")
            return
        await q.message.reply_text("⏳ در حال اعمال تغییر… (ممکن است یکی دو دقیقه طول بکشد)")
        await make_and_preview(q.message.chat_id, context, ai["edits"][-1])
    elif arg == "last":
        ai = context.user_data.get("ai")
        if not ai or not ai.get("file_id"):
            await q.message.reply_text("نسخه قبلی پیدا نشد.")
            return
        await context.application.bot.send_photo(q.message.chat_id, ai["file_id"],
                                                 caption="پیش‌نمایش — در کانال ارسال شود؟", reply_markup=PREVIEW_KB)
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


PREVIEW_KB = M([[B("📤 ارسال در کانال", callback_data="ai:post")],
                [B("✏️ ویرایش", callback_data="ai:edit"), B("🔄 ساخت دوباره", callback_data="ai:retry")],
                [B("❌ انصراف", callback_data="ai:cancel")]])
EDIT_UNSUPPORTED = (400, 404, 405, 415, 422, 501)   # provider/model can't edit images → rebuild instead


async def make_and_preview(chat_id: int, context: ContextTypes.DEFAULT_TYPE, change: str | None = None) -> None:
    """Builds a new poster, or – with `change` – edits the last one (rebuilding with the change if editing
    isn't supported)."""
    app = context.application
    ai = context.user_data.setdefault("ai", {})
    http = app.bot_data["http"]
    note = ""
    await app.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
    try:
        raw = None
        if change and ai.get("image"):
            try:
                raw = await imagegen.edit_image(http, ai["image"], change, cfg.IMAGE_API_BASE_URL, cfg.IMAGE_API_KEY,
                                                cfg.IMAGE_MODEL, cfg.IMAGE_SIZE, cfg.IMAGE_QUALITY)
            except imagegen.ImageError as e:
                if e.status not in EDIT_UNSUPPORTED:
                    raise
                log.warning("image edit not supported (%s); rebuilding with the change", e)
                note = "\n(ویرایش مستقیم در این سرویس ممکن نبود؛ پوستر با تغییرات شما از نو ساخته شد.)"
        if raw is None:
            brief = await imagegen.write_prompt(http, cfg.CHAT_API_BASE_URL, cfg.CHAT_API_KEY, cfg.CHAT_MODEL,
                                                ai["request"])
            prompt = imagegen.build_poster_prompt(ai["request"], brief, ai.get("edits"))
            raw = await imagegen.generate_image(http, prompt, cfg.IMAGE_API_BASE_URL, cfg.IMAGE_API_KEY,
                                                cfg.IMAGE_MODEL, cfg.IMAGE_SIZE, cfg.IMAGE_QUALITY)
        data = imagegen.to_jpeg(raw)
    except Exception as e:  # noqa: BLE001
        log.exception("image generation failed")
        rows = [[B("🔄 تلاش دوباره", callback_data="ai:redo" if change else "ai:retry")]]
        if ai.get("file_id"):
            rows.append([B("↩️ برگشت به نسخه قبلی", callback_data="ai:last")])
        rows.append([B("⬅️ منو", callback_data="menu")])
        await app.bot.send_message(chat_id, f"❌ ساخت تصویر انجام نشد: {e}", reply_markup=M(rows))
        return
    sent = await app.bot.send_photo(chat_id, data, caption="پیش‌نمایش — در کانال ارسال شود؟" + note,
                                    reply_markup=PREVIEW_KB)
    ai["file_id"] = sent.photo[-1].file_id
    ai["image"] = raw            # kept in memory so the next ✏️ edit starts from this version


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

    elif state[0] in ("ai_request", "ai_text", "ai_prompt"):   # old two-step states → treated as one request
        context.user_data["ai"] = {"request": text.strip()[:1500]}
        context.user_data.pop("await", None)
        await update.message.reply_text("⏳ در حال طراحی پوستر… (ممکن است یکی دو دقیقه طول بکشد)")
        await make_and_preview(update.effective_chat.id, context)

    elif state[0] == "ai_edit":
        change = text.strip()[:800]
        if not change:
            await update.message.reply_text("لطفاً تغییر را بنویسید یا /cancel.")
            return
        context.user_data.pop("await", None)
        context.user_data.setdefault("ai", {}).setdefault("edits", []).append(change)
        await update.message.reply_text("⏳ در حال اعمال تغییر… (ممکن است یکی دو دقیقه طول بکشد)")
        await make_and_preview(update.effective_chat.id, context, change)

    elif state[0] == "songs":
        await update.message.reply_text("فایل صوتی بفرستید، یا دکمه «تمام شد» را بزنید.", reply_markup=DONE_BTN)

    elif state[0] == "join_text":
        value = text.strip()[:1000]
        if not value:
            await update.message.reply_text("متن خالی است؛ دوباره بفرستید یا /cancel.")
            return
        store.set("join_text", value)
        context.user_data.pop("await", None)
        screen, kb = jointext_screen(store)
        await update.message.reply_text("✅ پیام عضویت ذخیره شد.\n\n" + screen, parse_mode=ParseMode.HTML,
                                        reply_markup=kb)


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
