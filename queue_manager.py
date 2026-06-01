import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from config import QUEUE_FILE, MAX_QUEUE_SIZE, PER_CHAT_QUEUE_LIMIT

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
        session_id  TEXT,
        created_at  TEXT NOT NULL,
        updated_at  TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_status ON tasks(status);
"""

_migration_lock = threading.Lock()
_migrated = False


def _migrate(conn: sqlite3.Connection) -> None:
    """One-time, idempotent migration for DBs created before the session_id column.

    Runs once per process (double-checked under its own lock, separate from
    _lock to avoid deadlocking callers that already hold it). The session_id
    index lives here — not in _SCHEMA — so executescript() never references the
    column on an old DB before this ALTER adds it.
    """
    global _migrated
    if _migrated:
        return
    with _migration_lock:
        if _migrated:
            return
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
        if "session_id" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN session_id TEXT")
            log.info("Migrated tasks table: added session_id column")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON tasks(session_id)")
        conn.commit()
        _migrated = True


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
    conn = sqlite3.connect(QUEUE_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
    except sqlite3.DatabaseError as e:
        log.critical("Queue database corrupted — all queued tasks lost. Resetting: %s", e)
        conn.close()
        os.remove(QUEUE_FILE)
        conn = sqlite3.connect(QUEUE_FILE, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["force_new"] = bool(d["force_new"])
    return d


def push(
    text: str, chat_id: int, message_id: int,
    force_new: bool = False, session_id: str | None = None,
) -> str | None:
    """Add task to queue. Returns task_id, or None if queue is full (rate limit)."""
    with _lock:
        conn = _get_conn()
        try:
            pending_running = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status IN ('pending','running')"
            ).fetchone()[0]
            if pending_running >= MAX_QUEUE_SIZE:
                log.warning("Global queue full (%d/%d), rejecting task from chat %d",
                            pending_running, MAX_QUEUE_SIZE, chat_id)
                return None
            chat_active = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE chat_id=? AND status IN ('pending','running')",
                (chat_id,),
            ).fetchone()[0]
            if chat_active >= PER_CHAT_QUEUE_LIMIT:
                log.warning("Per-chat limit (%d) reached for chat %d",
                            PER_CHAT_QUEUE_LIMIT, chat_id)
                return None
            task_id = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
            now = datetime.now(timezone.utc).isoformat()
            with conn:
                conn.execute(
                    "INSERT INTO tasks"
                    " (id, text, chat_id, message_id, force_new, status, session_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (task_id, text, chat_id, message_id, int(force_new), session_id, now),
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


def set_session_id(task_id: str, session_id: str):
    """Backfill the session a task actually ran in.

    For /new and implicit --continue tasks the session is unknown at enqueue
    time (it's resolved by Claude only after the run), so push() stores NULL.
    The worker calls this once the real session id is known so the task shows
    up under /history <session_id>.
    """
    with _lock:
        conn = _get_conn()
        try:
            with conn:
                conn.execute(
                    "UPDATE tasks SET session_id=? WHERE id=?",
                    (session_id, task_id),
                )
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


def get_recent_tasks(chat_id: int, limit: int = 10) -> list[dict]:
    """Return the most recent tasks for a chat, newest first."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE chat_id=? ORDER BY created_at DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_tasks_for_session(session_id: str, chat_id: int, limit: int = 20) -> list[dict]:
    """Return tasks linked to a specific session for a chat, newest first."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE session_id=? AND chat_id=?"
            " ORDER BY created_at DESC LIMIT ?",
            (session_id, chat_id, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_running_task() -> dict | None:
    """Return the current running task, or None if nothing is running."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM tasks WHERE status='running' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()
