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
from datetime import datetime, timezone

from config import (
    BOT_TOKEN, ALLOWED_CHAT_IDS, PROJECT_DIR, CLAUDE_BIN,
    LOG_FILE, TASK_TIMEOUT, MAX_QUEUE_SIZE, HEARTBEAT_FILE,
    QUEUE_CLEANUP_DAYS, SESSION_CLEANUP_DAYS, validate_config,
)
from queue_manager import (
    push, set_status, claim_next_pending,
    is_running, next_pending, reset_running_to_pending, cleanup_old_tasks, get_stats,
    get_recent_tasks, get_tasks_for_session,
)
import session_manager as sm
import circuit_breaker as cb

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

_shutdown = threading.Event()
_start_time = time.time()


def _write_heartbeat():
    try:
        os.makedirs(os.path.dirname(HEARTBEAT_FILE), exist_ok=True)
        tmp = HEARTBEAT_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
        os.replace(tmp, HEARTBEAT_FILE)
    except OSError as e:
        log.warning("Failed to write heartbeat: %s", e)


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
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    delay = 1
    for attempt in range(3):
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json=payload,
                timeout=10,
            )
            return
        except Exception as e:
            if attempt < 2:
                log.warning("sendMessage attempt %d failed: %s — retrying in %ds",
                            attempt + 1, type(e).__name__, delay)
                time.sleep(delay)
                delay *= 2
            else:
                log.warning("sendMessage failed after 3 attempts: %s", type(e).__name__)


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
            cb.record_success(chat_id)
            set_status(task_id, "done")
        else:
            cb.record_failure(chat_id)
            err = (result.stderr or "Неизвестная ошибка")[:500]
            tg_send(chat_id, f"❌ *Ошибка*\n```\n{err}\n```")
            set_status(task_id, "error")

    except subprocess.TimeoutExpired:
        # subprocess.run() already kills the child on timeout before re-raising
        cb.record_failure(chat_id)
        tg_send(chat_id, f"⏱ Таймаут {TASK_TIMEOUT}s — задача остановлена.")
        set_status(task_id, "error")
    except Exception:
        cb.record_failure(chat_id)
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
            "• `/status` — статус текущей задачи\n"
            "• `/history` — последние 10 задач\n"
            "• `/stats` — статистика бота\n"
            "• `/new текст` — задача в новой сессии"
        )
        return

    if text == "/stats":
        counts  = get_stats()
        sessions = sm.get_all(chat_id)
        active   = sm.get_active_id(chat_id)
        elapsed  = int(time.time() - _start_time)
        days, rem = divmod(elapsed, 86400)
        hours, rem = divmod(rem, 3600)
        mins = rem // 60
        uptime = (f"{days}д " if days else "") + (f"{hours}ч " if hours or days else "") + f"{mins}м"
        sid_line = f"`{active[:12]}`" if active else "_нет_"
        tg_send(chat_id,
            f"📊 *Статистика*\n\n"
            f"🗂 *Очередь:*\n"
            f"  ожидает: {counts['pending']}\n"
            f"  выполняется: {counts['running']}\n"
            f"  выполнено: {counts['done']}\n"
            f"  ошибок: {counts['error']}\n\n"
            f"💬 *Сессии (этот чат):* {len(sessions)}, активная: {sid_line}\n\n"
            f"⏱ *Аптайм:* {uptime}"
        )
        return

    if text == "/history" or text.startswith("/history "):
        status_icon = {"pending": "⏳", "running": "⚙️", "done": "✅", "error": "❌"}
        if text.startswith("/history "):
            sid = text[9:].strip()
            if not sm.exists(sid, chat_id):
                tg_send(chat_id, f"❓ Сессия `{sid[:12]}` не найдена.")
                return
            tasks = get_tasks_for_session(sid, chat_id, limit=20)
            header = f"📜 *История сессии* `{sid[:12]}`\n"
        else:
            tasks = get_recent_tasks(chat_id, limit=10)
            header = "📜 *История задач (последние 10)*\n"
        if not tasks:
            tg_send(chat_id, "📭 Нет задач в истории.")
            return
        lines = [header]
        for t in tasks:
            icon = status_icon.get(t["status"], "❓")
            ts = t["created_at"][:16].replace("T", " ")
            preview = t["text"][:60].replace("`", "'")
            lines.append(f"{icon} `{ts}` — {preview}")
        tg_send(chat_id, "\n".join(lines))
        return

    if text == "/sessions" or text.startswith("/sessions "):
        sessions = sm.get_all(chat_id)
        active   = sm.get_active_id(chat_id)
        if not sessions:
            tg_send(chat_id, "📭 Нет сохранённых сессий.")
            return
        page = 1
        if text.startswith("/sessions "):
            try:
                page = max(1, int(text.split()[1]))
            except (ValueError, IndexError):
                pass
        page_size = 10
        total = len(sessions)
        start = (page - 1) * page_size
        chunk = sessions[start:start + page_size]
        header = f"📋 *Сессии* (стр. {page}/{(total - 1) // page_size + 1}, всего {total})\n"
        lines = [header]
        for s in chunk:
            marker = "▶️" if s["id"] == active else "  "
            lines.append(f"{marker} `{s['id'][:12]}` — {s.get('label', s['id'][:8])} ({s.get('task_count', 0)} задач)")
        if total > start + page_size:
            lines.append(f"\n_Следующая страница:_ `/sessions {page + 1}`")
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
        elif (pending := next_pending()):
            tg_send(chat_id, f"📋 В очереди: `{pending['text'][:100]}`")
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

    if cb.is_open(chat_id):
        secs = cb.remaining_cooldown(chat_id)
        tg_send(chat_id, f"⚡ Claude упал несколько раз подряд. Пауза {secs}с перед следующей задачей.")
        return

    active = sm.get_active_id(chat_id)
    task_id = push(text, chat_id, message_id, force_new=force_new,
                   session_id=None if force_new else active)
    if task_id is None:
        tg_send(chat_id, f"🚫 Слишком много задач в очереди. Подождите завершения текущих.")
        return
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

    cleanup_old_tasks(QUEUE_CLEANUP_DAYS)
    sm.cleanup_old_sessions(SESSION_CLEANUP_DAYS)

    log.info("=== Bridge started | project=%s | queue_limit=%d ===",
             PROJECT_DIR, MAX_QUEUE_SIZE)

    threading.Thread(target=queue_worker, daemon=True).start()

    offset = 0
    _last_heartbeat = 0.0
    _last_cleanup = 0.0
    while not _shutdown.is_set():
        for update in tg_get_updates(offset):
            offset = update["update_id"] + 1
            if "message" in update:
                try:
                    handle_message(update["message"])
                except Exception:
                    log.exception("Unhandled error in handle_message")
        now = time.time()
        if now - _last_heartbeat >= 30:
            _write_heartbeat()
            _last_heartbeat = now
        if now - _last_cleanup >= 3600:
            cleanup_old_tasks(QUEUE_CLEANUP_DAYS)
            sm.cleanup_old_sessions(SESSION_CLEANUP_DAYS)
            _last_cleanup = now
        time.sleep(1)

    log.info("=== Bridge stopped ===")


if __name__ == "__main__":
    main()
