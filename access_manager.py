from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI, OWNER_ID

_client = None

def _collection():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(MONGO_URI)
    return _client["reportbot"]["access"]

def is_owner(user_id: int) -> bool:
    return bool(OWNER_ID and int(user_id) == OWNER_ID)

async def remember_user(user) -> None:
    if not user:
        return
    await _collection().update_one(
        {"user_id": int(user.id)},
        {"$set": {"user_id": int(user.id), "first_name": user.first_name or "",
                  "last_name": user.last_name or "", "username": user.username or ""},
         "$setOnInsert": {"role": "user"}},
        upsert=True,
    )

async def is_sudo(user_id: int) -> bool:
    doc = await _collection().find_one({"user_id": int(user_id), "role": "sudo"})
    return bool(doc)

async def add_sudo(user_id: int, actor_id: int) -> bool:
    if is_owner(user_id):
        return False
    result = await _collection().update_one(
        {"user_id": int(user_id)},
        {"$set": {"user_id": int(user_id), "role": "sudo", "granted_by": int(actor_id)}},
        upsert=True,
    )
    return bool(result.acknowledged)

async def remove_sudo(user_id: int) -> bool:
    result = await _collection().delete_one({"user_id": int(user_id), "role": "sudo"})
    return result.deleted_count > 0

async def list_access_users() -> list[dict]:
    return await _collection().find({"role": "sudo"}).sort("user_id", 1).to_list(length=None)
