import json
import os
import threading
from datetime import datetime
from config import QUEUE_FILE

_lock = threading.Lock()


def _load() -> list:
    if not os.path.exists(QUEUE_FILE):
        return []
    try:
        with open(QUEUE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save(q: list):
    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
    tmp = QUEUE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(q, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, QUEUE_FILE)


def push(text: str, chat_id: int, message_id: int, force_new: bool = False) -> str:
    with _lock:
        q = _load()
        task_id = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{len(q)}"
        q.append({
            "id": task_id,
            "text": text,
            "chat_id": chat_id,
            "message_id": message_id,
            "force_new": force_new,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
        })
        _save(q)
        return task_id


def set_status(task_id: str, status: str):
    with _lock:
        q = _load()
        for t in q:
            if t["id"] == task_id:
                t["status"] = status
                t["updated_at"] = datetime.utcnow().isoformat()
        _save(q)


def claim_next_pending() -> dict | None:
    """Atomically claim the next pending task only if nothing is currently running.
    Eliminates the TOCTOU race condition between is_running() and next_pending()."""
    with _lock:
        q = _load()
        if any(t["status"] == "running" for t in q):
            return None
        for t in q:
            if t["status"] == "pending":
                t["status"] = "running"
                t["updated_at"] = datetime.utcnow().isoformat()
                _save(q)
                return t
        return None


def is_running() -> bool:
    return any(t["status"] == "running" for t in _load())


def next_pending() -> dict | None:
    for t in _load():
        if t["status"] == "pending":
            return t
    return None
