import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from config import SESSION_FILE

_lock = threading.Lock()
log = logging.getLogger(__name__)

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,128}$")


def _load() -> dict:
    if not os.path.exists(SESSION_FILE):
        return {"chats": {}}
    try:
        with open(SESSION_FILE) as f:
            data = json.load(f)
        if "chats" not in data:
            # Old single-chat format — start fresh
            log.warning("Session file has old format, resetting to per-chat storage.")
            return {"chats": {}}
        return data
    except (json.JSONDecodeError, OSError) as e:
        log.error("Failed to load session file, returning empty: %s", e)
        return {"chats": {}}


def _save(data: dict):
    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
    tmp = SESSION_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, SESSION_FILE)


def _chat(data: dict, chat_id: int) -> dict:
    key = str(chat_id)
    if key not in data["chats"]:
        data["chats"][key] = {"active_id": None, "sessions": []}
    return data["chats"][key]


def _is_valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID_RE.match(session_id))


def get_active_id(chat_id: int) -> str | None:
    return _chat(_load(), chat_id).get("active_id")


def get_all(chat_id: int) -> list[dict]:
    return list(reversed(_chat(_load(), chat_id).get("sessions", [])))


def exists(session_id: str, chat_id: int) -> bool:
    return any(s["id"] == session_id for s in _chat(_load(), chat_id)["sessions"])


def set_active(session_id: str | None, chat_id: int):
    with _lock:
        data = _load()
        _chat(data, chat_id)["active_id"] = session_id
        _save(data)


def register(session_id: str, chat_id: int, label: str = ""):
    if not _is_valid_session_id(session_id):
        log.warning("Rejected invalid session_id from Claude output: %r", session_id[:64])
        return
    with _lock:
        data = _load()
        chat = _chat(data, chat_id)
        if any(s["id"] == session_id for s in chat["sessions"]):
            chat["active_id"] = session_id
            _save(data)
            return
        chat["sessions"].append({
            "id": session_id,
            "label": label or session_id[:8],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "task_count": 1,
        })
        chat["active_id"] = session_id
        _save(data)
        log.info("Session registered for chat %d: %s", chat_id, session_id[:12])


def increment_task_count(session_id: str, chat_id: int):
    with _lock:
        data = _load()
        for s in _chat(data, chat_id)["sessions"]:
            if s["id"] == session_id:
                s["task_count"] = s.get("task_count", 0) + 1
                s["last_used"] = datetime.now(timezone.utc).isoformat()
        _save(data)


def set_label(session_id: str, label: str, chat_id: int):
    with _lock:
        data = _load()
        for s in _chat(data, chat_id)["sessions"]:
            if s["id"] == session_id:
                s["label"] = label
        _save(data)


def build_claude_args(chat_id: int, force_new: bool = False) -> list[str]:
    if force_new:
        return []
    active = get_active_id(chat_id)
    if active:
        return ["--resume", active]
    return ["--continue"]
