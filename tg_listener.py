#!/usr/bin/env python3
import re
import time
import subprocess
import threading
import logging
import requests
import os

from config import BOT_TOKEN, ALLOWED_CHAT_IDS, PROJECT_DIR, CLAUDE_BIN, LOG_FILE, TASK_TIMEOUT
from queue_manager import push, set_status, is_running, next_pending
import session_manager as sm

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


def tg_get_updates(offset: int) -> list:
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 25, "allowed_updates": ["message"]},
            timeout=30,
        )
        return r.json().get("result", [])
    except Exception as e:
        log.warning(f"getUpdates: {e}")
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
        log.warning(f"sendMessage: {e}")


SESSION_ID_RE = re.compile(r"session[_\s-]?id[:\s]+([a-zA-Z0-9_-]{8,})", re.IGNORECASE)


def extract_session_id(output: str) -> str | None:
    m = SESSION_ID_RE.search(output)
    if m:
        return m.group(1)
    first_line = output.strip().splitlines()[0] if output.strip() else ""
    if re.fullmatch(r"[a-zA-Z0-9_-]{16,}", first_line.strip()):
        return first_line.strip()
    return None


def run_claude(task: dict):
    task_id   = task["id"]
    chat_id   = task["chat_id"]
    text      = task["text"]
    force_new = task.get("force_new", False)

    set_status(task_id, "running")

    session_args = sm.build_claude_args(force_new=force_new)
    active_id    = sm.get_active_id()

    if force_new:
        hint = "_(новая сессия)_"
    elif active_id:
        hint = f"_(сессия `{active_id[:8]}...`)_"
    else:
        hint = "_(--continue)_"

    tg_send(chat_id, f"⚙️ *Запускаю задачу* {hint}\n`{text[:200]}`")

    cmd = [CLAUDE_BIN, *session_args, "--dangerously-skip-permissions", "-p", text]
    kwargs = dict(cwd=PROJECT_DIR, capture_output=True, text=True)
    if TASK_TIMEOUT > 0:
        kwargs["timeout"] = TASK_TIMEOUT

    try:
        result = subprocess.run(cmd, **kwargs)

        new_sid = extract_session_id(result.stdout or "")
        if new_sid:
            sm.register(new_sid)
        elif active_id:
            sm.increment_task_count(active_id)

        if result.returncode == 0:
            set_status(task_id, "done")
        else:
            err = (result.stderr or "Неизвестная ошибка")[:500]
            tg_send(chat_id, f"❌ *Ошибка*\n```\n{err}\n```")
            set_status(task_id, "error")

    except subprocess.TimeoutExpired:
        tg_send(chat_id, f"⏱ Таймаут {TASK_TIMEOUT}s")
        set_status(task_id, "error")
    except Exception as e:
        log.exception(e)
        tg_send(chat_id, f"💥 `{e}`")
        set_status(task_id, "error")


def queue_worker():
    while True:
        if not is_running():
            task = next_pending()
            if task:
                threading.Thread(target=run_claude, args=(task,), daemon=True).start()
        time.sleep(2)


def handle_message(msg: dict):
    chat_id    = msg.get("chat", {}).get("id")
    text       = (msg.get("text") or "").strip()
    message_id = msg.get("message_id")

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
        sessions = sm.get_all()
        active   = sm.get_active_id()
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
        if not sm.exists(sid):
            tg_send(chat_id, f"❓ Сессия `{sid}` не найдена.")
            return
        sm.set_active(sid)
        tg_send(chat_id, f"✅ Переключились на `{sid[:12]}`")
        return

    if text == "/new":
        sm.set_active(None)
        tg_send(chat_id, "🆕 Следующая задача начнёт новую сессию.")
        return

    if text.startswith("/label "):
        parts = text[7:].strip().split(" ", 1)
        if len(parts) < 2:
            tg_send(chat_id, "Синтаксис: `/label SESSION_ID Имя`")
            return
        sid, label = parts
        if not sm.exists(sid):
            tg_send(chat_id, f"❓ Сессия `{sid}` не найдена.")
            return
        sm.set_label(sid, label)
        tg_send(chat_id, f"✅ Сессия `{sid[:12]}` переименована: *{label}*")
        return

    if text == "/status":
        active = sm.get_active_id()
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
        sm.set_active(None)

    task_id = push(text, chat_id, message_id, force_new=force_new)
    active  = sm.get_active_id()
    sid_hint = "\n🆕 Новая сессия" if force_new else (f"\nСессия: `{active[:12]}`" if active else "")
    tg_send(chat_id, f"📥 Принято `{task_id}`{sid_hint}", reply_to=message_id)
    log.info(f"Задача {task_id} от {chat_id}: {text[:80]}")


def main():
    log.info(f"=== Bridge запущен | Проект: {PROJECT_DIR} | Сессия: {sm.get_active_id() or 'новая'} ===")
    threading.Thread(target=queue_worker, daemon=True).start()
    offset = 0
    while True:
        for update in tg_get_updates(offset):
            offset = update["update_id"] + 1
            if "message" in update:
                try:
                    handle_message(update["message"])
                except Exception as e:
                    log.exception(e)
        time.sleep(1)


if __name__ == "__main__":
    main()
