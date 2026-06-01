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

### v0.8 — LOW-задачи: rate limiting, circuit breaker, SQLite sessions, staging

- **Per-chat rate limiting**: `PER_CHAT_QUEUE_LIMIT=5` — каждый чат не может занять всю глобальную очередь
- **Circuit breaker**: `circuit_breaker.py` — после N подряд падений (default 3) блокирует чат на COOLDOWN секунд (default 300); состояние in-memory, настраивается через env
- **SQLite для sessions**: `session_manager.py` полностью переписан на SQLite; WAL-режим, corrupt-DB recovery, та же публичная API
- **Staging**: `docs/STAGING.md` — инструкция по запуску второго бота с отдельным `.env.staging`

**Тесты: 57 тестов, 100% pass**
- `tests/test_circuit.py` — 7 тестов circuit breaker (threshold, cooldown, per-chat isolation, auto-reset)
- `tests/test_queue.py` — +2 теста per-chat rate limit
- `tests/test_session.py` — переписан под SQLite, corrupt-DB тест
- `tests/test_cleanup.py` — backdating через SQL вместо `_load`/`_save`

---

### v0.9 — /history SESSION_ID, session_id в задачах, DEPLOY.md (текущий)

- **`/history SESSION_ID`**: задачи теперь хранят `session_id` при постановке в очередь; `/history <sid>` показывает до 20 задач конкретной сессии; `/history` без аргумента — последние 10 задач чата
- **Миграция схемы**: `ALTER TABLE tasks ADD COLUMN session_id TEXT` — без потери данных для существующих БД
- **DEPLOY.md**: шаг «Healthcheck cron», rollback обновлён под `.db`-файлы
- **README.md, .env.example**: актуализированы под v0.8 (все новые env-переменные и команды)

**Тесты: 59 тестов, 100% pass** (+2 теста `get_tasks_for_session`)

---

## Что предстоит

Все HIGH, MEDIUM, LOW задачи выполнены. Проект в production-ready состоянии.

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
[x] Staging-окружение (docs/STAGING.md)
[ ] autoApprove вместо --dangerously-skip-permissions (преднамеренное решение)
```
