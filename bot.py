import asyncio
import aiohttp
import time
import html
import logging
import random
import re
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
    delete_expired_session
)
from access_manager import (
    is_owner, is_sudo, remember_user, add_sudo, remove_sudo, list_access_users
)
from reporter import mass_report, mass_join, mass_leave
from proxy_manager import fetch_proxies, rotate_forever

logging.basicConfig(level=logging.INFO)

# ── Conversation states ──────────────────────────────────────────────────────
(
    ADD_PHONE, ADD_CODE, ADD_2FA,
    REPORT_TARGET, REPORT_REASON_CAT, REPORT_REASON_SUB,
    REPORT_MESSAGE, REPORT_COUNT, CONFIRM_REPORT,
) = range(9)

# ── Report categories ────────────────────────────────────────────────────────
REASONS = {
    "1":  ("😤 I don't like it",        []),
    "2":  ("👶 Child Abuse",            [("2a","Child illegal abuse"),("2b","Child sexual abuse")]),
    "3":  ("🔪 Violence",               [("3a","Insult / false info"),("3b","Graphic content"),
                                         ("3c","Extreme violence"),("3d","Hate speech"),
                                         ("3e","Calling for violence"),("3f","Terrorism")]),
    "4":  ("🚫 Illegal Goods",          [("4a","Weapons"),("4b","Drugs"),("4c","Fake documents"),
                                         ("4d","Counterfeit money"),("4e","Hacking tools"),
                                         ("4f","Counterfeit merch"),("4g","Other")]),
    "5":  ("🔞 Illegal Adult Content",  [("5a","Child abuse"),("5b","Illegal sexual service"),
                                         ("5c","Animal abuse"),("5d","Non-consensual imagery"),
                                         ("5e","Pornography"),("5f","Other")]),
    "6":  ("🧾 Personal Data",          [("6a","Private images"),("6b","Private number"),
                                         ("6c","Address"),("6d","Credentials"),("6e","Other")]),
    "7":  ("💸 Scam or Fraud",          [("7a","Impersonation"),("7b","Financial scams"),
                                         ("7c","Malware/phishing"),("7d","Fraudulent seller")]),
    "8":  ("©️ Copyright",              []),
    "9":  ("🔎 Other",                  []),
    "10": ("⚠️ Not illegal but remove", []),
}

tmp = {}


# ── Access helpers ───────────────────────────────────────────────────────────
async def has_access(user_id: int) -> bool:
    return is_owner(user_id) or await is_sudo(user_id)

# Use the configured owner username, falling back to the optional default contact.
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

# ── Keyboards ────────────────────────────────────────────────────────────────
_BTN_STYLES = ["primary", "success", "danger"]

# Unicode small-cap lettering inspired by the supplied button screenshot.
# Telegram buttons do not support custom font files, so use Unicode glyphs.
_BUTTON_SMALL_CAPS = str.maketrans({
    "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ",
    "f": "ꜰ", "g": "ɢ", "h": "ʜ", "i": "ɪ", "j": "ᴊ",
    "k": "ᴋ", "l": "ʟ", "m": "ᴍ", "n": "ɴ", "o": "ᴏ",
    "p": "ᴘ", "q": "ꞯ", "r": "ʀ", "s": "ꜱ", "t": "ᴛ",
    "u": "ᴜ", "v": "ᴠ", "w": "ᴡ", "x": "x", "y": "ʏ", "z": "ᴢ",
})


# Custom emoji icons supported by Telegram Bot API 9.4 / python-telegram-bot 22.7.
# The UPDATE artwork supplied by the owner is used for every Refresh button.
_BUTTON_CUSTOM_EMOJI_IDS = {
    "🔄": "5375338737028841420",
    "👑": "6321314543922323466",
    "💎": "6264791387032523779",
    "⚡": "5456140674028019486",
    "❌": "6321108381197147408",
    "🚀": "5188481279963715781",
    "🤖": "6321144798224850562",
    "🔑": "5420094143089111506",
    "🟪": "6276238759071586100",
    "✅": "5190782321577441092",
    "📊": "5042290883949495533",
    "🆙": "5375338737028841420",
    "⚠️": "6275791747465352530",
    "🛡️": "5042328396193864923",
    "➕": "6228956533556776442",
    "🚨": "5213285132709929474",
    "🔴": "6321315716448394517",
    "📋": "5431420628978641216",
    "🟢": "6084897373329296142",
    "🔐": "5870972873450984431",
    "🔒": "5429405838345265327",
    "🔙": "5253997076169115797",
    "📦": "5314665894406805639",
    "👤": "5298545073963679624",
    "⚙️": "5841276284155467413",
    "🎯": "5256131095094652290",
    "✍️": "5458382591121964689",
    "👥": "5249053508681883137",
    "🆔": "5841276284155467413",
    "💠": "6325309396083543238",
    "🔢": "5203993413346680064",
    "❔": "6136198451282581752",
    "🗑": "5372825386591732174",
    "ℹ️": "6325852696561583823",
    "🕒": "5985570245950053733",
    "➖": "5346289768672020876",
    "⌨️": "5364335245564862751",
    "📱": "5426991207731437507",
    "⭐": "6280502836962794866",
    "🌟": "6242282747629413332",
    # ── Added emoji IDs ──────────────────────────────────────────────────────
    "⏳": "5386367538735104399",
    "🚫": "6098120323503235997",
    "🕐": "5400258343438655703",
    "😤": "5452056954698475544",
    "👶": "5454407470040491470",
    "🔪": "5258061163203148267",
    "🔞": "5472183128497862606",
    "🧾": "6289752212243483195",
    "💸": "5949785441728205181",
    "©️": "5458496841547015692",
    "📂": "5167958401510016217",
    "📡": "6093607322192455111",
    "🔎": "5348282577662778261",
}


def btn(text: str, data: str) -> InlineKeyboardButton:
    icon_custom_emoji_id = None
    # Use a real custom emoji icon when the leading Unicode emoji has a mapped ID.
    for emoji, custom_id in sorted(
        _BUTTON_CUSTOM_EMOJI_IDS.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if text.startswith(emoji):
            text = text[len(emoji):].lstrip()
            icon_custom_emoji_id = custom_id
            break

    styled_text = text.translate(_BUTTON_SMALL_CAPS)
    style = random.choice(_BTN_STYLES)
    return InlineKeyboardButton(
        styled_text,
        callback_data=data,
        style=style,
        icon_custom_emoji_id=icon_custom_emoji_id,
    )


def main_menu_kb() -> InlineKeyboardMarkup:
    developer_button = InlineKeyboardButton(
        "◈ 𝗗𝗘𝗩𝗘𝗟𝗢𝗣𝗘𝗥 ◈",
        url="https://t.me/SAREEF_FUCKER",
        style="danger",
        icon_custom_emoji_id="6091123254842366873",
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


# Telegram Custom Emoji IDs collected from the user's Premium emoji pack.
# Unicode alternatives remain inside each tag as a compatibility fallback.
CUSTOM_EMOJI_IDS = {
    "👑": "6321314543922323466",
    "💎": "6264791387032523779",
    "⚡": "5456140674028019486",
    "❌": "6321108381197147408",
    "🚀": "5188481279963715781",
    "🤖": "6321144798224850562",
    "🔤": "5870972873450984431",
    "🔑": "5420094143089111506",
    "🟪": "6276238759071586100",
    "✅": "5190782321577441092",
    "📊": "5042290883949495533",
    "🆙": "5375338737028841420",
    "⚠️": "6275791747465352530",
    "🛡️": "5042328396193864923",
    "➕": "6228956533556776442",
    "🚨": "5213285132709929474",
    "🔴": "6321315716448394517",
    "📋": "5431420628978641216",
    "🟢": "6084897373329296142",
    "🔐": "5870972873450984431",
    "🔒": "5429405838345265327",
    "🔙": "5253997076169115797",
    "📦": "5314665894406805639",
    "👤": "5298545073963679624",
    "⚙️": "5841276284155467413",
    "🎯": "5256131095094652290",
    "✍️": "5458382591121964689",
    "👥": "5249053508681883137",
    "🆔": "5841276284155467413",
    "💠": "6325309396083543238",
    "🔢": "5203993413346680064",
    "❔": "6136198451282581752",
    "🗑️": "5372825386591732174",
    "🗑": "5372825386591732174",
    "ℹ️": "6325852696561583823",
    "🕒": "5985570245950053733",
    "➖": "5346289768672020876",
    "⌨️": "5364335245564862751",
    "⭐": "6280502836962794866",
    "🌟": "6242282747629413332",
    "📱": "5426991207731437507",
    # ── Added emoji IDs ────────────────────────────────────────────────────
    "⏳": "5386367538735104399",
    "🚫": "6098120323503235997",
    "🕐": "5400258343438655703",
    "😤": "5452056954698475544",
    "👶": "5454407470040491470",
    "🔪": "5258061163203148267",
    "🔞": "5472183128497862606",
    "🧾": "6289752212243483195",
    "💸": "5949785441728205181",
    "©️": "5458496841547015692",
    "📂": "5167958401510016217",
    "📡": "6093607322192455111",
    "🔎": "5348282577662778261",
}

def apply_custom_emojis(rich_html: str) -> str:
    """Replace collected Unicode emoji with Telegram custom-emoji HTML tags."""
    for emoji, custom_id in sorted(CUSTOM_EMOJI_IDS.items(), key=lambda item: len(item[0]), reverse=True):
        rich_html = rich_html.replace(
            emoji,
            f'<tg-emoji emoji-id="{custom_id}">{emoji}</tg-emoji>'
        )
    return rich_html

# ── Premium message presentation (UI only) ───────────────────────────────────
async def send_rich_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                            rich_html: str, reply_markup=None, edit: bool = False):
    """Render safe informational UI with Telegram Rich Messages; keep a HTML fallback."""
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
        await update.callback_query.edit_message_text(
            plain_html, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )
    else:
        await update.effective_message.reply_text(
            plain_html, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )


# ── Rich edit helper (for non-callback message edits by message_id) ──────────
async def _rich_edit_by_id(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int,
                           message_id: int, rich_html: str,
                           reply_markup=None, fallback_text: str = None,
                           fallback_markup=None):
    """Edit an existing message by message_id using Rich Message API; HTML fallback."""
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
            await ctx.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=fallback_text,
                parse_mode=ParseMode.HTML,
                reply_markup=fallback_markup,
            )
        except Exception as exc2:
            logging.warning("Fallback edit also failed: %s", exc2)


# ── Rich blocks send helper (returns message_id; HTML fallback) ──────────────
async def _rich_send_blocks_new(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int,
                                blocks: list, fallback_html: str,
                                reply_markup=None, fallback_text: str = None,
                                fallback_markup=None) -> int:
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    payload = {"chat_id": chat_id, "rich_message": {"blocks": blocks}}
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
        msg = await ctx.bot.send_message(
            chat_id=chat_id,
            text=fallback_text or fallback_html,
            parse_mode=ParseMode.HTML,
            reply_markup=fallback_markup or reply_markup,
        )
        return msg.message_id


# ── Rich send helper (returns message_id; HTML fallback) ─────────────────────
async def _rich_send_new(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int,
                         rich_html: str, reply_markup=None,
                         fallback_text: str = None, fallback_markup=None) -> int:
    """Send a new Rich Message and return the message_id; HTML fallback on failure."""
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
        msg = await ctx.bot.send_message(
            chat_id=chat_id,
            text=fallback_text,
            parse_mode=ParseMode.HTML,
            reply_markup=fallback_markup,
        )
        return msg.message_id
    return None


# ── /start ───────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await remember_user(update.effective_user)

    welcome_html = (
        "<h1>⭐ 𝗪𝗘𝗟𝗖𝗢𝗠𝗘 𝗧𝗢 𝗠𝗔𝗦𝗦 𝗥𝗘𝗣𝗢𝗥𝗧 𝗕𝗢𝗧</h1>"
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
        await update.effective_message.reply_text(
            "⭐ <b>WELCOME TO MASS REPORT BOT</b>\n"
            "<i>Your account management command center</i>\n\n"
            "🚀 <b>Get Started</b>\n"
            "Manage your connected accounts and available tools from the menu below.\n\n"
            "<i>Select an option to continue.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )


# ── /help ────────────────────────────────────────────────────────────────────
async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    priv = await has_access(uid)
    owner = is_owner(uid)

    # Telegram Rich Messages render real headings and bordered tables.
    rows_account = (
        "<table bordered striped>"
        "<tr><th>Command</th><th>Description</th></tr>"
        "<tr><td><mark><b>/start</b></mark></td><td>Open the bot menu.</td></tr>"
        "<tr><td><mark><b>/host</b></mark></td><td>Connect your own Telegram account (OTP / optional 2FA).</td></tr>"
        "<tr><td><mark><b>/cancel</b></mark></td><td>Cancel the current setup.</td></tr>"
        "<tr><td><mark><b>/help</b></mark></td><td>Show this command guide.</td></tr>"
        "</table>"
    )

    rich_html = (
        "<h1>🤖 𝗠𝗔𝗦𝗦 𝗥𝗘𝗣𝗢𝗥𝗧 𝗕𝗢𝗧</h1>"
        "<p><i>⭐ COMMAND CENTER  ·  QUICK REFERENCE ⭐</i></p>"
        "<hr/>"
        "<h2>👤 𝗔ᴄᴄᴏᴜɴᴛ &amp; 𝗠ᴇɴᴜ</h2>"
        + rows_account
    )

    if priv:
        rich_html += (
            "<h2>⚙️ 𝗕ᴏᴛ 𝗢ᴘᴇʀᴀᴛɪᴏɴꜱ</h2>"
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
            "<h2>👑 𝗢ᴡɴᴇʀ 𝗖ᴏɴᴛʀᴏʟꜱ</h2>"
            "<table bordered striped>"
            "<tr><th>Command</th><th>Description</th></tr>"
            "<tr><td><mark><b>/sudo USER_ID</b></mark></td><td>Grant Sudo access.</td></tr>"
            "<tr><td><mark><b>/removesudo USER_ID</b></mark></td><td>Revoke Sudo access.</td></tr>"
            "</table>"
        )

    if priv:
        rich_html += (
            "<h2>🔐 𝗔ᴄᴄᴇꜱꜱ 𝗟ᴇᴠᴇʟ</h2>"
            "<p><b>Owner / Sudo</b> · Authorized bot operations.</p>"
        )
    else:
        rich_html += (
            "<h2>🔐 𝗔ᴄᴄᴇꜱꜱ</h2>"
            "<p>Some operations are restricted to Owner / Sudo.</p>"
        )

    rich_html += (
        "<hr/>"
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
            "🤖 <b>𝗠𝗔𝗦𝗦 𝗥𝗘𝗣𝗢𝗥𝗧 𝗕𝗢𝗧</b>\n"
            "<i>⭐ COMMAND CENTER · QUICK REFERENCE ⭐</i>\n\n"
            "<b>👤 𝗔ᴄᴄᴏᴜɴᴛ &amp; 𝗠ᴇɴᴜ</b>\n"
            "<b>/start</b> — Open the bot menu\n"
            "<b>/host</b> — Connect your own account (OTP / optional 2FA)\n"
            "<b>/cancel</b> — Cancel the current setup\n"
            "<b>/help</b> — Show this guide\n"
        )
        if priv:
            fallback += (
                "\n<b>⚙️ 𝗕ᴏᴛ 𝗢ᴘᴇʀᴀᴛɪᴏɴꜱ</b>\n"
                "<b>/status</b> — View session statistics\n"
                "<b>/refresh</b> — Verify saved session status\n"
                "<b>/session</b> — List saved sessions\n"
                "<b>/unhost +919876543210</b> — Remove a hosted session\n"
                "<b>/join @username</b> — Join a chat with authorized accounts\n"
                "<b>/sudoinfo</b> — View Owner and Sudo directory\n"
            )
        if owner:
            fallback += (
                "\n<b>👑 𝗢ᴡɴᴇʀ 𝗖ᴏɴᴛʀᴏʟꜱ</b>\n"
                "<b>/sudo USER_ID</b> — Grant Sudo access\n"
                "<b>/removesudo USER_ID</b> — Revoke Sudo access\n"
            )
        fallback += "\n<b>🔐 𝗔ᴄᴄᴇꜱꜱ</b>\nOwner / Sudo: authorized bot operations."
        if update.callback_query:
            await update.callback_query.edit_message_text(
                fallback, parse_mode=ParseMode.HTML, reply_markup=kb
            )
        else:
            await update.effective_message.reply_text(
                fallback, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb()
            )

# ── /host ────────────────────────────────────────────────────────────────────
async def host_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await remember_user(update.effective_user)
    await send_rich_message(
        update, ctx,
        "<h2>➕ ADD ACCOUNT</h2>"
        "<p><i>Connect your Telegram account via OTP</i></p><hr/>"
        "<p>Enter your phone number with country code:</p>"
        "<p><code>+919876543210</code></p>"
        "<footer>An OTP will be sent to your Telegram app.</footer>",
        cancel_kb()
    )
    return ADD_PHONE


# ── /status ──────────────────────────────────────────────────────────────────

def rich_text_with_custom_emojis(value: str):
    """Build RichText parts so message-body emojis render as Telegram custom emojis."""
    parts = []
    remaining = str(value)
    emoji_items = sorted(CUSTOM_EMOJI_IDS.items(), key=lambda item: len(item[0]), reverse=True)
    while remaining:
        match = None
        match_pos = len(remaining)
        for emoji, custom_id in emoji_items:
            pos = remaining.find(emoji)
            if pos != -1 and pos < match_pos:
                match = (emoji, custom_id)
                match_pos = pos
        if match is None:
            parts.append(remaining)
            break
        emoji, custom_id = match
        if match_pos:
            parts.append(remaining[:match_pos])
        parts.append({
            "type": "custom_emoji",
            "custom_emoji_id": custom_id,
            "alternative_text": emoji,
        })
        remaining = remaining[match_pos + len(emoji):]
    return parts or [str(value)]


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
        "rich_message": {"blocks": build_status_summary_blocks(counts)},
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
            await update.callback_query.edit_message_text(
                rich_fallback, parse_mode=ParseMode.HTML, reply_markup=status_view_kb()
            )
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
        "rich_message": {"blocks": build_refresh_summary_blocks(result)},
        "reply_markup": status_kb().to_dict(),
    }
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(api_base + "editMessageText", json=payload) as response:
            data = await response.json(content_type=None)
            if not response.ok or not data.get("ok"):
                raise RuntimeError(data.get("description", "Rich summary edit failed"))


# ── Join result summary (blocks with ᴠɪᴇᴡ buttons) ──────────────────────────
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
        "rich_message": {"blocks": build_join_summary_blocks(join_result, total, target)},
        "reply_markup": main_menu_kb().to_dict(),
    }
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(api_base + "editMessageText", json=payload) as response:
            data = await response.json(content_type=None)
            if not response.ok or not data.get("ok"):
                raise RuntimeError(data.get("description", "Join summary edit failed"))


# ── Report result summary (blocks with ᴠɪᴇᴡ buttons) ────────────────────────
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
        "rich_message": {"blocks": build_report_summary_blocks(result, target, total_ids, count)},
        "reply_markup": main_menu_kb().to_dict(),
    }
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(api_base + "editMessageText", json=payload) as response:
            data = await response.json(content_type=None)
            if not response.ok or not data.get("ok"):
                raise RuntimeError(data.get("description", "Report summary edit failed"))


# ── Leave result summary (blocks with ᴠɪᴇᴡ buttons) ─────────────────────────
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
        "rich_message": {"blocks": build_leave_summary_blocks(leave_result, total, target)},
        "reply_markup": main_menu_kb().to_dict(),
    }
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(api_base + "editMessageText", json=payload) as response:
            data = await response.json(content_type=None)
            if not response.ok or not data.get("ok"):
                raise RuntimeError(data.get("description", "Leave summary edit failed"))


# ── /refresh ─────────────────────────────────────────────────────────────────
async def refresh_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update, ctx):
        return

    chat_id = update.effective_chat.id
    api_base = f"https://api.telegram.org/bot{ctx.bot.token}/"
    progress_message = update.callback_query.message if update.callback_query else None
    progress_message_id = progress_message.message_id if progress_message else None
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

    async def publish_progress(html_content: str, fallback_text: str, initial: bool = False):
        nonlocal progress_message_id, progress_message
        rich_html = apply_custom_emojis(html_content)
        method = "sendRichMessage" if progress_message_id is None else "editMessageText"
        payload = {
            "chat_id": chat_id,
            "rich_message": {"html": rich_html},
        }
        if progress_message_id is not None:
            payload["message_id"] = progress_message_id
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(api_base + method, json=payload) as response:
                    result = await response.json(content_type=None)
                    if not response.ok or not result.get("ok"):
                        raise RuntimeError(result.get("description", "Rich progress update failed"))
                    if method == "sendRichMessage":
                        progress_message_id = result["result"]["message_id"]
            return
        except Exception as exc:
            logging.debug("Rich refresh panel unavailable; using HTML fallback: %s", exc)

        try:
            if progress_message_id is None:
                progress_message = await update.effective_message.reply_text(
                    fallback_text, parse_mode=ParseMode.HTML
                )
                progress_message_id = progress_message.message_id
            else:
                await ctx.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_message_id,
                    text=fallback_text,
                    parse_mode=ParseMode.HTML,
                )
        except Exception as exc:
            logging.debug("Refresh fallback update skipped: %s", exc)

    async def report_progress(index: int, total: int, phone: str, status: str):
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
        fallback = (
            f"⭐ SESSION REFRESH\n📊 Progress: {index}/{total}\n\n"
            + "\n".join(f"{row[0]} {row[1]} — {row[2]}" for row in visible_lines)
        )
        await publish_progress(panel, fallback)

    await publish_progress(
        build_progress_html(0, 0, []),
        "🔄 SESSION REFRESH\nPreparing session checks…",
        initial=True,
    )

    result = await refresh_all_sessions(on_progress=report_progress)
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
    final_text = (
        "⭐ SESSION REFRESH COMPLETE\\n\\n"
        f"🟢 ACTIVE: {result['active']}\\n"
        f"🔴 EXPIRED: {result['expired']}\\n"
        f"⚠️ ERRORS: {result['errors']}\\n"
        f"🎁 CHECKED: {result['checked']}"
    )
    if progress_message_id is not None:
        ctx.application.bot_data[f"refresh:{chat_id}:{progress_message_id}"] = {
            "owner_id": update.effective_user.id,
            "active": result.get("active_ids", []),
            "expired": result.get("expired_ids", []),
            "errors": result.get("error_ids", []),
            "checked": result.get("checked_ids", []),
            "counts": {k: result[k] for k in ("active", "expired", "errors", "checked")},
        }
    try:
        if progress_message_id is None:
            raise RuntimeError("Refresh message ID unavailable")
        await edit_refresh_summary(ctx, chat_id, progress_message_id, result)
    except Exception as exc:
        logging.debug("Rich refresh summary unavailable; using fallback: %s", exc)
        if progress_message_id is not None:
            try:
                await ctx.bot.edit_message_text(
                    chat_id=chat_id, message_id=progress_message_id,
                    text=final_text, reply_markup=refresh_view_kb()
                )
            except Exception:
                await send_rich_message(update, ctx, final_html, refresh_view_kb(), edit=bool(update.callback_query))
        else:
            await send_rich_message(update, ctx, final_html, refresh_view_kb())

# ── /session ─────────────────────────────────────────────────────────────────
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

# ── /unhost ──────────────────────────────────────────────────────────────────
async def unhost_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update, ctx):
        return
    if not ctx.args:
        await send_rich_message(
            update, ctx,
            "<h2>🗑 REMOVE A SESSION</h2><p>Provide the phone number of the session you want to remove.</p>"
            "<p><code>/unhost +919876543210</code></p>"
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


# ── /removeexpiredid ─────────────────────────────────────────────────────────
async def remove_expired_id_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Owner and authorized sudo users may permanently remove expired IDs.
    if not await has_access(update.effective_user.id):
        await send_access_denied(update, ctx)
        return

    if not ctx.args:
        await send_rich_message(
            update, ctx,
            "<h1>🗑 REMOVE EXPIRED ID</h1>"
            "<p>Enter the exact phone number of an account already listed as expired.</p>"
            "<p>Usage: <code>/removeexpiredid +919876543210</code></p>"
            "<footer>⚠️ The saved session record will be permanently deleted.</footer>"
        )
        return

    phone = ctx.args[0].strip()
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


# ── /join ────────────────────────────────────────────────────────────────────
async def join_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update, ctx):
        return
    if not ctx.args:
        await send_rich_message(
            update, ctx,
            "<h2>👥 JOIN CHAT</h2>"
            "<p>Provide a target to join with all authorized accounts.</p><hr/>"
            "<p>Usage:</p>"
            "<p><code>/join @username</code></p>"
            "<p><code>/join https://t.me/+hash</code></p>",
            main_menu_kb()
        )
        return

    target   = ctx.args[0]
    sessions = await get_all_sessions()
    if not sessions:
        await send_rich_message(
            update, ctx,
            "<h2>❌ NO ACTIVE SESSIONS</h2>"
            "<p>Add accounts first before using the join command.</p>",
            main_menu_kb()
        )
        return

    total    = len(sessions)
    chat_id  = update.effective_chat.id
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
    join_initial_fallback = (
        f"⏳ <b>Joining</b> <code>{html.escape(target)}</code>\n\n"
        f"✅ Joined  : <code>0</code>\n"
        f"🔄 Already : <code>0</code>\n"
        f"❌ Failed  : <code>0</code>\n"
        f"📊 Done    : <code>0 / {total}</code>"
    )
    msg_id = await _rich_send_new(
        ctx, chat_id, join_initial_rich, None,
        join_initial_fallback
    )

    last = {"t": 0}

    async def cb(r):
        now  = time.time()
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
            progress_payload = {
                "chat_id": chat_id,
                "message_id": msg_id,
                "rich_message": {"html": apply_custom_emojis(progress_html)},
            }
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        f"https://api.telegram.org/bot{ctx.bot.token}/editMessageText",
                        json=progress_payload,
                    ) as response:
                        result = await response.json(content_type=None)
                        if not response.ok or not result.get("ok"):
                            raise RuntimeError(result.get("description", "Rich join progress update failed"))
            except Exception as exc:
                logging.debug("Rich join progress update failed: %s", exc)
                try:
                    await ctx.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=(
                            f"⏳ <b>Joining</b> <code>{html.escape(target)}</code>\\n\\n"
                            f"✅ Joined  : <code>{r['joined']}</code>\\n"
                            f"🔄 Already : <code>{r['already']}</code>\\n"
                            f"❌ Failed  : <code>{r['failed']}</code>\\n"
                            f"📊 Done    : <code>{done} / {total}</code>"
                        ),
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass

    join_result = await mass_join(sessions, target, progress_cb=cb)

    # Store result data for ᴠɪᴇᴡ button navigation
    all_phones = [s.get("phone", "?") for s in sessions]
    ctx.application.bot_data[f"join:{chat_id}:{msg_id}"] = {
        "owner_id":   update.effective_user.id,
        "target":     target,
        "total":      total,
        "counts":     {k: join_result[k] for k in ("joined", "already", "failed")},
        "joined":     join_result.get("joined_ids", []),
        "already":    join_result.get("already_ids", []),
        "failed":     join_result.get("failed_ids_arr", []),
        "total_list": all_phones,
    }
    try:
        await edit_join_summary(ctx, chat_id, msg_id, join_result, total, target)
    except Exception as exc:
        logging.debug("Rich join summary blocks unavailable; using HTML fallback: %s", exc)
        rich_done = (
            "<h1>✅ JOIN COMPLETE</h1>"
            f"<p><i>All accounts processed for <code>{html.escape(target)}</code></i></p><hr/>"
            "<table bordered striped>"
            "<tr><th>RESULT</th><th>COUNT</th></tr>"
            f"<tr><td>✅ Joined</td><td><b>{join_result['joined']}</b></td></tr>"
            f"<tr><td>🔄 Already Member</td><td><b>{join_result['already']}</b></td></tr>"
            f"<tr><td>❌ Failed</td><td><b>{join_result['failed']}</b></td></tr>"
            f"<tr><td>👥 Total Accounts</td><td><b>{total}</b></td></tr>"
            "</table>"
            "<footer>Join operation finished.</footer>"
        )
        fallback_done = (
            "╔══════════════════╗\n"
            "║    ✅ JOIN DONE   ║\n"
            "╚══════════════════╝\n\n"
            f"🎯 Target  : <code>{html.escape(target)}</code>\n"
            f"✅ Joined  : <code>{join_result['joined']}</code>\n"
            f"🔄 Already : <code>{join_result['already']}</code>\n"
            f"❌ Failed  : <code>{join_result['failed']}</code>\n"
            f"👥 Total   : <code>{total}</code>"
        )
        await _rich_edit_by_id(
            ctx, chat_id, msg_id,
            rich_done, main_menu_kb(),
            fallback_done, main_menu_kb()
        )


# ── /leave ───────────────────────────────────────────────────────────────────
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

    target   = ctx.args[0]
    sessions = await get_all_sessions()
    if not sessions:
        await send_rich_message(
            update, ctx,
            "<h2>❌ NO ACTIVE SESSIONS</h2>"
            "<p>Add accounts first before using the leave command.</p>",
            main_menu_kb()
        )
        return

    total    = len(sessions)
    chat_id  = update.effective_chat.id

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
    leave_initial_fallback = (
        f"⏳ <b>Leaving</b> <code>{html.escape(target)}</code>\n\n"
        f"🟢 Leaved    : <code>0</code>\n"
        f"🟡 No Member : <code>0</code>\n"
        f"🔴 Failed    : <code>0</code>\n"
        f"📊 Done      : <code>0 / {total}</code>"
    )
    msg_id = await _rich_send_new(
        ctx, chat_id, leave_initial_rich, None,
        leave_initial_fallback
    )

    last = {"t": 0}

    async def cb(r):
        now  = time.time()
        done = r["leaved"] + r["no_member"] + r["failed"]
        if now - last["t"] >= 1 or done >= total:
            last["t"] = now
            try:
                await ctx.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=(
                        f"⏳ <b>Leaving</b> <code>{html.escape(target)}</code>\n\n"
                        f"🟢 Leaved    : <code>{r['leaved']}</code>\n"
                        f"🟡 No Member : <code>{r['no_member']}</code>\n"
                        f"🔴 Failed    : <code>{r['failed']}</code>\n"
                        f"📊 Done      : <code>{done} / {total}</code>"
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

    leave_result = await mass_leave(sessions, target, progress_cb=cb)

    # Store result data for ᴠɪᴇᴡ button navigation
    all_phones = [s.get("phone", "?") for s in sessions]
    ctx.application.bot_data[f"leave:{chat_id}:{msg_id}"] = {
        "owner_id":    update.effective_user.id,
        "target":      target,
        "total":       total,
        "counts":      {k: leave_result[k] for k in ("leaved", "no_member", "failed")},
        "leaved":      leave_result.get("leaved_ids", []),
        "no_member":   leave_result.get("no_member_ids", []),
        "failed":      leave_result.get("failed_ids", []),
        "total_list":  all_phones,
    }

    try:
        await edit_leave_summary(ctx, chat_id, msg_id, leave_result, total, target)
    except Exception as exc:
        logging.debug("Rich leave summary blocks unavailable; using HTML fallback: %s", exc)
        rich_done = (
            "<h1>🚪 LEAVE COMPLETE</h1>"
            f"<p><i>All accounts processed for <code>{html.escape(target)}</code></i></p><hr/>"
            "<table bordered striped>"
            "<tr><th>RESULT</th><th>COUNT</th></tr>"
            f"<tr><td>🟢 Leaved</td><td><b>{leave_result['leaved']}</b></td></tr>"
            f"<tr><td>🟡 No Member</td><td><b>{leave_result['no_member']}</b></td></tr>"
            f"<tr><td>🔴 Failed</td><td><b>{leave_result['failed']}</b></td></tr>"
            f"<tr><td>📦 Total Accounts</td><td><b>{total}</b></td></tr>"
            "</table>"
            "<footer>Leave operation finished.</footer>"
        )
        fallback_done = (
            "╔══════════════════╗\n"
            "║   🚪 LEAVE DONE   ║\n"
            "╚══════════════════╝\n\n"
            f"🎯 Target     : <code>{html.escape(target)}</code>\n"
            f"🟢 Leaved     : <code>{leave_result['leaved']}</code>\n"
            f"🟡 No Member  : <code>{leave_result['no_member']}</code>\n"
            f"🔴 Failed     : <code>{leave_result['failed']}</code>\n"
            f"📦 Total      : <code>{total}</code>"
        )
        await _rich_edit_by_id(
            ctx, chat_id, msg_id,
            rich_done, main_menu_kb(),
            fallback_done, main_menu_kb()
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


# ── /otp (hidden) ────────────────────────────────────────────────────────────
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


# ── /cancel ──────────────────────────────────────────────────────────────────
async def cancel_flow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    data = tmp.pop(uid, None)
    if data and data.get("client"):
        try:
            from pyrogram import Client as PyroClient
            if isinstance(data["client"], PyroClient):
                await data["client"].stop()
            else:
                await data["client"].disconnect()
        except Exception:
            pass
    rich = "<h2>✓ ACTION CANCELLED</h2><p>Your current operation was cancelled.</p><footer>You can choose another option from the menu.</footer>"
    if update.callback_query:
        await update.callback_query.answer("Cancelled.")
        await send_rich_message(update, ctx, rich, main_menu_kb())
    else:
        await send_rich_message(update, ctx, rich, main_menu_kb())
    return ConversationHandler.END


# ── Button handler ───────────────────────────────────────────────────────────
async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    uid  = q.from_user.id
    await remember_user(q.from_user)

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
                        "◈ 𝗗𝗘𝗩𝗘𝗟𝗢𝗣𝗘𝗥 ◈",
                        url=f"https://t.me/{developer}",
                        style="danger",
                        icon_custom_emoji_id="6091123254842366873",
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

        target         = tmp[uid]["target"]
        reason         = tmp[uid]["reason"]
        count          = tmp[uid]["count"]
        report_message = tmp[uid]["report_message"]
        total_ids      = len(sessions)
        total_expected = total_ids * count

        # Show "started" indicator on the confirm message
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
        report_initial_fallback = (
            f"📡 <b>Reporting in progress...</b>\n\n"
            f"✅ Success : <code>0</code>\n"
            f"❌ Failed  : <code>0</code>\n"
            f"🔴 Expired : <code>0</code>\n"
            f"📊 Done    : <code>0 / {total_expected}</code>"
        )
        sent_msg_id = await _rich_send_new(
            ctx, report_chat_id, report_initial_rich, None,
            report_initial_fallback
        )

        last = {"t": 0}

        async def cb(r):
            now  = time.time()
            done = r["success"] + r["failed"] + r["expired"]
            if now - last["t"] >= 1 or done >= total_expected:
                last["t"] = now
                try:
                    await ctx.bot.edit_message_text(
                        chat_id=report_chat_id,
                        message_id=sent_msg_id,
                        text=(
                            f"📡 <b>Reporting in progress...</b>\n\n"
                            f"✅ Success : <code>{r['success']}</code>\n"
                            f"❌ Failed  : <code>{r['failed']}</code>\n"
                            f"🔴 Expired : <code>{r['expired']}</code>\n"
                            f"📊 Done    : <code>{done} / {total_expected}</code>"
                        ),
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass

        result = await mass_report(
            sessions, target, reason, count, report_message, progress_cb=cb
        )

        # Store result data for ᴠɪᴇᴡ button navigation
        all_phones = [s.get("phone", "?") for s in sessions]
        ctx.application.bot_data[f"report:{report_chat_id}:{sent_msg_id}"] = {
            "owner_id":    uid,
            "target":      target,
            "count":       count,
            "total_ids":   total_ids,
            "counts":      {k: result[k] for k in ("success", "failed", "expired")},
            "success":     result.get("success_ids", []),
            "failed":      result.get("failed_ids_arr", []),
            "expired":     result.get("expired_ids_arr", []),
            "total_list":  all_phones,
        }

        try:
            await edit_report_summary(ctx, report_chat_id, sent_msg_id,
                                      result, target, total_ids, count)
        except Exception as exc:
            logging.debug("Rich report summary blocks unavailable; using HTML fallback: %s", exc)
            rich_done = (
                "<h1>📊 REPORT COMPLETE</h1>"
                "<p><i>Mass report operation finished</i></p><hr/>"
                "<table bordered striped>"
                "<tr><th>RESULT</th><th>COUNT</th></tr>"
                f"<tr><td>🎯 Target</td><td><code>{html.escape(target)}</code></td></tr>"
                f"<tr><td>✅ Success</td><td><b>{result['success']}</b></td></tr>"
                f"<tr><td>❌ Failed</td><td><b>{result['failed']}</b></td></tr>"
                f"<tr><td>🔴 Expired</td><td><b>{result['expired']}</b></td></tr>"
                f"<tr><td>👥 Total IDs</td><td><b>{total_ids}</b></td></tr>"
                f"<tr><td>📊 Total Reports</td><td><b>{total_ids * count}</b></td></tr>"
                "</table>"
            )
            if result.get("errors"):
                unique = list(set(result["errors"]))[:5]
                rich_done += (
                    "<h3>⚠️ ERRORS</h3>"
                    + "".join(f"<p><code>{html.escape(e)}</code></p>" for e in unique)
                )
            rich_done += "<footer>Report session complete.</footer>"
            err_line = ""
            if result.get("errors"):
                unique   = list(set(result["errors"]))[:5]
                err_line = "\n\n⚠️ <b>Errors:</b>\n" + "\n".join(
                    f"• <code>{html.escape(e)}</code>" for e in unique
                )
            fallback_done = (
                "╔══════════════════╗\n"
                "║   📊 REPORT DONE  ║\n"
                "╚══════════════════╝\n\n"
                f"🎯 Target   : <code>{html.escape(target)}</code>\n"
                f"✅ Success  : <code>{result['success']}</code>\n"
                f"❌ Failed   : <code>{result['failed']}</code>\n"
                f"🔴 Expired  : <code>{result['expired']}</code>\n"
                f"👥 Total IDs: <code>{total_ids}</code>\n"
                f"📊 Total    : <code>{total_ids * count}</code>"
                f"{err_line}"
            )
            await _rich_edit_by_id(
                ctx, report_chat_id, sent_msg_id,
                rich_done, main_menu_kb(),
                fallback_done, main_menu_kb()
            )
        tmp.pop(uid, None)
        return ConversationHandler.END

    return None


# ── Account add flow (Pyrogram + Official Android credentials) ───────────────
async def recv_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    phone = update.message.text.strip()

    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        await send_rich_message(
            update, ctx,
            "<h2>❌ INVALID FORMAT</h2>"
            "<p>Use international format: <code>+919876543210</code></p>"
            "<footer>Include the country code with + prefix.</footer>",
            cancel_kb()
        )
        return ADD_PHONE

    try:
        from pyrogram import Client

        # Official Telegram Android app credentials
        # Telegram trusts these even from datacenter IPs — OTP delivery works for all countries
        OFFICIAL_API_ID   = 6
        OFFICIAL_API_HASH = "eb06d4abfb49dc3eeb1aeb98ae0f581e"

        client = Client(
            name=f"host_{uid}",
            api_id=OFFICIAL_API_ID,
            api_hash=OFFICIAL_API_HASH,
            in_memory=True,
            device_model="Samsung Galaxy S23",
            system_version="Android 13",
            app_version="10.0.1",
            lang_code="en",
            no_updates=True,
        )
        await client.connect()
        sent_code = await client.send_code(phone)
        tmp[uid]  = {
            "client":          client,
            "phone":           phone,
            "phone_code_hash": sent_code.phone_code_hash,
            "client_type":     "pyrogram",
        }
        await send_rich_message(
            update, ctx,
            "<h2>🔑 OTP SENT</h2>"
            "<p>A verification code was sent to your Telegram app.</p>"
            "<p>Enter the OTP below to continue:</p>"
            "<footer>The code expires in a few minutes.</footer>",
            cancel_kb()
        )
        return ADD_CODE

    except Exception as e:
        await send_rich_message(
            update, ctx,
            f"<h2>❌ ERROR</h2><p><code>{html.escape(str(e))}</code></p>",
            cancel_kb()
        )
        return ADD_PHONE


async def recv_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    code   = update.message.text.strip()
    data   = tmp.get(uid, {})
    client = data.get("client")

    try:
        await client.sign_in(
            phone_number=data["phone"],
            phone_code_hash=data["phone_code_hash"],
            phone_code=code,
        )
    except Exception as e:
        if "SessionPasswordNeeded" in type(e).__name__:
            await send_rich_message(
                update, ctx,
                "<h2>🔐 TWO-FACTOR AUTH</h2>"
                "<p>Your account has 2FA enabled.</p>"
                "<p>Enter your 2FA password to continue:</p>",
                cancel_kb()
            )
            return ADD_2FA
        await send_rich_message(
            update, ctx,
            f"<h2>❌ OTP ERROR</h2><p><code>{html.escape(str(e))}</code></p>",
        )
        try:
            await client.disconnect()
        except Exception:
            pass
        tmp.pop(uid, None)
        return ConversationHandler.END

    try:
        session_str = await client.export_session_string()
        await client.disconnect()
        await save_session(uid, data["phone"], session_str, client_type="pyrogram")
        await send_rich_message(
            update, ctx,
            f"<h1>✅ ACCOUNT ADDED</h1>"
            f"<p><code>{html.escape(data['phone'])}</code> connected successfully.</p>"
            "<footer>Your session has been saved.</footer>",
            main_menu_kb()
        )
        tmp.pop(uid, None)
    except Exception as e:
        await send_rich_message(
            update, ctx,
            f"<h2>❌ SESSION SAVE ERROR</h2><p><code>{html.escape(str(e))}</code></p>"
        )
    return ConversationHandler.END


async def recv_2fa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    password = update.message.text.strip()
    data     = tmp.get(uid, {})
    client   = data.get("client")

    try:
        await client.check_password(password)
        session_str = await client.export_session_string()
        await client.disconnect()
        await save_session(uid, data["phone"], session_str, client_type="pyrogram")
        await send_rich_message(
            update, ctx,
            f"<h1>✅ ACCOUNT ADDED</h1>"
            f"<p><code>{html.escape(data['phone'])}</code> connected successfully.</p>"
            "<footer>Your session has been saved.</footer>",
            main_menu_kb()
        )
        tmp.pop(uid, None)
    except Exception as e:
        await send_rich_message(
            update, ctx,
            f"<h2>❌ 2FA FAILED</h2><p><code>{html.escape(str(e))}</code></p>"
        )
    return ConversationHandler.END


# ── Report flow ──────────────────────────────────────────────────────────────
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


# ── Main ─────────────────────────────────────────────────────────────────────
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

    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("status",     status_cmd))
    app.add_handler(CommandHandler("refresh",    refresh_cmd))
    app.add_handler(CommandHandler("session",    session_cmd))
    app.add_handler(CommandHandler("unhost",     unhost_cmd))
    app.add_handler(CommandHandler("removeexpiredid", remove_expired_id_cmd))
    app.add_handler(CommandHandler("help",       help_cmd))
    app.add_handler(CommandHandler("join",       join_cmd))
    app.add_handler(CommandHandler("leave",      leave_cmd))
    app.add_handler(CommandHandler("sudo",       sudo_cmd))
    app.add_handler(CommandHandler("removesudo", removesudo_cmd))
    app.add_handler(CommandHandler("sudoinfo",   sudoinfo_cmd))
    app.add_handler(CommandHandler("otp",        otp_cmd))  # hidden
    app.add_handler(conv)
    app.add_handler(CommandHandler("cancel",     cancel_flow))
    app.add_handler(CallbackQueryHandler(button_handler))

    async def on_startup(app):
        await fetch_proxies()
        asyncio.create_task(rotate_forever())
        await app.bot.set_my_commands([
            BotCommand("start",      "Main menu"),
            BotCommand("host",       "Add a Telegram account"),
            BotCommand("status",     "Session statistics"),
            BotCommand("refresh",    "Verify all sessions"),
            BotCommand("session",    "List your sessions"),
            BotCommand("unhost",     "Remove a session by phone"),
            BotCommand("removeexpiredid", "Permanently remove an expired ID"),
            BotCommand("join",       "Join with all accounts"),
            BotCommand("leave",      "Leave a chat with all accounts"),
            BotCommand("sudoinfo",   "Access directory"),
            BotCommand("cancel",     "Cancel current flow"),
            BotCommand("help",       "All commands"),
        ])

    app.post_init = on_startup
    app.run_polling()


if __name__ == "__main__":
    main()
