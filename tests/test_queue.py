import json
import os
import pytest
import queue_manager as qm
from config import MAX_QUEUE_SIZE


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path, monkeypatch):
    """Redirect QUEUE_FILE to a temp path for each test."""
    queue_file = str(tmp_path / ".queue.json")
    monkeypatch.setattr(qm, "QUEUE_FILE", queue_file)
    # Also patch the import in queue_manager module-level reference
    import config
    monkeypatch.setattr(config, "QUEUE_FILE", queue_file)
    yield queue_file


def test_push_adds_task(isolated_queue):
    task_id = qm.push("do something", chat_id=1, message_id=1)
    assert task_id is not None
    q = json.loads(open(isolated_queue).read())
    assert len(q) == 1
    assert q[0]["text"] == "do something"
    assert q[0]["status"] == "pending"


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
    q = json.loads(open(isolated_queue).read())
    assert q[0]["status"] == "done"


def test_reset_running_to_pending(isolated_queue):
    task_id = qm.push("task A", chat_id=1, message_id=1)
    qm.set_status(task_id, "running")
    qm.reset_running_to_pending()
    q = json.loads(open(isolated_queue).read())
    assert q[0]["status"] == "pending"


def test_is_running_true_when_running(isolated_queue):
    task_id = qm.push("task A", chat_id=1, message_id=1)
    qm.set_status(task_id, "running")
    assert qm.is_running() is True


def test_is_running_false_when_empty(isolated_queue):
    assert qm.is_running() is False


def test_load_returns_empty_on_corrupt_file(isolated_queue):
    open(isolated_queue, "w").write("not json{{{")
    result = qm._load()
    assert result == []


def test_atomic_write_creates_no_tmp_on_success(isolated_queue):
    qm.push("task", chat_id=1, message_id=1)
    assert not os.path.exists(isolated_queue + ".tmp")
