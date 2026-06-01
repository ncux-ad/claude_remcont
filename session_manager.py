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
        return {"active_id": None, "sessions": []}
    try:
        with open(SESSION_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error("Failed to load session file, returning empty: %s", e)
        return {"active_id": None, "sessions": []}


def _save(data: dict):
    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
    tmp = SESSION_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, SESSION_FILE)


def _is_valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID_RE.match(session_id))


def get_active_id() -> str | None:
    return _load().get("active_id")


def get_all() -> list[dict]:
    return list(reversed(_load().get("sessions", [])))


def exists(session_id: str) -> bool:
    return any(s["id"] == session_id for s in _load()["sessions"])


def set_active(session_id: str | None):
    with _lock:
        data = _load()
        data["active_id"] = session_id
        _save(data)


def register(session_id: str, label: str = ""):
    if not _is_valid_session_id(session_id):
        log.warning("Rejected invalid session_id from Claude output: %r", session_id[:64])
        return
    with _lock:
        data = _load()
        if any(s["id"] == session_id for s in data["sessions"]):
            data["active_id"] = session_id
            _save(data)
            return
        data["sessions"].append({
            "id": session_id,
            "label": label or session_id[:8],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "task_count": 1,
        })
        data["active_id"] = session_id
        _save(data)
        log.info("Session registered: %s", session_id[:12])


def increment_task_count(session_id: str):
    with _lock:
        data = _load()
        for s in data["sessions"]:
            if s["id"] == session_id:
                s["task_count"] = s.get("task_count", 0) + 1
                s["last_used"] = datetime.now(timezone.utc).isoformat()
        _save(data)


def set_label(session_id: str, label: str):
    with _lock:
        data = _load()
        for s in data["sessions"]:
            if s["id"] == session_id:
                s["label"] = label
        _save(data)


def build_claude_args(force_new: bool = False) -> list[str]:
    if force_new:
        return []
    active = get_active_id()
    if active:
        return ["--resume", active]
    return ["--continue"]
