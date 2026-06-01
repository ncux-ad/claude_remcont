from unittest.mock import patch, call
import pytest
import tg_listener


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(tg_listener.time, "sleep", lambda _: None)


def test_send_succeeds_on_first_attempt():
    with patch("tg_listener.requests.post") as mock_post:
        tg_listener.tg_send(123, "hello")
    assert mock_post.call_count == 1


def test_send_retries_on_failure_then_succeeds():
    side_effects = [Exception("timeout"), Exception("timeout"), None]
    with patch("tg_listener.requests.post", side_effect=side_effects) as mock_post:
        tg_listener.tg_send(123, "hello")
    assert mock_post.call_count == 3


def test_send_gives_up_after_3_attempts():
    with patch("tg_listener.requests.post", side_effect=Exception("conn")):
        tg_listener.tg_send(123, "hello")  # must not raise
    # 3 attempts, no exception propagated


def test_send_retries_with_backoff(monkeypatch):
    delays = []
    monkeypatch.setattr(tg_listener.time, "sleep", lambda d: delays.append(d))
    side_effects = [Exception("e1"), Exception("e2"), None]
    with patch("tg_listener.requests.post", side_effect=side_effects):
        tg_listener.tg_send(123, "hello")
    assert delays == [1, 2]


def test_send_reply_to_included_in_payload():
    with patch("tg_listener.requests.post") as mock_post:
        tg_listener.tg_send(123, "hello", reply_to=42)
    payload = mock_post.call_args.kwargs["json"]
    assert payload["reply_to_message_id"] == 42
