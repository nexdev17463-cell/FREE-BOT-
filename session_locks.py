import asyncio

# One in-process lock per saved Telegram account. Locks are shared by refresh
# and reporter workers so a session is never connected by two jobs at once.
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}


def get_session_lock(phone: str) -> asyncio.Lock:
    key = str(phone).strip()
    lock = _SESSION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SESSION_LOCKS[key] = lock
    return lock
