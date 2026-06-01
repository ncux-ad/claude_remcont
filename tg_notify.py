#!/usr/bin/env python3
import logging
import sys
import json
import os
import requests
from config import BOT_TOKEN, ALLOWED_CHAT_IDS, QUEUE_FILE, LOG_FILE
from queue_manager import set_status
import session_manager as sm

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] tg_notify %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger(__name__)


def send(chat_id: int, text: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        log.warning("sendMessage failed: %s", type(e).__name__)


def get_running_task() -> dict | None:
    if not os.path.exists(QUEUE_FILE):
        return None
    try:
        with open(QUEUE_FILE) as f:
            q = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error("Failed to read queue: %s", e)
        return None
    for t in reversed(q):
        if t["status"] == "running":
            return t
    return q[-1] if q else None


def main():
    stdin_data = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            stdin_data = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("Invalid JSON from stdin: %s", e)
    except OSError as e:
        log.warning("Cannot read stdin: %s", e)

    if stdin_data.get("stop_hook_active"):
        sys.exit(0)

    last_msg   = stdin_data.get("last_assistant_message", "")
    session_id = stdin_data.get("session_id") or stdin_data.get("sessionId")

    if session_id:
        sm.register(session_id)

    preview = (last_msg[:600] + "...") if len(last_msg) > 600 else last_msg
    if not preview:
        preview = "Задача выполнена."

    task = get_running_task()
    if not task:
        sys.exit(0)

    chat_id = task.get("chat_id")
    if chat_id not in ALLOWED_CHAT_IDS:
        sys.exit(0)

    active   = sm.get_active_id()
    sid_line = f"\n🔖 Сессия: `{active[:12]}`" if active else ""
    send(chat_id, f"✅ *Готово*{sid_line}\n\n{preview}")

    task_id = task.get("id")
    if task_id:
        set_status(task_id, "done")


if __name__ == "__main__":
    main()
