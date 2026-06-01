import pytest
from datetime import datetime, timezone, timedelta
import queue_manager as qm
import session_manager as sm

CHAT = 100


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(qm, "QUEUE_FILE", str(tmp_path / ".queue.db"))
    monkeypatch.setattr(sm, "SESSION_FILE", str(tmp_path / "sessions.json"))
    import config
    monkeypatch.setattr(config, "QUEUE_FILE", str(tmp_path / ".queue.db"))
    monkeypatch.setattr(config, "SESSION_FILE", str(tmp_path / "sessions.json"))


def _old_ts(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _backdate_task(task_id: str, days: int):
    """Set updated_at to `days` ago directly via SQL."""
    ts = _old_ts(days)
    conn = qm._get_conn()
    try:
        with conn:
            conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (ts, task_id))
    finally:
        conn.close()


# --- Queue cleanup ---

def test_cleanup_removes_old_done_tasks():
    t1 = qm.push("task1", CHAT, 1)
    t2 = qm.push("task2", CHAT, 2)
    qm.set_status(t1, "done")
    _backdate_task(t1, days=10)

    removed = qm.cleanup_old_tasks(max_age_days=7)
    assert removed == 1
    stats = qm.get_stats()
    assert stats["done"] == 0
    assert stats["pending"] == 1


def test_cleanup_keeps_recent_done_tasks():
    t1 = qm.push("task1", CHAT, 1)
    qm.set_status(t1, "done")
    removed = qm.cleanup_old_tasks(max_age_days=7)
    assert removed == 0


def test_cleanup_never_removes_pending_or_running():
    t1 = qm.push("task1", CHAT, 1)
    _backdate_task(t1, days=30)
    removed = qm.cleanup_old_tasks(max_age_days=7)
    assert removed == 0


# --- Session cleanup ---

def test_cleanup_removes_old_sessions():
    sm.register("aaaa1111bbbb2222", CHAT)
    sm.register("cccc3333dddd4444", CHAT)
    data = sm._load()
    for s in data["chats"][str(CHAT)]["sessions"]:
        if s["id"] == "aaaa1111bbbb2222":
            s["last_used"] = _old_ts(40)
            s["created_at"] = _old_ts(40)
    data["chats"][str(CHAT)]["active_id"] = "cccc3333dddd4444"
    sm._save(data)

    removed = sm.cleanup_old_sessions(max_age_days=30)
    assert removed == 1
    assert not sm.exists("aaaa1111bbbb2222", CHAT)
    assert sm.exists("cccc3333dddd4444", CHAT)


def test_cleanup_never_removes_active_session():
    sm.register("aaaa1111bbbb2222", CHAT)
    data = sm._load()
    for s in data["chats"][str(CHAT)]["sessions"]:
        s["last_used"] = _old_ts(60)
        s["created_at"] = _old_ts(60)
    sm._save(data)

    removed = sm.cleanup_old_sessions(max_age_days=30)
    assert removed == 0
    assert sm.exists("aaaa1111bbbb2222", CHAT)


def test_cleanup_returns_zero_when_nothing_to_remove():
    sm.register("aaaa1111bbbb2222", CHAT)
    removed = sm.cleanup_old_sessions(max_age_days=30)
    assert removed == 0


# --- get_stats ---

def test_get_stats_empty_queue():
    stats = qm.get_stats()
    assert stats == {"pending": 0, "running": 0, "done": 0, "error": 0}


def test_get_stats_counts_by_status():
    t1 = qm.push("t1", CHAT, 1)
    qm.push("t2", CHAT, 2)
    qm.set_status(t1, "done")
    stats = qm.get_stats()
    assert stats["pending"] == 1
    assert stats["done"] == 1
    assert stats["running"] == 0
