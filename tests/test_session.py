import pytest
import session_manager as sm


@pytest.fixture(autouse=True)
def isolated_sessions(tmp_path, monkeypatch):
    """Redirect SESSION_FILE to a temp path for each test."""
    session_file = str(tmp_path / "sessions.json")
    monkeypatch.setattr(sm, "SESSION_FILE", session_file)
    import config
    monkeypatch.setattr(config, "SESSION_FILE", session_file)
    yield session_file


def test_register_saves_session(isolated_sessions):
    sm.register("abcdef1234567890")
    assert sm.exists("abcdef1234567890")
    assert sm.get_active_id() == "abcdef1234567890"


def test_register_rejects_invalid_session_id(isolated_sessions):
    sm.register("../../etc/passwd")
    assert not sm.exists("../../etc/passwd")
    assert sm.get_active_id() is None


def test_register_rejects_too_short_id(isolated_sessions):
    sm.register("abc")
    assert sm.get_active_id() is None


def test_register_deduplicates(isolated_sessions):
    sm.register("abcdef1234567890")
    sm.register("abcdef1234567890")
    sessions = sm.get_all()
    assert len(sessions) == 1


def test_set_active_and_get(isolated_sessions):
    sm.register("aaaa1111bbbb2222")
    sm.register("cccc3333dddd4444")
    sm.set_active("aaaa1111bbbb2222")
    assert sm.get_active_id() == "aaaa1111bbbb2222"


def test_set_active_none(isolated_sessions):
    sm.register("aaaa1111bbbb2222")
    sm.set_active(None)
    assert sm.get_active_id() is None


def test_build_claude_args_resume(isolated_sessions):
    sm.register("aaaa1111bbbb2222")
    args = sm.build_claude_args()
    assert args == ["--resume", "aaaa1111bbbb2222"]


def test_build_claude_args_continue_when_no_active(isolated_sessions):
    args = sm.build_claude_args()
    assert args == ["--continue"]


def test_build_claude_args_force_new(isolated_sessions):
    sm.register("aaaa1111bbbb2222")
    args = sm.build_claude_args(force_new=True)
    assert args == []


def test_set_label(isolated_sessions):
    sm.register("aaaa1111bbbb2222")
    sm.set_label("aaaa1111bbbb2222", "My Project")
    sessions = sm.get_all()
    assert sessions[0]["label"] == "My Project"


def test_load_returns_empty_on_corrupt_file(isolated_sessions):
    open(isolated_sessions, "w").write("{bad json")
    data = sm._load()
    assert data == {"active_id": None, "sessions": []}
