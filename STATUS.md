# Project Status

## Что сделано

### v0.1 — Реализация (коммит `f95bfca`)

**Базовая архитектура:**
- `tg_listener.py` — Telegram long-polling бот, очередь задач, запуск `claude -p`
- `tg_notify.py` — Stop Hook для Claude Code, отправляет результат в Telegram
- `session_manager.py` — JSON-хранилище сессий, поддержка `--resume` / `--continue`
- `queue_manager.py` — JSON-очередь задач со статусами (pending/running/done/error)
- `config.py` — все настройки через переменные окружения

**Команды бота:** `/sessions`, `/session ID`, `/new`, `/new текст`, `/label ID Имя`, `/status`, `/start`

**Деплой:** `requirements.txt`, `.env.example`, `claude-tg-bridge.service` (systemd template unit)

---

### v0.2 — Критические баги QA (коммит `03bf404`)

Устранено 7 критических проблем из QA-ревью:

| Баг | Решение |
|-----|---------|
| Race condition (TOCTOU) | `claim_next_pending()` — atomic check+set в одном lock |
| Нет атомарной записи JSON | `write → fsync → os.replace` в `_save()` |
| Молчаливый сбой stdin | `JSONDecodeError` / `OSError` логируются в stderr |
| Битый JSON роняет процесс | `try/except (JSONDecodeError, OSError)` в `_load()` |
| Утечка BOT_TOKEN в логах | Логируется только тип исключения, не URL |
| Вечное зависание задачи | `process.kill()` при `TimeoutExpired` + warning при `TASK_TIMEOUT=0` |
| Ненадёжный session ID regex | Убран fallback «любые 16 символов = ID» |

Дополнительно: `chat_id=None` guard, проверка бинарника Claude при старте, пустые задачи отклоняются.

---

### v0.3 — HIGH-баги QA (коммит `f539006`)

| Баг | Решение |
|-----|---------|
| Нет rate limiting (DoS) | `MAX_QUEUE_SIZE=50`; `push()` возвращает `None` при переполнении |
| Нет graceful shutdown | SIGTERM handler → `_shutdown` event; воркер и main loop останавливаются чисто |
| Нет валидации конфига | `validate_config()` при старте: пустой токен, placeholder, несуществующий `PROJECT_DIR` |
| `datetime.utcnow()` deprecated | Заменён на `datetime.now(timezone.utc)` везде |
| Нет логирования в queue_manager | Полное logging на всех событиях очереди |
| Session ID не валидируется | `register()` проверяет `^[a-zA-Z0-9_-]{8,128}$` до сохранения |
| Нет crash recovery | `reset_running_to_pending()` при старте возвращает зависшие задачи в очередь |

---

### v0.4 — Systemd (коммит `f211a28`)

- `claude-tg-bridge.service` — template unit (`%i` = имя пользователя)
- `KillSignal=SIGTERM` + `TimeoutStopSec=30` — graceful shutdown
- `Restart=on-failure`, `StartLimitBurst=3` — автоперезапуск с ограничением
- `MemoryMax=512M`, `LimitNOFILE=8192` — ресурсные лимиты

---

### v0.5 — Production Readiness (коммит `dd3add3`)

Полный цикл по протоколу `docs/AGENTS.md`. Вердикт: **READY**.

**Документация:** `README.md`, `DEPLOY.md`, `docs/AGENTS.md`

**Тесты — 27 smoke-тестов, 100% pass**

**Observability:** `claude-tg-bridge.logrotate`

---

### v0.6 — HIGH-баги + MEDIUM-фичи + SQLite (коммиты `fecaf18`, `8f718e9`)

**HIGH — исправлено:**
- Изоляция сессий по `chat_id`: `session_manager.py` полностью переработан, все функции принимают `chat_id`
- Healthcheck: heartbeat-файл (`logs/heartbeat`) + `check_health.py` + cron

**MEDIUM — реализовано:**
- Retry Telegram API: exponential backoff в `tg_send()` (3 попытки, 1s/2s)
- Cleanup: `cleanup_old_tasks(days)` + `cleanup_old_sessions(days)`, запуск при старте и раз в час
- `/stats` команда: очередь, сессии, аптайм
- SQLite вместо JSON: `queue_manager.py` на `sqlite3` + WAL, corrupt-DB recovery, `get_running_task()` перенесён из `tg_notify.py`

**Тесты расширены: 48 тестов, 100% pass**
- `tests/test_retry.py` — 5 тестов retry + backoff
- `tests/test_cleanup.py` — 8 тестов + 2 stats
- `tests/test_health.py` — 5 тестов healthcheck
- `tests/test_session.py` — +3 теста изоляции чатов
- `tests/test_queue.py` — переписан под SQLite

**Code review (high-effort): 5 багов исправлено:**
- `TimeoutExpired` не имеет `.process` → `AttributeError` → поток умирал молча
- `task_id` коллизия после cleanup (COUNT-суффикс) → microseconds
- TOCTOU в `/status` (двойной `next_pending()`) → walrus operator
- `get_running_task()` возвращал задачу чужого чата → удалён fallback
- Corrupt DB: `log.error` → `log.critical` с "all tasks lost"

---

### v0.7 — LOW-фичи + dev-tooling (текущий)

- `/history` команда: последние 10 задач чата из SQLite
- `/sessions` пагинация: `/sessions 2`, `/sessions 3` и т.д.
- `pyproject.toml`: `python_requires>=3.10`, ruff + mypy конфиг
- `.pre-commit-config.yaml`: ruff (lint + format) + mypy
- `requirements-dev.txt`: добавлены `pre-commit`, `ruff`, `mypy`, `types-requests`

---

## Что предстоит

### 🟢 LOW — nice-to-have

| Задача | Описание |
|--------|----------|
| Per-user rate limiting | Один пользователь не может спамить очередь (сейчас лимит общий на все чаты) |
| Circuit breaker для Claude | Не вызывать Claude если он стабильно падает N раз подряд |
| SQLite для sessions | Перевести `session_manager.py` с JSON на SQLite |
| Staging-окружение | Отдельный бот-токен для тестирования правок |

---

## Production Gate (текущее состояние)

```
[x] Scope понятен
[x] Tests проходят (48/48)
[x] Секреты не в репозитории
[x] .env.example есть
[x] README.md + DEPLOY.md есть
[x] Зависимости зафиксированы
[x] Права доступа проверены (ALLOWED_CHAT_IDS)
[x] Логи не содержат токены
[x] systemd config проверен
[x] Log rotation настроен
[x] Graceful shutdown + crash recovery
[x] Rollback-план описан
[x] Healthcheck (heartbeat + check_health.py)
[x] SQLite вместо JSON (queue_manager)
[x] Session isolation by chat_id
[ ] Staging-окружение
[ ] autoApprove вместо --dangerously-skip-permissions (преднамеренное решение)
```
