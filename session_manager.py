from motor.motor_asyncio import AsyncIOMotorClient
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import API_ID, API_HASH, MONGO_URI
from proxy_manager import get_proxy
from datetime import datetime, timezone

_db = None

def get_db():
    global _db
    if _db is None:
        client = AsyncIOMotorClient(MONGO_URI)
        _db = client["reportbot"]
    return _db

async def save_session(user_id: int, phone: str, session_string: str):
    db = get_db()
    await db.sessions.update_one(
        {"phone": phone, "owner": user_id},
        {"$set": {"phone": phone, "session": session_string, "owner": user_id,
                  "active": True, "status_checked_at": datetime.now(timezone.utc)}},
        upsert=True
    )

async def get_all_sessions() -> list[dict]:
    db = get_db()
    return await db.sessions.find({"active": True}).to_list(length=None)

async def get_user_sessions(user_id: int) -> list[dict]:
    db = get_db()
    return await db.sessions.find({"owner": user_id, "active": True}).to_list(length=None)

async def delete_session(phone: str, user_id: int) -> bool:
    db = get_db()
    result = await db.sessions.delete_one({"phone": phone, "owner": user_id})
    return result.deleted_count > 0

async def mark_expired(phone: str):
    db = get_db()
    await db.sessions.update_one({"phone": phone}, {"$set": {"active": False, "status_checked_at": datetime.now(timezone.utc)}})

async def mark_active(phone: str):
    db = get_db()
    await db.sessions.update_one({"phone": phone}, {"$set": {"active": True, "status_checked_at": datetime.now(timezone.utc)}})

async def get_expired_sessions() -> list[dict]:
    db = get_db()
    return await db.sessions.find({"active": False}).sort("phone", 1).to_list(length=None)

async def refresh_all_sessions() -> dict:
    """Verify saved sessions; transient network errors do not change stored status."""
    db = get_db()
    records = await db.sessions.find({}).to_list(length=None)
    result = {"active": 0, "expired": 0, "checked": 0, "errors": 0}
    for index, item in enumerate(records):
        client = None
        try:
            client = await build_client(item["session"], index)
            await client.connect()
            authorized = await client.is_user_authorized()
            if authorized:
                await mark_active(item["phone"])
                result["active"] += 1
            else:
                await mark_expired(item["phone"])
                result["expired"] += 1
            result["checked"] += 1
        except Exception:
            result["errors"] += 1
        finally:
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass
    return result

async def get_total_count() -> int:
    db = get_db()
    return await db.sessions.count_documents({})

async def get_active_count() -> int:
    db = get_db()
    return await db.sessions.count_documents({"active": True})

async def get_expired_count() -> int:
    db = get_db()
    return await db.sessions.count_documents({"active": False})

async def build_client(session_str: str, index: int) -> TelegramClient:
    proxy = get_proxy(index)
    client = TelegramClient(
        StringSession(session_str),
        API_ID, API_HASH,
        proxy=proxy
    )
    return client
