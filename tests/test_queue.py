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
