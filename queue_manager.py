import json
import logging
import os
import threading
from datetime import datetime, timezone, timedelta
from config import QUEUE_FILE, MAX_QUEUE_SIZE

_lock = threading.Lock()
log = logging.getLogger(__name__)


def _load() -> list:
    if not os.path.exists(QUEUE_FILE):
        return []
    try:
        with open(QUEUE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error("Failed to load queue file, returning empty: %s", e)
        return []


def _save(q: list):
    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
    tmp = QUEUE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(q, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, QUEUE_FILE)


def push(text: str, chat_id: int, message_id: int, force_new: bool = False) -> str | None:
    """Add task to queue. Returns task_id, or None if queue is full (rate limit)."""
    with _lock:
        q = _load()
        pending = sum(1 for t in q if t["status"] in ("pending", "running"))
        if pending >= MAX_QUEUE_SIZE:
            log.warning("Queue full (%d/%d), rejecting task from chat %d", pending, MAX_QUEUE_SIZE, chat_id)
            return None
        task_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{len(q)}"
        q.append({
            "id": task_id,
            "text": text,
            "chat_id": chat_id,
            "message_id": message_id,
            "force_new": force_new,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        _save(q)
        log.info("Task queued: %s (chat=%d, queue=%d/%d)", task_id, chat_id, pending + 1, MAX_QUEUE_SIZE)
        return task_id


def set_status(task_id: str, status: str):
    with _lock:
        q = _load()
        for t in q:
            if t["id"] == task_id:
                t["status"] = status
                t["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save(q)
        log.debug("Task %s → %s", task_id, status)


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
                t["updated_at"] = datetime.now(timezone.utc).isoformat()
                _save(q)
                log.info("Task claimed: %s", t["id"])
                return t
        return None


def reset_running_to_pending():
    """Mark any stuck 'running' tasks as 'pending' — call on startup after unclean shutdown."""
    with _lock:
        q = _load()
        reset = [t for t in q if t["status"] == "running"]
        for t in reset:
            t["status"] = "pending"
            t["updated_at"] = datetime.now(timezone.utc).isoformat()
        if reset:
            _save(q)
            log.warning("Reset %d stuck 'running' task(s) to 'pending' on startup", len(reset))


def cleanup_old_tasks(max_age_days: int) -> int:
    """Remove done/error tasks older than max_age_days. Returns count removed."""
    with _lock:
        q = _load()
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        before = len(q)
        q = [
            t for t in q
            if t["status"] in ("pending", "running")
            or datetime.fromisoformat(t.get("updated_at", t["created_at"])) > cutoff
        ]
        removed = before - len(q)
        if removed:
            _save(q)
            log.info("Cleanup: removed %d old task(s) (>%d days)", removed, max_age_days)
        return removed


def is_running() -> bool:
    return any(t["status"] == "running" for t in _load())


def next_pending() -> dict | None:
    for t in _load():
        if t["status"] == "pending":
            return t
    return None
