import asyncio
import re
import logging
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.functions.messages import ReportRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest as TelethonImportInvite
from telethon.errors import (
    UserAlreadyParticipantError as TelethonAlreadyParticipant,
    FloodWaitError, UserDeactivatedError,
    AuthKeyUnregisteredError, SessionRevokedError,
)
from telethon.tl.types import (
    InputReportReasonSpam, InputReportReasonViolence,
    InputReportReasonPornography, InputReportReasonChildAbuse,
    InputReportReasonCopyright, InputReportReasonOther,
    InputReportReasonFake, InputReportReasonIllegalDrugs,
    InputReportReasonPersonalDetails, PeerChannel,
)
from session_manager import mark_expired

logger = logging.getLogger(__name__)

# Telethon reason map
TELETHON_REASON_MAP = {
    "1":  InputReportReasonSpam(),
    "2a": InputReportReasonChildAbuse(), "2b": InputReportReasonChildAbuse(),
    "3a": InputReportReasonViolence(),   "3b": InputReportReasonViolence(),
    "3c": InputReportReasonViolence(),   "3d": InputReportReasonViolence(),
    "3e": InputReportReasonViolence(),   "3f": InputReportReasonViolence(),
    "4a": InputReportReasonOther(),      "4b": InputReportReasonIllegalDrugs(),
    "4c": InputReportReasonFake(),       "4d": InputReportReasonFake(),
    "4e": InputReportReasonOther(),      "4f": InputReportReasonOther(),
    "4g": InputReportReasonOther(),
    "5a": InputReportReasonChildAbuse(), "5b": InputReportReasonPornography(),
    "5c": InputReportReasonPornography(),"5d": InputReportReasonPornography(),
    "5e": InputReportReasonPornography(),"5f": InputReportReasonPornography(),
    "6a": InputReportReasonPersonalDetails(), "6b": InputReportReasonPersonalDetails(),
    "6c": InputReportReasonPersonalDetails(), "6d": InputReportReasonPersonalDetails(),
    "6e": InputReportReasonPersonalDetails(),
    "7a": InputReportReasonFake(),  "7b": InputReportReasonFake(),
    "7c": InputReportReasonOther(), "7d": InputReportReasonOther(),
    "8":  InputReportReasonCopyright(),
    "9":  InputReportReasonOther(),
    "10": InputReportReasonOther(),
}

# Pyrogram reason map
def get_pyro_reason(reason_key: str):
    from pyrogram.raw.types import (
        InputReportReasonSpam as PyroSpam,
        InputReportReasonViolence as PyroViolence,
        InputReportReasonPornography as PyroPorn,
        InputReportReasonChildAbuse as PyroChild,
        InputReportReasonCopyright as PyroCopy,
        InputReportReasonOther as PyroOther,
        InputReportReasonFake as PyroFake,
        InputReportReasonIllegalDrugs as PyroDrugs,
        InputReportReasonPersonalDetails as PyroPersonal,
    )
    pyro_map = {
        "1":  PyroSpam(),
        "2a": PyroChild(), "2b": PyroChild(),
        "3a": PyroViolence(), "3b": PyroViolence(),
        "3c": PyroViolence(), "3d": PyroViolence(),
        "3e": PyroViolence(), "3f": PyroViolence(),
        "4a": PyroOther(),   "4b": PyroDrugs(),
        "4c": PyroFake(),    "4d": PyroFake(),
        "4e": PyroOther(),   "4f": PyroOther(),
        "4g": PyroOther(),
        "5a": PyroChild(),   "5b": PyroPorn(),
        "5c": PyroPorn(),    "5d": PyroPorn(),
        "5e": PyroPorn(),    "5f": PyroPorn(),
        "6a": PyroPersonal(), "6b": PyroPersonal(),
        "6c": PyroPersonal(), "6d": PyroPersonal(),
        "6e": PyroPersonal(),
        "7a": PyroFake(),    "7b": PyroFake(),
        "7c": PyroOther(),   "7d": PyroOther(),
        "8":  PyroCopy(),
        "9":  PyroOther(),
        "10": PyroOther(),
    }
    return pyro_map.get(reason_key, PyroSpam())

SESSION_ERRORS = (UserDeactivatedError, AuthKeyUnregisteredError, SessionRevokedError)


def parse_message_link(target: str):
    # Private FIRST — avoids 'c' being captured as username
    m = re.match(r"https?://t\.me/c/(\d+)/(\d+)", target)
    if m:
        return int("-100" + m.group(1)), int(m.group(2))
    m = re.match(r"https?://t\.me/([a-zA-Z0-9_]+)/(\d+)", target)
    if m:
        return m.group(1), int(m.group(2))
    return None, None


def is_invite_link(target: str) -> bool:
    return bool(re.match(r"https?://t\.me/\+[a-zA-Z0-9_-]+", target))


def extract_invite_hash(target: str) -> str:
    m = re.match(r"https?://t\.me/\+([a-zA-Z0-9_-]+)", target)
    return m.group(1) if m else target.lstrip("+")


# ── Telethon entity resolution ───────────────────────────────────────────────
async def _telethon_get_entity(client, chat_id):
    for attempt in range(2):
        try:
            return await client.get_entity(chat_id)
        except Exception:
            if attempt == 0:
                try:
                    await client.get_dialogs(limit=200)
                except Exception:
                    pass
    if isinstance(chat_id, int) and chat_id < 0:
        try:
            raw_id = int(str(abs(chat_id))[3:])
            return await client.get_entity(PeerChannel(raw_id))
        except Exception as e:
            raise ValueError(f"Cannot resolve private channel: {e}")
    raise ValueError(f"Cannot resolve entity: {chat_id}")


# ── Telethon report ──────────────────────────────────────────────────────────
async def _report_telethon(client, target: str, reason_key: str, report_message: str) -> tuple[bool, str]:
    reason = TELETHON_REASON_MAP.get(reason_key, InputReportReasonSpam())
    try:
        chat_id, msg_id = parse_message_link(target)
        if chat_id is not None:
            entity = await _telethon_get_entity(client, chat_id)
            result = await client(ReportRequest(
                peer=entity, id=[msg_id], reason=reason, message=report_message
            ))
        else:
            entity = await _telethon_get_entity(client, target)
            result = await client(ReportPeerRequest(
                peer=entity, reason=reason, message=report_message
            ))
        return (True, "") if result else (False, "rejected_by_telegram")
    except SESSION_ERRORS as e:
        raise ConnectionError("session_expired") from e
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
        return False, f"flood_wait_{e.seconds}s"
    except ConnectionError:
        raise
    except Exception as e:
        logger.error(f"telethon report error [{target}]: {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {e}"


# ── Pyrogram report ──────────────────────────────────────────────────────────
async def _report_pyrogram(client, target: str, reason_key: str, report_message: str) -> tuple[bool, str]:
    from pyrogram.raw.functions.account import ReportPeer
    from pyrogram.raw.functions.messages import Report as ReportMsg
    from pyrogram.errors import FloodWait, AuthKeyUnregistered, UserDeactivated, SessionRevoked
    try:
        reason = get_pyro_reason(reason_key)
        chat_id, msg_id = parse_message_link(target)

        if chat_id is not None:
            # Try to get peer, populate cache if needed
            try:
                peer = await client.resolve_peer(chat_id)
            except Exception:
                try:
                    await client.get_dialogs()
                    peer = await client.resolve_peer(chat_id)
                except Exception as e:
                    return False, f"entity_error: {e}"

            result = await client.invoke(ReportMsg(
                peer=peer,
                id=[msg_id],
                reason=reason,
                message=report_message,
            ))
        else:
            try:
                peer = await client.resolve_peer(target)
            except Exception:
                try:
                    await client.get_dialogs()
                    peer = await client.resolve_peer(target)
                except Exception as e:
                    return False, f"entity_error: {e}"

            result = await client.invoke(ReportPeer(
                peer=peer,
                reason=reason,
                message=report_message,
            ))

        return (True, "") if result else (False, "rejected_by_telegram")

    except (AuthKeyUnregistered, UserDeactivated, SessionRevoked) as e:
        raise ConnectionError("session_expired") from e
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return False, f"flood_wait_{e.value}s"
    except ConnectionError:
        raise
    except Exception as e:
        logger.error(f"pyrogram report error [{target}]: {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {e}"


# ── Unified report_once ──────────────────────────────────────────────────────
async def report_once(client, target: str, reason_key: str, report_message: str) -> tuple[bool, str]:
    from pyrogram import Client as PyroClient
    if isinstance(client, PyroClient):
        return await _report_pyrogram(client, target, reason_key, report_message)
    return await _report_telethon(client, target, reason_key, report_message)


# ── mass_report ──────────────────────────────────────────────────────────────
async def mass_report(
    sessions: list[dict],
    target: str,
    reason_key: str,
    count_per_id: int,
    report_message: str,
    progress_cb=None,
) -> dict:
    from session_manager import build_client
    from pyrogram import Client as PyroClient

    results = {
        "success": 0, "failed": 0, "expired": 0,
        "total_expected": len(sessions) * count_per_id,
        "errors": [],
    }

    async def worker(sdata: dict):
        phone = sdata["phone"]
        client = None
        is_pyro = sdata.get("client_type", "telethon") == "pyrogram"
        try:
            client = await build_client(sdata)
            if is_pyro:
                await client.start()
                me = await client.get_me()
                authorized = me is not None
            else:
                await client.connect()
                authorized = await client.is_user_authorized()

            if not authorized:
                await mark_expired(phone)
                results["expired"] += 1
                if progress_cb:
                    await progress_cb(results)
                return

            for _ in range(count_per_id):
                try:
                    ok, err = await report_once(client, target, reason_key, report_message)
                    if ok:
                        results["success"] += 1
                    else:
                        results["failed"] += 1
                        if err and err not in results["errors"]:
                            results["errors"].append(err)
                except ConnectionError:
                    await mark_expired(phone)
                    results["expired"] += 1
                    break
                finally:
                    if progress_cb:
                        await progress_cb(results)
                await asyncio.sleep(1)

        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            logger.error(f"worker failed [{phone}]: {err}")
            results["failed"] += count_per_id
            if err not in results["errors"]:
                results["errors"].append(err)
            if progress_cb:
                await progress_cb(results)
        finally:
            if client:
                try:
                    if is_pyro:
                        await client.stop()
                    else:
                        await client.disconnect()
                except Exception:
                    pass

    await asyncio.gather(*[worker(s) for s in sessions])
    return results


# ── mass_join ────────────────────────────────────────────────────────────────
async def mass_join(sessions: list[dict], target: str, progress_cb=None) -> dict:
    from session_manager import build_client
    from pyrogram import Client as PyroClient
    from pyrogram.errors import UserAlreadyParticipant as PyroAlreadyParticipant

    results = {"joined": 0, "already": 0, "failed": 0}

    async def worker(sdata: dict):
        phone = sdata["phone"]
        client = None
        is_pyro = sdata.get("client_type", "telethon") == "pyrogram"
        try:
            client = await build_client(sdata)
            if is_pyro:
                await client.start()
                authorized = await client.get_me() is not None
            else:
                await client.connect()
                authorized = await client.is_user_authorized()

            if not authorized:
                await mark_expired(phone)
                results["failed"] += 1
                if progress_cb:
                    await progress_cb(results)
                return

            try:
                if is_pyro:
                    if is_invite_link(target):
                        await client.join_chat(extract_invite_hash(target))
                    else:
                        await client.join_chat(target)
                else:
                    if is_invite_link(target):
                        await client(TelethonImportInvite(extract_invite_hash(target)))
                    else:
                        await client(JoinChannelRequest(target))
                results["joined"] += 1

            except (TelethonAlreadyParticipant, PyroAlreadyParticipant):
                results["already"] += 1
            except Exception as e:
                err = str(e).lower()
                if "already" in err or "participant" in err:
                    results["already"] += 1
                else:
                    results["failed"] += 1
                    logger.warning(f"join [{phone}]: {e}")

        except Exception as e:
            results["failed"] += 1
            logger.warning(f"worker [{phone}]: {e}")
        finally:
            if client:
                try:
                    if is_pyro:
                        await client.stop()
                    else:
                        await client.disconnect()
                except Exception:
                    pass
        if progress_cb:
            await progress_cb(results)

    await asyncio.gather(*[worker(s) for s in sessions])
    return results
