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

**Документация:**
- `README.md` — архитектура, быстрый старт, команды, env-таблица
- `DEPLOY.md` — пошаговый гайд: clone → configure → systemd → rollback план
- `docs/AGENTS.md` — протокол production readiness review для будущих релизов

**Тесты — 27 smoke-тестов, 100% pass:**
- `tests/test_queue.py` — push, rate limit, atomic claim, crash recovery, corrupt JSON (11 тестов)
- `tests/test_session.py` — register, path injection, dedup, label, build_claude_args (11 тестов)
- `tests/test_config.py` — пустой токен, placeholder, invalid dir, valid config (5 тестов)

**Observability:**
- `claude-tg-bridge.logrotate` — daily, 14 дней, compress

**Code quality:**
- `tg_notify.py`: `print()` → `logging` с записью в `LOG_FILE`
- `requirements.txt`: `requests>=2.28.0,<3.0.0`
- `requirements-dev.txt`: `pytest>=8.0`
- `.gitignore`: fix `.env*` → `.env` + `!.env.example`

---

## Что предстоит

### 🔴 HIGH — критично для надёжности

| Задача | Проблема | Решение |
|--------|----------|---------|
| Убрать `--dangerously-skip-permissions` | Claude выполняет любой код без проверок | Настроить `autoApprove` в `.claude/settings.json` |
| Healthcheck | Нельзя автоматически проверить живость | Файл-heartbeat (`/tmp/claude-tg-health`) обновляется каждые N секунд; cron проверяет свежесть |
| Session_manager не изолирован по чату | Два разных чата переключают сессии друг у друга | Ключ сессии `f"{chat_id}_{session_id}"` вместо просто `session_id` |

---

### 🟡 MEDIUM — улучшение стабильности

| Задача | Проблема | Решение |
|--------|----------|---------|
| Перейти с JSON на SQLite | Деградация при росте очереди; нет транзакций | `queue_manager.py` на `sqlite3` + WAL mode |
| Cleanup старых задач | `.queue.json` растёт бесконечно | Автоудаление `done/error` старше N дней |
| Cleanup старых сессий | `sessions.json` растёт бесконечно | TTL + команда `/cleanup` |
| Retry для Telegram API | Потеря уведомления при сбое сети | Exponential backoff в `tg_send()` и `tg_get_updates()` |
| Мониторинг | Нет метрик размера очереди, ошибок | Prometheus exporter или минимальный `/health` endpoint |
| Staging-окружение | Правки проверяются сразу на prod | Отдельный бот-токен для тестирования |

---

### 🟢 LOW — nice-to-have

| Задача | Описание |
|--------|----------|
| `/history [SESSION_ID]` | Список задач в сессии |
| `/health` команда | Статус бота: очередь, память, аптайм |
| Пагинация в `/sessions` | При 20+ сессиях список нечитаем |
| Pre-commit hooks | `ruff`, `mypy`, `black` автоматически при коммите |
| `pyproject.toml` | Единый конфиг: `python_requires>=3.10`, ruff, mypy |
| Per-user rate limiting | Один пользователь не может спамить очередь |
| Circuit breaker для Claude | Не вызывать Claude если он стабильно падает |

---

## Production Gate (текущее состояние)

```
[x] Scope понятен
[x] Tests проходят (27/27)
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
[ ] Healthcheck
[ ] Staging-окружение
[ ] SQLite вместо JSON
[ ] autoApprove вместо --dangerously-skip-permissions
```
