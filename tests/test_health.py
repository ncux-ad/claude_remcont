import os
import pytest
from datetime import datetime, timezone, timedelta
import check_health


@pytest.fixture(autouse=True)
def patch_config(tmp_path, monkeypatch):
    hb_file = str(tmp_path / "heartbeat")
    monkeypatch.setattr(check_health, "HEARTBEAT_FILE", hb_file)
    monkeypatch.setattr(check_health, "HEARTBEAT_MAX_AGE", 120)
    monkeypatch.setattr(check_health, "BOT_TOKEN", "TEST")
    monkeypatch.setattr(check_health, "ALLOWED_CHAT_IDS", set())
    yield hb_file


def write_heartbeat(path: str, age_seconds: float):
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    with open(path, "w") as f:
        f.write(ts.isoformat())


def test_missing_heartbeat_returns_1(patch_config):
    assert check_health.main() == 1


def test_fresh_heartbeat_returns_0(patch_config):
    write_heartbeat(patch_config, age_seconds=10)
    assert check_health.main() == 0


def test_stale_heartbeat_returns_1(patch_config):
    write_heartbeat(patch_config, age_seconds=300)
    assert check_health.main() == 1


def test_exactly_at_limit_returns_0(patch_config):
    write_heartbeat(patch_config, age_seconds=119)
    assert check_health.main() == 0


def test_corrupt_heartbeat_returns_1(patch_config):
    with open(patch_config, "w") as f:
        f.write("not-a-timestamp")
    assert check_health.main() == 1
