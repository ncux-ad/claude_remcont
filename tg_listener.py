#!/usr/bin/env python3
import re
import shutil
import signal
import sys
import time
import subprocess
import threading
import logging
import requests
import os

from config import (
    BOT_TOKEN, ALLOWED_CHAT_IDS, PROJECT_DIR, CLAUDE_BIN,
    LOG_FILE, TASK_TIMEOUT, MAX_QUEUE_SIZE, validate_config,
)
from queue_manager import (
    push, set_status, claim_next_pending,
    is_running, next_pending, reset_running_to_pending,
)
import session_manager as sm

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

_shutdown = threading.Event()


def _handle_sigterm(signum, frame):
    log.info("SIGTERM received — shutting down gracefully.")
    _shutdown.set()


def tg_get_updates(offset: int) -> list:
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 25, "allowed_updates": ["message"]},
            timeout=30,
        )
        return r.json().get("result", [])
    except Exception as e:
        # Log only exception type to avoid leaking BOT_TOKEN via URL in traceback
        log.warning("getUpdates failed: %s", type(e).__name__)
        return []


def tg_send(chat_id: int, text: str, reply_to: int = None):
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=10,
        )
    except Exception as e:
        log.warning("sendMessage failed: %s", type(e).__name__)


# Only match Claude's structured session ID output — no dangerous fallback
SESSION_ID_RE = re.compile(r"session[_\s-]?id[:\s]+([a-zA-Z0-9_-]{8,})", re.IGNORECASE)


def extract_session_id(output: str) -> str | None:
    m = SESSION_ID_RE.search(output)
    return m.group(1) if m else None


def run_claude(task: dict):
    task_id   = task["id"]
    chat_id   = task["chat_id"]
    text      = task["text"]
    force_new = task.get("force_new", False)

    # Status is already "running" — set atomically by claim_next_pending()

    session_args = sm.build_claude_args(chat_id, force_new=force_new)
    active_id    = sm.get_active_id(chat_id)

    if force_new:
        hint = "_(новая сессия)_"
    elif active_id:
        hint = f"_(сессия `{active_id[:8]}...`)_"
    else:
        hint = "_(--continue)_"

    tg_send(chat_id, f"⚙️ *Запускаю задачу* {hint}\n`{text[:200]}`")

    cmd = [CLAUDE_BIN, *session_args, "--dangerously-skip-permissions", "-p", text]
    kwargs: dict = dict(cwd=PROJECT_DIR, capture_output=True, text=True)
    if TASK_TIMEOUT > 0:
        kwargs["timeout"] = TASK_TIMEOUT

    try:
        result = subprocess.run(cmd, **kwargs)

        new_sid = extract_session_id(result.stdout or "")
        if new_sid:
            sm.register(new_sid, chat_id)
        elif active_id:
            sm.increment_task_count(active_id, chat_id)

        if result.returncode == 0:
            set_status(task_id, "done")
        else:
            err = (result.stderr or "Неизвестная ошибка")[:500]
            tg_send(chat_id, f"❌ *Ошибка*\n```\n{err}\n```")
            set_status(task_id, "error")

    except subprocess.TimeoutExpired as e:
        if e.process:
            e.process.kill()
        tg_send(chat_id, f"⏱ Таймаут {TASK_TIMEOUT}s — задача остановлена.")
        set_status(task_id, "error")
    except Exception:
        log.exception("run_claude failed for task %s", task_id)
        # Don't send raw exception to user — it may contain sensitive paths/data
        tg_send(chat_id, "💥 Внутренняя ошибка. Подробности в логах сервера.")
        set_status(task_id, "error")


def queue_worker():
    while not _shutdown.is_set():
        # claim_next_pending() is atomic: check-and-set in one lock — no race condition
        task = claim_next_pending()
        if task:
            threading.Thread(target=run_claude, args=(task,), daemon=True).start()
        time.sleep(2)
    log.info("Queue worker stopped.")


def handle_message(msg: dict):
    chat_id    = msg.get("chat", {}).get("id")
    text       = (msg.get("text") or "").strip()
    message_id = msg.get("message_id")

    # Guard against malformed updates where chat_id is absent
    if chat_id is None:
        return

    if chat_id not in ALLOWED_CHAT_IDS:
        tg_send(chat_id, "⛔ Нет доступа.")
        return
    if not text:
        return

    if text == "/start":
        tg_send(chat_id,
            "👋 *Claude Code Bot*\n\n"
            "Пишите задачи — Claude Code выполнит их в текущей сессии.\n\n"
            "• `/sessions` — список всех сессий\n"
            "• `/session ID` — переключиться на сессию\n"
            "• `/new` — начать новую сессию\n"
            "• `/label ID Имя` — дать сессии имя\n"
            "• `/status` — статус задачи\n"
            "• `/new текст` — задача в новой сессии"
        )
        return

    if text == "/sessions":
        sessions = sm.get_all(chat_id)
        active   = sm.get_active_id(chat_id)
        if not sessions:
            tg_send(chat_id, "📭 Нет сохранённых сессий.")
            return
        lines = ["📋 *Сессии*\n"]
        for s in sessions[:10]:
            marker = "▶️" if s["id"] == active else "  "
            lines.append(f"{marker} `{s['id'][:12]}` — {s.get('label', s['id'][:8])} ({s.get('task_count', 0)} задач)")
        tg_send(chat_id, "\n".join(lines))
        return

    if text.startswith("/session "):
        sid = text[9:].strip()
        if not sm.exists(sid, chat_id):
            tg_send(chat_id, f"❓ Сессия `{sid}` не найдена.")
            return
        sm.set_active(sid, chat_id)
        tg_send(chat_id, f"✅ Переключились на `{sid[:12]}`")
        return

    if text == "/new":
        sm.set_active(None, chat_id)
        tg_send(chat_id, "🆕 Следующая задача начнёт новую сессию.")
        return

    if text.startswith("/label "):
        parts = text[7:].strip().split(" ", 1)
        if len(parts) < 2:
            tg_send(chat_id, "Синтаксис: `/label SESSION_ID Имя`")
            return
        sid, label = parts
        if not sm.exists(sid, chat_id):
            tg_send(chat_id, f"❓ Сессия `{sid}` не найдена.")
            return
        sm.set_label(sid, label, chat_id)
        tg_send(chat_id, f"✅ Сессия `{sid[:12]}` переименована: *{label}*")
        return

    if text == "/status":
        active = sm.get_active_id(chat_id)
        if is_running():
            sid_hint = f"\nСессия: `{active[:12]}`" if active else ""
            tg_send(chat_id, f"⚙️ Claude Code работает.{sid_hint}")
        elif next_pending():
            tg_send(chat_id, f"📋 В очереди: `{next_pending()['text'][:100]}`")
        else:
            sid_hint = f"\nАктивная сессия: `{active[:12]}`" if active else "\nНовая сессия при следующем запуске."
            tg_send(chat_id, f"✅ Свободен.{sid_hint}")
        return

    force_new = False
    if text.startswith("/new "):
        force_new = True
        text = text[5:].strip()
        sm.set_active(None, chat_id)

    if not text:
        tg_send(chat_id, "⚠️ Пустая задача — напишите текст после `/new`.")
        return

    task_id = push(text, chat_id, message_id, force_new=force_new)
    if task_id is None:
        tg_send(chat_id, f"🚫 Очередь переполнена (лимит {MAX_QUEUE_SIZE}). Подождите завершения текущих задач.")
        return

    active   = sm.get_active_id(chat_id)
    sid_hint = "\n🆕 Новая сессия" if force_new else (f"\nСессия: `{active[:12]}`" if active else "")
    tg_send(chat_id, f"📥 Принято `{task_id}`{sid_hint}", reply_to=message_id)
    log.info("Task %s queued from chat %d: %s", task_id, chat_id, text[:80])


def main():
    # Validate config before anything else
    errors = validate_config()
    if errors:
        for e in errors:
            log.error("Config error: %s", e)
        sys.exit(1)

    if shutil.which(CLAUDE_BIN) is None:
        log.error("Claude binary not found: '%s'. Set CLAUDE_BIN or install claude.", CLAUDE_BIN)
        sys.exit(1)

    if TASK_TIMEOUT == 0:
        log.warning("CLAUDE_TASK_TIMEOUT=0: tasks have no timeout and can hang indefinitely.")

    # Graceful shutdown on SIGTERM (systemd stop, kill)
    signal.signal(signal.SIGTERM, _handle_sigterm)

    # Recover tasks stuck in "running" state from a previous unclean shutdown
    reset_running_to_pending()

    log.info("=== Bridge started | project=%s | queue_limit=%d ===",
             PROJECT_DIR, MAX_QUEUE_SIZE)

    threading.Thread(target=queue_worker, daemon=True).start()

    offset = 0
    while not _shutdown.is_set():
        for update in tg_get_updates(offset):
            offset = update["update_id"] + 1
            if "message" in update:
                try:
                    handle_message(update["message"])
                except Exception:
                    log.exception("Unhandled error in handle_message")
        time.sleep(1)

    log.info("=== Bridge stopped ===")


if __name__ == "__main__":
    main()
