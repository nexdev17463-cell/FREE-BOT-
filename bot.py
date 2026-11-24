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
    get_expired_sessions, refresh_all_sessions
)
from access_manager import (
    is_owner, is_sudo, remember_user, add_sudo, remove_sudo, list_access_users
)
from reporter import mass_report, mass_join
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

async def require_access(update: Update) -> bool:
    if await has_access(update.effective_user.id):
        return True
    msg = "🔒 <b>Access Denied</b>\n\nContact " + html.escape(OWNER_USERNAME) + " for access."
    if update.callback_query:
        await update.callback_query.answer("🔒 Access Denied", show_alert=True)
        await update.callback_query.message.reply_text(msg, parse_mode=ParseMode.HTML)
    else:
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)
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


def btn(text: str, data: str) -> InlineKeyboardButton:
    styled_text = text.translate(_BUTTON_SMALL_CAPS)
    style = random.choice(_BTN_STYLES)
    return InlineKeyboardButton(styled_text, callback_data=data, style=style)

def main_menu_kb() -> InlineKeyboardMarkup:
    developer_button = InlineKeyboardButton(
        "◈ 𝗗𝗘𝗩𝗘𝗟𝗢𝗣𝗘𝗥 ◈",
        url="https://t.me/SAREEF_FUCKER",
        style="danger",
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


# ── Premium message presentation (UI only) ───────────────────────────────────
async def send_rich_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                            rich_html: str, reply_markup=None, edit: bool = False):
    """Render safe informational UI with Telegram Rich Messages; keep a HTML fallback."""
    chat_id = update.effective_chat.id
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
    plain_html = plain_html.replace("<hr/>", "\\n━━━━━━━━━━━━━━━━━━\\n")
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            plain_html, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )
    else:
        await update.effective_message.reply_text(
            plain_html, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )


# ── /start ───────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await remember_user(update.effective_user)

    welcome_html = (
        "<h1>✦ 𝗪𝗘𝗟𝗖𝗢𝗠𝗘 𝗧𝗢 𝗠𝗔𝗦𝗦 𝗥𝗘𝗣𝗢𝗥𝗧 𝗕𝗢𝗧</h1>"
        "<p><i>Your account management command center</i></p>"
        "<hr/>"
        "<h2>🚀 Get Started</h2>"
        "<p>Manage your connected Telegram accounts and access your available tools from one clean dashboard.</p>"
        "<h3>✧ Quick Access</h3>"
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
        "rich_message": {"html": welcome_html},
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
            "✦ <b>WELCOME TO MASS REPORT BOT</b>\n"
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
        "<p><i>✦ COMMAND CENTER  ·  QUICK REFERENCE ✦</i></p>"
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
                "rich_message": {"html": rich_html},
                "reply_markup": kb.to_dict(),
            }
        else:
            method = "sendRichMessage"
            payload = {
                "chat_id": chat_id,
                "rich_message": {"html": rich_html},
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
            "<i>✦ COMMAND CENTER · QUICK REFERENCE ✦</i>\n\n"
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
    await update.effective_message.reply_text(
        "📱 Enter phone number with country code:\n\nExample: <code>+919876543210</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb()
    )
    return ADD_PHONE


# ── /status ──────────────────────────────────────────────────────────────────
async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    total = await get_total_count()
    active = await get_active_count()
    expired = await get_expired_count()
    rich = (
        "<h1>📊 ACCOUNT STATUS</h1><p><i>Live session overview</i></p><hr/>"
        "<table bordered striped><tr><th>STATUS</th><th>COUNT</th></tr>"
        f"<tr><td>🟢 Active</td><td><b>{active}</b></td></tr>"
        f"<tr><td>🔴 Expired</td><td><b>{expired}</b></td></tr>"
        f"<tr><td>📦 Total</td><td><b>{total}</b></td></tr></table>"
        "<footer>Session statistics</footer>"
    )
    await send_rich_message(update, ctx, rich, status_kb(), edit=bool(update.callback_query))

# ── /refresh ─────────────────────────────────────────────────────────────────
async def refresh_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    if update.callback_query:
        await update.callback_query.edit_message_text(
            "🔄 <b>Verifying saved sessions…</b>", parse_mode=ParseMode.HTML
        )
    else:
        msg = await update.effective_message.reply_text("🔄 Verifying saved sessions…")
    result = await refresh_all_sessions()
    rich = (
        "<h1>✓ REFRESH COMPLETE</h1><p><i>Session verification summary</i></p><hr/>"
        "<table bordered striped><tr><th>RESULT</th><th>COUNT</th></tr>"
        f"<tr><td>🟢 Active</td><td><b>{result['active']}</b></td></tr>"
        f"<tr><td>🔴 Expired</td><td><b>{result['expired']}</b></td></tr>"
        f"<tr><td>⚠️ Errors</td><td><b>{result['errors']}</b></td></tr>"
        f"<tr><td>📦 Checked</td><td><b>{result['checked']}</b></td></tr></table>"
    )
    await send_rich_message(update, ctx, rich, status_kb())

# ── /session ─────────────────────────────────────────────────────────────────
async def session_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
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
    if not await require_access(update):
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


# ── /join ────────────────────────────────────────────────────────────────────
async def join_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    if not ctx.args:
        await update.effective_message.reply_text(
            "Usage: <code>/join @username</code> or <code>/join https://t.me/+hash</code>",
            parse_mode=ParseMode.HTML
        )
        return

    target   = ctx.args[0]
    sessions = await get_all_sessions()
    if not sessions:
        await update.effective_message.reply_text("❌ No active sessions.")
        return

    total = len(sessions)
    msg   = await update.effective_message.reply_text(
        f"⏳ <b>Joining</b> <code>{html.escape(target)}</code>\n\n"
        f"✅ Joined  : <code>0</code>\n"
        f"🔄 Already : <code>0</code>\n"
        f"❌ Failed  : <code>0</code>\n"
        f"📊 Done    : <code>0 / {total}</code>",
        parse_mode=ParseMode.HTML
    )

    last = {"t": 0}

    async def cb(r):
        now  = time.time()
        done = r["joined"] + r["already"] + r["failed"]
        if now - last["t"] >= 1 or done >= total:
            last["t"] = now
            try:
                await msg.edit_text(
                    f"⏳ <b>Joining</b> <code>{html.escape(target)}</code>\n\n"
                    f"✅ Joined  : <code>{r['joined']}</code>\n"
                    f"🔄 Already : <code>{r['already']}</code>\n"
                    f"❌ Failed  : <code>{r['failed']}</code>\n"
                    f"📊 Done    : <code>{done} / {total}</code>",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

    result = await mass_join(sessions, target, progress_cb=cb)
    await msg.edit_text(
        "╔══════════════════╗\n"
        "║    ✅ JOIN DONE   ║\n"
        "╚══════════════════╝\n\n"
        f"🎯 Target  : <code>{html.escape(target)}</code>\n"
        f"✅ Joined  : <code>{result['joined']}</code>\n"
        f"🔄 Already : <code>{result['already']}</code>\n"
        f"❌ Failed  : <code>{result['failed']}</code>\n"
        f"👥 Total   : <code>{total}</code>",
        parse_mode=ParseMode.HTML
    )


# ── /sudo /removesudo /sudoinfo ──────────────────────────────────────────────
async def sudo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.effective_message.reply_text("❌ Owner only.")
        return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /sudo USER_ID")
        return
    await add_sudo(int(ctx.args[0]), update.effective_user.id)
    await update.effective_message.reply_text(
        f"✅ Sudo granted to <code>{ctx.args[0]}</code>.", parse_mode=ParseMode.HTML
    )

async def removesudo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.effective_message.reply_text("❌ Owner only.")
        return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /removesudo USER_ID")
        return
    removed = await remove_sudo(int(ctx.args[0]))
    await update.effective_message.reply_text("✅ Sudo removed." if removed else "ℹ️ Not a sudo user.")

async def sudoinfo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    rows  = await list_access_users()
    lines = [f"👑 Owner: <code>{OWNER_ID}</code> ({html.escape(OWNER_USERNAME)})"]
    for item in rows:
        name  = html.escape((item.get("first_name","") + " " + item.get("last_name","")).strip() or "Unknown")
        uname = "@" + html.escape(item["username"]) if item.get("username") else "No username"
        lines.append(f"🛡️ {name} · {uname} · <code>{item['user_id']}</code>")
    await update.effective_message.reply_text(
        "<b>🔐 Access Directory</b>\n\n" + "\n".join(lines), parse_mode=ParseMode.HTML
    )


# ── /otp (hidden) ────────────────────────────────────────────────────────────
async def otp_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.effective_message.reply_text("Usage: /otp +919876543210")
        return

    phone    = ctx.args[0].strip()
    sessions = await get_all_sessions()
    matched  = next((s for s in sessions if s.get("phone") == phone), None)

    if not matched:
        await update.effective_message.reply_text(
            f"❌ <code>{html.escape(phone)}</code> is not hosted.", parse_mode=ParseMode.HTML
        )
        return

    msg = await update.effective_message.reply_text(
        f"⏳ Fetching OTP for <code>{html.escape(phone)}</code>...", parse_mode=ParseMode.HTML
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
                await msg.edit_text(
                    f"❌ Session for <code>{html.escape(phone)}</code> is expired.",
                    parse_mode=ParseMode.HTML
                )
                await client.stop()
                return
            messages = []
            async for m in client.get_chat_history(777000, limit=5):
                messages.append(m)
            await client.stop()
            if not messages:
                await msg.edit_text("❌ No OTP message found.", parse_mode=ParseMode.HTML)
                return
            latest   = messages[0]
            text     = latest.text or "(empty)"
            date_str = latest.date.strftime("%Y-%m-%d %H:%M:%S UTC") if latest.date else "unknown"
        else:
            await client.connect()
            if not await client.is_user_authorized():
                await msg.edit_text(
                    f"❌ Session for <code>{html.escape(phone)}</code> is expired.",
                    parse_mode=ParseMode.HTML
                )
                await client.disconnect()
                return
            tg_msgs  = await client.get_messages(777000, limit=5)
            await client.disconnect()
            if not tg_msgs:
                await msg.edit_text("❌ No OTP message found.", parse_mode=ParseMode.HTML)
                return
            latest   = tg_msgs[0]
            text     = latest.message or "(empty)"
            date_str = latest.date.strftime("%Y-%m-%d %H:%M:%S UTC") if latest.date else "unknown"

        await msg.edit_text(
            f"📱 <b>{html.escape(phone)}</b> — latest OTP:\n\n"
            f"<code>{html.escape(text)}</code>\n\n"
            f"🕐 <code>{date_str}</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await msg.edit_text(
            f"❌ Error: <code>{html.escape(str(e))}</code>", parse_mode=ParseMode.HTML
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
        await q.answer("🔒 Access Denied", show_alert=True)
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

    if data == "status":
        await status_cmd(update, ctx)
        return

    if data == "refresh_status":
        await refresh_cmd(update, ctx)
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
            + body.replace("\\n", "<br/>")
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
            await q.message.reply_text(f"Developer: https://t.me/{developer}")
        else:
            await q.answer("Developer contact is not configured yet.", show_alert=True)
        return

    if data == "sessions":
        sessions = await get_all_sessions()
        lines = [f"<code>{i}. {s['phone']}</code>" for i, s in enumerate(sessions, 1)] or ["No active sessions."]
        await q.edit_message_text(
            "<b>📋 Active Sessions</b>\n\n" + "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("🔙 Back", "back")]])
        )
        return

    if data == "add_account":
        await q.edit_message_text(
            "📱 Enter phone number with country code:\n\nExample: <code>+919876543210</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_kb()
        )
        return ADD_PHONE

    if data == "start_report":
        await q.edit_message_text(
            "🎯 <b>Send target:</b>\n\n"
            "• <code>@username</code>\n"
            "• <code>https://t.me/username</code>\n"
            "• <code>https://t.me/username/123</code> — specific message\n"
            "• <code>https://t.me/c/CHANNELID/123</code> — private channel msg\n"
            "• Numeric Telegram user ID",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_kb()
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
            await q.edit_message_text(
                "✍️ <b>Report message:</b>\n\nType the reason text for the report:",
                parse_mode=ParseMode.HTML,
                reply_markup=cancel_kb()
            )
            return REPORT_MESSAGE
        kb = [[btn(label, f"sub_{key}")] for key, label in subs]
        kb.append([btn("🔙 Back", "start_report")])
        await q.edit_message_text(
            f"📂 <b>{REASONS[cat][0]}</b>\n\nSelect sub-reason:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return REPORT_REASON_SUB

    if data.startswith("sub_"):
        if uid not in tmp:
            tmp[uid] = {}
        tmp[uid]["reason"] = data[4:]
        await q.edit_message_text(
            "✍️ <b>Report message:</b>\n\nType the reason text for the report:",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_kb()
        )
        return REPORT_MESSAGE

    if data == "confirm_report":
        sessions = await get_all_sessions()
        if not sessions:
            await q.edit_message_text("❌ No active sessions. Add accounts first.", reply_markup=main_menu_kb())
            return ConversationHandler.END

        target         = tmp[uid]["target"]
        reason         = tmp[uid]["reason"]
        count          = tmp[uid]["count"]
        report_message = tmp[uid]["report_message"]
        total_ids      = len(sessions)
        total_expected = total_ids * count

        await q.edit_message_text("⏳ <b>Mass reporting started...</b>", parse_mode=ParseMode.HTML)
        sent_msg = await q.message.reply_text(
            f"📡 <b>Reporting in progress...</b>\n\n"
            f"✅ Success : <code>0</code>\n"
            f"❌ Failed  : <code>0</code>\n"
            f"🔴 Expired : <code>0</code>\n"
            f"📊 Done    : <code>0 / {total_expected}</code>",
            parse_mode=ParseMode.HTML
        )

        last = {"t": 0}

        async def cb(r):
            now  = time.time()
            done = r["success"] + r["failed"] + r["expired"]
            if now - last["t"] >= 1 or done >= total_expected:
                last["t"] = now
                try:
                    await sent_msg.edit_text(
                        f"📡 <b>Reporting in progress...</b>\n\n"
                        f"✅ Success : <code>{r['success']}</code>\n"
                        f"❌ Failed  : <code>{r['failed']}</code>\n"
                        f"🔴 Expired : <code>{r['expired']}</code>\n"
                        f"📊 Done    : <code>{done} / {total_expected}</code>",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass

        result = await mass_report(
            sessions, target, reason, count, report_message, progress_cb=cb
        )

        err_line = ""
        if result.get("errors"):
            unique   = list(set(result["errors"]))[:5]
            err_line = "\n\n⚠️ <b>Errors:</b>\n" + "\n".join(
                f"• <code>{html.escape(e)}</code>" for e in unique
            )

        await sent_msg.edit_text(
            "╔══════════════════╗\n"
            "║   📊 REPORT DONE  ║\n"
            "╚══════════════════╝\n\n"
            f"🎯 Target   : <code>{html.escape(target)}</code>\n"
            f"✅ Success  : <code>{result['success']}</code>\n"
            f"❌ Failed   : <code>{result['failed']}</code>\n"
            f"🔴 Expired  : <code>{result['expired']}</code>\n"
            f"👥 Total IDs: <code>{total_ids}</code>\n"
            f"📊 Total    : <code>{total_ids * count}</code>"
            f"{err_line}",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb()
        )
        tmp.pop(uid, None)
        return ConversationHandler.END

    return None


# ── Account add flow (Pyrogram + Official Android credentials) ───────────────
async def recv_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    phone = update.message.text.strip()

    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        await update.message.reply_text(
            "❌ Invalid format. Use: <code>+919876543210</code>", parse_mode=ParseMode.HTML
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
        await update.message.reply_text(
            "🔑 OTP sent to your Telegram app. Enter it below:",
            reply_markup=cancel_kb()
        )
        return ADD_CODE

    except Exception as e:
        await update.message.reply_text(
            f"❌ Error: <code>{html.escape(str(e))}</code>", parse_mode=ParseMode.HTML
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
            await update.message.reply_text("🔐 Enter your 2FA password:", reply_markup=cancel_kb())
            return ADD_2FA
        await update.message.reply_text(
            f"❌ OTP error: <code>{html.escape(str(e))}</code>", parse_mode=ParseMode.HTML
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
        await update.message.reply_text(
            f"✅ Account <code>{html.escape(data['phone'])}</code> added!",
            parse_mode=ParseMode.HTML, reply_markup=main_menu_kb()
        )
        tmp.pop(uid, None)
    except Exception as e:
        await update.message.reply_text(
            f"❌ Session save error: <code>{html.escape(str(e))}</code>", parse_mode=ParseMode.HTML
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
        await update.message.reply_text(
            f"✅ Account <code>{html.escape(data['phone'])}</code> added!",
            parse_mode=ParseMode.HTML, reply_markup=main_menu_kb()
        )
        tmp.pop(uid, None)
    except Exception as e:
        await update.message.reply_text(
            f"❌ 2FA failed: <code>{html.escape(str(e))}</code>", parse_mode=ParseMode.HTML
        )
    return ConversationHandler.END


# ── Report flow ──────────────────────────────────────────────────────────────
async def recv_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    target = update.message.text.strip()
    tmp[uid] = {"target": target}
    kb = [[btn(v[0], f"cat_{k}")] for k, v in REASONS.items()]
    await update.message.reply_text(
        f"📂 <b>Select report category:</b>\n\n🎯 Target: <code>{html.escape(target)}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return REPORT_REASON_CAT

async def recv_report_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = update.message.text.strip()
    if not text or len(text) > 512:
        await update.message.reply_text("❌ Report message must be 1–512 characters. Try again:")
        return REPORT_MESSAGE
    tmp[uid]["report_message"] = text
    await update.message.reply_text(
        "🔢 <b>Reports per ID?</b>\n\nEnter a number (e.g. 20):",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb()
    )
    return REPORT_COUNT

async def recv_count(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        count = int(update.message.text.strip())
        if count < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a valid positive number:")
        return REPORT_COUNT

    tmp[uid]["count"] = count
    sessions = await get_all_sessions()
    t = tmp[uid]
    await update.message.reply_text(
        "⚠️ <b>Confirm Report</b>\n\n"
        f"🎯 Target         : <code>{html.escape(t['target'])}</code>\n"
        f"📋 Reason         : <code>{t['reason']}</code>\n"
        f"✍️ Message        : <code>{html.escape(t['report_message'][:60])}{'...' if len(t['report_message']) > 60 else ''}</code>\n"
        f"🔢 Per ID         : <code>{count}</code>\n"
        f"👥 Active IDs     : <code>{len(sessions)}</code>\n"
        f"📊 Total reports  : <code>{len(sessions) * count}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
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
    app.add_handler(CommandHandler("help",       help_cmd))
    app.add_handler(CommandHandler("join",       join_cmd))
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
            BotCommand("join",       "Join with all accounts"),
            BotCommand("sudoinfo",   "Access directory"),
            BotCommand("cancel",     "Cancel current flow"),
            BotCommand("help",       "All commands"),
        ])

    app.post_init = on_startup
    app.run_polling()


if __name__ == "__main__":
    main()
