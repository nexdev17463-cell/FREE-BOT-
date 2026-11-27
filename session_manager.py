from motor.motor_asyncio import AsyncIOMotorClient
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import API_ID, API_HASH, MONGO_URI
from datetime import datetime, timezone
import logging

_db = None

# Only explicit Telegram authentication/session failures make a session expired.
# Transport, rate-limit, server, and other API failures remain retryable errors.
_AUTHENTICATION_ERROR_NAMES = frozenset({
    "AuthKeyUnregisteredError",
    "AuthKeyInvalidError",
    "AuthKeyInvalid",
    "AuthKeyUnregistered",
    "AuthKeyDuplicatedError",
    "AuthKeyDuplicated",
    "SessionRevokedError",
    "SessionRevoked",
    "SessionExpiredError",
    "SessionExpired",
    "UserDeactivatedError",
    "UserDeactivated",
    "UserDeactivatedBanError",
    "UserDeactivatedBan",
    "UnauthorizedError",
    "Unauthorized",
})


def is_authentication_error(exc: Exception) -> bool:
    """Return True only for known, explicit Telegram session/auth failures."""
    return any(
        cls.__name__ in _AUTHENTICATION_ERROR_NAMES
        for cls in type(exc).__mro__
    )

def get_db():
    global _db
    if _db is None:
        _db = AsyncIOMotorClient(MONGO_URI)["reportbot"]
    return _db


async def save_session(user_id: int, phone: str, session_string: str, client_type: str = "pyrogram"):
    await get_db().sessions.update_one(
        {"phone": phone},
        {"$set": {
            "phone": phone,
            "session": session_string,
            "owner": user_id,
            "active": True,
            "client_type": client_type,
            "status_checked_at": datetime.now(timezone.utc),
        }},
        upsert=True
    )

async def get_all_status_sessions() -> list[dict]:
    """Return all stored sessions for the status screen's read-only ID views."""
    return await get_db().sessions.find({}).sort("phone", 1).to_list(length=None)


async def get_all_sessions() -> list[dict]:
    return await get_db().sessions.find({"active": True}).to_list(length=None)

async def get_user_sessions(user_id: int) -> list[dict]:
    return await get_db().sessions.find({"owner": user_id, "active": True}).to_list(length=None)

async def delete_session(phone: str, user_id: int) -> bool:
    result = await get_db().sessions.delete_one({"phone": phone, "owner": user_id})
    return result.deleted_count > 0

async def mark_expired(phone: str):
    await get_db().sessions.update_one(
        {"phone": phone},
        {"$set": {"active": False, "status_checked_at": datetime.now(timezone.utc)}}
    )

async def mark_active(phone: str):
    await get_db().sessions.update_one(
        {"phone": phone},
        {"$set": {"active": True, "status_checked_at": datetime.now(timezone.utc)}}
    )

async def get_expired_sessions() -> list[dict]:
    return await get_db().sessions.find({"active": False}).sort("phone", 1).to_list(length=None)


async def delete_expired_session(phone: str) -> int:
    """Permanently delete all session documents for a phone only if it is expired."""
    sessions = get_db().sessions
    expired = await sessions.find_one({"phone": phone, "active": False}, {"_id": 1})
    if not expired:
        return 0
    result = await sessions.delete_many({"phone": phone})
    return result.deleted_count


async def delete_all_expired_sessions() -> int:
    """Permanently delete only session documents explicitly marked as expired."""
    result = await get_db().sessions.delete_many({"active": False})
    return result.deleted_count

async def get_total_count() -> int:
    return await get_db().sessions.count_documents({})

async def get_active_count() -> int:
    return await get_db().sessions.count_documents({"active": True})

async def get_expired_count() -> int:
    return await get_db().sessions.count_documents({"active": False})


def build_telethon_client(session_str: str) -> TelegramClient:
    return TelegramClient(
        StringSession(session_str),
        API_ID, API_HASH,
        connection_retries=1,
        retry_delay=1,
        timeout=15,
    )


def build_pyrogram_client(session_str: str):
    from pyrogram import Client
    return Client(
        name="pyro_session",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_str,
        in_memory=True,
        device_model="Samsung Galaxy S23",
        system_version="Android 13",
        app_version="10.0.1",
        lang_code="en",
        no_updates=True,
    )


async def build_client(sdata: dict):
    """Returns the right client based on client_type stored in MongoDB."""
    client_type = sdata.get("client_type", "telethon")
    if client_type == "pyrogram":
        return build_pyrogram_client(sdata["session"])
    return build_telethon_client(sdata["session"])


async def refresh_all_sessions(on_progress=None) -> dict:
    """Check every stored session without treating transient failures as expired."""
    records = await get_db().sessions.find({}).to_list(length=None)
    result = {
        "active": 0, "expired": 0, "checked": 0, "errors": 0,
        "active_ids": [], "expired_ids": [], "error_ids": [], "checked_ids": [],
    }
    total = len(records)

    for index, item in enumerate(records, 1):
        client = None
        status = "error"
        phone = item.get("phone", "Unknown")
        client_type = item.get("client_type", "telethon")

        try:
            client = await build_client(item)
            if client_type == "pyrogram":
                await client.start()
                authorized = await client.get_me() is not None
                await client.stop()
                client = None
            else:
                await client.connect()
                authorized = await client.is_user_authorized()
                await client.disconnect()
                client = None

            if authorized:
                await mark_active(item["phone"])
                result["active"] += 1
                result["active_ids"].append(phone)
                result["checked_ids"].append(phone)
                status = "active"
            else:
                # A completed authorization check returning False is definitive.
                await mark_expired(item["phone"])
                result["expired"] += 1
                result["expired_ids"].append(phone)
                result["checked_ids"].append(phone)
                status = "expired"
            result["checked"] += 1

        except Exception as exc:
            if is_authentication_error(exc):
                try:
                    await mark_expired(item["phone"])
                except Exception:
                    # A failed database write must not be reported as expired.
                    logging.exception("Could not persist expired status for session %s", phone)
                    result["errors"] += 1
                    result["error_ids"].append(phone)
                    status = "error"
                else:
                    result["expired"] += 1
                    result["expired_ids"].append(phone)
                    result["checked"] += 1
                    result["checked_ids"].append(phone)
                    status = "expired"
            else:
                # Network/API/rate-limit failures leave the stored status untouched.
                result["errors"] += 1
                result["error_ids"].append(phone)
                status = "error"
                logging.warning("Temporary or unclassified refresh failure for %s: %s", phone, exc)

            if client is not None:
                try:
                    if client_type == "pyrogram":
                        await client.stop()
                    else:
                        await client.disconnect()
                except Exception:
                    pass

        finally:
            if on_progress:
                try:
                    await on_progress(index, total, phone, status)
                except Exception:
                    # UI reporting must never interrupt the actual session refresh.
                    pass

    return result
