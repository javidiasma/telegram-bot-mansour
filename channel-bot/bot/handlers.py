"""Admin panel: inline-button menus in the bot's private chat."""
import html
import logging
import re

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M, Update
from telegram.constants import ParseMode
from telegram.ext import (Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, TypeHandler, filters)

from . import config as cfg
from . import imagegen
from .jobs import next_run, post_job, schedule_job
from .persian import to_en

log = logging.getLogger(__name__)

TITLES = {"dollar": "💵 Dollar rate", "flights": "✈️ IKA departures", "songs": "🎵 Song of the day"}
TIME_RE = re.compile(r"^\s*(\d{1,2})\s*[:.]\s*(\d{2})\s*$")


# ------------------------------------------------------------------ keyboards
def main_menu() -> M:
    return M([
        [B(TITLES["dollar"], callback_data="job:dollar"), B(TITLES["flights"], callback_data="job:flights")],
        [B("🎵 Songs", callback_data="job:songs"), B("🎨 AI image", callback_data="ai:start")],
        [B("📊 Status", callback_data="status")],
    ])


def job_menu(store, name: str) -> M:
    job = store.job(name)
    rows = [
        [B("🚀 Send now", callback_data=f"now:{name}"), B("⏰ Change time", callback_data=f"time:{name}")],
        [B("✅ On" if job["enabled"] else "⛔ Off", callback_data=f"toggle:{name}"),
         B(f"🗑 Delete previous: {'on' if job['delete_prev'] else 'off'}", callback_data=f"delprev:{name}")],
    ]
    if name == "songs":
        rows.insert(0, [B("➕ Add songs", callback_data="songs:add"), B("📃 List", callback_data="songs:list")])
        rows.append([B("🧹 Delete all songs", callback_data="songs:clear")])
    rows.append([B("⬅️ Back", callback_data="menu")])
    return M(rows)


def job_text(app: Application, name: str) -> str:
    store = app.bot_data["store"]
    job = store.job(name)
    lines = [f"<b>{TITLES[name]}</b>",
             f"Daily time: <b>{job['time']}</b> (Tehran)",
             f"Status: {'✅ on' if job['enabled'] else '⛔ off'} · next run: {next_run(app, name)}",
             f"Delete previous post: {'yes' if job['delete_prev'] else 'no'}",
             f"Last sent: {job['last_sent_at'] or '—'}"]
    if name == "songs":
        lines.append(f"Cached songs: <b>{store.song_count()}</b>")
    return "\n".join(lines)


# ------------------------------------------------------------------ guards
async def admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs before every handler; silently stops anything not from an admin in private chat."""
    user, chat = update.effective_user, update.effective_chat
    if chat and chat.type == "private" and user and user.id in cfg.ADMIN_IDS:
        return
    if update.message and update.message.text and update.message.text.startswith("/id") and user:
        await update.message.reply_text(f"Your Telegram user id: <code>{user.id}</code>", parse_mode=ParseMode.HTML)
    elif update.message and chat and chat.type == "private":
        await update.message.reply_text("This bot is private.")
    raise ApplicationHandlerStop


# ------------------------------------------------------------------ commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("await", None)
    await update.message.reply_text("Admin panel — choose an option:", reply_markup=main_menu())


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("await", None)
    await update.message.reply_text("Cancelled.", reply_markup=main_menu())


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Your Telegram user id: <code>{update.effective_user.id}</code>",
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
        await q.edit_message_text("Admin panel — choose an option:", reply_markup=main_menu())

    elif action == "job":
        await q.edit_message_text(job_text(app, arg), parse_mode=ParseMode.HTML, reply_markup=job_menu(store, arg))

    elif action == "now":
        await q.edit_message_text(f"⏳ Posting {TITLES[arg]}…")
        try:
            result = "✅ " + await post_job(app, arg)
        except Exception as e:  # noqa: BLE001
            log.exception("manual %s failed", arg)
            result = f"❌ Failed: {e}"
        await q.edit_message_text(f"{result}\n\n{job_text(app, arg)}", parse_mode=ParseMode.HTML,
                                  reply_markup=job_menu(store, arg))

    elif action == "time":
        context.user_data["await"] = ("time", arg)
        await q.edit_message_text(
            f"Send the new daily time for {TITLES[arg]} as <b>HH:MM</b> (24h, Tehran time), e.g. <code>09:30</code>.\n"
            "Send /cancel to go back.", parse_mode=ParseMode.HTML)

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
        parts = [f"Channel: <code>{html.escape(str(cfg.CHANNEL_ID))}</code>"]
        parts += [job_text(app, n) for n in ("dollar", "flights", "songs")]
        ai = "OpenAI" if cfg.OPENAI_API_KEY else "Pollinations (free)"
        parts.append(f"AI image provider: {ai}")
        await q.edit_message_text("\n\n".join(parts), parse_mode=ParseMode.HTML,
                                  reply_markup=M([[B("⬅️ Back", callback_data="menu")]]))


# ------------------------------------------------------------------ songs
async def songs_button(q, context, arg: str) -> None:
    store = context.application.bot_data["store"]
    if arg == "add":
        context.user_data["await"] = ("songs", 0)
        await q.edit_message_text(
            "🎵 Send or forward as many songs as you like (audio files or music documents).\n"
            "Each one is cached and will be posted one per day at the songs time.\n\n"
            "Press <b>Done</b> when you're finished.", parse_mode=ParseMode.HTML,
            reply_markup=M([[B("✅ Done", callback_data="songs:done")]]))
    elif arg == "done":
        added = context.user_data.pop("await", ("songs", 0))
        n = added[1] if added and added[0] == "songs" else 0
        await q.edit_message_text(f"✅ Added {n} new song(s). Total cached: {store.song_count()}.\n\n"
                                  + job_text(context.application, "songs"),
                                  parse_mode=ParseMode.HTML, reply_markup=job_menu(store, "songs"))
    elif arg == "list":
        songs = store.list_songs(40)
        if not songs:
            text = "No songs cached yet."
        else:
            text = "\n".join(f"#{s['id']} · {html.escape(s['title'] or 'untitled')}"
                             f"{' — ' + html.escape(s['performer']) if s['performer'] else ''}"
                             f" · played {s['sent_count']}×" for s in songs)
            more = store.song_count() - len(songs)
            if more > 0:
                text += f"\n… and {more} more"
            text += "\n\nTo remove one, send <code>/delsong ID</code>."
        await q.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=M([[B("⬅️ Back", callback_data="job:songs")]]))
    elif arg == "clear":
        await q.edit_message_text("Delete ALL cached songs? This can't be undone.",
                                  reply_markup=M([[B("🧹 Yes, delete all", callback_data="songs:clear_yes"),
                                                   B("Cancel", callback_data="job:songs")]]))
    elif arg == "clear_yes":
        n = store.clear_songs()
        await q.edit_message_text(f"Deleted {n} song(s).\n\n" + job_text(context.application, "songs"),
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
    note = "✅ Cached" if is_new else "ℹ️ Already cached"
    await msg.reply_text(f"{note}: {title or 'untitled'} (total {store.song_count()})",
                         reply_markup=M([[B("✅ Done", callback_data="songs:done")]]))


async def del_song(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = context.application.bot_data["store"]
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /delsong ID (see 📃 List)")
        return
    ok = store.delete_song(int(context.args[0]))
    await update.message.reply_text("Deleted." if ok else "No song with that id.")


# ------------------------------------------------------------------ AI image
async def ai_button(q, context, arg: str) -> None:
    app = context.application
    if arg == "start":
        context.user_data["await"] = ("ai_text",)
        await q.edit_message_text("🎨 Send the text to write on the image (Persian or English).\n"
                                  "Use new lines for line breaks. /cancel to go back.")
    elif arg == "skip":
        context.user_data.pop("await", None)
        await q.edit_message_text("⏳ Generating image… (can take up to a minute)")
        await make_and_preview(q.message.chat_id, context, None)
    elif arg == "retry":
        ai = context.user_data.get("ai")
        if not ai:
            await q.message.reply_text("Nothing to regenerate. Start again from 🎨 AI image.")
            return
        await q.message.reply_text("⏳ Generating another version…")
        await make_and_preview(q.message.chat_id, context, ai.get("description"))
    elif arg == "post":
        ai = context.user_data.get("ai")
        if not ai or not ai.get("file_id"):
            await q.message.reply_text("Nothing to post.")
            return
        await app.bot.send_photo(cfg.CHANNEL_ID, ai["file_id"], caption=cfg.SIGNATURE or None)
        await q.edit_message_reply_markup(None)
        await q.message.reply_text("✅ Posted to the channel.", reply_markup=main_menu())
    elif arg == "cancel":
        context.user_data.pop("ai", None)
        await q.edit_message_reply_markup(None)
        await q.message.reply_text("Discarded.", reply_markup=main_menu())


async def make_and_preview(chat_id: int, context: ContextTypes.DEFAULT_TYPE, description: str | None) -> None:
    app = context.application
    ai = context.user_data.setdefault("ai", {})
    ai["description"] = description
    http = app.bot_data["http"]
    try:
        prompt = imagegen.build_prompt(ai["text"], description)
        bg = await imagegen.generate_background(http, prompt, cfg.OPENAI_API_KEY, cfg.OPENAI_IMAGE_MODEL,
                                                cfg.POLLINATIONS_URL)
        font = await imagegen.ensure_font(http, cfg.DATA_DIR, cfg.FONT_URL)
        data = imagegen.render_text(bg, ai["text"], font)
    except Exception as e:  # noqa: BLE001
        log.exception("image generation failed")
        await app.bot.send_message(chat_id, f"❌ Image generation failed: {e}",
                                   reply_markup=M([[B("🔄 Try again", callback_data="ai:retry")],
                                                   [B("⬅️ Menu", callback_data="menu")]]))
        return
    sent = await app.bot.send_photo(
        chat_id, data, caption="Preview — post it to the channel?",
        reply_markup=M([[B("📤 Post to channel", callback_data="ai:post")],
                        [B("🔄 Regenerate", callback_data="ai:retry"), B("❌ Cancel", callback_data="ai:cancel")]]))
    ai["file_id"] = sent.photo[-1].file_id


# ------------------------------------------------------------------ free text (time / AI input)
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get("await")
    text = update.message.text
    app = context.application
    store = app.bot_data["store"]

    if not state:
        await update.message.reply_text("Choose an option:", reply_markup=main_menu())
        return

    if state[0] == "time":
        name = state[1]
        m = TIME_RE.match(to_en(text))
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            await update.message.reply_text("Please send a valid time like 08:00 or 21:30 (or /cancel).")
            return
        value = f"{int(m.group(1)):02d}:{m.group(2)}"
        store.update_job(name, time=value, enabled=True)
        schedule_job(app, name)
        context.user_data.pop("await", None)
        await update.message.reply_text(f"✅ {TITLES[name]} will be posted daily at {value} (Tehran).\n\n"
                                         + job_text(app, name), parse_mode=ParseMode.HTML,
                                         reply_markup=job_menu(store, name))

    elif state[0] == "ai_text":
        context.user_data["ai"] = {"text": text[:400]}
        context.user_data["await"] = ("ai_prompt",)
        await update.message.reply_text(
            "Now describe the background picture (English works best), e.g. "
            "<i>Tehran skyline at sunset, watercolor</i>.\nOr press Skip to let the AI pick one from your text.",
            parse_mode=ParseMode.HTML, reply_markup=M([[B("⏭ Skip", callback_data="ai:skip")]]))

    elif state[0] == "ai_prompt":
        context.user_data.pop("await", None)
        await update.message.reply_text("⏳ Generating image… (can take up to a minute)")
        await make_and_preview(update.effective_chat.id, context, text[:500])

    elif state[0] == "songs":
        await update.message.reply_text("Send audio files, or press Done.",
                                        reply_markup=M([[B("✅ Done", callback_data="songs:done")]]))


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
