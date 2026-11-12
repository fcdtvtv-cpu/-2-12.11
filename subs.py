import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SUBS_FILE = DATA_DIR / "subscriptions.json"
ADMINS_FILE = DATA_DIR / "admins.json"
CONNECTIONS_FILE = DATA_DIR / "connections.json"

MASTER_ADMIN_ID = 7234362765


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading {path}: {e}")
        return default


def _write_json(path: Path, data):
    tmp_path = path.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
    except Exception as e:
        logger.error(f"Error writing {path}: {e}")
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Admin Management
# ---------------------------------------------------------------------------

def get_admins_data() -> dict:
    default = {"user_ids": [MASTER_ADMIN_ID], "usernames": []}
    data = _read_json(ADMINS_FILE, default)
    if isinstance(data, list):
        data = {
            "user_ids": [int(x) for x in data if str(x).isdigit()],
            "usernames": [str(x).lower().lstrip("@") for x in data if not str(x).isdigit()],
        }
    if not isinstance(data, dict):
        data = default
    data.setdefault("user_ids", [])
    data.setdefault("usernames", [])
    if MASTER_ADMIN_ID not in data["user_ids"]:
        data["user_ids"].append(MASTER_ADMIN_ID)
    return data


def is_admin(user_id: int | None = None, username: str | None = None) -> bool:
    if user_id is not None and user_id == MASTER_ADMIN_ID:
        return True
    data = get_admins_data()
    if user_id is not None and user_id in data.get("user_ids", []):
        return True
    if username and username.lower().lstrip("@") in [u.lower() for u in data.get("usernames", [])]:
        return True
    return False


def add_admin(target_type: str, target_val: str) -> bool:
    data = get_admins_data()
    if target_type == "id":
        uid = int(target_val)
        if uid not in data["user_ids"]:
            data["user_ids"].append(uid)
            _write_json(ADMINS_FILE, data)
            return True
        return False
    else:
        uname = target_val.lower().lstrip("@")
        if uname not in data["usernames"]:
            data["usernames"].append(uname)
            _write_json(ADMINS_FILE, data)
            return True
        return False


def remove_admin(target_type: str, target_val: str) -> bool:
    data = get_admins_data()
    changed = False
    if target_type == "id":
        uid = int(target_val)
        if uid == MASTER_ADMIN_ID:
            return False  # Cannot remove master admin
        if uid in data["user_ids"]:
            data["user_ids"].remove(uid)
            changed = True
    else:
        uname = target_val.lower().lstrip("@")
        if uname in data["usernames"]:
            data["usernames"].remove(uname)
            changed = True

    if changed:
        _write_json(ADMINS_FILE, data)
    return changed


# ---------------------------------------------------------------------------
# Subscription Management
# ---------------------------------------------------------------------------

def parse_duration_string(raw: str) -> tuple[int, str]:
    """
    Parses duration string like '2 дня', '1 месяц', '24 часа', '30d', '1 year' into (seconds, readable_str).
    """
    s = raw.strip()
    m = re.match(r"^(\d+)\s*([a-zA-Zа-яА-ЯёЁ]*)$", s)
    if not m:
        return 0, ""

    count = int(m.group(1))
    unit = (m.group(2) or "").lower()

    if not unit or unit in ["д", "дн", "дня", "дней", "день", "d", "day", "days"]:
        sec = count * 86400
        readable = f"{count} дн."
    elif unit in ["ч", "час", "часа", "часов", "h", "hour", "hours"]:
        sec = count * 3600
        readable = f"{count} час."
    elif unit in ["м", "мес", "месяц", "месяца", "месяцев", "month", "months"]:
        sec = count * 30 * 86400
        readable = f"{count} мес."
    elif unit in ["г", "год", "года", "лет", "y", "year", "years"]:
        sec = count * 365 * 86400
        readable = f"{count} г."
    elif unit in ["мин", "минут", "минуты", "min", "minutes"]:
        sec = count * 60
        readable = f"{count} мин."
    else:
        sec = count * 86400
        readable = f"{count} дн."

    return sec, readable


def get_subscriptions() -> dict:
    return _read_json(SUBS_FILE, {})


def grant_subscription(user_id: int, duration_str: str, granted_by: int = MASTER_ADMIN_ID) -> tuple[bool, str, int]:
    seconds, readable = parse_duration_string(duration_str)
    if seconds <= 0:
        return False, "Неверный формат срока", 0

    subs = get_subscriptions()
    uid_str = str(user_id)
    entry = subs.get(uid_str, {})

    now = int(time.time())
    current_expiry = entry.get("expires_at", 0)
    # If already active, extend from current expiration
    start_point = max(now, current_expiry)
    new_expiry = start_point + seconds

    entry.update({
        "user_id": user_id,
        "granted_at": now,
        "expires_at": new_expiry,
        "duration_str": duration_str,
        "granted_by": granted_by,
    })
    subs[uid_str] = entry
    _write_json(SUBS_FILE, subs)
    return True, readable, new_expiry


def revoke_subscription(user_id: int) -> bool:
    subs = get_subscriptions()
    uid_str = str(user_id)
    if uid_str in subs:
        del subs[uid_str]
        _write_json(SUBS_FILE, subs)
        return True
    return False


def is_subscription_active(user_id: int | None, conn_id: str | None = None) -> bool:
    # Admins always have active access
    if user_id and is_admin(user_id=user_id):
        return True

    now = int(time.time())
    subs = get_subscriptions()

    if user_id and str(user_id) in subs:
        if subs[str(user_id)].get("expires_at", 0) > now:
            return True

    if conn_id:
        conns = _read_json(CONNECTIONS_FILE, {})
        for entry in conns.values():
            if entry.get("connection_id") == conn_id:
                c_uid = entry.get("user_id")
                if c_uid and is_admin(user_id=c_uid):
                    return True
                if c_uid and str(c_uid) in subs and subs[str(c_uid)].get("expires_at", 0) > now:
                    return True

    return False


def get_user_sub_info(user_id: int) -> dict | None:
    subs = get_subscriptions()
    uid_str = str(user_id)
    if uid_str in subs:
        entry = subs[uid_str]
        now = int(time.time())
        exp = entry.get("expires_at", 0)
        entry["is_active"] = exp > now
        entry["remaining_seconds"] = max(0, exp - now)
        return entry
    return None


# ---------------------------------------------------------------------------
# Business Connection Tracker
# ---------------------------------------------------------------------------

def save_connection(
    conn_id: str | None,
    user_id: int,
    username: str | None,
    full_name: str | None = None,
    is_enabled: bool = True,
    can_reply: bool | None = None,
    bot_username: str | None = None,
):
    conns = _read_json(CONNECTIONS_FILE, {})
    uid_str = str(user_id)
    entry = conns.get(uid_str, {})
    entry.update({
        "user_id": user_id,
        "username": username.lower().lstrip("@") if username else entry.get("username"),
        "full_name": full_name or entry.get("full_name", ""),
        "connection_id": conn_id or entry.get("connection_id"),
        "is_enabled": is_enabled,
        "can_reply": can_reply if can_reply is not None else entry.get("can_reply", True),
        "bot_username": bot_username or entry.get("bot_username", ""),
        "updated_at": int(time.time()),
    })
    conns[uid_str] = entry
    _write_json(CONNECTIONS_FILE, conns)


def get_connected_users() -> list[dict]:
    conns = _read_json(CONNECTIONS_FILE, {})
    user_list = list(conns.values())
    user_list.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    return user_list
