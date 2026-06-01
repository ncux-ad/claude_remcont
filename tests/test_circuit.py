import time
import pytest
import circuit_breaker as cb


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """Each test gets a clean breaker state and default thresholds."""
    cb._state.clear()
    monkeypatch.setattr(cb, "CIRCUIT_BREAKER_THRESHOLD", 3)
    monkeypatch.setattr(cb, "CIRCUIT_BREAKER_COOLDOWN", 300)
    yield
    cb._state.clear()


def test_closed_by_default():
    assert not cb.is_open(42)
    assert cb.remaining_cooldown(42) == 0


def test_opens_after_threshold_failures():
    for _ in range(3):
        cb.record_failure(1)
    assert cb.is_open(1)


def test_does_not_open_before_threshold():
    cb.record_failure(1)
    cb.record_failure(1)
    assert not cb.is_open(1)


def test_success_resets_failures():
    cb.record_failure(1)
    cb.record_failure(1)
    cb.record_success(1)
    cb.record_failure(1)
    assert not cb.is_open(1)  # counter restarted — only 1 failure since reset


def test_chats_are_independent():
    for _ in range(3):
        cb.record_failure(1)
    assert cb.is_open(1)
    assert not cb.is_open(2)


def test_auto_resets_after_cooldown(monkeypatch):
    monkeypatch.setattr(cb, "CIRCUIT_BREAKER_COOLDOWN", 0)
    for _ in range(3):
        cb.record_failure(1)
    assert not cb.is_open(1)  # cooldown=0 → already expired


def test_remaining_cooldown_decreases(monkeypatch):
    monkeypatch.setattr(cb, "CIRCUIT_BREAKER_COOLDOWN", 60)
    for _ in range(3):
        cb.record_failure(1)
    secs = cb.remaining_cooldown(1)
    assert 0 < secs <= 60
