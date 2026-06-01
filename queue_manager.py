import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from config import QUEUE_FILE, MAX_QUEUE_SIZE

_lock = threading.Lock()
log = logging.getLogger(__name__)


_SCHEMA = """
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS tasks (
        id          TEXT PRIMARY KEY,
        text        TEXT NOT NULL,
        chat_id     INTEGER NOT NULL,
        message_id  INTEGER NOT NULL,
        force_new   INTEGER NOT NULL DEFAULT 0,
        status      TEXT NOT NULL DEFAULT 'pending',
        created_at  TEXT NOT NULL,
        updated_at  TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_status ON tasks(status);
"""


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
    conn = sqlite3.connect(QUEUE_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
    except sqlite3.DatabaseError as e:
        log.error("Queue database corrupted, resetting: %s", e)
        conn.close()
        os.remove(QUEUE_FILE)
        conn = sqlite3.connect(QUEUE_FILE, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["force_new"] = bool(d["force_new"])
    return d


def push(text: str, chat_id: int, message_id: int, force_new: bool = False) -> str | None:
    """Add task to queue. Returns task_id, or None if queue is full (rate limit)."""
    with _lock:
        conn = _get_conn()
        try:
            pending_running = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status IN ('pending','running')"
            ).fetchone()[0]
            if pending_running >= MAX_QUEUE_SIZE:
                log.warning("Queue full (%d/%d), rejecting task from chat %d",
                            pending_running, MAX_QUEUE_SIZE, chat_id)
                return None
            total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            task_id = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + f"_{total}"
            now = datetime.now(timezone.utc).isoformat()
            with conn:
                conn.execute(
                    "INSERT INTO tasks (id, text, chat_id, message_id, force_new, status, created_at)"
                    " VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                    (task_id, text, chat_id, message_id, int(force_new), now),
                )
            log.info("Task queued: %s (chat=%d, queue=%d/%d)",
                     task_id, chat_id, pending_running + 1, MAX_QUEUE_SIZE)
            return task_id
        finally:
            conn.close()


def set_status(task_id: str, status: str):
    with _lock:
        conn = _get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            with conn:
                conn.execute(
                    "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                    (status, now, task_id),
                )
            log.debug("Task %s → %s", task_id, status)
        finally:
            conn.close()


def claim_next_pending() -> dict | None:
    """Atomically claim the next pending task only if nothing is currently running."""
    with _lock:
        conn = _get_conn()
        try:
            running = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status='running'"
            ).fetchone()[0]
            if running > 0:
                return None
            row = conn.execute(
                "SELECT * FROM tasks WHERE status='pending' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                return None
            now = datetime.now(timezone.utc).isoformat()
            with conn:
                conn.execute(
                    "UPDATE tasks SET status='running', updated_at=? WHERE id=?",
                    (now, row["id"]),
                )
            task = _row_to_dict(row)
            task["status"] = "running"
            log.info("Task claimed: %s", task["id"])
            return task
        finally:
            conn.close()


def reset_running_to_pending():
    """Mark any stuck 'running' tasks as 'pending' — call on startup after unclean shutdown."""
    with _lock:
        conn = _get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            with conn:
                cur = conn.execute(
                    "UPDATE tasks SET status='pending', updated_at=? WHERE status='running'",
                    (now,),
                )
            if cur.rowcount:
                log.warning("Reset %d stuck 'running' task(s) to 'pending' on startup",
                            cur.rowcount)
        finally:
            conn.close()


def get_stats() -> dict:
    """Return task counts grouped by status."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM tasks GROUP BY status"
        ).fetchall()
        counts: dict[str, int] = {"pending": 0, "running": 0, "done": 0, "error": 0}
        for row in rows:
            counts[row["status"]] = row["cnt"]
        return counts
    finally:
        conn.close()


def cleanup_old_tasks(max_age_days: int) -> int:
    """Remove done/error tasks older than max_age_days. Returns count removed."""
    with _lock:
        conn = _get_conn()
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
            with conn:
                cur = conn.execute(
                    "DELETE FROM tasks WHERE status IN ('done','error')"
                    " AND COALESCE(updated_at, created_at) < ?",
                    (cutoff,),
                )
            removed = cur.rowcount
            if removed:
                log.info("Cleanup: removed %d old task(s) (>%d days)", removed, max_age_days)
            return removed
        finally:
            conn.close()


def is_running() -> bool:
    conn = _get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status='running'"
        ).fetchone()[0] > 0
    finally:
        conn.close()


def next_pending() -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM tasks WHERE status='pending' ORDER BY created_at LIMIT 1"
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_running_task() -> dict | None:
    """Return the current running task, or the most recent task if none is running."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM tasks WHERE status='running' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row:
            return _row_to_dict(row)
        row = conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()
