import os
import pytest


def test_validate_config_empty_token(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "BOT_TOKEN", "")
    monkeypatch.setattr(config, "ALLOWED_CHAT_IDS", {123})
    monkeypatch.setattr(config, "PROJECT_DIR", str(tmp_path))
    errors = config.validate_config()
    assert any("TG_BOT_TOKEN" in e for e in errors)


def test_validate_config_placeholder_token(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "BOT_TOKEN", "YOUR_TOKEN")
    monkeypatch.setattr(config, "ALLOWED_CHAT_IDS", {123})
    monkeypatch.setattr(config, "PROJECT_DIR", str(tmp_path))
    errors = config.validate_config()
    assert any("placeholder" in e for e in errors)


def test_validate_config_missing_project_dir(monkeypatch):
    import config
    monkeypatch.setattr(config, "BOT_TOKEN", "1234567890:real_token_here")
    monkeypatch.setattr(config, "ALLOWED_CHAT_IDS", {123})
    monkeypatch.setattr(config, "PROJECT_DIR", "/nonexistent/path/xyz")
    errors = config.validate_config()
    assert any("PROJECT_DIR" in e for e in errors)


def test_validate_config_no_allowed_chats(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "BOT_TOKEN", "1234567890:real_token_here")
    monkeypatch.setattr(config, "ALLOWED_CHAT_IDS", {0})
    monkeypatch.setattr(config, "PROJECT_DIR", str(tmp_path))
    errors = config.validate_config()
    assert any("ALLOWED_CHATS" in e or "chat" in e.lower() for e in errors)


def test_validate_config_valid(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "BOT_TOKEN", "1234567890:real_token_here")
    monkeypatch.setattr(config, "ALLOWED_CHAT_IDS", {123456789})
    monkeypatch.setattr(config, "PROJECT_DIR", str(tmp_path))
    errors = config.validate_config()
    assert errors == []
