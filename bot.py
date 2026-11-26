import html
import logging
import os
import sqlite3
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

from config import BOT_TOKEN, OWNER_ID, OWNER_USERNAME

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("community_bot")
DB_PATH = Path(os.getenv("BOT_DB_PATH", "community_bot.db"))


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                welcome TEXT NOT NULL DEFAULT 'Welcome, {first_name}! 👋',
                rules TEXT NOT NULL DEFAULT 'Please be respectful and follow the group rules.'
            )
        """)


def get_settings(chat_id):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute(
            "SELECT welcome, rules FROM chat_settings WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    return row or ("Welcome, {first_name}! 👋",
                   "Please be respectful and follow the group rules.")


def save_setting(chat_id, column, value):
    if column not in {"welcome", "rules"}:
        raise ValueError("Unsupported setting")
    welcome, rules = get_settings(chat_id)
    if column == "welcome":
        welcome = value
    else:
        rules = value
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """INSERT INTO chat_settings(chat_id, welcome, rules)
               VALUES(?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
               welcome=excluded.welcome, rules=excluded.rules""",
            (chat_id, welcome, rules),
        )


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Help & Commands", callback_data="help"),
         InlineKeyboardButton("📜 Group Rules", callback_data="rules")],
        [InlineKeyboardButton("🆔 My ID", callback_data="myid")],
    ])


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return False
    if user.id == OWNER_ID and OWNER_ID:
        return True
    if chat.type == "private":
        return False
    member = await context.bot.get_chat_member(chat.id, user.id)
    return member.status in {"administrator", "creator"}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = html.escape(user.first_name or "there")
    await update.effective_message.reply_text(
        f"<b>✨ Welcome, {name}!</b>\n\n"
        "I'm your community assistant. Add me to a group to send welcome "
        "messages and manage group rules.\n\n"
        "Choose an option below to get started.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>🤖 COMMUNITY BOT — HELP</b>\n\n"
        "<b>Everyone</b>\n"
        "• /start — Open the home menu\n"
        "• /help — Show this help\n"
        "• /id — Show your Telegram ID\n"
        "• /rules — View this group's rules\n\n"
        "<b>Group admins</b>\n"
        "• /setwelcome Your welcome text\n"
        "• /setrules Your group rules\n"
        "• /settings — View current configuration\n\n"
        "<i>Welcome placeholders: {first_name}, {username}</i>"
    )
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard()
    )


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    await update.effective_message.reply_text(
        f"🆔 <b>Your ID:</b> <code>{user.id}</code>\n"
        f"💬 <b>Chat ID:</b> <code>{chat.id}</code>",
        parse_mode=ParseMode.HTML,
    )


async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    _, rules = get_settings(chat.id)
    await update.effective_message.reply_text(
        "<b>📜 GROUP RULES</b>\n\n" + html.escape(rules),
        parse_mode=ParseMode.HTML,
    )


async def setwelcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.effective_message.reply_text("🔒 Only group admins can change the welcome message.")
        return
    value = " ".join(context.args).strip()
    if not value:
        await update.effective_message.reply_text(
            "Usage: /setwelcome Welcome {first_name}! Please read /rules."
        )
        return
    if len(value) > 1000:
        await update.effective_message.reply_text("Welcome text must be 1000 characters or fewer.")
        return
    save_setting(update.effective_chat.id, "welcome", value)
    await update.effective_message.reply_text("✅ Welcome message updated.")


async def setrules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.effective_message.reply_text("🔒 Only group admins can change the rules.")
        return
    value = " ".join(context.args).strip()
    if not value:
        await update.effective_message.reply_text("Usage: /setrules Be respectful. No spam.")
        return
    if len(value) > 3000:
        await update.effective_message.reply_text("Rules must be 3000 characters or fewer.")
        return
    save_setting(update.effective_chat.id, "rules", value)
    await update.effective_message.reply_text("✅ Group rules updated.")


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.effective_message.reply_text("🔒 Only group admins can view these settings.")
        return
    welcome, rules = get_settings(update.effective_chat.id)
    await update.effective_message.reply_text(
        "<b>⚙️ GROUP SETTINGS</b>\n\n"
        f"<b>Welcome:</b> {html.escape(welcome)}\n\n"
        f"<b>Rules:</b> {html.escape(rules)}",
        parse_mode=ParseMode.HTML,
    )


async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    template, _ = get_settings(chat.id)
    for member in update.effective_message.new_chat_members:
        if member.is_bot:
            continue
        first_name = html.escape(member.first_name or "friend")
        username = html.escape("@" + member.username) if member.username else "not set"
        rendered = template.replace("{first_name}", first_name).replace(
            "{username}", username
        )
        await update.effective_message.reply_text(
            "👋 " + rendered, parse_mode=ParseMode.HTML
        )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "help":
        text = (
            "<b>📖 COMMANDS</b>\n/start • /help • /id • /rules\n\n"
            "<b>Admin:</b> /setwelcome, /setrules, /settings"
        )
    elif query.data == "rules":
        _, rules = get_settings(query.message.chat_id)
        text = "<b>📜 GROUP RULES</b>\n\n" + html.escape(rules)
    elif query.data == "myid":
        text = f"🆔 Your Telegram ID: <code>{query.from_user.id}</code>"
    else:
        return
    await query.message.reply_text(text, parse_mode=ParseMode.HTML)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled update error", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it in GitHub Actions secrets.")
    init_db()
    app: Application = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("rules", rules_command))
    app.add_handler(CommandHandler("setwelcome", setwelcome_command))
    app.add_handler(CommandHandler("setrules", setrules_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_members))
    app.add_error_handler(error_handler)
    log.info("Community Bot started. Personal-account login/session features are disabled.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
