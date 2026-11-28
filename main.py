"""Minimal Telegram bot: the only user command is /host.

This service uses the bot token only. It never asks for or stores user login
codes, passwords, API credentials, or Telegram user-session strings.
"""
from __future__ import annotations

import logging
import os
import sys
from html import escape

from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    filters,
)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("host_only")


def get_bot_token() -> str:
    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Missing BOT_TOKEN secret. Add BOT_TOKEN in Railway Variables."
        )
    return token.strip()


async def on_startup(application: Application) -> None:
    me = await application.bot.get_me()
    logger.info(
        "Bot online: id=%s username=@%s",
        me.id,
        me.username or "no_username",
    )


async def host_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    if message is None:
        return

    try:
        me = await context.bot.get_me()
        username = f"@{escape(me.username)}" if me.username else "Not set"
        first_name = escape(me.first_name or "Telegram Bot")
        runtime = "Railway" if os.getenv("RAILWAY_PROJECT_ID") else "Python process"

        response = (
            "<b>🟢 BOT HOST STATUS</b>\n\n"
            f"<b>Name:</b> {first_name}\n"
            f"<b>Username:</b> {username}\n"
            f"<b>Bot ID:</b> <code>{me.id}</code>\n"
            f"<b>Status:</b> Online\n"
            f"<b>Runtime:</b> {runtime}\n\n"
            "<i>This is the bot's own identity. No personal Telegram account "
            "login or session is requested.</i>"
        )
        await message.reply_text(response, parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Failed to process /host for chat_id=%s", update.effective_chat.id if update.effective_chat else "unknown")
        await message.reply_text(
            "⚠️ Bot is running, but Telegram identity lookup failed. "
            "Check Railway Deploy Logs."
        )


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logger.error("Unhandled Telegram update error", exc_info=context.error)


def main() -> None:
    token = get_bot_token()
    application = (
        Application.builder()
        .token(token)
        .post_init(on_startup)
        .build()
    )

    # Deliberately register exactly one command: /host.
    application.add_handler(
        CommandHandler("host", host_command, filters=filters.ChatType.PRIVATE)
    )
    application.add_error_handler(error_handler)

    logger.info("Starting host-only bot with long polling")
    application.run_polling(
        allowed_updates=["message"],
        drop_pending_updates=True,
        close_loop=True,
    )


if __name__ == "__main__":
    main()
