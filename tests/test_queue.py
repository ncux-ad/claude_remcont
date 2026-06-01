import os
import pytest
import queue_manager as qm
from config import MAX_QUEUE_SIZE


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path, monkeypatch):
    """Redirect QUEUE_FILE to a temp path for each test."""
    queue_file = str(tmp_path / ".queue.db")
    monkeypatch.setattr(qm, "QUEUE_FILE", queue_file)
    import config
    monkeypatch.setattr(config, "QUEUE_FILE", queue_file)
    yield queue_file


def test_push_adds_task(isolated_queue):
    task_id = qm.push("do something", chat_id=1, message_id=1)
    assert task_id is not None
    task = qm.next_pending()
    assert task is not None
    assert task["text"] == "do something"
    assert task["status"] == "pending"


def test_push_returns_none_when_queue_full(isolated_queue, monkeypatch):
    monkeypatch.setattr(qm, "MAX_QUEUE_SIZE", 2)
    qm.push("task 1", chat_id=1, message_id=1)
    qm.push("task 2", chat_id=1, message_id=2)
    result = qm.push("task 3", chat_id=1, message_id=3)
    assert result is None


def test_claim_next_pending_returns_task(isolated_queue):
    qm.push("task A", chat_id=1, message_id=1)
    task = qm.claim_next_pending()
    assert task is not None
    assert task["text"] == "task A"
    assert task["status"] == "running"


def test_claim_next_pending_none_if_already_running(isolated_queue):
    qm.push("task A", chat_id=1, message_id=1)
    qm.claim_next_pending()  # marks as running
    result = qm.claim_next_pending()
    assert result is None


def test_claim_next_pending_none_if_empty(isolated_queue):
    assert qm.claim_next_pending() is None


def test_set_status_changes_status(isolated_queue):
    task_id = qm.push("task A", chat_id=1, message_id=1)
    qm.set_status(task_id, "done")
    stats = qm.get_stats()
    assert stats["done"] == 1
    assert stats["pending"] == 0


def test_reset_running_to_pending(isolated_queue):
    task_id = qm.push("task A", chat_id=1, message_id=1)
    qm.set_status(task_id, "running")
    qm.reset_running_to_pending()
    assert not qm.is_running()
    assert qm.next_pending() is not None


def test_is_running_true_when_running(isolated_queue):
    task_id = qm.push("task A", chat_id=1, message_id=1)
    qm.set_status(task_id, "running")
    assert qm.is_running() is True


def test_is_running_false_when_empty(isolated_queue):
    assert qm.is_running() is False


def test_corrupt_db_handled_gracefully(isolated_queue):
    open(isolated_queue, "wb").write(b"not a sqlite db at all!!!")
    # Functions should not raise — they should return safe defaults
    assert qm.get_stats() == {"pending": 0, "running": 0, "done": 0, "error": 0}
    assert qm.is_running() is False
    assert qm.next_pending() is None


def test_push_generates_unique_ids(isolated_queue):
    id1 = qm.push("task 1", chat_id=1, message_id=1)
    id2 = qm.push("task 2", chat_id=1, message_id=2)
    assert id1 != id2


def test_per_chat_limit_blocks_same_chat(isolated_queue, monkeypatch):
    monkeypatch.setattr(qm, "PER_CHAT_QUEUE_LIMIT", 2)
    qm.push("task 1", chat_id=1, message_id=1)
    qm.push("task 2", chat_id=1, message_id=2)
    result = qm.push("task 3", chat_id=1, message_id=3)
    assert result is None


def test_per_chat_limit_allows_other_chats(isolated_queue, monkeypatch):
    monkeypatch.setattr(qm, "PER_CHAT_QUEUE_LIMIT", 1)
    qm.push("task 1", chat_id=1, message_id=1)
    result = qm.push("task 2", chat_id=2, message_id=2)
    assert result is not None


def test_push_stores_session_id(isolated_queue):
    qm.push("task 1", chat_id=1, message_id=1, session_id="sess-aaa")
    qm.push("task 2", chat_id=1, message_id=2, session_id="sess-bbb")
    tasks = qm.get_tasks_for_session("sess-aaa", chat_id=1)
    assert len(tasks) == 1
    assert tasks[0]["text"] == "task 1"


def test_get_tasks_for_session_empty(isolated_queue):
    assert qm.get_tasks_for_session("no-such-session", chat_id=1) == []


def test_set_session_id_backfills(isolated_queue):
    # Implicit task: enqueued without a session (session_id is NULL).
    task_id = qm.push("implicit task", chat_id=1, message_id=1)
    assert qm.get_tasks_for_session("resolved-sid", chat_id=1) == []
    # Worker learns the real session after the run and backfills it.
    qm.set_session_id(task_id, "resolved-sid")
    tasks = qm.get_tasks_for_session("resolved-sid", chat_id=1)
    assert len(tasks) == 1
    assert tasks[0]["id"] == task_id


def test_migration_upgrades_old_db_without_data_loss(isolated_queue, monkeypatch):
    import sqlite3
    # Build a pre-v0.9 DB: tasks table WITHOUT session_id, holding one task.
    old_schema = """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, text TEXT NOT NULL, chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL, force_new INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL, updated_at TEXT
        );
        CREATE INDEX idx_status ON tasks(status);
    """
    conn = sqlite3.connect(isolated_queue)
    conn.executescript(old_schema)
    conn.execute(
        "INSERT INTO tasks (id, text, chat_id, message_id, status, created_at)"
        " VALUES ('old1', 'pre-existing', 1, 1, 'pending', '2026-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(qm, "_migrated", False)  # force migration on this fresh process state
    # First access runs _get_conn -> _migrate; the old task must NOT be wiped.
    task = qm.next_pending()
    assert task is not None
    assert task["id"] == "old1"
    assert task["session_id"] is None
    # The new column is usable after migration.
    qm.set_session_id("old1", "sess-x")
    assert qm.get_tasks_for_session("sess-x", chat_id=1)[0]["id"] == "old1"
