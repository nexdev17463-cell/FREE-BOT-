import asyncio
import aiohttp
import time
import html
import logging
import random
import re
from types import SimpleNamespace
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters
)
from telegram.constants import ParseMode
from config import BOT_TOKEN, API_ID, API_HASH, OWNER_ID, OWNER_USERNAME
from session_manager import (
    save_session, get_all_sessions, delete_session,
    get_total_count, get_active_count, get_expired_count,
    get_expired_sessions, get_all_status_sessions, refresh_all_sessions,
    delete_expired_session, delete_all_expired_sessions
)
from access_manager import (
    is_owner, is_sudo, remember_user, add_sudo, remove_sudo, list_access_users
)
from reporter import mass_report, mass_join, mass_leave
from proxy_manager import fetch_proxies, rotate_forever
from emoji_manager import (
    CUSTOM_EMOJI_IDS, apply_custom_emojis, get_button_custom_emoji,
    rich_text_with_custom_emojis as _shared_rich_text_with_custom_emojis,
    convert_rich_blocks, custom_emoji_id_for,
)

logging.basicConfig(level=logging.INFO)

# ── Conversation states ────────────────────────────────────────────────
(
    ADD_PHONE, ADD_CODE, ADD_2FA,
    REPORT_TARGET, REPORT_REASON_CAT, REPORT_REASON_SUB,
    REPORT_MESSAGE, REPORT_COUNT, CONFIRM_REPORT,
) = range(9)

# ── Report categories ────────────────────────────────────────────────
REASONS = {
    "1":  ("😤 I don't like it",        []),
    "2":  ("👶 Child Abuse",            [("…2a","Child illegal abuse"),("…2b","Child sexual abuse")]),
    "3":  ("🔪 Violence",               [("…3a","Insult / false info"),("…3b","Graphic content"),
                                         ("…3c","Extreme violence"),("…3d","Hate speech"),
                                         ("…3e","Calling for violence"),("…3f","Terrorism")]),
    "4":  ("🚫 Illegal Goods",          [("…4a","Weapons"),("…4b","Drugs"),("…4c","Fake documents"),
                                         ("…4d","Counterfeit money"),("…4e","Hacking tools"),
                                         ("…4f","Counterfeit merch"),("…4g","Other")]),
    "5":  ("🔞 Illegal Adult Content",  [("…5a","Child abuse"),("…5b","Illegal sexual service"),
                                         ("…5c","Animal abuse"),("…5d","Non-consensual imagery"),
                                         ("…5e","Pornography"),("…5f","Other")]),
    "6":  ("🧧 Personal Data",          [("…6a","Private images"),("…6b","Private number"),
                                         ("…6c","Address"),("…6d","Credentials"),("…6e","Other")]),
    "7":  ("💸 Scam or Fraud",          [("…7a","Impersonation"),("…7b","Financial scams"),
                                         ("…7c","Malware/phishing"),("…7d","Fraudulent seller")]),
    "8":  ("©️ Copyright",              []),
    "9":  ("🔎 Other",                  []),
    "10": ("⚠️ Not illegal but remove", []),
}

tmp = {}

# ── Per-user background task registry ───────────────────────────────────
# key: "{user_id}:{msg_id}" → asyncio.Task
_active_tasks: dict = {}
# key: "{user_id}:{msg_id}" → asyncio.Event  (set = cancel requested)
_cancel_events: dict = {}


# ── Access helpers ────────────────────────────────────────────────
async def has_access(user_id: int) -> bool:
    return is_owner(user_id) or await is_sudo(user_id)

OWNER_CONTACT_USERNAME = (OWNER_USERNAME or "SAREEF_FUCKER").strip().lstrip("@") or "SAREEF_FUCKER"
OWNER_CONTACT = html.escape("@" + OWNER_CONTACT_USERNAME)

ACCESS_DENIED_HTML = (
    "<h1>🔒 ACCESS DENIED</h1>"
    "<hr/>"
    "<p>🚫 You are not authorized to access this feature.</p>"
    "<p>This command is restricted to Owner &amp; Sudo Users only.</p>"
    "<h2>👑 Need Full Bot Access?</h2>"
    "<p>Contact the Bot Owner to request Sudo Access.</p>"
    f"<p>👤 Owner: <b>{OWNER_CONTACT}</b></p>"
    "<hr/>"
    "<footer>🛡️ SECURE ACCESS • AUTHORIZED USERS ONLY</footer>"
)


async def send_access_denied(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                             edit: bool = False):
    keyboard = InlineKeyboardMarkup([[btn("🔙 Back to Menu", "back")]])
    await send_rich_message(
        update, ctx, ACCESS_DENIED_HTML, keyboard,
        edit=edit and bool(update.callback_query)
    )


async def require_access(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    if await has_access(update.effective_user.id):
        return True
    if update.callback_query:
        await update.callback_query.answer()
    await send_access_denied(update, ctx, edit=bool(update.callback_query))
    return False

# ── Keyboards ────────────────────────────────────────────────
_BTN_STYLES = ["primary", "success", "danger"]

_BUTTON_SMALL_CAPS = str.maketrans({
    "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ",
    "f": "\ua730", "g": "ɢ", "h": "ʜ", "i": "ɪ", "j": "ᴊ",
    "k": "ᴋ", "l": "ʟ", "m": "ᴍ", "n": "ɴ", "o": "ᴏ",
    "p": "ᴘ", "q": "\ua76f", "r": "ʀ", "s": "\ua731", "t": "ᴛ",
    "u": "ᴜ", "v": "ᴠ", "w": "ᴡ", "x": "x", "y": "ʏ", "z": "ᴢ",
})


def btn(text: str, data: str) -> InlineKeyboardButton:
    leading_emoji, icon_custom_emoji_id = get_button_custom_emoji(text)
    if leading_emoji:
        text = text.replace(leading_emoji, "", 1).strip()

    styled_text = text.translate(_BUTTON_SMALL_CAPS)
    style = random.choice(_BTN_STYLES)
    return InlineKeyboardButton(
        styled_text,
        callback_data=data,
        style=style,
        icon_custom_emoji_id=icon_custom_emoji_id,
    )


def stop_progress_kb(owner_uid: int, msg_id: int) -> InlineKeyboardMarkup:
    """Stop Progress button shown during live operations."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "ᴄᴀɴᴄᴇʟ  ᴘʀᴏɢʀᴇᴄᴄ",
            callback_data=f"stop_progress:{owner_uid}:{msg_id}",
            icon_custom_emoji_id=custom_emoji_id_for("🚫"),
            style="danger",
        )
    ]])


def main_menu_kb() -> InlineKeyboardMarkup:
    developer_button = InlineKeyboardButton(
        "◈ 𝗗𝗘𝗩𝗘𝗟𝗢𝗣𝗘𝗥 ◈",
        url="https://t.me/SAREEF_FUCKER",
        style="danger",
        icon_custom_emoji_id=custom_emoji_id_for("🕵🏻"),
    )
    return InlineKeyboardMarkup([
        [btn("➕ Add Account", "add_account"), btn("📊 Status", "status")],
        [btn("🚨 Mass Report", "start_report"), btn("📋 Sessions", "sessions")],
        [btn("🔄 Refresh Sessions", "refresh_status"), btn("❔ Help", "help")],
        [developer_button],
    ])

def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[btn("❌ Cancel", "cancel_flow")]])

def status_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("🔄 Refresh", "refresh_status")],
        [btn("🔴 Expired IDs", "expired_ids"), btn("🔙 Back", "back")],
    ])


def status_view_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("🟢 Active View", "status_view:active"), btn("🔴 Expired View", "status_view:expired")],
        [btn("📦 Total View", "status_view:total")],
        [btn("🔄 Refresh", "refresh_status"), btn("🔙 Back", "back")],
    ])


def refresh_view_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("🟢 Active View", "refresh_view:active"), btn("🔴 Expired View", "refresh_view:expired")],
        [btn("⚠️ Errors View", "refresh_view:errors"), btn("🎁 Checked View", "refresh_view:checked")],
        [btn("🔄 Refresh Again", "refresh_status"), btn("🔙 Back", "back")],
    ])


# ── Premium message presentation ────────────────────────────────────────────────
async def _send_html_message(ctx, chat_id: int, text: str, reply_markup=None):
    """Send HTML through Bot API so mapped custom-emoji tags work in fallbacks too."""
    payload = {"chat_id": chat_id, "text": apply_custom_emojis(text), "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup.to_dict()
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(api_base + "sendMessage", json=payload) as response:
            result = await response.json(content_type=None)
            if not response.ok or not result.get("ok"):
                raise RuntimeError(result.get("description", "Telegram sendMessage failed"))
            return SimpleNamespace(**result["result"])


async def _edit_html_message(ctx, chat_id: int, message_id: int,
                             text: str, reply_markup=None):
    """Edit HTML through Bot API so mapped custom-emoji tags work in fallbacks too."""
    payload = {
        "chat_id": chat_id, "message_id": message_id,
        "text": apply_custom_emojis(text), "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup.to_dict()
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(api_base + "editMessageText", json=payload) as response:
            result = await response.json(content_type=None)
            if not response.ok or not result.get("ok"):
                raise RuntimeError(result.get("description", "Telegram editMessageText failed"))
            return result["result"]


async def send_rich_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                            rich_html: str, reply_markup=None, edit: bool = False):
    chat_id = update.effective_chat.id
    rich_html = apply_custom_emojis(rich_html)
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    payload = {
        "chat_id": chat_id,
        "rich_message": {"html": rich_html},
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup.to_dict()
    if edit and update.callback_query:
        payload["message_id"] = update.callback_query.message.message_id
        method = "editMessageText"
    else:
        method = "sendRichMessage"

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_base + method, json=payload) as response:
                result = await response.json(content_type=None)
                if not response.ok or not result.get("ok"):
                    raise RuntimeError(result.get("description", f"Telegram API HTTP {response.status}"))
        return
    except Exception as exc:
        logging.warning("Rich message unavailable; using HTML fallback: %s", exc)

    plain_html = re.sub(r"<(h[1-6]|p|div|footer|table|tr|th|td|hr|mark)[^>]*>", "", rich_html)
    plain_html = re.sub(r"</(h[1-6]|p|div|footer|table|tr|th|td|mark)>", "\\n", plain_html)
    plain_html = re.sub(r"</?tg-emoji[^>]*>", "", plain_html)
    plain_html = plain_html.replace("<hr/>", "\\n━━━━━━━━━━━━━━━━━━\\n")
    if edit and update.callback_query:
        await _edit_html_message(ctx, chat_id, update.callback_query.message.message_id, plain_html, reply_markup=reply_markup)
    else:
        await _send_html_message(ctx, chat_id, plain_html, reply_markup=reply_markup)


async def _rich_edit_by_id(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int,
                           message_id: int, rich_html: str,
                           reply_markup=None, fallback_text: str = None,
                           fallback_markup=None):
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "rich_message": {"html": apply_custom_emojis(rich_html)},
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup.to_dict()
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_base + "editMessageText", json=payload) as response:
                result = await response.json(content_type=None)
                if not response.ok or not result.get("ok"):
                    raise RuntimeError(result.get("description", "rich edit failed"))
        return
    except Exception as exc:
        logging.warning("Rich edit unavailable; using HTML fallback: %s", exc)

    if fallback_text is not None:
        try:
            await _edit_html_message(ctx, chat_id, message_id, fallback_text, reply_markup=fallback_markup)
        except Exception as exc2:
            logging.warning("Fallback edit also failed: %s", exc2)


async def _rich_send_blocks_new(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int,
                                blocks: list, fallback_html: str,
                                reply_markup=None, fallback_text: str = None,
                                fallback_markup=None) -> int:
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    payload = {"chat_id": chat_id, "rich_message": {"blocks": convert_rich_blocks(blocks)}}
    if reply_markup:
        payload["reply_markup"] = reply_markup.to_dict()
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_base + "sendRichMessage", json=payload) as response:
                result = await response.json(content_type=None)
                if not response.ok or not result.get("ok"):
                    raise RuntimeError(result.get("description", "Rich blocks send failed"))
                return result["result"]["message_id"]
    except Exception as exc:
        logging.warning("Rich blocks send unavailable; using HTML fallback: %s", exc)
        msg = await _send_html_message(ctx, chat_id, fallback_text or fallback_html, reply_markup=fallback_markup or reply_markup)
        return msg.message_id


async def _rich_send_new(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int,
                         rich_html: str, reply_markup=None,
                         fallback_text: str = None, fallback_markup=None) -> int:
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    payload = {
        "chat_id": chat_id,
        "rich_message": {"html": apply_custom_emojis(rich_html)},
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup.to_dict()
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_base + "sendRichMessage", json=payload) as response:
                result = await response.json(content_type=None)
                if not response.ok or not result.get("ok"):
                    raise RuntimeError(result.get("description", "rich send failed"))
                logging.info("Rich Message sent (new) successfully, message_id=%s",
                             result["result"]["message_id"])
                return result["result"]["message_id"]
    except Exception as exc:
        logging.warning("Rich send (new) unavailable; using HTML fallback: %s", exc)

    if fallback_text is not None:
        msg = await _send_html_message(ctx, chat_id, fallback_text, reply_markup=fallback_markup)
        return msg.message_id
    return None


# ── Task registry helpers ────────────────────────────────────────────────
def _task_key(uid: int, msg_id: int) -> str:
    return f"{uid}:{msg_id}"

def _register_task(uid: int, msg_id: int, task: asyncio.Task, cancel_event: asyncio.Event):
    key = _task_key(uid, msg_id)
    _active_tasks[key] = task
    _cancel_events[key] = cancel_event

def _unregister_task(uid: int, msg_id: int):
    key = _task_key(uid, msg_id)
    _active_tasks.pop(key, None)
    _cancel_events.pop(key, None)

def _get_cancel_event(uid: int, msg_id: int) -> asyncio.Event | None:
    return _cancel_events.get(_task_key(uid, msg_id))

def _get_user_tasks(uid: int) -> list:
    """Return list of (msg_id, task) for a user."""
    result = []
    for key, task in list(_active_tasks.items()):
        k_uid, k_mid = key.split(":", 1)
        if int(k_uid) == uid:
            result.append((int(k_mid), task))
    return result


# ── /start ────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await remember_user(update.effective_user)

    welcome_html = (
        "<h1>⭐ 𝗪𝗘𝗟𝗖𝗢𝗠𝗘 𝗧𝗢 𝗠𝗔𝗦𝗦 𝗥𝗘𝗣𝗢𝗥𝗧 ⭐  𝗕𝗢𝗧</h1>"
        "<p><i>Your account management command center</i></p>"
        "<hr/>"
        "<h2>🚀 Get Started</h2>"
        "<p>Manage your connected Telegram accounts and access your available tools from one clean dashboard.</p>"
        "<h3>🌟 Quick Access</h3>"
        "<table bordered striped>"
        "<tr><th>SECTION</th><th>WHAT YOU CAN DO</th></tr>"
        "<tr><td><b>Accounts</b></td><td>Connect and manage accounts you own.</td></tr>"
        "<tr><td><b>Sessions</b></td><td>Review saved sessions and their status.</td></tr>"
        "<tr><td><b>Tools</b></td><td>Open the tools available to your access level.</td></tr>"
        "<tr><td><b>Help Center</b></td><td>Read commands and usage guidance.</td></tr>"
        "</table>"
        "<p><i>Select an option below to continue.</i></p>"
        "<footer>Use only accounts you own or are explicitly authorized to manage.</footer>"
    )

    keyboard = main_menu_kb()
    api_url = f"https://api.telegram.org/bot{ctx.bot.token}/sendRichMessage"
    payload = {
        "chat_id": update.effective_chat.id,
        "rich_message": {"html": apply_custom_emojis(welcome_html)},
        "reply_markup": keyboard.to_dict(),
    }

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_url, json=payload) as response:
                result = await response.json(content_type=None)
                if not response.ok or not result.get("ok"):
                    raise RuntimeError(
                        result.get("description", f"Telegram API HTTP {response.status}")
                    )
    except Exception as exc:
        logging.warning("Rich welcome unavailable; using standard message fallback: %s", exc)
        await _send_html_message(
            ctx, update.effective_chat.id,
            "⭐ <b>WELCOME TO MASS REPORT BOT</b>\n"
            "<i>Your account management command center</i>\n\n"
            "🚀 <b>Get Started</b>\n"
            "Manage your connected accounts and available tools from the menu below.\n\n"
            "<i>Select an option to continue.</i>",
            reply_markup=keyboard,
        )


# ── /help ────────────────────────────────────────────────────────────────
async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    priv = await has_access(uid)
    owner = is_owner(uid)

    rows_account = (
        "<table bordered striped>"
        "<tr><th>Command</th><th>Description</th></tr>"
        "<tr><td><mark><b>/start</b></mark></td><td>Open the bot menu.</td></tr>"
        "<tr><td><mark><b>/host</b></mark></td><td>Connect your own Telegram account (OTP / optional 2FA).</td></tr>"
        "<tr><td><mark><b>/addsession &lt;string&gt;</b></mark></td><td>Add a session directly via string session.</td></tr>"
        "<tr><td><mark><b>/cancel</b></mark></td><td>Cancel the current setup.</td></tr>"
        "<tr><td><mark><b>/stop</b></mark></td><td>Stop your currently running background operation.</td></tr>"
        "<tr><td><mark><b>/help</b></mark></td><td>Show this command guide.</td></tr>"
        "</table>"
    )

    rich_html = (
        "<h1>🤖 𝗘𝗔𝗦𝗦 𝗥𝗘𝗣𝗢𝗥𝗧 𝗕𝗢𝗧</h1>"
        "<p><i>⭐ COMMAND CENTER  ·  QUICK REFERENCE ⭐</i></p>"
        "<hr/>"
        "<h2>👤 𝗔ᴄᴄᴏᴜɴᴛ &amp; 𝗘ᴇɴᴜ</h2>"
        + rows_account
    )

    if priv:
        rich_html += (
            "<h2>⚙️ 𝗕ᴏᴛ 𝗢ᴘᴇʀᴀᴛɪᴏɴᴄ</h2>"
            "<table bordered striped>"
            "<tr><th>Command</th><th>Description</th></tr>"
            "<tr><td><mark><b>/status</b></mark></td><td>View session statistics.</td></tr>"
            "<tr><td><mark><b>/refresh</b></mark></td><td>Verify saved session status.</td></tr>"
            "<tr><td><mark><b>/session</b></mark></td><td>List your saved sessions.</td></tr>"
            "<tr><td><mark><b>/unhost +919876543210</b></mark></td><td>Remove a hosted session.</td></tr>"
            "<tr><td><mark><b>/join @username</b></mark></td><td>Join a chat with accounts you are authorized to use.</td></tr>"
            "<tr><td><mark><b>/sudoinfo</b></mark></td><td>View the Owner and Sudo directory.</td></tr>"
            "</table>"
        )

    if owner:
        rich_html += (
            "<h2>👑 𝗢ᴡɴᴇʀ 𝗖ᴏɴᴛʀᴏʟᴄ</h2>"
            "<table bordered striped>"
            "<tr><th>Command</th><th>Description</th></tr>"
            "<tr><td><mark><b>/sudo USER_ID</b></mark></td><td>Grant Sudo access.</td></tr>"
            "<tr><td><mark><b>/removesudo USER_ID</b></mark></td><td>Revoke Sudo access.</td></tr>"
            "</table>"
        )

    if priv:
        rich_html += (
            "<h2>🔐 𝗔ᴄᴄᴇᴄ 𝗣ᴇʀᴄᴇɪᴄɪᴏɴ</h2>"
            "<p><b>Owner / Sudo</b> · Authorized bot operations.</p>"
        )
    else:
        rich_html += (
            "<h2>🔐 𝗔ᴄᴄᴇᴄ</h2>"
            "<p>Some operations are restricted to Owner / Sudo.</p>"
        )

    rich_html += (
        "<hr/>"
        "<footer><i>Designed for clear, responsible account management</i> · Only use accounts you own or are authorized to manage.</footer>"
    )

    kb = InlineKeyboardMarkup([[btn("‹  BACK TO MENU  ›", "back")]])
    chat_id = update.effective_chat.id
    api_url = f"https://api.telegram.org/bot{ctx.bot.token}/"

    try:
        if update.callback_query:
            method = "editMessageText"
            payload = {
                "chat_id": chat_id,
                "message_id": update.callback_query.message.message_id,
                "rich_message": {"html": apply_custom_emojis(rich_html)},
                "reply_markup": kb.to_dict(),
            }
        else:
            method = "sendRichMessage"
            payload = {
                "chat_id": chat_id,
                "rich_message": {"html": apply_custom_emojis(rich_html)},
                "reply_markup": kb.to_dict(),
            }

        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_url + method, json=payload) as response:
                result = await response.json(content_type=None)
                if not response.ok or not result.get("ok"):
                    raise RuntimeError(
                        result.get("description", f"Telegram API HTTP {response.status}")
                    )

    except Exception as exc:
        logging.warning("Rich help message unavailable; using standard help fallback: %s", exc)
        fallback = (
            "🤖 <b>𝗘𝗔𝗦𝗦 𝗥𝗘𝗣𝗢𝗥𝗧 𝗕𝗢𝗧</b>\n"
            "<i>⭐ COMMAND CENTER · QUICK REFERENCE ⭐</i>\n\n"
            "<b>👤 𝗔ᴄᴄᴏᴜɴᴛ &amp; 𝗘ᴇɴᴜ</b>\n"
            "<b>/start</b> — Open the bot menu\n"
            "<b>/host</b> — Connect your own account (OTP / optional 2FA)\n"
            "<b>/addsession &lt;string&gt;</b> — Add session via string session\n"
            "<b>/stop</b> — Stop your running background operation\n"
            "<b>/cancel</b> — Cancel the current setup\n"
            "<b>/help</b> — Show this guide\n"
        )
        if priv:
            fallback += (
                "\n<b>⚙️ 𝗕ᴏᴛ 𝗢ᴘᴇʀᴀᴛɪᴏɴᴄ</b>\n"
                "<b>/status</b> — View session statistics\n"
                "<b>/refresh</b> — Verify saved session status\n"
                "<b>/session</b> — List saved sessions\n"
                "<b>/unhost +919876543210</b> — Remove a hosted session\n"
                "<b>/join @username</b> — Join a chat with authorized accounts\n"
                "<b>/sudoinfo</b> — View Owner and Sudo directory\n"
            )
        if owner:
            fallback += (
                "\n<b>👑 𝗢ᴡɴᴇʀ 𝗖ᴏɴᴛʀᴏʟᴄ</b>\n"
                "<b>/sudo USER_ID</b> — Grant Sudo access\n"
                "<b>/removesudo USER_ID</b> — Revoke Sudo access\n"
            )
        fallback += "\n<b>🔐 𝗔ᴄᴄᴇᴄ</b>\nOwner / Sudo: authorized bot operations."
        if update.callback_query:
            await _edit_html_message(ctx, chat_id, update.callback_query.message.message_id, fallback, reply_markup=kb)
        else:
            await _send_html_message(ctx, chat_id, fallback, reply_markup=main_menu_kb())

# ── /host ────────────────────────────────────────────────────────────────
async def host_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Start the native Pyrogram phone → OTP → optional 2FA session flow."""
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text(
            "For account security, /host can only be used in a private chat with this bot."
        )
        return ConversationHandler.END

    await remember_user(update.effective_user)
    uid = update.effective_user.id

    previous = tmp.pop(uid, None)
    if previous:
        await _close_host_client(previous.get("client"))

    if not API_ID or not API_HASH:
        await send_rich_message(
            update, ctx,
            "<h2>❌ PYROGRAM CONFIGURATION MISSING</h2>"
            "<p>Set valid <code>API_ID</code> and <code>API_HASH</code> in hosting Secrets, then restart the bot.</p>",
            main_menu_kb()
        )
        return ConversationHandler.END

    await send_rich_message(
        update, ctx,
        "<h1>📲 PYROGRAM ACCOUNT HOST</h1>"
        "<p>This flow creates a fresh Pyrogram session using the API credentials configured in hosting Secrets.</p>"
        "<hr/><p>Send your phone number in international format:</p>"
        "<p><code>+919876543210</code></p>"
        "<footer>Use only your own Telegram account. Never share your login code or 2FA password with anyone.</footer>",
        cancel_kb()
    )
    return ADD_PHONE

async def addsession_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Add a session directly via string session. Same flow as /host."""
    await remember_user(update.effective_user)

    if not ctx.args:
        await send_rich_message(
            update, ctx,
            "<h2>🔑 ADD SESSION — STRING SESSION</h2>"
            "<p><i>Add an account using an existing Pyrogram or Telethon string session.</i></p><hr/>"
            "<h3>⌨️ Usage</h3>"
            "<p><code>/addsession YOUR_STRING_SESSION</code></p>"
            "<h3>📋 Notes</h3>"
            "<table bordered striped>"
            "<tr><th>FIELD</th><th>INFO</th></tr>"
            "<tr><td>Pyrogram string</td><td>Supported ✅</td></tr>"
            "<tr><td>Telethon string</td><td>Supported ✅</td></tr>"
            "<tr><td>Already hosted</td><td>Rejected ❌ (same as /host)</td></tr>"
            "</table>"
            "<footer>⚠️ Only add sessions for accounts you own or are authorized to manage.</footer>",
            main_menu_kb()
        )
        return

    session_string = ctx.args[0].strip()
    uid = update.effective_user.id

    # Sending status
    await send_rich_message(
        update, ctx,
        "<h2>⏳ VALIDATING SESSION…</h2>"
        "<p>Connecting to Telegram to verify the session string.</p>",
    )

    # Try Pyrogram first
    phone = None
    client_type_used = None
    try:
        from pyrogram import Client as PyroClient
        client = PyroClient(
            name="verify_add_session",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_string,
            in_memory=True,
            device_model="Samsung Galaxy S23",
            system_version="Android 13",
            app_version="10.0.1",
            lang_code="en",
            no_updates=True,
        )
        await client.start()
        me = await client.get_me()
        raw_phone = str(me.phone_number or me.id or "")
        phone = ("+" + raw_phone) if raw_phone and not raw_phone.startswith("+") else raw_phone
        await client.stop()
        client_type_used = "pyrogram"
    except Exception as pyro_err:
        logging.info("Pyrogram session validation failed; trying Telethon: %s", pyro_err)
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            tl_client = TelegramClient(StringSession(session_string), API_ID, API_HASH,
                                       connection_retries=1, retry_delay=1, timeout=15)
            await tl_client.connect()
            if not await tl_client.is_user_authorized():
                await tl_client.disconnect()
                raise Exception("Telethon session not authorized")
            me = await tl_client.get_me()
            raw_phone = str(me.phone or me.id or "")
            phone = ("+" + raw_phone) if raw_phone and not raw_phone.startswith("+") else raw_phone
            await tl_client.disconnect()
            client_type_used = "telethon"
        except Exception as tl_err:
            await send_rich_message(
                update, ctx,
                "<h2>❌ INVALID SESSION</h2>"
                "<p>The provided string session could not be validated.</p>"
                f"<p><code>{html.escape(str(tl_err))}</code></p>"
                "<footer>Make sure the session string is complete and not expired.</footer>",
                main_menu_kb()
            )
            return

    if not phone:
        await send_rich_message(
            update, ctx,
            "<h2>❌ COULD NOT RESOLVE PHONE</h2>"
            "<p>Session is valid but phone number could not be retrieved.</p>",
            main_menu_kb()
        )
        return

    # Check if already hosted (same as /host flow)
    try:
        existing_sessions = await get_all_sessions()
    except Exception:
        logging.exception("Could not check whether session is already hosted")
        await send_rich_message(
            update, ctx,
            "<h2>❌ DATABASE CHECK FAILED</h2>"
            "<p>Could not verify whether this account is already hosted.</p>",
            main_menu_kb()
        )
        return

    for item in existing_sessions:
        stored = str(item.get("phone", "")).strip()
        if stored == phone or stored == phone.lstrip("+") or "+" + stored == phone:
            await send_rich_message(
                update, ctx,
                "<h2>⚠️ ACCOUNT ALREADY HOSTED</h2>"
                f"<p><code>{html.escape(phone)}</code> is already hosted in this bot.</p>"
                "<footer>No changes were made. The existing session was left unchanged.</footer>",
                main_menu_kb()
            )
            return

    # Save to database
    try:
        await save_session(uid, phone, session_string, client_type=client_type_used)
        await send_rich_message(
            update, ctx,
            "<h1>✅ SESSION ADDED</h1>"
            f"<p><code>{html.escape(phone)}</code> has been connected successfully.</p>"
            "<p><i>This session was added via string session — same as a hosted account.</i></p>"
            "<footer>Your session is now active and saved in the database.</footer>",
            main_menu_kb()
        )
    except Exception as e:
        await send_rich_message(
            update, ctx,
            f"<h2>❌ SESSION SAVE ERROR</h2><p><code>{html.escape(str(e))}</code></p>",
            main_menu_kb()
        )


# ── /stop ────────────────────────────────────────────────────────────────
async def stop_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_tasks = _get_user_tasks(uid)
    if not user_tasks:
        await send_rich_message(
            update, ctx,
            "<h2>🚫 NO ACTIVE OPERATION</h2>"
            "<p>You have no running background operation to stop.</p>"
            "<footer>Use the 🚫 Stop Progress button on the live progress message to cancel an operation.</footer>",
            main_menu_kb()
        )
        return
    # Cancel all user tasks
    cancelled = 0
    for msg_id, task in user_tasks:
        event = _get_cancel_event(uid, msg_id)
        if event:
            event.set()
        if not task.done():
            task.cancel()
        cancelled += 1
    await send_rich_message(
        update, ctx,
        f"<h2>🚫 STOP SIGNAL SENT</h2>"
        f"<p>Cancellation requested for <b>{cancelled}</b> active operation(s).</p>"
        "<footer>The operation(s) will stop after the current step finishes.</footer>",
        main_menu_kb()
    )


# ── /status ────────────────────────────────────────────────────────────────
def rich_text_with_custom_emojis(value: str):
    return _shared_rich_text_with_custom_emojis(value)


def build_status_summary_blocks(counts: dict):
    rows = [
        ("🟢 Active", counts["active"], "success", "status_view:active"),
        ("🔴 Expired", counts["expired"], "danger", "status_view:expired"),
        ("📦 Total", counts["total"], "primary", "status_view:total"),
    ]
    cells = [[
        {"text": "STATUS", "is_header": True},
        {"text": "COUNT", "is_header": True, "align": "center"},
        {"text": "DETAILS", "is_header": True, "align": "center"},
    ]]
    for label, count, style, callback in rows:
        cells.append([
            {"text": rich_text_with_custom_emojis(label)},
            {"text": str(count), "align": "center"},
            {"text": [{"type": "button", "button": {
                "text": "ᴠɪᴇᴡ", "style": style, "callback_data": callback
            }}], "align": "center"},
        ])
    return [
        {"type": "heading", "size": 1, "text": rich_text_with_custom_emojis("📊 ACCOUNT STATUS")},
        {"type": "paragraph", "text": "Live session overview"},
        {"type": "divider"},
        {"type": "table", "cells": cells, "is_bordered": True, "is_striped": True},
        {"type": "footer", "text": "Session statistics"},
    ]


async def edit_status_summary(ctx, chat_id: int, message_id: int, counts: dict):
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "rich_message": {"blocks": convert_rich_blocks(build_status_summary_blocks(counts))},
        "reply_markup": status_kb().to_dict(),
    }
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(api_base + "editMessageText", json=payload) as response:
            data = await response.json(content_type=None)
            if not response.ok or not data.get("ok"):
                raise RuntimeError(data.get("description", "Status summary edit failed"))


async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update, ctx):
        return
    records = await get_all_status_sessions()
    counts = {
        "active": sum(1 for item in records if item.get("active", False)),
        "expired": sum(1 for item in records if not item.get("active", False)),
        "total": len(records),
    }
    cached = {
        "owner_id": update.effective_user.id,
        "counts": counts,
        "active": [item.get("phone", "Unknown") for item in records if item.get("active", False)],
        "expired": [item.get("phone", "Unknown") for item in records if not item.get("active", False)],
        "total": [item.get("phone", "Unknown") for item in records],
    }
    rich_fallback = (
        "<h1>📊 ACCOUNT STATUS</h1><p><i>Live session overview</i></p><hr/>"
        "<table bordered striped><tr><th>STATUS</th><th>COUNT</th><th>DETAILS</th></tr>"
        f"<tr><td>🟢 Active</td><td><b>{counts['active']}</b></td><td>ᴠɪᴇᴡ</td></tr>"
        f"<tr><td>🔴 Expired</td><td><b>{counts['expired']}</b></td><td>ᴠɪᴇᴡ</td></tr>"
        f"<tr><td>📦 Total</td><td><b>{counts['total']}</b></td><td>ᴠɪᴇᴡ</td></tr></table>"
        "<footer>Session statistics</footer>"
    )
    if update.callback_query and update.callback_query.message:
        message_id = update.callback_query.message.message_id
        chat_id = update.callback_query.message.chat_id
        ctx.application.bot_data[f"status:{chat_id}:{message_id}"] = cached
        try:
            await edit_status_summary(ctx, chat_id, message_id, counts)
            return
        except Exception as exc:
            logging.debug("Rich status summary edit unavailable; using fallback: %s", exc)
            await _edit_html_message(ctx, chat_id, message_id, rich_fallback, reply_markup=status_view_kb())
            return

    chat_id = update.effective_chat.id
    fallback_text = (
        "📊 <b>ACCOUNT STATUS</b>\n\n"
        f"🟢 Active: <code>{counts['active']}</code>\n"
        f"🔴 Expired: <code>{counts['expired']}</code>\n"
        f"📦 Total: <code>{counts['total']}</code>"
    )
    message_id = await _rich_send_blocks_new(
        ctx, chat_id, build_status_summary_blocks(counts),
        rich_fallback, status_kb(), fallback_text,
        fallback_markup=status_view_kb()
    )
    if message_id is not None:
        ctx.application.bot_data[f"status:{chat_id}:{message_id}"] = cached


def build_refresh_summary_blocks(result: dict):
    rows = [
        ("🟢 ACTIVE", result["active"], "success", "refresh_view:active"),
        ("🔴 EXPIRED", result["expired"], "danger", "refresh_view:expired"),
        ("⚠️ ERRORS", result["errors"], "danger", "refresh_view:errors"),
        ("🎁 CHECKED", result["checked"], "primary", "refresh_view:checked"),
    ]
    cells = [[
        {"text": "STATUS", "is_header": True},
        {"text": "COUNT", "is_header": True, "align": "center"},
        {"text": "DETAILS", "is_header": True, "align": "center"},
    ]]
    for label, count, style, callback in rows:
        cells.append([
            {"text": rich_text_with_custom_emojis(label)},
            {"text": str(count), "align": "center"},
            {"text": [{"type": "button", "button": {
                "text": "ᴠɪᴇᴡ", "style": style, "callback_data": callback
            }}], "align": "center"},
        ])
    return [
        {"type": "heading", "size": 1, "text": rich_text_with_custom_emojis("⭐ SESSION REFRESH COMPLETE")},
        {"type": "paragraph", "text": "All available session checks have finished."},
        {"type": "divider"},
        {"type": "table", "cells": cells, "is_bordered": True, "is_striped": True},
        {"type": "footer", "text": rich_text_with_custom_emojis("🔐 Account numbers were kept private during verification")},
    ]


async def edit_refresh_summary(ctx, chat_id: int, message_id: int, result: dict):
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "rich_message": {"blocks": convert_rich_blocks(build_refresh_summary_blocks(result))},
        "reply_markup": status_kb().to_dict(),
    }
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(api_base + "editMessageText", json=payload) as response:
            data = await response.json(content_type=None)
            if not response.ok or not data.get("ok"):
                raise RuntimeError(data.get("description", "Rich summary edit failed"))


def build_join_summary_blocks(join_result: dict, total: int, target: str) -> list:
    rows = [
        ("✅ Joined",        join_result["joined"],  "success", "join_view:joined"),
        ("🔄 Already Member",join_result["already"], "primary", "join_view:already"),
        ("❌ Failed",        join_result["failed"],  "danger",  "join_view:failed"),
        ("👥 Total Accounts",total,                  "primary", "join_view:total"),
    ]
    cells = [[
        {"text": "RESULT", "is_header": True},
        {"text": "COUNT", "is_header": True, "align": "center"},
        {"text": "DETAILS", "is_header": True, "align": "center"},
    ]]
    for label, count, style, cb_data in rows:
        cells.append([
            {"text": label},
            {"text": [{"type": "bold", "text": str(count)}], "align": "center"},
            {"text": [{"type": "button", "button": {
                "text": "ᴠɪᴇᴡ", "style": style, "callback_data": cb_data,
            }}], "align": "center"},
        ])
    return [
        {"type": "heading", "size": 1, "text": "✅ JOIN COMPLETE"},
        {"type": "paragraph", "text": f"All accounts processed for {target}"},
        {"type": "divider"},
        {"type": "table", "cells": cells, "is_bordered": True, "is_striped": True},
        {"type": "footer", "text": "Join operation finished."},
    ]


async def edit_join_summary(ctx, chat_id: int, message_id: int,
                            join_result: dict, total: int, target: str):
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "rich_message": {"blocks": convert_rich_blocks(build_join_summary_blocks(join_result, total, target))},
        "reply_markup": main_menu_kb().to_dict(),
    }
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(api_base + "editMessageText", json=payload) as response:
            data = await response.json(content_type=None)
            if not response.ok or not data.get("ok"):
                raise RuntimeError(data.get("description", "Join summary edit failed"))


def build_report_summary_blocks(result: dict, target: str, total_ids: int, count: int) -> list:
    rows = [
        ("✅ Success",    result["success"],    "success", "report_view:success"),
        ("❌ Failed",     result["failed"],     "danger",  "report_view:failed"),
        ("🔴 Expired",   result["expired"],    "danger",  "report_view:expired"),
        ("👥 Total IDs", total_ids,            "primary", "report_view:total"),
        ("📊 Total Reports", total_ids * count, None,     None),
    ]
    cells = [[
        {"text": "RESULT", "is_header": True},
        {"text": "COUNT",  "is_header": True, "align": "center"},
    ]]
    for label, count_val, style, cb_data in rows:
        if cb_data:
            count_cell = {"text": [
                {"type": "bold", "text": str(count_val)},
                {"type": "button", "button": {
                    "text": "ᴠɪᴇᴡ", "style": style, "callback_data": cb_data,
                }},
            ], "align": "center"}
        else:
            count_cell = {"text": str(count_val), "align": "center"}
        cells.append([{"text": label}, count_cell])
    blocks = [
        {"type": "heading",  "size": 1, "text": "📊 REPORT COMPLETE"},
        {"type": "paragraph","text": f"Mass report finished · Target: {target}"},
        {"type": "divider"},
        {"type": "table", "cells": cells, "is_bordered": True, "is_striped": True},
    ]
    if result.get("errors"):
        unique = list(set(result["errors"]))[:5]
        err_items = [{"type": "paragraph", "text": e} for e in unique]
        blocks += [{"type": "heading", "size": 3, "text": "⚠️ ERRORS"}] + err_items
    blocks.append({"type": "footer", "text": "Report session complete."})
    return blocks


async def edit_report_summary(ctx, chat_id: int, message_id: int,
                              result: dict, target: str, total_ids: int, count: int):
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "rich_message": {"blocks": convert_rich_blocks(build_report_summary_blocks(result, target, total_ids, count))},
        "reply_markup": main_menu_kb().to_dict(),
    }
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(api_base + "editMessageText", json=payload) as response:
            data = await response.json(content_type=None)
            if not response.ok or not data.get("ok"):
                raise RuntimeError(data.get("description", "Report summary edit failed"))


def build_leave_summary_blocks(leave_result: dict, total: int, target: str) -> list:
    rows = [
        ("🟢 Leaved",        leave_result["leaved"],    "success", "leave_view:leaved"),
        ("🟡 No Member",     leave_result["no_member"], "primary", "leave_view:no_member"),
        ("🔴 Failed",        leave_result["failed"],    "danger",  "leave_view:failed"),
        ("📦 Total Accounts",total,                     "primary", "leave_view:total"),
    ]
    cells = [[
        {"text": "RESULT", "is_header": True},
        {"text": "COUNT", "is_header": True, "align": "center"},
        {"text": "DETAILS", "is_header": True, "align": "center"},
    ]]
    for label, count, style, cb_data in rows:
        cells.append([
            {"text": label},
            {"text": [{"type": "bold", "text": str(count)}], "align": "center"},
            {"text": [{"type": "button", "button": {
                "text": "ᴠɪᴇᴡ", "style": style, "callback_data": cb_data,
            }}], "align": "center"},
        ])
    return [
        {"type": "heading",  "size": 1, "text": "🚪 LEAVE COMPLETE"},
        {"type": "paragraph","text": f"All accounts processed for {target}"},
        {"type": "divider"},
        {"type": "table", "cells": cells, "is_bordered": True, "is_striped": True},
        {"type": "footer", "text": "Leave operation finished."},
    ]


async def edit_leave_summary(ctx, chat_id: int, message_id: int,
                             leave_result: dict, total: int, target: str):
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "rich_message": {"blocks": convert_rich_blocks(build_leave_summary_blocks(leave_result, total, target))},
        "reply_markup": main_menu_kb().to_dict(),
    }
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(api_base + "editMessageText", json=payload) as response:
            data = await response.json(content_type=None)
            if not response.ok or not data.get("ok"):
                raise RuntimeError(data.get("description", "Leave summary edit failed"))


# ── Background task: Refresh ────────────────────────────────────────────────
async def _run_refresh_task(
    ctx: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    uid: int,
    msg_id: int,
    cancel_event: asyncio.Event,
):
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    progress_lines = []

    def build_progress_html(index: int, total: int, latest: list, finished: bool = False):
        percent = int((index / total) * 100) if total else 100
        title = "⭐ SESSION REFRESH COMPLETE" if finished else "⭐ SESSION REFRESH"
        state = "All session checks finished" if finished else "Verifying connected sessions"
        rows = "".join(
            f"<tr><td>{icon}</td><td><code>{phone}</code></td><td><b>{label}</b></td></tr>"
            for icon, phone, label in latest
        )
        if not rows:
            rows = '<tr><td>🔄</td><td colspan="2">Preparing checks…</td></tr>'
        return (
            f"<h1>{title}</h1>"
            f"<p><i>{state}</i></p><hr/>"
            f"<h2>📊 Progress · {index}/{total}</h2>"
            f"<p><b>{percent}%</b> completed</p>"
            "<table bordered striped>"
            "<tr><th>STATUS</th><th>ACCOUNT</th><th>RESULT</th></tr>"
            f"{rows}"
            "</table>"
            "<footer>🔐 Account numbers are masked for privacy</footer>"
        )

    async def publish_progress(html_content: str, with_stop_btn: bool = True):
        rich_html = apply_custom_emojis(html_content)
        payload = {
            "chat_id": chat_id,
            "message_id": msg_id,
            "rich_message": {"html": rich_html},
        }
        # Show stop button only while running
        if with_stop_btn:
            payload["reply_markup"] = stop_progress_kb(uid, msg_id).to_dict()
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(api_base + "editMessageText", json=payload) as response:
                    result = await response.json(content_type=None)
                    if not response.ok or not result.get("ok"):
                        raise RuntimeError(result.get("description", "Refresh progress update failed"))
        except Exception as exc:
            logging.warning("Refresh progress update failed: %s", exc)

    async def report_progress(index: int, total: int, phone: str, status: str):
        # Check cancellation
        if cancel_event.is_set():
            raise asyncio.CancelledError("User requested stop")
        icon, label = {
            "active": ("🟢", "ACTIVE"),
            "expired": ("🔴", "EXPIRED"),
            "error": ("⚠️", "CHECK ERROR"),
        }.get(status, ("ℹ️", status.upper()))
        phone_text = str(phone or "Unknown")
        masked_phone = ("••••" + phone_text[-4:]) if len(phone_text) > 4 else phone_text
        progress_lines.append((icon, html.escape(masked_phone), label))
        visible_lines = progress_lines[-6:]
        panel = build_progress_html(index, total, visible_lines)
        await publish_progress(panel, with_stop_btn=True)

    stopped = False
    try:
        result = await refresh_all_sessions(on_progress=report_progress)
    except asyncio.CancelledError:
        stopped = True
        result = {"active": 0, "expired": 0, "errors": 0, "checked": 0,
                  "active_ids": [], "expired_ids": [], "error_ids": [], "checked_ids": []}
    finally:
        _unregister_task(uid, msg_id)

    if stopped:
        await _rich_edit_by_id(
            ctx, chat_id, msg_id,
            "<h2>🚫 SESSION REFRESH STOPPED</h2>"
            "<p><i>The refresh operation was cancelled by user request.</i></p>",
            main_menu_kb(),
            "🚫 Session refresh was stopped.",
            main_menu_kb()
        )
        return

    final_html = (
        "<h1>⭐ SESSION REFRESH COMPLETE</h1>"
        "<p><i>All available session checks have finished.</i></p><hr/>"
        "<table bordered striped><tr><th>STATUS</th><th>COUNT</th><th>DETAILS</th></tr>"
        f"<tr><td>🟢 ACTIVE</td><td><b>{result['active']}</b></td><td>ᴠɪᴇᴡ</td></tr>"
        f"<tr><td>🔴 EXPIRED</td><td><b>{result['expired']}</b></td><td>ᴠɪᴇᴡ</td></tr>"
        f"<tr><td>⚠️ ERRORS</td><td><b>{result['errors']}</b></td><td>ᴠɪᴇᴡ</td></tr>"
        f"<tr><td>🎁 CHECKED</td><td><b>{result['checked']}</b></td><td>ᴠɪᴇᴡ</td></tr>"
        "</table>"
        "<footer>🔐 Account numbers were kept private during verification</footer>"
    )

    ctx.application.bot_data[f"refresh:{chat_id}:{msg_id}"] = {
        "owner_id": uid,
        "active": result.get("active_ids", []),
        "expired": result.get("expired_ids", []),
        "errors": result.get("error_ids", []),
        "checked": result.get("checked_ids", []),
        "counts": {k: result[k] for k in ("active", "expired", "errors", "checked")},
    }
    # Update message WITHOUT stop button
    try:
        await edit_refresh_summary(ctx, chat_id, msg_id, result)
    except Exception as exc:
        logging.debug("Rich refresh summary unavailable; using fallback: %s", exc)
        final_text = (
            "⭐ SESSION REFRESH COMPLETE\n\n"
            f"🟢 ACTIVE: {result['active']}\n"
            f"🔴 EXPIRED: {result['expired']}\n"
            f"⚠️ ERRORS: {result['errors']}\n"
            f"🎁 CHECKED: {result['checked']}"
        )
        try:
            await _edit_html_message(ctx, chat_id, msg_id, final_text, reply_markup=refresh_view_kb())
        except Exception:
            pass


# ── /refresh ────────────────────────────────────────────────────────────────
async def refresh_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update, ctx):
        return

    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"

    # Send initial progress message with stop button placeholder
    initial_html = (
        "<h1>⭐ SESSION REFRESH</h1>"
        "<p><i>Verifying connected sessions…</i></p><hr/>"
        "<table bordered striped>"
        "<tr><th>STATUS</th><th>ACCOUNT</th><th>RESULT</th></tr>"
        "<tr><td>🔄</td><td colspan=\"2\">Preparing checks…</td></tr>"
        "</table>"
        "<footer>🔐 Account numbers are masked for privacy</footer>"
    )

    # Edit existing message if from callback, else send new
    if update.callback_query and update.callback_query.message:
        msg_id = update.callback_query.message.message_id
        payload = {
            "chat_id": chat_id,
            "message_id": msg_id,
            "rich_message": {"html": apply_custom_emojis(initial_html)},
            "reply_markup": stop_progress_kb(uid, msg_id).to_dict(),
        }
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(api_base + "editMessageText", json=payload) as response:
                    result = await response.json(content_type=None)
                    if not response.ok or not result.get("ok"):
                        raise RuntimeError(result.get("description"))
        except Exception as exc:
            logging.warning("Could not set initial refresh message: %s", exc)
    else:
        # New message
        msg_id = await _rich_send_new(
            ctx, chat_id, initial_html,
            stop_progress_kb(uid, 0),  # temp placeholder, will update after we have msg_id
            fallback_text="🔄 SESSION REFRESH\nPreparing session checks…"
        )
        if msg_id:
            # Update with correct stop button
            payload = {
                "chat_id": chat_id,
                "message_id": msg_id,
                "reply_markup": stop_progress_kb(uid, msg_id).to_dict(),
            }
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    await session.post(api_base + "editMessageReplyMarkup", json=payload)
            except Exception:
                pass

    if not msg_id:
        return

    # Fire background task
    cancel_event = asyncio.Event()
    task = asyncio.create_task(
        _run_refresh_task(ctx, chat_id, uid, msg_id, cancel_event)
    )
    _register_task(uid, msg_id, task, cancel_event)


# ── Background task: Join ────────────────────────────────────────────────
async def _run_join_task(
    ctx: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    uid: int,
    msg_id: int,
    target: str,
    sessions: list,
    cancel_event: asyncio.Event,
):
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    total = len(sessions)
    last = {"t": 0}
    stopped = False

    async def cb(r):
        if cancel_event.is_set():
            raise asyncio.CancelledError("User requested stop")
        now = time.time()
        done = r["joined"] + r["already"] + r["failed"]
        if now - last["t"] >= 1 or done >= total:
            last["t"] = now
            progress_html = (
                "<h2>⏳ JOIN IN PROGRESS</h2>"
                f"<p><i>Target: <code>{html.escape(target)}</code></i></p><hr/>"
                "<table bordered striped>"
                "<tr><th>RESULT</th><th>COUNT</th></tr>"
                f"<tr><td>✅ Joined</td><td><b>{r['joined']}</b></td></tr>"
                f"<tr><td>🔄 Already Member</td><td><b>{r['already']}</b></td></tr>"
                f"<tr><td>❌ Failed</td><td><b>{r['failed']}</b></td></tr>"
                f"<tr><td>📊 Done</td><td><b>{done} / {total}</b></td></tr>"
                "</table>"
                "<footer>Join operation is running…</footer>"
            )
            payload = {
                "chat_id": chat_id,
                "message_id": msg_id,
                "rich_message": {"html": apply_custom_emojis(progress_html)},
                "reply_markup": stop_progress_kb(uid, msg_id).to_dict(),
            }
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(api_base + "editMessageText", json=payload) as response:
                        result = await response.json(content_type=None)
                        if not response.ok or not result.get("ok"):
                            raise RuntimeError(result.get("description"))
            except Exception as exc:
                logging.warning("Join progress update failed: %s", exc)

    try:
        join_result = await mass_join(sessions, target, progress_cb=cb)
    except asyncio.CancelledError:
        stopped = True
        join_result = {"joined": 0, "already": 0, "failed": 0, "joined_ids": [], "already_ids": [], "failed_ids_arr": []}
    finally:
        _unregister_task(uid, msg_id)

    if stopped:
        await _rich_edit_by_id(
            ctx, chat_id, msg_id,
            "<h2>🚫 JOIN STOPPED</h2><p>The join operation was cancelled.</p>",
            main_menu_kb(), "🚫 Join stopped.", main_menu_kb()
        )
        return

    all_phones = [s.get("phone", "?") for s in sessions]
    ctx.application.bot_data[f"join:{chat_id}:{msg_id}"] = {
        "owner_id": uid, "target": target, "total": total,
        "counts": {k: join_result[k] for k in ("joined", "already", "failed")},
        "joined": join_result.get("joined_ids", []),
        "already": join_result.get("already_ids", []),
        "failed": join_result.get("failed_ids_arr", []),
        "total_list": all_phones,
    }
    try:
        await edit_join_summary(ctx, chat_id, msg_id, join_result, total, target)
    except Exception as exc:
        logging.debug("Rich join summary unavailable; using fallback: %s", exc)
        rich_done = (
            "<h1>✅ JOIN COMPLETE</h1>"
            f"<p><i>All accounts processed for <code>{html.escape(target)}</code></i></p><hr/>"
            "<table bordered striped>"
            "<tr><th>RESULT</th><th>COUNT</th></tr>"
            f"<tr><td>✅ Joined</td><td><b>{join_result['joined']}</b></td></tr>"
            f"<tr><td>🔄 Already Member</td><td><b>{join_result['already']}</b></td></tr>"
            f"<tr><td>❌ Failed</td><td><b>{join_result['failed']}</b></td></tr>"
            f"<tr><td>👥 Total Accounts</td><td><b>{total}</b></td></tr>"
            "</table><footer>Join operation finished.</footer>"
        )
        await _rich_edit_by_id(
            ctx, chat_id, msg_id,
            rich_done, main_menu_kb(),
            f"✅ Join done | 📦 {total} | ✅ {join_result['joined']} | ❌ {join_result['failed']}",
            main_menu_kb()
        )


# ── /join ────────────────────────────────────────────────────────────────
async def join_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update, ctx):
        return
    if not ctx.args:
        await send_rich_message(
            update, ctx,
            "<h1>👥 JOIN CHAT</h1>"
            "<p><i>Join a group or channel with your authorized connected accounts.</i></p><hr/>"
            "<h2>🎯 SUPPORTED TARGET FORMATS</h2>"
            "<table bordered striped>"
            "<tr><th>TYPE</th><th>FORMAT</th></tr>"
            "<tr><td>Username</td><td><code>@groupusername</code></td></tr>"
            "<tr><td>Public link</td><td><code>https://t.me/groupusername</code></td></tr>"
            "<tr><td>Invite link</td><td><code>https://t.me/+INVITE_CODE</code></td></tr>"
            "<tr><td>Chat ID</td><td><code>-1001234567890</code></td></tr>"
            "</table>"
            "<h3>⌨️ EXAMPLES</h3>"
            "<p><code>/join @groupusername</code></p>"
            "<p><code>/join https://t.me/+ABC123</code></p>"
            "<p><code>/join -1001234567890</code></p>"
            "<footer>Use only accounts you own or are explicitly authorized to manage.</footer>",
            main_menu_kb()
        )
        return

    target = ctx.args[0]
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    sessions = await get_all_sessions()
    if not sessions:
        await send_rich_message(
            update, ctx,
            "<h2>❌ NO ACTIVE SESSIONS</h2>"
            "<p>Add accounts first before using the join command.</p>",
            main_menu_kb()
        )
        return

    total = len(sessions)
    join_initial_rich = (
        "<h2>⏳ JOIN IN PROGRESS</h2>"
        f"<p><i>Target: <code>{html.escape(target)}</code></i></p><hr/>"
        "<table bordered striped>"
        "<tr><th>RESULT</th><th>COUNT</th></tr>"
        "<tr><td>✅ Joined</td><td><b>0</b></td></tr>"
        "<tr><td>🔄 Already</td><td><b>0</b></td></tr>"
        "<tr><td>❌ Failed</td><td><b>0</b></td></tr>"
        f"<tr><td>📊 Done</td><td><b>0 / {total}</b></td></tr>"
        "</table>"
    )
    msg_id = await _rich_send_new(
        ctx, chat_id, join_initial_rich,
        stop_progress_kb(uid, 0),  # temp, updated below
        f"⏳ Joining {html.escape(target)}…"
    )
    if msg_id:
        api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
        payload = {"chat_id": chat_id, "message_id": msg_id,
                   "reply_markup": stop_progress_kb(uid, msg_id).to_dict()}
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                await session.post(api_base + "editMessageReplyMarkup", json=payload)
        except Exception:
            pass

    cancel_event = asyncio.Event()
    task = asyncio.create_task(
        _run_join_task(ctx, chat_id, uid, msg_id, target, sessions, cancel_event)
    )
    _register_task(uid, msg_id, task, cancel_event)


# ── Background task: Leave ────────────────────────────────────────────────
async def _run_leave_task(
    ctx: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    uid: int,
    msg_id: int,
    target: str,
    sessions: list,
    cancel_event: asyncio.Event,
):
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    total = len(sessions)
    last = {"t": 0}
    stopped = False

    async def cb(r):
        if cancel_event.is_set():
            raise asyncio.CancelledError("User requested stop")
        now = time.time()
        done = r["leaved"] + r["no_member"] + r["failed"]
        if now - last["t"] >= 1 or done >= total:
            last["t"] = now
            progress_html = (
                "<h2>⏳ LEAVE IN PROGRESS</h2>"
                f"<p><i>Target: <code>{html.escape(target)}</code></i></p><hr/>"
                "<table bordered striped>"
                "<tr><th>RESULT</th><th>COUNT</th></tr>"
                f"<tr><td>🟢 Leaved</td><td><b>{r['leaved']}</b></td></tr>"
                f"<tr><td>🟡 No Member</td><td><b>{r['no_member']}</b></td></tr>"
                f"<tr><td>🔴 Failed</td><td><b>{r['failed']}</b></td></tr>"
                f"<tr><td>📊 Done</td><td><b>{done} / {total}</b></td></tr>"
                "</table>"
                "<footer>Leave operation is running…</footer>"
            )
            payload = {
                "chat_id": chat_id,
                "message_id": msg_id,
                "rich_message": {"html": apply_custom_emojis(progress_html)},
                "reply_markup": stop_progress_kb(uid, msg_id).to_dict(),
            }
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(api_base + "editMessageText", json=payload) as response:
                        result = await response.json(content_type=None)
                        if not response.ok or not result.get("ok"):
                            raise RuntimeError(result.get("description"))
            except Exception as exc:
                logging.warning("Leave progress update failed: %s", exc)

    try:
        leave_result = await mass_leave(sessions, target, progress_cb=cb)
    except asyncio.CancelledError:
        stopped = True
        leave_result = {"leaved": 0, "no_member": 0, "failed": 0, "leaved_ids": [], "no_member_ids": [], "failed_ids": []}
    finally:
        _unregister_task(uid, msg_id)

    if stopped:
        await _rich_edit_by_id(
            ctx, chat_id, msg_id,
            "<h2>🚫 LEAVE STOPPED</h2><p>The leave operation was cancelled.</p>",
            main_menu_kb(), "🚫 Leave stopped.", main_menu_kb()
        )
        return

    all_phones = [s.get("phone", "?") for s in sessions]
    ctx.application.bot_data[f"leave:{chat_id}:{msg_id}"] = {
        "owner_id": uid, "target": target, "total": total,
        "counts": {k: leave_result[k] for k in ("leaved", "no_member", "failed")},
        "leaved": leave_result.get("leaved_ids", []),
        "no_member": leave_result.get("no_member_ids", []),
        "failed": leave_result.get("failed_ids", []),
        "total_list": all_phones,
    }
    try:
        await edit_leave_summary(ctx, chat_id, msg_id, leave_result, total, target)
    except Exception as exc:
        logging.debug("Rich leave summary unavailable; using fallback: %s", exc)
        rich_done = (
            "<h1>🚪 LEAVE COMPLETE</h1>"
            f"<p><i>All accounts processed for <code>{html.escape(target)}</code></i></p><hr/>"
            "<table bordered striped>"
            "<tr><th>RESULT</th><th>COUNT</th></tr>"
            f"<tr><td>🟢 Leaved</td><td><b>{leave_result['leaved']}</b></td></tr>"
            f"<tr><td>🟡 No Member</td><td><b>{leave_result['no_member']}</b></td></tr>"
            f"<tr><td>🔴 Failed</td><td><b>{leave_result['failed']}</b></td></tr>"
            f"<tr><td>📦 Total Accounts</td><td><b>{total}</b></td></tr>"
            "</table><footer>Leave operation finished.</footer>"
        )
        await _rich_edit_by_id(
            ctx, chat_id, msg_id,
            rich_done, main_menu_kb(),
            f"🚪 Leave done | 📦 {total} | 🟢 {leave_result['leaved']} | 🔴 {leave_result['failed']}",
            main_menu_kb()
        )


# ── /leave ────────────────────────────────────────────────────────────────
async def leave_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update, ctx):
        return
    if not ctx.args:
        await send_rich_message(
            update, ctx,
            "<h2>🚪 LEAVE CHAT</h2>"
            "<p>Leave a group or channel with all authorized accounts.</p><hr/>"
            "<h3>🎯 Supported Target Formats</h3>"
            "<table bordered striped>"
            "<tr><th>TYPE</th><th>FORMAT</th></tr>"
            "<tr><td>Username</td><td><code>@groupusername</code></td></tr>"
            "<tr><td>Public link</td><td><code>https://t.me/groupusername</code></td></tr>"
            "<tr><td>Invite link</td><td><code>https://t.me/+INVITE_CODE</code></td></tr>"
            "<tr><td>Chat ID</td><td><code>-1001234567890</code></td></tr>"
            "</table>"
            "<p>Examples:</p>"
            "<p><code>/leave @groupusername</code></p>"
            "<p><code>/leave https://t.me/+ABC123</code></p>"
            "<p><code>/leave -1001234567890</code></p>",
            main_menu_kb()
        )
        return

    target = ctx.args[0]
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    sessions = await get_all_sessions()
    if not sessions:
        await send_rich_message(
            update, ctx,
            "<h2>❌ NO ACTIVE SESSIONS</h2>"
            "<p>Add accounts first before using the leave command.</p>",
            main_menu_kb()
        )
        return

    total = len(sessions)
    leave_initial_rich = (
        "<h2>⏳ LEAVE IN PROGRESS</h2>"
        f"<p><i>Target: <code>{html.escape(target)}</code></i></p><hr/>"
        "<table bordered striped>"
        "<tr><th>RESULT</th><th>COUNT</th></tr>"
        "<tr><td>🟢 Leaved</td><td><b>0</b></td></tr>"
        "<tr><td>🟡 No Member</td><td><b>0</b></td></tr>"
        "<tr><td>🔴 Failed</td><td><b>0</b></td></tr>"
        f"<tr><td>📊 Done</td><td><b>0 / {total}</b></td></tr>"
        "</table>"
    )
    msg_id = await _rich_send_new(
        ctx, chat_id, leave_initial_rich,
        stop_progress_kb(uid, 0),  # temp
        f"⏳ Leaving {html.escape(target)}…"
    )
    if msg_id:
        api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
        payload = {"chat_id": chat_id, "message_id": msg_id,
                   "reply_markup": stop_progress_kb(uid, msg_id).to_dict()}
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                await session.post(api_base + "editMessageReplyMarkup", json=payload)
        except Exception:
            pass

    cancel_event = asyncio.Event()
    task = asyncio.create_task(
        _run_leave_task(ctx, chat_id, uid, msg_id, target, sessions, cancel_event)
    )
    _register_task(uid, msg_id, task, cancel_event)


# ── Background task: Report ────────────────────────────────────────────────
async def _run_report_task(
    ctx: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    uid: int,
    msg_id: int,
    sessions: list,
    target: str,
    reason: str,
    count: int,
    report_message: str,
    cancel_event: asyncio.Event,
):
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    total_ids = len(sessions)
    total_expected = total_ids * count
    last = {"t": 0}
    stopped = False

    async def cb(r):
        if cancel_event.is_set():
            raise asyncio.CancelledError("User requested stop")
        now = time.time()
        done = r["success"] + r["failed"] + r["expired"]
        if now - last["t"] >= 1 or done >= total_expected:
            last["t"] = now
            progress_html = (
                "<h2>📡 REPORTING IN PROGRESS</h2>"
                f"<p><i>Target: <code>{html.escape(target)}</code></i></p><hr/>"
                "<table bordered striped>"
                "<tr><th>RESULT</th><th>COUNT</th></tr>"
                f"<tr><td>✅ Success</td><td><b>{r['success']}</b></td></tr>"
                f"<tr><td>❌ Failed</td><td><b>{r['failed']}</b></td></tr>"
                f"<tr><td>🔴 Expired</td><td><b>{r['expired']}</b></td></tr>"
                f"<tr><td>📊 Done</td><td><b>{done} / {total_expected}</b></td></tr>"
                "</table>"
                "<footer>Report operation is running…</footer>"
            )
            payload = {
                "chat_id": chat_id,
                "message_id": msg_id,
                "rich_message": {"html": apply_custom_emojis(progress_html)},
                "reply_markup": stop_progress_kb(uid, msg_id).to_dict(),
            }
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(api_base + "editMessageText", json=payload) as response:
                        result = await response.json(content_type=None)
                        if not response.ok or not result.get("ok"):
                            raise RuntimeError(result.get("description"))
            except Exception as exc:
                logging.warning("Report progress update failed: %s", exc)

    try:
        result = await mass_report(
            sessions, target, reason, count, report_message, progress_cb=cb
        )
    except asyncio.CancelledError:
        stopped = True
        result = {"success": 0, "failed": 0, "expired": 0, "errors": [],
                  "success_ids": [], "failed_ids_arr": [], "expired_ids_arr": []}
    finally:
        _unregister_task(uid, msg_id)

    if stopped:
        await _rich_edit_by_id(
            ctx, chat_id, msg_id,
            "<h2>🚫 REPORT STOPPED</h2><p>The report operation was cancelled.</p>",
            main_menu_kb(), "🚫 Report stopped.", main_menu_kb()
        )
        return

    all_phones = [s.get("phone", "?") for s in sessions]
    ctx.application.bot_data[f"report:{chat_id}:{msg_id}"] = {
        "owner_id": uid, "target": target, "count": count, "total_ids": total_ids,
        "counts": {k: result[k] for k in ("success", "failed", "expired")},
        "success": result.get("success_ids", []),
        "failed": result.get("failed_ids_arr", []),
        "expired": result.get("expired_ids_arr", []),
        "total_list": all_phones,
    }
    try:
        await edit_report_summary(ctx, chat_id, msg_id, result, target, total_ids, count)
    except Exception as exc:
        logging.debug("Rich report summary unavailable; using fallback: %s", exc)
        rich_done = (
            "<h1>📊 REPORT COMPLETE</h1>"
            "<p><i>Mass report operation finished</i></p><hr/>"
            "<table bordered striped><tr><th>RESULT</th><th>COUNT</th></tr>"
            f"<tr><td>🎯 Target</td><td><code>{html.escape(target)}</code></td></tr>"
            f"<tr><td>✅ Success</td><td><b>{result['success']}</b></td></tr>"
            f"<tr><td>❌ Failed</td><td><b>{result['failed']}</b></td></tr>"
            f"<tr><td>🔴 Expired</td><td><b>{result['expired']}</b></td></tr>"
            f"<tr><td>👥 Total IDs</td><td><b>{total_ids}</b></td></tr>"
            f"<tr><td>📊 Total Reports</td><td><b>{total_ids * count}</b></td></tr></table>"
            "<footer>Report session complete.</footer>"
        )
        await _rich_edit_by_id(
            ctx, chat_id, msg_id,
            rich_done, main_menu_kb(),
            f"📊 Report done | ✅ {result['success']} | ❌ {result['failed']}",
            main_menu_kb()
        )


# ── /session ────────────────────────────────────────────────────────────────
async def session_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update, ctx):
        return
    from session_manager import get_user_sessions
    sessions = await get_user_sessions(update.effective_user.id)
    rows = "".join(
        f"<tr><td>{i}</td><td><code>{html.escape(str(item.get('phone','?')))}</code></td></tr>"
        for i, item in enumerate(sessions, 1)
    )
    rich = (
        "<h1>📋 YOUR SESSIONS</h1><p><i>Saved account overview</i></p><hr/>"
        + ("<table bordered striped><tr><th>#</th><th>PHONE</th></tr>" + rows + "</table>"
           if rows else "<p>There are no saved sessions yet.</p>")
        + "<footer>Only sessions linked to your account are listed here.</footer>"
    )
    await send_rich_message(update, ctx, rich, main_menu_kb())

# ── /unhost ────────────────────────────────────────────────────────────────
async def unhost_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update, ctx):
        return
    if not ctx.args:
        await send_rich_message(
            update, ctx,
            "<h1>🗑 REMOVE HOSTED ACCOUNT</h1>"
            "<p><i>Permanently remove one saved Telegram session from the bot.</i></p><hr/>"
            "<h2>📌 REQUIRED DETAILS</h2>"
            "<table bordered striped>"
            "<tr><th>FIELD</th><th>FORMAT</th></tr>"
            "<tr><td>Phone number</td><td><code>+919876543210</code></td></tr>"
            "</table>"
            "<h3>⌨️ COMMAND</h3>"
            "<p><code>/unhost +919876543210</code></p>"
            "<footer>⚠️ This removes the saved session. Make sure the number is correct before proceeding.</footer>"
        )
        return
    phone   = ctx.args[0].strip()
    removed = await delete_session(phone, update.effective_user.id)
    if removed:
        await send_rich_message(
            update, ctx,
            f"<h2>✓ SESSION REMOVED</h2><p><code>{html.escape(phone)}</code></p>"
            "<footer>The saved session was removed.</footer>",
            main_menu_kb()
        )
    else:
        await send_rich_message(
            update, ctx,
            f"<h2>ⓘ SESSION NOT FOUND</h2><p>No saved session matches <code>{html.escape(phone)}</code>.</p>",
            main_menu_kb()
        )


# ── /removeexpiredid ───────────────────────────────────────────────────────────
async def remove_expired_id_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await has_access(update.effective_user.id):
        await send_access_denied(update, ctx)
        return

    if not ctx.args:
        await send_rich_message(
            update, ctx,
            "<h1>🧹 EXPIRED SESSION CLEANUP</h1>"
            "<p><i>Remove expired session records from the database.</i></p><hr/>"
            "<h2>🎯 SUPPORTED OPTIONS</h2>"
            "<table bordered striped>"
            "<tr><th>TYPE</th><th>COMMAND</th></tr>"
            "<tr><td>Single expired ID</td><td><code>/removeexpiredid +919876543210</code></td></tr>"
            "<tr><td>All expired IDs</td><td><code>/removeexpiredid all</code></td></tr>"
            "</table>"
            "<h3>📌 IMPORTANT</h3>"
            "<p>Only records marked as expired are eligible for deletion. Active session records are kept safe when using <code>all</code>.</p>"
            "<footer>⚠️ Deleted session records cannot be restored from this command.</footer>"
        )
        return

    phone = ctx.args[0].strip()

    if phone.lower() == "all":
        try:
            deleted = await delete_all_expired_sessions()
            await send_rich_message(
                update, ctx,
                "<h1>🧹 EXPIRED ID CLEANUP COMPLETE</h1>"
                f"<p>🗑 Deleted expired session records: <b>{deleted}</b></p>"
                "<footer>Only records marked as expired were removed. Active sessions were kept safe.</footer>",
                main_menu_kb()
            )
        except Exception:
            logging.exception("Failed to remove all expired sessions")
            await send_rich_message(
                update, ctx,
                "<h2>❌ DATABASE ERROR</h2>"
                "<p>Expired sessions could not be deleted. No success was reported.</p>",
                main_menu_kb()
            )
        return

    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        await send_rich_message(
            update, ctx,
            "<h2>❌ INVALID PHONE NUMBER</h2>"
            "<p>Use international format, for example:</p>"
            "<p><code>/removeexpiredid +919876543210</code></p>"
        )
        return

    try:
        deleted = await delete_expired_session(phone)
        if deleted:
            await send_rich_message(
                update, ctx,
                "<h1>✅ EXPIRED ID REMOVED</h1>"
                f"<p>📱 Number: <code>{html.escape(phone)}</code></p>"
                f"<p>🗑 Deleted database records: <b>{deleted}</b></p>"
                "<footer>The expired session is permanently removed from the bot's session database.</footer>",
                main_menu_kb()
            )
        else:
            await send_rich_message(
                update, ctx,
                "<h1>ℹ️ EXPIRED ID NOT FOUND</h1>"
                f"<p><code>{html.escape(phone)}</code> is not currently present in the expired sessions list.</p>"
                "<footer>No database data was deleted.</footer>",
                main_menu_kb()
            )
    except Exception as exc:
        logging.exception("Failed to remove expired session for %s", phone)
        await send_rich_message(
            update, ctx,
            "<h2>❌ DATABASE ERROR</h2>"
            "<p>The expired session could not be removed. No success was reported.</p>"
        )


# ── /sudo /removesudo /sudoinfo ──────────────────────────────────────────────
async def sudo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await send_access_denied(update, ctx)
        return
    if not ctx.args or not ctx.args[0].isdigit():
        await send_rich_message(
            update, ctx,
            "<h1>👑 GRANT SUDO ACCESS</h1><p>➕ Add a trusted administrator to the access list.</p>"
            "<hr/><p>⌨️ Command: <code>/sudo USER_ID</code></p><footer>🔐 Only grant access to people you trust.</footer>"
        )
        return
    user_id = int(ctx.args[0])
    await add_sudo(user_id, update.effective_user.id)
    await send_rich_message(
        update, ctx,
        f"<h1>💎 SUDO ACCESS GRANTED</h1><p>👤 User ID: <code>{user_id}</code></p>"
        "<footer>✅ Administrator added to the authorized access list.</footer>"
    )


async def removesudo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await send_access_denied(update, ctx)
        return
    if not ctx.args or not ctx.args[0].isdigit():
        await send_rich_message(
            update, ctx,
            "<h1>⚠️ REVOKE SUDO ACCESS</h1><p>➖ Remove an administrator from the access list.</p>"
            "<hr/><p>⌨️ Command: <code>/removesudo USER_ID</code></p><footer>🔐 Verify the user ID before proceeding.</footer>"
        )
        return
    user_id = int(ctx.args[0])
    removed = await remove_sudo(user_id)
    if removed:
        rich = f"<h1>🗑️ SUDO ACCESS REMOVED</h1><p>👤 User ID: <code>{user_id}</code></p><footer>✅ Administrator privileges have been revoked.</footer>"
    else:
        rich = f"<h1>🔎 ADMIN NOT FOUND</h1><p>👤 User ID: <code>{user_id}</code> is not in the sudo list.</p><footer>ℹ️ No access changes were made.</footer>"
    await send_rich_message(update, ctx, rich)


async def sudoinfo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update, ctx):
        return
    rows = await list_access_users()
    table_rows = "".join(
        f"<tr><td>{html.escape((item.get('first_name','') + ' ' + item.get('last_name','')).strip() or 'Unknown')}</td>"
        f"<td>{'@' + html.escape(item['username']) if item.get('username') else '—'}</td>"
        f"<td><code>{item['user_id']}</code></td></tr>"
        for item in rows
    )
    rich = (
        "<h1>💎 ACCESS DIRECTORY</h1><p><i>👑 Owner and 🛡️ authorized administrators</i></p><hr/>"
        f"<p>👑 <b>BOT OWNER</b><br/>🆔 <code>{OWNER_ID}</code> · {OWNER_CONTACT}</p>"
        "<h2>💠 SUDO ADMINISTRATORS</h2>"
        + ("<table bordered striped><tr><th>NAME</th><th>USERNAME</th><th>USER ID</th></tr>"
           + table_rows + "</table>" if table_rows else "<p>No additional sudo users are registered.</p>")
        + f"<footer>📊 Total authorized admins: {len(rows)}</footer>"
    )
    await send_rich_message(update, ctx, rich)


# ── /otp (hidden) ───────────────────────────────────────────────────────────
async def otp_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update, ctx):
        return
    if not ctx.args:
        await send_rich_message(
            update, ctx,
            "<h2>📱 OTP LOOKUP</h2>"
            "<p>Provide the phone number of a hosted account.</p>"
            "<p>Usage: <code>/otp +919876543210</code></p>"
        )
        return

    phone    = ctx.args[0].strip()
    sessions = await get_all_sessions()
    matched  = next((s for s in sessions if s.get("phone") == phone), None)

    if not matched:
        await send_rich_message(
            update, ctx,
            f"<h2>❌ ACCOUNT NOT HOSTED</h2>"
            f"<p><code>{html.escape(phone)}</code> is not a hosted session.</p>"
            "<footer>Add the account first with /host.</footer>"
        )
        return

    otp_chat_id = update.effective_chat.id
    msg_id = await _rich_send_new(
        ctx, otp_chat_id,
        "<h2>⏳ FETCHING OTP</h2>"
        f"<p>Looking up latest OTP for <code>{html.escape(phone)}</code>…</p>",
        None,
        f"⏳ Fetching OTP for <code>{html.escape(phone)}</code>..."
    )

    try:
        from session_manager import build_client
        from pyrogram import Client as PyroClient

        client  = await build_client(matched)
        is_pyro = isinstance(client, PyroClient)

        if is_pyro:
            await client.start()
            me = await client.get_me()
            if not me:
                await _rich_edit_by_id(
                    ctx, otp_chat_id, msg_id,
                    f"<h2>❌ SESSION EXPIRED</h2><p><code>{html.escape(phone)}</code> session is no longer valid.</p>",
                    None,
                    f"❌ Session for <code>{html.escape(phone)}</code> is expired.",
                )
                await client.stop()
                return
            messages = []
            async for m in client.get_chat_history(777000, limit=5):
                messages.append(m)
            await client.stop()
            if not messages:
                await _rich_edit_by_id(
                    ctx, otp_chat_id, msg_id,
                    "<h2>❌ NO OTP FOUND</h2><p>No OTP message was found in the inbox.</p>",
                    None, "❌ No OTP message found."
                )
                return
            latest   = messages[0]
            text     = latest.text or "(empty)"
            date_str = latest.date.strftime("%Y-%m-%d %H:%M:%S UTC") if latest.date else "unknown"
        else:
            await client.connect()
            if not await client.is_user_authorized():
                await _rich_edit_by_id(
                    ctx, otp_chat_id, msg_id,
                    f"<h2>❌ SESSION EXPIRED</h2><p><code>{html.escape(phone)}</code> session is no longer valid.</p>",
                    None,
                    f"❌ Session for <code>{html.escape(phone)}</code> is expired.",
                )
                await client.disconnect()
                return
            tg_msgs  = await client.get_messages(777000, limit=5)
            await client.disconnect()
            if not tg_msgs:
                await _rich_edit_by_id(
                    ctx, otp_chat_id, msg_id,
                    "<h2>❌ NO OTP FOUND</h2><p>No OTP message was found in the inbox.</p>",
                    None, "❌ No OTP message found."
                )
                return
            latest   = tg_msgs[0]
            text     = latest.message or "(empty)"
            date_str = latest.date.strftime("%Y-%m-%d %H:%M:%S UTC") if latest.date else "unknown"

        otp_rich = (
            "<h1>📱 OTP MESSAGE</h1>"
            f"<p><b>{html.escape(phone)}</b> — latest OTP</p><hr/>"
            f"<p><code>{html.escape(text)}</code></p>"
            f"<footer>🕐 {html.escape(date_str)}</footer>"
        )
        otp_fallback = (
            f"📱 <b>{html.escape(phone)}</b> — latest OTP:\n\n"
            f"<code>{html.escape(text)}</code>\n\n"
            f"🕐 <code>{date_str}</code>"
        )
        await _rich_edit_by_id(
            ctx, otp_chat_id, msg_id,
            otp_rich, None, otp_fallback
        )
    except Exception as e:
        await _rich_edit_by_id(
            ctx, otp_chat_id, msg_id,
            f"<h2>❌ ERROR</h2><p><code>{html.escape(str(e))}</code></p>",
            None,
            f"❌ Error: <code>{html.escape(str(e))}</code>"
        )


# ── /cancel ────────────────────────────────────────────────────────────────
async def cancel_flow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    data = tmp.pop(uid, None)
    if data and data.get("client"):
        try:
            from pyrogram import Client as PyroClient
            if isinstance(data["client"], PyroClient):
                # /host uses connect(), not start(); disconnect is the matching cleanup.
                await data["client"].disconnect()
            else:
                await data["client"].disconnect()
        except Exception:
            pass
    rich = (
        "<h1>🚫 OPERATION CANCELLED</h1>"
        "<p>Your current process has been safely stopped.</p>"
        "<table bordered striped>"
        "<tr><th>STATUS</th><th>DETAILS</th></tr>"
        "<tr><td><b>Result</b></td><td>Cancelled</td></tr>"
        "<tr><td><b>Session</b></td><td>Active operation disconnected, if one was running</td></tr>"
        "<tr><td><b>Next step</b></td><td>Choose an option from the main menu to continue</td></tr>"
        "</table>"
        "<footer>You are back at the main menu. No further action is running.</footer>"
    )
    if update.callback_query:
        await update.callback_query.answer("Cancelled.")
        await send_rich_message(update, ctx, rich, main_menu_kb())
    else:
        await send_rich_message(update, ctx, rich, main_menu_kb())
    return ConversationHandler.END


# ── Button handler ────────────────────────────────────────────────────────────────
async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    uid  = q.from_user.id
    await remember_user(q.from_user)

    # ── Stop Progress button ───────────────────────────────────────────
    if data.startswith("stop_progress:"):
        await q.answer()
        try:
            parts = data.split(":")
            task_uid = int(parts[1])
            task_msg_id = int(parts[2])
        except (IndexError, ValueError):
            return

        # Security: only the owner of the task can stop it
        if uid != task_uid:
            await q.answer("🚫 Not your operation.", show_alert=True)
            return

        cancel_event = _get_cancel_event(task_uid, task_msg_id)
        if cancel_event:
            cancel_event.set()

        task = _active_tasks.get(_task_key(task_uid, task_msg_id))
        if task and not task.done():
            task.cancel()

        await q.answer("⏹ Stop signal sent — finishing current step…")
        return
    # ────────────────────────────────────────────────────────────────────

    public = {"back", "help", "add_account", "cancel_flow", "developer"}
    if data not in public and not await has_access(uid):
        await q.answer()
        await send_access_denied(update, ctx, edit=True)
        return
    await q.answer()

    if data == "back":
        await send_rich_message(
            update, ctx,
            "<h1>🤖 MASS REPORT BOT</h1><p><i>Your account management dashboard</i></p>"
            "<hr/><p>Select an option from the menu below.</p>",
            main_menu_kb(), edit=True
        )
        return ConversationHandler.END

    if data.startswith("status_view:") or data == "status_summary":
        message = q.message
        if not message:
            await q.answer("This message is no longer available.", show_alert=True)
            return
        cache_key = f"status:{message.chat_id}:{message.message_id}"
        cached = ctx.application.bot_data.get(cache_key)
        if not cached or cached.get("owner_id") != uid:
            await q.answer("Status details expired. Open Status again.", show_alert=True)
            return
        if data == "status_summary":
            try:
                await edit_status_summary(ctx, message.chat_id, message.message_id, cached["counts"])
            except Exception as exc:
                logging.warning("Could not restore status summary: %s", exc)
                await q.answer("Could not restore status summary.", show_alert=True)
            return
        category = data.split(":", 1)[1]
        titles = {
            "active": "🟢 ACTIVE ACCOUNTS",
            "expired": "🔴 EXPIRED ACCOUNTS",
            "total": "📦 ALL SAVED ACCOUNTS",
        }
        if category not in titles:
            return
        ids = cached.get(category, [])
        body = "".join(
            f"<p><b>{index}. <code>{html.escape(str(phone))}</code></b></p>"
            for index, phone in enumerate(ids, 1)
        ) if ids else "<p>No accounts found in this category.</p>"
        await send_rich_message(
            update, ctx, f"<h1>{titles[category]}</h1><hr/>{body}",
            InlineKeyboardMarkup([[btn("🔙 Back", "status_summary")]]), edit=True
        )
        return

    if data == "status":
        await status_cmd(update, ctx)
        return

    if data == "refresh_status":
        await refresh_cmd(update, ctx)
        return

    if data.startswith("refresh_view:") or data == "refresh_summary":
        message = q.message
        if not message:
            await q.answer("This message is no longer available.", show_alert=True)
            return
        cache_key = f"refresh:{message.chat_id}:{message.message_id}"
        cached = ctx.application.bot_data.get(cache_key)
        if not cached or cached.get("owner_id") != uid:
            await q.answer("Refresh details expired. Please refresh sessions again.", show_alert=True)
            return
        if data == "refresh_summary":
            try:
                await edit_refresh_summary(ctx, message.chat_id, message.message_id, cached["counts"])
            except Exception as exc:
                logging.warning("Could not restore refresh summary: %s", exc)
                await q.answer("Could not restore summary. Please try again.", show_alert=True)
            return
        category = data.split(":", 1)[1]
        labels = {
            "active": "🟢 ACTIVE SESSIONS",
            "expired": "🔴 EXPIRED SESSIONS",
            "errors": "⚠️ ERROR SESSIONS",
            "checked": "🎁 CHECKED SESSIONS",
        }
        if category not in labels:
            return
        ids = cached.get(category, [])
        body = "".join(
            f"<p><b>{index}. <code>{html.escape(str(phone))}</code></b></p>"
            for index, phone in enumerate(ids, 1)
        ) if ids else "<p>No IDs found in this category.</p>"
        await send_rich_message(
            update, ctx, f"<h1>{labels[category]}</h1><hr/>{body}",
            InlineKeyboardMarkup([[btn("🔙 Back", "refresh_summary")]]), edit=True
        )
        return

    if data.startswith("join_view:") or data == "join_summary":
        message = q.message
        if not message:
            await q.answer("This message is no longer available.", show_alert=True)
            return
        cache_key = f"join:{message.chat_id}:{message.message_id}"
        cached = ctx.application.bot_data.get(cache_key)
        if not cached or cached.get("owner_id") != uid:
            await q.answer("Join details expired. Run /join again.", show_alert=True)
            return
        if data == "join_summary":
            try:
                await edit_join_summary(ctx, message.chat_id, message.message_id,
                                        cached["counts"], cached["total"], cached["target"])
            except Exception as exc:
                logging.warning("Could not restore join summary: %s", exc)
                await q.answer("Could not restore summary.", show_alert=True)
            return
        category = data.split(":", 1)[1]
        titles = {
            "joined":  "✅ JOINED ACCOUNTS",
            "already": "🔄 ALREADY MEMBER",
            "failed":  "❌ FAILED ACCOUNTS",
            "total":   "👥 ALL ACCOUNTS",
        }
        if category not in titles:
            return
        ids = cached.get("total_list" if category == "total" else category, [])
        body = "".join(
            f"<p><b>{i}. <code>{html.escape(str(p))}</code></b></p>"
            for i, p in enumerate(ids, 1)
        ) if ids else "<p>No accounts in this category.</p>"
        await send_rich_message(
            update, ctx, f"<h1>{titles[category]}</h1><hr/>{body}",
            InlineKeyboardMarkup([[btn("🔙 Back", "join_summary")]]), edit=True
        )
        return

    if data.startswith("report_view:") or data == "report_summary":
        message = q.message
        if not message:
            await q.answer("This message is no longer available.", show_alert=True)
            return
        cache_key = f"report:{message.chat_id}:{message.message_id}"
        cached = ctx.application.bot_data.get(cache_key)
        if not cached or cached.get("owner_id") != uid:
            await q.answer("Report details expired. Run report again.", show_alert=True)
            return
        if data == "report_summary":
            try:
                await edit_report_summary(ctx, message.chat_id, message.message_id,
                                          cached["counts"], cached["target"],
                                          cached["total_ids"], cached["count"])
            except Exception as exc:
                logging.warning("Could not restore report summary: %s", exc)
                await q.answer("Could not restore summary.", show_alert=True)
            return
        category = data.split(":", 1)[1]
        titles = {
            "success": "✅ SUCCESS ACCOUNTS",
            "failed":  "❌ FAILED ACCOUNTS",
            "expired": "🔴 EXPIRED ACCOUNTS",
            "total":   "👥 ALL SESSION ACCOUNTS",
        }
        if category not in titles:
            return
        ids = cached.get("total_list" if category == "total" else category, [])
        body = "".join(
            f"<p><b>{i}. <code>{html.escape(str(p))}</code></b></p>"
            for i, p in enumerate(ids, 1)
        ) if ids else "<p>No accounts in this category.</p>"
        await send_rich_message(
            update, ctx, f"<h1>{titles[category]}</h1><hr/>{body}",
            InlineKeyboardMarkup([[btn("🔙 Back", "report_summary")]]), edit=True
        )
        return

    if data.startswith("leave_view:") or data == "leave_summary":
        message = q.message
        if not message:
            await q.answer("This message is no longer available.", show_alert=True)
            return
        cache_key = f"leave:{message.chat_id}:{message.message_id}"
        cached = ctx.application.bot_data.get(cache_key)
        if not cached or cached.get("owner_id") != uid:
            await q.answer("Leave details expired. Run /leave again.", show_alert=True)
            return
        if data == "leave_summary":
            try:
                await edit_leave_summary(ctx, message.chat_id, message.message_id,
                                         cached["counts"], cached["total"], cached["target"])
            except Exception as exc:
                logging.warning("Could not restore leave summary: %s", exc)
                await q.answer("Could not restore summary.", show_alert=True)
            return
        category = data.split(":", 1)[1]
        titles = {
            "leaved":    "🟢 LEAVED ACCOUNTS",
            "no_member": "🟡 NOT MEMBER ACCOUNTS",
            "failed":    "🔴 FAILED ACCOUNTS",
            "total":     "📦 ALL ACCOUNTS",
        }
        if category not in titles:
            return
        ids = cached.get("total_list" if category == "total" else category, [])
        body = "".join(
            f"<p><b>{i}. <code>{html.escape(str(p))}</code></b></p>"
            for i, p in enumerate(ids, 1)
        ) if ids else "<p>No accounts in this category.</p>"
        await send_rich_message(
            update, ctx, f"<h1>{titles[category]}</h1><hr/>{body}",
            InlineKeyboardMarkup([[btn("🔙 Back", "leave_summary")]]), edit=True
        )
        return

    if data == "expired_ids":
        records = await get_expired_sessions()
        if not records:
            body = "🔴 <b>Expired Accounts</b>\n\nNone found."
        else:
            lines = []
            for i, item in enumerate(records, 1):
                phone   = html.escape(str(item.get("phone", "?")))
                checked = item.get("status_checked_at")
                when    = checked.strftime("%Y-%m-%d %H:%M UTC") if checked else "Never"
                lines.append(f"<b>{i}. {phone}</b>\n   🕒 {html.escape(when)}")
            body = "🔴 <b>Expired Accounts</b>\n\n" + "\n\n".join(lines)
        rich_body = (
            "<h1>🔴 EXPIRED SESSIONS</h1><p><i>Accounts requiring attention</i></p><hr/>"
            + body.replace("\n", "<br/>")
        )
        await send_rich_message(update, ctx, rich_body, InlineKeyboardMarkup([
            [btn("🔄 Refresh", "refresh_status")],
            [btn("🔙 Back", "status")],
        ]), edit=True)
        return

    if data == "help":
        await help_cmd(update, ctx)
        return

    if data == "developer":
        developer = (OWNER_USERNAME or "").strip().lstrip("@")
        if developer and re.fullmatch(r"[A-Za-z0-9_]{5,32}", developer):
            await q.answer("Opening developer profile…")
            await send_rich_message(
                update, ctx,
                "<h2>◈ DEVELOPER</h2>"
                f"<p>Contact the developer directly on Telegram:</p>"
                f"<p><a href=\"https://t.me/{html.escape(developer)}\">@{html.escape(developer)}</a></p>",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "◈ 𝗗𝗘𝗙𝗛𝗜𝗓𝗖𝗘𝗥 ◈",
                        url=f"https://t.me/{developer}",
                        style="danger",
                        icon_custom_emoji_id=custom_emoji_id_for("🕵🏻"),
                    )
                ]])
            )
        else:
            await q.answer("Developer contact is not configured yet.", show_alert=True)
        return

    if data == "sessions":
        sessions = await get_all_sessions()
        rows = "".join(
            f"<tr><td>{i}</td><td><code>{html.escape(str(item.get('phone','?')))}</code></td></tr>"
            for i, item in enumerate(sessions, 1)
        )
        rich = (
            "<h1>📋 ALL SESSIONS</h1><p><i>Saved account overview</i></p><hr/>"
            + ("<table bordered striped><tr><th>#</th><th>PHONE</th></tr>" + rows + "</table>"
               if rows else "<p>No saved sessions were found.</p>")
        )
        await send_rich_message(
            update, ctx, rich,
            InlineKeyboardMarkup([[btn("🔙 Back", "back")]]), edit=True
        )
        return

    if data == "add_account":
        await send_rich_message(
            update, ctx,
            "<h2>➕ ADD ACCOUNT</h2>"
            "<p><i>Connect your Telegram account via OTP</i></p><hr/>"
            "<p>Enter your phone number with country code:</p>"
            "<p><code>+919876543210</code></p>"
            "<footer>An OTP will be sent to your Telegram app.</footer>",
            cancel_kb(), edit=True
        )
        return ADD_PHONE

    if data == "start_report":
        await send_rich_message(
            update, ctx,
            "<h2>🚨 MASS REPORT</h2>"
            "<p><i>Send the target to begin the report flow</i></p><hr/>"
            "<h3>🎯 Supported Target Formats</h3>"
            "<table bordered striped>"
            "<tr><th>TYPE</th><th>FORMAT</th></tr>"
            "<tr><td>Username</td><td><code>@username</code></td></tr>"
            "<tr><td>Profile link</td><td><code>https://t.me/username</code></td></tr>"
            "<tr><td>Message link</td><td><code>https://t.me/username/123</code></td></tr>"
            "<tr><td>Private msg</td><td><code>https://t.me/c/CHANNELID/123</code></td></tr>"
            "<tr><td>Numeric ID</td><td>Telegram user ID</td></tr>"
            "</table>",
            cancel_kb(), edit=True
        )
        return REPORT_TARGET

    if data.startswith("cat_"):
        cat = data[4:]
        if uid not in tmp:
            tmp[uid] = {}
        tmp[uid]["cat"] = cat
        subs = REASONS[cat][1]
        if not subs:
            tmp[uid]["reason"] = cat
            await send_rich_message(
                update, ctx,
                "<h2>✍️ REPORT MESSAGE</h2>"
                "<p>Type the reason text for your report:</p>"
                "<footer>Maximum 512 characters.</footer>",
                cancel_kb(), edit=True
            )
            return REPORT_MESSAGE
        kb = [[btn(label, f"sub_{key}")] for key, label in subs]
        kb.append([btn("🔙 Back", "start_report")])
        await send_rich_message(
            update, ctx,
            f"<h2>📂 {html.escape(REASONS[cat][0])}</h2>"
            "<p><i>Select a sub-category for your report:</i></p>",
            InlineKeyboardMarkup(kb), edit=True
        )
        return REPORT_REASON_SUB

    if data.startswith("sub_"):
        if uid not in tmp:
            tmp[uid] = {}
        tmp[uid]["reason"] = data[4:]
        await send_rich_message(
            update, ctx,
            "<h2>✍️ REPORT MESSAGE</h2>"
            "<p>Type the reason text for your report:</p>"
            "<footer>Maximum 512 characters.</footer>",
            cancel_kb(), edit=True
        )
        return REPORT_MESSAGE

    if data == "confirm_report":
        sessions = await get_all_sessions()
        if not sessions:
            await send_rich_message(
                update, ctx,
                "<h2>❌ NO ACTIVE SESSIONS</h2>"
                "<p>Add accounts first before reporting.</p>",
                main_menu_kb(), edit=True
            )
            return ConversationHandler.END

        t              = tmp.get(uid, {})
        target         = t.get("target", "")
        reason         = t.get("reason", "")
        count          = t.get("count", 1)
        report_message = t.get("report_message", "")
        total_ids      = len(sessions)
        total_expected = total_ids * count

        # Dismiss the confirm message
        await send_rich_message(
            update, ctx,
            "<h2>⏳ REPORTING IN PROGRESS</h2>"
            "<p><i>Mass report operation has started…</i></p>",
            edit=True
        )

        report_chat_id = update.effective_chat.id
        report_initial_rich = (
            "<h2>📡 REPORTING IN PROGRESS</h2>"
            f"<p><i>Target: <code>{html.escape(target)}</code></i></p><hr/>"
            "<table bordered striped>"
            "<tr><th>RESULT</th><th>COUNT</th></tr>"
            "<tr><td>✅ Success</td><td><b>0</b></td></tr>"
            "<tr><td>❌ Failed</td><td><b>0</b></td></tr>"
            "<tr><td>🔴 Expired</td><td><b>0</b></td></tr>"
            f"<tr><td>📊 Done</td><td><b>0 / {total_expected}</b></td></tr>"
            "</table>"
        )
        sent_msg_id = await _rich_send_new(
            ctx, report_chat_id, report_initial_rich,
            stop_progress_kb(uid, 0),  # temp
            f"📡 Reporting in progress... 0 / {total_expected}"
        )
        if sent_msg_id:
            api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
            payload = {"chat_id": report_chat_id, "message_id": sent_msg_id,
                       "reply_markup": stop_progress_kb(uid, sent_msg_id).to_dict()}
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    await session.post(api_base + "editMessageReplyMarkup", json=payload)
            except Exception:
                pass

        cancel_event = asyncio.Event()
        task = asyncio.create_task(
            _run_report_task(
                ctx, report_chat_id, uid, sent_msg_id,
                sessions, target, reason, count, report_message,
                cancel_event
            )
        )
        _register_task(uid, sent_msg_id, task, cancel_event)

        tmp.pop(uid, None)
        return ConversationHandler.END

    return None


# ── Account add flow ─────────────────────────────────────────────────────────────
async def recv_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Request a Telegram login code using Telethon's supported auth flow."""
    uid = update.effective_user.id
    phone = (update.message.text or "").strip()

    if update.effective_chat.type != "private":
        await update.message.reply_text("Please continue the /host login in a private chat.")
        return ConversationHandler.END

    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        await send_rich_message(
            update, ctx,
            "<h2>❌ INVALID PHONE NUMBER</h2>"
            "<p>Use international format, for example <code>+919876543210</code>.</p>",
            cancel_kb()
        )
        return ADD_PHONE

    # Close any stale login attempt belonging to this user.
    previous = tmp.pop(uid, None)
    if previous:
        await _close_host_client(previous.get("client"))

    client = None
    try:
        records = await get_all_status_sessions()
        normalized = phone.lstrip("+")
        if any(str(item.get("phone", "")).strip().lstrip("+") == normalized for item in records):
            await send_rich_message(
                update, ctx,
                "<h2>⚠️ ACCOUNT ALREADY ADDED</h2>"
                f"<p><code>{html.escape(phone)}</code> already exists in saved sessions.</p>"
                "<footer>The existing session was not changed.</footer>",
                main_menu_kb()
            )
            return ConversationHandler.END

        from telethon import TelegramClient
        from telethon.sessions import StringSession

        client = TelegramClient(
            StringSession(), API_ID, API_HASH,
            connection_retries=1,
            retry_delay=1,
            timeout=20,
        )
        await client.connect()

        sent_code = await client.send_code_request(phone)
        phone_code_hash = getattr(sent_code, "phone_code_hash", None)
        if not phone_code_hash:
            raise RuntimeError("Telegram did not return a login-code hash")

        tmp[uid] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": phone_code_hash,
            "client_type": "telethon",
            "host_stage": "otp",
        }

        sent_type = type(getattr(sent_code, "type", None)).__name__
        logging.info("Telegram login code request accepted for %s (delivery=%s)",
                     phone, sent_type or "unknown")

        await send_rich_message(
            update, ctx,
            "<h1>🔐 TELEGRAM CODE SENT</h1>"
            "<p>Enter the login code Telegram sent to your account.</p>"
            "<footer>Send the code only in this private bot chat. Use /cancel to close the login.</footer>",
            cancel_kb()
        )
        return ADD_CODE

    except Exception as exc:
        logging.exception("Telethon /host phone step failed for user %s", uid)
        await _close_host_client(client)
        tmp.pop(uid, None)
        await send_rich_message(
            update, ctx,
            "<h2>❌ LOGIN REQUEST FAILED</h2>"
            f"<p><code>{html.escape(str(exc))}</code></p>"
            "<footer>Check API_ID/API_HASH in hosting Secrets and try again later.</footer>",
            main_menu_kb()
        )
        return ConversationHandler.END


async def _close_host_client(client):
    """Best-effort cleanup for an in-progress Telethon login."""
    if client is None:
        return
    try:
        await client.disconnect()
    except Exception:
        logging.exception("Could not close Telegram /host client")


async def _complete_pyrogram_host(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                                  uid: int, data: dict):
    """Persist the authenticated Telethon StringSession using the shared session manager."""
    client = data.get("client")
    try:
        me = await client.get_me()
        if not me:
            raise RuntimeError("Telegram did not return an authenticated account")

        session_string = client.session.save()
        if not session_string:
            raise RuntimeError("Telegram returned an empty session string")

        phone = str(getattr(me, "phone", None) or data.get("phone") or "").strip()
        if phone and not phone.startswith("+"):
            phone = "+" + phone
        if not phone:
            raise RuntimeError("Could not read the account phone number")

        await save_session(uid, phone, session_string, client_type="telethon")
        tmp.pop(uid, None)
        await send_rich_message(
            update, ctx,
            "<h1>✅ PYROGRAM SESSION CREATED</h1>"
            f"<p>Account <code>{html.escape(phone)}</code> was authenticated and saved.</p>"
            "<footer>The reusable Pyrogram session is stored. You can now manage this account.</footer>",
            main_menu_kb()
        )
    except Exception as exc:
        logging.exception("Could not complete Telegram /host for user %s", uid)
        tmp.pop(uid, None)
        await send_rich_message(
            update, ctx,
            "<h2>❌ SESSION CREATION FAILED</h2>"
            f"<p><code>{html.escape(str(exc))}</code></p>"
            "<footer>No success was reported. Start again with /host.</footer>",
            main_menu_kb()
        )
    finally:
        await _close_host_client(client)


async def recv_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    code = re.sub(r"\s+", "", update.message.text or "")
    data = tmp.get(uid) or {}
    client = data.get("client")

    if not client or data.get("host_stage") != "otp":
        await send_rich_message(
            update, ctx,
            "<h2>⚠️ NO ACTIVE LOGIN</h2><p>Start a new login with /host.</p>",
            main_menu_kb()
        )
        return ConversationHandler.END

    if not re.fullmatch(r"\d{4,8}", code):
        await send_rich_message(
            update, ctx,
            "<h2>❌ INVALID CODE FORMAT</h2><p>Enter the numeric code Telegram sent.</p>",
            cancel_kb()
        )
        return ADD_CODE

    try:
        await client.sign_in(
            phone=data["phone"],
            code=code,
            phone_code_hash=data["phone_code_hash"],
        )
    except Exception as exc:
        from telethon.errors import SessionPasswordNeededError, PhoneCodeExpiredError, PhoneCodeInvalidError

        error_name = type(exc).__name__
        if isinstance(exc, SessionPasswordNeededError):
            data["host_stage"] = "2fa"
            await send_rich_message(
                update, ctx,
                "<h1>🔒 TWO-STEP VERIFICATION</h1>"
                "<p>Your Telegram account has 2-Step Verification enabled.</p>"
                "<p>Enter your Telegram 2FA password in this private chat.</p>"
                "<footer>Your password is used only to complete this login and is never saved.</footer>",
                cancel_kb()
            )
            return ADD_2FA

        logging.warning("Telegram /host OTP verification failed (%s): %s", error_name, exc)
        if isinstance(exc, (PhoneCodeExpiredError, PhoneCodeInvalidError)):
            await _close_host_client(client)
            tmp.pop(uid, None)
            await send_rich_message(
                update, ctx,
                "<h2>❌ LOGIN CODE REJECTED</h2>"
                "<p>The code is invalid or expired. Start again with /host to request a fresh code.</p>",
                main_menu_kb()
            )
            return ConversationHandler.END

        await send_rich_message(
            update, ctx,
            "<h2>❌ OTP VERIFICATION FAILED</h2>"
            f"<p><code>{html.escape(str(exc))}</code></p>"
            "<footer>You can retry the code or use /cancel to stop.</footer>",
            cancel_kb()
        )
        return ADD_CODE

    await _complete_pyrogram_host(update, ctx, uid, data)
    return ConversationHandler.END


async def recv_2fa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    password = update.message.text or ""
    data = tmp.get(uid) or {}
    client = data.get("client")

    # Remove the password message from the chat as soon as it has been read.
    try:
        await update.message.delete()
    except Exception:
        logging.debug("Could not delete 2FA password message for user %s", uid)

    if not client or data.get("host_stage") != "2fa":
        await send_rich_message(
            update, ctx,
            "<h2>⚠️ NO ACTIVE 2FA LOGIN</h2><p>Start a new login with /host.</p>",
            main_menu_kb()
        )
        return ConversationHandler.END

    if not password:
        await send_rich_message(
            update, ctx,
            "<h2>❌ EMPTY PASSWORD</h2><p>Enter your Telegram 2FA password.</p>",
            cancel_kb()
        )
        return ADD_2FA

    try:
        await client.sign_in(password=password)
    except Exception as exc:
        logging.warning("Telegram /host 2FA verification failed (%s)", type(exc).__name__)
        await send_rich_message(
            update, ctx,
            "<h2>❌ 2FA VERIFICATION FAILED</h2>"
            "<p>The password was not accepted. Try again or use /cancel.</p>"
            "<footer>Your password is not stored by the bot.</footer>",
            cancel_kb()
        )
        return ADD_2FA

    await _complete_pyrogram_host(update, ctx, uid, data)
    return ConversationHandler.END

async def recv_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    target = update.message.text.strip()
    tmp[uid] = {"target": target}
    kb = [[btn(v[0], f"cat_{k}")] for k, v in REASONS.items()]
    await send_rich_message(
        update, ctx,
        "<h2>📂 SELECT REPORT CATEGORY</h2>"
        f"<p>🎯 Target: <code>{html.escape(target)}</code></p><hr/>"
        "<p>Select the category that best describes the content to report:</p>",
        InlineKeyboardMarkup(kb)
    )
    return REPORT_REASON_CAT

async def recv_report_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = update.message.text.strip()
    if not text or len(text) > 512:
        await send_rich_message(
            update, ctx,
            "<h2>❌ INVALID MESSAGE</h2>"
            "<p>Report message must be between 1 and 512 characters.</p>"
            "<p>Please try again:</p>"
        )
        return REPORT_MESSAGE
    tmp[uid]["report_message"] = text
    await send_rich_message(
        update, ctx,
        "<h2>🔢 REPORTS PER ID</h2>"
        "<p>How many reports should be sent per account?</p>"
        "<p>Enter a number (e.g. <code>20</code>):</p>",
        cancel_kb()
    )
    return REPORT_COUNT

async def recv_count(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        count = int(update.message.text.strip())
        if count < 1:
            raise ValueError
    except ValueError:
        await send_rich_message(
            update, ctx,
            "<h2>❌ INVALID NUMBER</h2><p>Enter a valid positive integer:</p>"
        )
        return REPORT_COUNT

    tmp[uid]["count"] = count
    sessions = await get_all_sessions()
    t = tmp[uid]
    await send_rich_message(
        update, ctx,
        "<h1>⚠️ CONFIRM REPORT</h1>"
        "<p><i>Review the details before proceeding</i></p><hr/>"
        "<table bordered striped>"
        f"<tr><td>🎯 Target</td><td><code>{html.escape(t['target'])}</code></td></tr>"
        f"<tr><td>📋 Reason</td><td><code>{t['reason']}</code></td></tr>"
        f"<tr><td>✍️ Message</td><td><code>{html.escape(t['report_message'][:60])}{'...' if len(t['report_message']) > 60 else ''}</code></td></tr>"
        f"<tr><td>🔢 Per ID</td><td><b>{count}</b></td></tr>"
        f"<tr><td>👥 Active IDs</td><td><b>{len(sessions)}</b></td></tr>"
        f"<tr><td>📊 Total Reports</td><td><b>{len(sessions) * count}</b></td></tr>"
        "</table>"
        "<footer>⚠️ This action cannot be undone once started.</footer>",
        InlineKeyboardMarkup([
            [btn("✅ Start Reporting", "confirm_report"),
             btn("❌ Cancel", "back")]
        ])
    )
    return CONFIRM_REPORT


# ── Main ────────────────────────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("host", host_cmd),
            CallbackQueryHandler(button_handler, pattern="^(add_account|start_report)$"),
        ],
        states={
            ADD_PHONE:         [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_phone)],
            ADD_CODE:          [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_code)],
            ADD_2FA:           [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_2fa)],
            REPORT_TARGET:     [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_target)],
            REPORT_REASON_CAT: [CallbackQueryHandler(button_handler, pattern="^cat_")],
            REPORT_REASON_SUB: [CallbackQueryHandler(button_handler, pattern="^sub_")],
            REPORT_MESSAGE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_report_message)],
            REPORT_COUNT:      [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_count)],
            CONFIRM_REPORT:    [CallbackQueryHandler(button_handler, pattern="^confirm_report$")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_flow),
            CallbackQueryHandler(cancel_flow, pattern="^cancel_flow$"),
            CallbackQueryHandler(button_handler, pattern="^back$"),
        ],
        per_message=False,
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start",          start))
    app.add_handler(CommandHandler("status",         status_cmd))
    app.add_handler(CommandHandler("refresh",        refresh_cmd))
    app.add_handler(CommandHandler("session",        session_cmd))
    app.add_handler(CommandHandler("unhost",         unhost_cmd))
    app.add_handler(CommandHandler("removeexpiredid",remove_expired_id_cmd))
    app.add_handler(CommandHandler("help",           help_cmd))
    app.add_handler(CommandHandler("join",           join_cmd))
    app.add_handler(CommandHandler("leave",          leave_cmd))
    app.add_handler(CommandHandler("sudo",           sudo_cmd))
    app.add_handler(CommandHandler("removesudo",     removesudo_cmd))
    app.add_handler(CommandHandler("sudoinfo",       sudoinfo_cmd))
    app.add_handler(CommandHandler("otp",            otp_cmd))
    app.add_handler(CommandHandler("addsession",     addsession_cmd))
    app.add_handler(CommandHandler("stop",           stop_cmd))
    app.add_handler(conv)
    app.add_handler(CommandHandler("cancel",         cancel_flow))
    app.add_handler(CallbackQueryHandler(button_handler))

    async def on_startup(app):
        await fetch_proxies()
        asyncio.create_task(rotate_forever())
        await app.bot.set_my_commands([
            BotCommand("start",           "Main menu"),
            BotCommand("host",            "Add a Telegram account"),
            BotCommand("addsession",      "Add account via string session"),
            BotCommand("status",          "Session statistics"),
            BotCommand("refresh",         "Verify all sessions"),
            BotCommand("session",         "List your sessions"),
            BotCommand("unhost",          "Remove a session by phone"),
            BotCommand("removeexpiredid", "Permanently remove an expired ID"),
            BotCommand("join",            "Join with all accounts"),
            BotCommand("leave",           "Leave a chat with all accounts"),
            BotCommand("stop",            "Stop your running background operation"),
            BotCommand("sudoinfo",        "Access directory"),
            BotCommand("cancel",          "Cancel current flow"),
            BotCommand("help",            "All commands"),
        ])

    app.post_init = on_startup
    app.run_polling()


if __name__ == "__main__":
    main()
