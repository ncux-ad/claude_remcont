#!/usr/bin/env python3
"""Health check script for cron: alerts via Telegram if the bridge is stale."""
import os
import sys
import requests
from datetime import datetime, timezone
from config import BOT_TOKEN, ALLOWED_CHAT_IDS, HEARTBEAT_FILE, HEARTBEAT_MAX_AGE


def send_alert(text: str):
    for chat_id in ALLOWED_CHAT_IDS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
        except Exception:
            pass


def main() -> int:
    if not os.path.exists(HEARTBEAT_FILE):
        send_alert(
            "⚠️ *Claude Bridge*: heartbeat-файл не найден — "
            "сервис не запущен или `logs/heartbeat` удалён."
        )
        return 1

    try:
        with open(HEARTBEAT_FILE) as f:
            ts = datetime.fromisoformat(f.read().strip())
    except (ValueError, OSError) as e:
        send_alert(f"⚠️ *Claude Bridge*: не удалось прочитать heartbeat: `{e}`")
        return 1

    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > HEARTBEAT_MAX_AGE:
        send_alert(
            f"🚨 *Claude Bridge не отвечает*\n"
            f"Последний heartbeat: *{int(age)}с* назад (лимит {HEARTBEAT_MAX_AGE}с).\n"
            f"`sudo systemctl status claude-tg-bridge@$USER`"
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
