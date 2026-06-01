import pytest
import session_manager as sm

CHAT = 100
OTHER_CHAT = 200


@pytest.fixture(autouse=True)
def isolated_sessions(tmp_path, monkeypatch):
    """Redirect SESSION_FILE to a temp path for each test."""
    session_file = str(tmp_path / ".sessions.db")
    monkeypatch.setattr(sm, "SESSION_FILE", session_file)
    import config
    monkeypatch.setattr(config, "SESSION_FILE", session_file)
    yield session_file


def test_register_saves_session(isolated_sessions):
    sm.register("abcdef1234567890", CHAT)
    assert sm.exists("abcdef1234567890", CHAT)
    assert sm.get_active_id(CHAT) == "abcdef1234567890"


def test_register_rejects_invalid_session_id(isolated_sessions):
    sm.register("../../etc/passwd", CHAT)
    assert not sm.exists("../../etc/passwd", CHAT)
    assert sm.get_active_id(CHAT) is None


def test_register_rejects_too_short_id(isolated_sessions):
    sm.register("abc", CHAT)
    assert sm.get_active_id(CHAT) is None


def test_register_deduplicates(isolated_sessions):
    sm.register("abcdef1234567890", CHAT)
    sm.register("abcdef1234567890", CHAT)
    sessions = sm.get_all(CHAT)
    assert len(sessions) == 1


def test_set_active_and_get(isolated_sessions):
    sm.register("aaaa1111bbbb2222", CHAT)
    sm.register("cccc3333dddd4444", CHAT)
    sm.set_active("aaaa1111bbbb2222", CHAT)
    assert sm.get_active_id(CHAT) == "aaaa1111bbbb2222"


def test_set_active_none(isolated_sessions):
    sm.register("aaaa1111bbbb2222", CHAT)
    sm.set_active(None, CHAT)
    assert sm.get_active_id(CHAT) is None


def test_build_claude_args_resume(isolated_sessions):
    sm.register("aaaa1111bbbb2222", CHAT)
    args = sm.build_claude_args(CHAT)
    assert args == ["--resume", "aaaa1111bbbb2222"]


def test_build_claude_args_continue_when_no_active(isolated_sessions):
    args = sm.build_claude_args(CHAT)
    assert args == ["--continue"]


def test_build_claude_args_force_new(isolated_sessions):
    sm.register("aaaa1111bbbb2222", CHAT)
    args = sm.build_claude_args(CHAT, force_new=True)
    assert args == []


def test_set_label(isolated_sessions):
    sm.register("aaaa1111bbbb2222", CHAT)
    sm.set_label("aaaa1111bbbb2222", "My Project", CHAT)
    sessions = sm.get_all(CHAT)
    assert sessions[0]["label"] == "My Project"


def test_corrupt_db_handled_gracefully(isolated_sessions):
    open(isolated_sessions, "wb").write(b"not a sqlite db at all!!!")
    assert sm.get_active_id(CHAT) is None
    assert sm.get_all(CHAT) == []
    assert not sm.exists("aaaa1111bbbb2222", CHAT)


def test_chats_are_isolated(isolated_sessions):
    sm.register("aaaa1111bbbb2222", CHAT)
    assert sm.get_active_id(OTHER_CHAT) is None
    assert not sm.exists("aaaa1111bbbb2222", OTHER_CHAT)


def test_active_id_independent_per_chat(isolated_sessions):
    sm.register("aaaa1111bbbb2222", CHAT)
    sm.register("cccc3333dddd4444", OTHER_CHAT)
    assert sm.get_active_id(CHAT) == "aaaa1111bbbb2222"
    assert sm.get_active_id(OTHER_CHAT) == "cccc3333dddd4444"


def test_set_active_does_not_affect_other_chat(isolated_sessions):
    sm.register("aaaa1111bbbb2222", CHAT)
    sm.register("cccc3333dddd4444", OTHER_CHAT)
    sm.set_active(None, CHAT)
    assert sm.get_active_id(CHAT) is None
    assert sm.get_active_id(OTHER_CHAT) == "cccc3333dddd4444"
