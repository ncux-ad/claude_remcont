import os
import sys

BOT_TOKEN        = os.environ.get("TG_BOT_TOKEN", "")
ALLOWED_CHAT_IDS = {int(x) for x in os.environ.get("TG_ALLOWED_CHATS", "0").split(",") if x.strip()}

PROJECT_DIR   = os.environ.get("CLAUDE_PROJECT_DIR", os.path.expanduser("~/claude_remcont"))
CLAUDE_BIN    = os.environ.get("CLAUDE_BIN", "claude")
TASK_TIMEOUT  = int(os.environ.get("CLAUDE_TASK_TIMEOUT", "0"))
MAX_QUEUE_SIZE = int(os.environ.get("CLAUDE_MAX_QUEUE_SIZE", "50"))

QUEUE_FILE        = os.path.expanduser("~/claude_remcont/logs/.queue.json")
SESSION_FILE      = os.path.expanduser("~/claude_remcont/logs/sessions.json")
LOG_FILE          = os.path.expanduser("~/claude_remcont/logs/listener.log")
HEARTBEAT_FILE    = os.path.expanduser("~/claude_remcont/logs/heartbeat")
HEARTBEAT_MAX_AGE = int(os.environ.get("CLAUDE_HEARTBEAT_MAX_AGE", "120"))


def validate_config() -> list[str]:
    """Return list of fatal config errors. Empty list = OK."""
    errors = []
    if not BOT_TOKEN:
        errors.append("TG_BOT_TOKEN is not set")
    elif BOT_TOKEN == "YOUR_TOKEN":
        errors.append("TG_BOT_TOKEN is still the placeholder value")
    if ALLOWED_CHAT_IDS == {0}:
        errors.append("TG_ALLOWED_CHATS is not set — bot will accept no chats")
    if not os.path.isdir(PROJECT_DIR):
        errors.append(f"CLAUDE_PROJECT_DIR does not exist: {PROJECT_DIR}")
    return errors
