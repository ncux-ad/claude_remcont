#!/usr/bin/env python3
import sys
import json
import os
import requests
from config import BOT_TOKEN, ALLOWED_CHAT_IDS, QUEUE_FILE
import session_manager as sm


def send(chat_id: int, text: str):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=10,
    )


def get_running_task() -> dict | None:
    if not os.path.exists(QUEUE_FILE):
        return None
    with open(QUEUE_FILE) as f:
        q = json.load(f)
    for t in reversed(q):
        if t["status"] == "running":
            return t
    return q[-1] if q else None


def main():
    stdin_data = {}
    try:
        stdin_data = json.load(sys.stdin)
    except Exception:
        pass

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

    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE) as f:
            q = json.load(f)
        for t in q:
            if t.get("id") == task.get("id"):
                t["status"] = "done"
        with open(QUEUE_FILE, "w") as f:
            json.dump(q, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
