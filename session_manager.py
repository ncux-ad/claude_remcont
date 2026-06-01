import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from config import SESSION_FILE

_lock = threading.Lock()
log = logging.getLogger(__name__)

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,128}$")

_SCHEMA = """
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS sessions (
        id          TEXT NOT NULL,
        chat_id     INTEGER NOT NULL,
        label       TEXT NOT NULL DEFAULT '',
        task_count  INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL,
        last_used   TEXT NOT NULL,
        PRIMARY KEY (id, chat_id)
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_chat ON sessions(chat_id);
    CREATE TABLE IF NOT EXISTS active_sessions (
        chat_id     INTEGER PRIMARY KEY,
        session_id  TEXT
    );
"""


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
    conn = sqlite3.connect(SESSION_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
    except sqlite3.DatabaseError as e:
        log.critical("Session database corrupted — all sessions lost. Resetting: %s", e)
        conn.close()
        os.remove(SESSION_FILE)
        conn = sqlite3.connect(SESSION_FILE, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
    return conn


def _is_valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID_RE.match(session_id))


def get_active_id(chat_id: int) -> str | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT session_id FROM active_sessions WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return row["session_id"] if row else None
    finally:
        conn.close()


def get_all(chat_id: int) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE chat_id=? ORDER BY last_used DESC",
            (chat_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def exists(session_id: str, chat_id: int) -> bool:
    conn = _get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE id=? AND chat_id=?",
            (session_id, chat_id),
        ).fetchone()[0] > 0
    finally:
        conn.close()


def set_active(session_id: str | None, chat_id: int):
    with _lock:
        conn = _get_conn()
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)",
                    (chat_id, session_id),
                )
        finally:
            conn.close()


def register(session_id: str, chat_id: int, label: str = ""):
    if not _is_valid_session_id(session_id):
        log.warning("Rejected invalid session_id from Claude output: %r", session_id[:64])
        return
    with _lock:
        conn = _get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            with conn:
                existing = conn.execute(
                    "SELECT id FROM sessions WHERE id=? AND chat_id=?",
                    (session_id, chat_id),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE sessions SET last_used=? WHERE id=? AND chat_id=?",
                        (now, session_id, chat_id),
                    )
                else:
                    conn.execute(
                        "INSERT INTO sessions (id, chat_id, label, task_count, created_at, last_used)"
                        " VALUES (?, ?, ?, 1, ?, ?)",
                        (session_id, chat_id, label or session_id[:8], now, now),
                    )
                    log.info("Session registered for chat %d: %s", chat_id, session_id[:12])
                conn.execute(
                    "INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)",
                    (chat_id, session_id),
                )
        finally:
            conn.close()


def increment_task_count(session_id: str, chat_id: int):
    with _lock:
        conn = _get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            with conn:
                conn.execute(
                    "UPDATE sessions SET task_count=task_count+1, last_used=?"
                    " WHERE id=? AND chat_id=?",
                    (now, session_id, chat_id),
                )
        finally:
            conn.close()


def set_label(session_id: str, label: str, chat_id: int):
    with _lock:
        conn = _get_conn()
        try:
            with conn:
                conn.execute(
                    "UPDATE sessions SET label=? WHERE id=? AND chat_id=?",
                    (label, session_id, chat_id),
                )
        finally:
            conn.close()


def cleanup_old_sessions(max_age_days: int) -> int:
    """Remove sessions unused for max_age_days, except active ones. Returns count removed."""
    with _lock:
        conn = _get_conn()
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
            with conn:
                cur = conn.execute(
                    "DELETE FROM sessions WHERE last_used < ?"
                    " AND NOT EXISTS ("
                    "  SELECT 1 FROM active_sessions"
                    "  WHERE active_sessions.session_id = sessions.id"
                    "  AND active_sessions.chat_id = sessions.chat_id"
                    " )",
                    (cutoff,),
                )
            removed = cur.rowcount
            if removed:
                log.info("Cleanup: removed %d old session(s)", removed)
            return removed
        finally:
            conn.close()


def build_claude_args(chat_id: int, force_new: bool = False) -> list[str]:
    if force_new:
        return []
    active = get_active_id(chat_id)
    if active:
        return ["--resume", active]
    return ["--continue"]
