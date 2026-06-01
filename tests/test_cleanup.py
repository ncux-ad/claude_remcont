import pytest
from datetime import datetime, timezone, timedelta
import queue_manager as qm
import session_manager as sm

CHAT = 100


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(qm, "QUEUE_FILE", str(tmp_path / "queue.json"))
    monkeypatch.setattr(sm, "SESSION_FILE", str(tmp_path / "sessions.json"))
    import config
    monkeypatch.setattr(config, "QUEUE_FILE", str(tmp_path / "queue.json"))
    monkeypatch.setattr(config, "SESSION_FILE", str(tmp_path / "sessions.json"))


def _old_ts(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# --- Queue cleanup ---

def test_cleanup_removes_old_done_tasks():
    qm.push("task1", CHAT, 1)
    qm.push("task2", CHAT, 2)
    t1 = qm._load()[0]
    t2 = qm._load()[1]
    qm.set_status(t1["id"], "done")
    # backdate updated_at
    q = qm._load()
    for t in q:
        if t["id"] == t1["id"]:
            t["updated_at"] = _old_ts(10)
    qm._save(q)

    removed = qm.cleanup_old_tasks(max_age_days=7)
    assert removed == 1
    ids = [t["id"] for t in qm._load()]
    assert t1["id"] not in ids
    assert t2["id"] in ids


def test_cleanup_keeps_recent_done_tasks():
    qm.push("task1", CHAT, 1)
    t = qm._load()[0]
    qm.set_status(t["id"], "done")
    removed = qm.cleanup_old_tasks(max_age_days=7)
    assert removed == 0


def test_cleanup_never_removes_pending_or_running():
    qm.push("task1", CHAT, 1)
    q = qm._load()
    q[0]["updated_at"] = _old_ts(30)
    qm._save(q)
    removed = qm.cleanup_old_tasks(max_age_days=7)
    assert removed == 0


# --- Session cleanup ---

def test_cleanup_removes_old_sessions():
    sm.register("aaaa1111bbbb2222", CHAT)
    sm.register("cccc3333dddd4444", CHAT)
    # backdate first session
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
