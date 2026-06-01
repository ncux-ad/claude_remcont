import os

BOT_TOKEN        = os.environ.get("TG_BOT_TOKEN", "YOUR_TOKEN")
ALLOWED_CHAT_IDS = {int(x) for x in os.environ.get("TG_ALLOWED_CHATS", "0").split(",")}

PROJECT_DIR  = os.environ.get("CLAUDE_PROJECT_DIR", os.path.expanduser("~/claude_remcont"))
CLAUDE_BIN   = os.environ.get("CLAUDE_BIN", "claude")
TASK_TIMEOUT = int(os.environ.get("CLAUDE_TASK_TIMEOUT", "0"))

QUEUE_FILE   = os.path.expanduser("~/claude_remcont/logs/.queue.json")
SESSION_FILE = os.path.expanduser("~/claude_remcont/logs/sessions.json")
LOG_FILE     = os.path.expanduser("~/claude_remcont/logs/listener.log")
