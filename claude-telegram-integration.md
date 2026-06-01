# Claude Code ↔ Telegram: Полная интеграция (с управлением сессиями)

## Архитектура

```
┌──────────────────────────────────────────────────────────────────┐
│                          TELEGRAM                                │
│   Пользователь → [Бот] → Polling/Webhook                         │
└─────────────────────────┬────────────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────────────┐
│                     tg_listener.py                               │
│   auth → команды (/session, /new, /sessions) → очередь задач    │
└─────────────────────────┬────────────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────────────┐
│                   session_manager.py                             │
│   активная сессия │ история сессий │ --continue / --resume       │
└─────────────────────────┬────────────────────────────────────────┘
                          │  claude --continue -p   (обычно)
                          │  claude --resume ID -p  (по команде)
┌─────────────────────────▼────────────────────────────────────────┐
│                      CLAUDE CODE                                 │
│   Выполняет задачу → Stop hook → tg_notify.py → сохраняет ID    │
└─────────────────────────┬────────────────────────────────────────┘
                          │ Telegram Bot API
┌─────────────────────────▼────────────────────────────────────────┐
│   Пользователь получает ответ + ID сессии (при первом запуске)   │
└──────────────────────────────────────────────────────────────────┘
```

### Поведение сессий

| Ситуация | Флаг | Результат |
|---|---|---|
| Обычное сообщение | `--continue` | Продолжает текущую сессию |
| `/new` | _(без флага)_ | Новая сессия, контекст сброшен |
| `/session abc123` | `--resume abc123` | Переключение на конкретную сессию |
| Первый запуск | _(без флага)_ | Новая сессия автоматически |

---

## Структура проекта

```
~/claude-tg-bridge/
├── config.py
├── session_manager.py   # ← новый: управление сессиями
├── queue_manager.py
├── tg_listener.py
├── tg_notify.py
├── requirements.txt
└── logs/
    ├── listener.log
    └── sessions.json    # история сессий
```

---

## config.py

```python
import os

BOT_TOKEN       = os.environ.get("TG_BOT_TOKEN", "YOUR_TOKEN")
ALLOWED_CHAT_IDS = {int(x) for x in os.environ.get("TG_ALLOWED_CHATS", "0").split(",")}

PROJECT_DIR  = os.environ.get("CLAUDE_PROJECT_DIR", "/home/user/project")
CLAUDE_BIN   = os.environ.get("CLAUDE_BIN", "claude")
TASK_TIMEOUT = int(os.environ.get("CLAUDE_TASK_TIMEOUT", "0"))

QUEUE_FILE   = os.path.expanduser("~/.claude-tg-queue.json")
SESSION_FILE = os.path.expanduser("~/claude-tg-bridge/logs/sessions.json")
LOG_FILE     = os.path.expanduser("~/claude-tg-bridge/logs/listener.log")
```

---

## session_manager.py

Хранит историю сессий и знает, какую использовать следующей.

```python
# session_manager.py
import json
import os
import threading
from datetime import datetime
from config import SESSION_FILE

_lock = threading.Lock()

def _load() -> dict:
    if not os.path.exists(SESSION_FILE):
        return {"active_id": None, "sessions": []}
    with open(SESSION_FILE) as f:
        return json.load(f)

def _save(data: dict):
    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
    with open(SESSION_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── Чтение ───────────────────────────────────────────────────

def get_active_id() -> str | None:
    """Вернуть ID активной сессии (или None — новая сессия)."""
    return _load().get("active_id")

def get_all() -> list[dict]:
    """Список всех сессий, новые первыми."""
    return list(reversed(_load().get("sessions", [])))

def exists(session_id: str) -> bool:
    data = _load()
    return any(s["id"] == session_id for s in data["sessions"])

# ── Запись ───────────────────────────────────────────────────

def set_active(session_id: str | None):
    """Установить активную сессию. None = следующий запуск создаст новую."""
    with _lock:
        data = _load()
        data["active_id"] = session_id
        _save(data)

def register(session_id: str, label: str = ""):
    """
    Зарегистрировать сессию после первого запуска.
    Claude Code печатает Session ID в первой строке stdout.
    """
    with _lock:
        data = _load()
        # Не дублировать
        if any(s["id"] == session_id for s in data["sessions"]):
            data["active_id"] = session_id
            _save(data)
            return
        data["sessions"].append({
            "id": session_id,
            "label": label or session_id[:8],
            "created_at": datetime.utcnow().isoformat(),
            "task_count": 1,
        })
        data["active_id"] = session_id
        _save(data)

def increment_task_count(session_id: str):
    with _lock:
        data = _load()
        for s in data["sessions"]:
            if s["id"] == session_id:
                s["task_count"] = s.get("task_count", 0) + 1
                s["last_used"] = datetime.utcnow().isoformat()
        _save(data)

def set_label(session_id: str, label: str):
    """Дать сессии читаемое имя: /label abc123 Мой проект."""
    with _lock:
        data = _load()
        for s in data["sessions"]:
            if s["id"] == session_id:
                s["label"] = label
        _save(data)

# ── Построение аргументов CLI ────────────────────────────────

def build_claude_args(force_new: bool = False) -> list[str]:
    """
    Вернуть список флагов для subprocess.
    - force_new=True  → без флагов (Claude создаст новую сессию)
    - active_id есть  → ['--resume', 'ID']  (конкретная сессия)
    - active_id None  → ['--continue']       (последняя сессия)
    """
    if force_new:
        return []
    active = get_active_id()
    if active:
        return ["--resume", active]
    # Если сессий ещё нет — --continue безопасно создаст новую
    return ["--continue"]
```

---

## queue_manager.py

```python
# queue_manager.py
import json
import os
import threading
from datetime import datetime
from config import QUEUE_FILE

_lock = threading.Lock()

def _load():
    if not os.path.exists(QUEUE_FILE):
        return []
    with open(QUEUE_FILE) as f:
        return json.load(f)

def _save(q):
    with open(QUEUE_FILE, "w") as f:
        json.dump(q, f, ensure_ascii=False, indent=2)

def push(text: str, chat_id: int, message_id: int, force_new: bool = False) -> str:
    with _lock:
        q = _load()
        task_id = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{len(q)}"
        q.append({
            "id":         task_id,
            "text":       text,
            "chat_id":    chat_id,
            "message_id": message_id,
            "force_new":  force_new,   # ← флаг "начать новую сессию"
            "status":     "pending",
            "created_at": datetime.utcnow().isoformat(),
        })
        _save(q)
        return task_id

def set_status(task_id: str, status: str):
    with _lock:
        q = _load()
        for t in q:
            if t["id"] == task_id:
                t["status"] = status
                t["updated_at"] = datetime.utcnow().isoformat()
        _save(q)

def is_running() -> bool:
    return any(t["status"] == "running" for t in _load())

def next_pending():
    for t in _load():
        if t["status"] == "pending":
            return t
    return None
```

---

## tg_listener.py

```python
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

# ── Telegram ──────────────────────────────────────────────────

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
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json=payload, timeout=10)
    except Exception as e:
        log.warning(f"sendMessage: {e}")

# ── Запуск Claude Code ────────────────────────────────────────

SESSION_ID_RE = re.compile(r"session[_\s-]?id[:\s]+([a-zA-Z0-9_-]{8,})", re.IGNORECASE)

def extract_session_id(output: str) -> str | None:
    """
    Claude Code печатает Session ID в начале stdout.
    Пробуем несколько форматов.
    """
    m = SESSION_ID_RE.search(output)
    if m:
        return m.group(1)
    # Fallback: первое слово первой строки, если похоже на ID
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

    # Определяем флаги сессии
    session_args = sm.build_claude_args(force_new=force_new)
    active_id    = sm.get_active_id()

    session_hint = ""
    if force_new:
        session_hint = "_(новая сессия)_"
    elif active_id:
        session_hint = f"_(сессия `{active_id[:8]}...`)_"
    else:
        session_hint = "_(продолжение последней сессии)_"

    log.info(f"[{task_id}] {session_hint} {text[:80]}")
    tg_send(chat_id, f"⚙️ *Запускаю задачу* {session_hint}\n`{text[:200]}`")

    cmd = [CLAUDE_BIN, *session_args, "--dangerously-skip-permissions", "-p", text]
    kwargs = dict(cwd=PROJECT_DIR, capture_output=True, text=True)
    if TASK_TIMEOUT > 0:
        kwargs["timeout"] = TASK_TIMEOUT

    try:
        result = subprocess.run(cmd, **kwargs)

        # Пытаемся вытащить session ID из вывода
        new_sid = extract_session_id(result.stdout or "")
        if new_sid:
            sm.register(new_sid)
            log.info(f"[{task_id}] Сессия зарегистрирована: {new_sid}")
        elif active_id:
            sm.increment_task_count(active_id)

        if result.returncode == 0:
            set_status(task_id, "done")
            # Stop-хук отправит уведомление сам.
            # Раскомментировать если хук не настроен:
            # tg_send(chat_id, f"✅ Готово!\n{result.stdout[:1000]}")
        else:
            err = (result.stderr or "Неизвестная ошибка")[:500]
            log.error(f"[{task_id}] Ошибка: {err}")
            tg_send(chat_id, f"❌ *Ошибка*\n```\n{err}\n```")
            set_status(task_id, "error")

    except subprocess.TimeoutExpired:
        tg_send(chat_id, f"⏱ Таймаут {TASK_TIMEOUT}s")
        set_status(task_id, "error")
    except Exception as e:
        log.exception(e)
        tg_send(chat_id, f"💥 `{e}`")
        set_status(task_id, "error")

# ── Воркер очереди ────────────────────────────────────────────

def queue_worker():
    while True:
        if not is_running():
            task = next_pending()
            if task:
                threading.Thread(target=run_claude, args=(task,), daemon=True).start()
        time.sleep(2)

# ── Команды бота ──────────────────────────────────────────────

def cmd_sessions(chat_id: int):
    sessions = sm.get_all()
    if not sessions:
        tg_send(chat_id, "📭 Нет сохранённых сессий.")
        return
    active = sm.get_active_id()
    lines = ["📋 *Сессии* (используйте `/session ID` для переключения)\n"]
    for s in sessions[:10]:  # последние 10
        marker = "▶️" if s["id"] == active else "  "
        label  = s.get("label", s["id"][:8])
        count  = s.get("task_count", 0)
        lines.append(f"{marker} `{s['id'][:12]}` — {label} ({count} задач)")
    tg_send(chat_id, "\n".join(lines))

def cmd_switch_session(chat_id: int, session_id: str):
    if not sm.exists(session_id):
        tg_send(chat_id, f"❓ Сессия `{session_id}` не найдена.\nПосмотрите список: /sessions")
        return
    sm.set_active(session_id)
    tg_send(chat_id, f"✅ Переключились на сессию `{session_id[:12]}`\nСледующая задача продолжит её.")

def cmd_new_session(chat_id: int):
    sm.set_active(None)
    tg_send(chat_id,
        "🆕 *Следующая задача начнёт новую сессию.*\n"
        "Контекст предыдущей сессии сохранён — вернуться можно через `/sessions`."
    )

def cmd_label(chat_id: int, args: str):
    """Синтаксис: /label abc123 Имя сессии"""
    parts = args.strip().split(" ", 1)
    if len(parts) < 2:
        tg_send(chat_id, "Синтаксис: `/label SESSION_ID Имя`")
        return
    sid, label = parts[0], parts[1]
    if not sm.exists(sid):
        tg_send(chat_id, f"❓ Сессия `{sid}` не найдена.")
        return
    sm.set_label(sid, label)
    tg_send(chat_id, f"✅ Сессия `{sid[:12]}` переименована: *{label}*")

def cmd_status(chat_id: int):
    active = sm.get_active_id()
    if is_running():
        sid_hint = f"\nСессия: `{active[:12]}`" if active else ""
        tg_send(chat_id, f"⚙️ *Claude Code работает над задачей.*{sid_hint}")
    elif next_pending():
        tg_send(chat_id, f"📋 Задача в очереди: `{next_pending()['text'][:100]}`")
    else:
        sessions = sm.get_all()
        sid_hint = f"\nАктивная сессия: `{active[:12]}`" if active else "\nНовая сессия при следующем запуске."
        tg_send(chat_id, f"✅ Свободен.{sid_hint}\nВсего сессий: {len(sessions)}")

def handle_message(msg: dict):
    chat_id    = msg.get("chat", {}).get("id")
    text       = (msg.get("text") or "").strip()
    message_id = msg.get("message_id")

    if chat_id not in ALLOWED_CHAT_IDS:
        tg_send(chat_id, "⛔ Нет доступа.")
        return
    if not text:
        return

    # ── Команды ──────────────────────────────────────────────
    if text == "/start":
        tg_send(chat_id,
            "👋 *Claude Code Bot*\n\n"
            "Пишите задачи — Claude Code выполнит их в текущей сессии.\n\n"
            "Управление сессиями:\n"
            "• `/sessions` — список всех сессий\n"
            "• `/session ID` — переключиться на сессию\n"
            "• `/new` — начать новую сессию\n"
            "• `/label ID Имя` — дать сессии имя\n\n"
            "• `/status` — статус задачи\n"
            "• `/help` — полная справка"
        )
        return

    if text == "/sessions":
        cmd_sessions(chat_id)
        return

    if text.startswith("/session "):
        cmd_switch_session(chat_id, text[9:].strip())
        return

    if text == "/new":
        cmd_new_session(chat_id)
        return

    if text.startswith("/label "):
        cmd_label(chat_id, text[7:])
        return

    if text == "/status":
        cmd_status(chat_id)
        return

    if text == "/help":
        tg_send(chat_id,
            "📖 *Справка*\n\n"
            "*Задачи* — просто пишите текстом:\n"
            "`Добавь тесты для UserService`\n"
            "`Исправь баг в auth/middleware.py`\n\n"
            "*Сессии*\n"
            "`/sessions` — список сессий с ID\n"
            "`/session abc123def` — переключиться на сессию\n"
            "`/new` — начать с чистого контекста\n"
            "`/label abc123 Бэкенд` — назвать сессию\n\n"
            "*По умолчанию* каждая задача продолжает текущую сессию (`--continue`)."
        )
        return

    # ── Задача для Claude Code ───────────────────────────────
    # /new перед текстом = одноразовая новая сессия
    force_new = False
    if text.startswith("/new "):
        force_new = True
        text = text[5:].strip()
        sm.set_active(None)

    task_id = push(text, chat_id, message_id, force_new=force_new)
    active  = sm.get_active_id()
    sid_hint = f"\nСессия: `{active[:12]}`" if active and not force_new else "\n🆕 Новая сессия"
    tg_send(chat_id, f"📥 Принято `{task_id}`{sid_hint}", reply_to=message_id)
    log.info(f"Задача {task_id} от {chat_id}: {text[:80]}")

# ── Главный цикл ──────────────────────────────────────────────

def main():
    log.info("=== Claude Code Telegram Bridge ===")
    log.info(f"Проект: {PROJECT_DIR}")
    log.info(f"Активная сессия: {sm.get_active_id() or 'новая при первом запуске'}")

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
```

---

## tg_notify.py (обновлён)

```python
#!/usr/bin/env python3
# Вызывается Stop-хуком Claude Code.
# Получает JSON через stdin: { "last_assistant_message": "...", "session_id": "..." }

import sys
import json
import re
import requests
import os
from config import BOT_TOKEN, ALLOWED_CHAT_IDS, QUEUE_FILE, SESSION_FILE
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
    # Читаем JSON из stdin (Claude Code передаёт его автоматически)
    stdin_data = {}
    try:
        stdin_data = json.load(sys.stdin)
    except Exception:
        pass

    # Проверка stop_hook_active — защита от бесконечного цикла
    if stdin_data.get("stop_hook_active"):
        sys.exit(0)

    last_msg   = stdin_data.get("last_assistant_message", "")
    session_id = stdin_data.get("session_id") or stdin_data.get("sessionId")

    # Регистрируем/обновляем сессию
    if session_id:
        sm.register(session_id)

    # Обрезаем сообщение для Telegram
    preview = (last_msg[:600] + "...") if len(last_msg) > 600 else last_msg
    if not preview:
        preview = "Задача выполнена."

    task  = get_running_task()
    if not task:
        sys.exit(0)

    chat_id = task.get("chat_id")
    if chat_id not in ALLOWED_CHAT_IDS:
        sys.exit(0)

    active = sm.get_active_id()
    sid_line = f"\n🔖 Сессия: `{active[:12]}`" if active else ""

    send(chat_id, f"✅ *Готово*{sid_line}\n\n{preview}")

    # Обновить статус задачи
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
```

---

## .claude/settings.json

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/claude-tg-bridge/tg_notify.py"
          }
        ]
      }
    ],
    "Notification": [
      {
        "matcher": "permission_prompt",
        "hooks": [
          {
            "type": "command",
            "command": "python3 -c \"import requests,os; requests.post(f'https://api.telegram.org/bot{os.environ[\\\"TG_BOT_TOKEN\\\"]}/sendMessage', json={'chat_id': int(os.environ['TG_ALLOWED_CHATS'].split(',')[0]), 'text': '⚠️ Claude Code ожидает разрешения!'})\""
          }
        ]
      }
    ]
  }
}
```

---

## Пример диалога в Telegram

```
Вы:    Напиши unit-тесты для модуля payments

Бот:   📥 Принято 20260601_120000_0
       Сессия: `a1b2c3d4ef12`

       ⚙️ Запускаю задачу (сессия `a1b2c3d4ef12...`)
       `Напиши unit-тесты для модуля payments`

       ✅ Готово
       🔖 Сессия: `a1b2c3d4ef12`

       Написал 12 тестов в tests/test_payments.py:
       - test_charge_success
       - test_charge_insufficient_funds
       ...

Вы:    /sessions

Бот:   📋 Сессии
       ▶️ `a1b2c3d4ef12` — a1b2c3d4 (3 задачи)
          `f9e8d7c6b5a4` — f9e8d7c6 (1 задача)

Вы:    /label a1b2c3d4ef12 Основной бэкенд

Бот:   ✅ Сессия `a1b2c3d4ef12` переименована: Основной бэкенд

Вы:    /new Сделай отдельный модуль для работы с Redis

Бот:   📥 Принято 20260601_121500_3
       🆕 Новая сессия
```

---

## Запуск

```bash
# Переменные окружения
cat >> ~/.env.claude-tg << EOF
export TG_BOT_TOKEN="7123456789:AAF..."
export TG_ALLOWED_CHATS="123456789"
export CLAUDE_PROJECT_DIR="/home/user/project"
export CLAUDE_BIN="claude"
EOF
source ~/.env.claude-tg

# Зависимости
pip install requests

# Запуск
cd ~/claude-tg-bridge
python3 tg_listener.py
```

Или как сервис — см. предыдущий systemd-пример.

---

## Как работает сохранение сессии

Claude Code при запуске с `-p` выводит Session ID в начало stdout.
`tg_notify.py` получает его через поле `session_id` в JSON от Stop-хука и сохраняет.
При следующем запуске `session_manager.build_claude_args()` возвращает `--resume ID`,
и Claude Code восстанавливает полный контекст разговора.

```
Задача 1: claude -p "..."          → создаётся сессия a1b2c3
Задача 2: claude --resume a1b2c3 -p "..." → полный контекст сохранён ✓
Задача 3: claude --resume a1b2c3 -p "..." → контекст накапливается   ✓
/new
Задача 4: claude -p "..."          → новая сессия f9e8d7          ✓
```
