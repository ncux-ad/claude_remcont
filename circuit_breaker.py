"""
Per-chat circuit breaker: after THRESHOLD consecutive Claude failures, block new
tasks for COOLDOWN seconds. State is in-memory and resets on process restart.
"""
import logging
import threading
import time

from config import CIRCUIT_BREAKER_THRESHOLD, CIRCUIT_BREAKER_COOLDOWN

_lock = threading.Lock()
_state: dict[int, dict] = {}  # chat_id -> {failures, opened_at}

log = logging.getLogger(__name__)


def record_success(chat_id: int):
    with _lock:
        if chat_id in _state:
            log.info("Circuit breaker CLOSED for chat %d", chat_id)
        _state.pop(chat_id, None)


def record_failure(chat_id: int):
    with _lock:
        s = _state.setdefault(chat_id, {"failures": 0, "opened_at": None})
        s["failures"] += 1
        if s["failures"] >= CIRCUIT_BREAKER_THRESHOLD and s["opened_at"] is None:
            s["opened_at"] = time.monotonic()
            log.warning(
                "Circuit breaker OPEN for chat %d after %d consecutive failures",
                chat_id, s["failures"],
            )


def is_open(chat_id: int) -> bool:
    with _lock:
        s = _state.get(chat_id)
        if not s or s["opened_at"] is None:
            return False
        elapsed = time.monotonic() - s["opened_at"]
        if elapsed >= CIRCUIT_BREAKER_COOLDOWN:
            log.info("Circuit breaker auto-RESET for chat %d (cooldown expired)", chat_id)
            _state.pop(chat_id)
            return False
        return True


def remaining_cooldown(chat_id: int) -> int:
    """Seconds until circuit auto-resets, or 0 if closed."""
    with _lock:
        s = _state.get(chat_id)
        if not s or s["opened_at"] is None:
            return 0
        return max(0, int(CIRCUIT_BREAKER_COOLDOWN - (time.monotonic() - s["opened_at"])))
