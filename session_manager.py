from motor.motor_asyncio import AsyncIOMotorClient
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import API_ID, API_HASH, MONGO_URI
from datetime import datetime, timezone

_db = None

def get_db():
    global _db
    if _db is None:
        _db = AsyncIOMotorClient(MONGO_URI)["reportbot"]
    return _db

async def save_session(user_id: int, phone: str, session_string: str):
    await get_db().sessions.update_one(
        {"phone": phone},
        {"$set": {"phone": phone, "session": session_string,
                  "owner": user_id, "active": True,
                  "status_checked_at": datetime.now(timezone.utc)}},
        upsert=True
    )

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

async def refresh_all_sessions() -> dict:
    records = await get_db().sessions.find({}).to_list(length=None)
    result = {"active": 0, "expired": 0, "checked": 0, "errors": 0}
    for i, item in enumerate(records):
        client = None
        try:
            client = await build_client(item["session"])
            await client.connect()
            if await client.is_user_authorized():
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
    return await get_db().sessions.count_documents({})

async def get_active_count() -> int:
    return await get_db().sessions.count_documents({"active": True})

async def get_expired_count() -> int:
    return await get_db().sessions.count_documents({"active": False})

async def build_client(session_str: str, index: int = 0) -> TelegramClient:
    return TelegramClient(
        StringSession(session_str),
        API_ID, API_HASH,
        connection_retries=1,
        retry_delay=1,
        timeout=15,
    )
