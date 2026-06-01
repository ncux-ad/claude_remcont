# Staging Environment

Как запустить второй экземпляр бота для тестирования правок без влияния на production.

## Принцип

Systemd-юнит уже является template unit (`claude-tg-bridge@.service`). Каждый экземпляр идентифицируется именем пользователя (`%i`). Staging запускается как тот же пользователь, но с отдельным `.env`-файлом через `systemd-run` или прямым запуском в терминале.

## Быстрый старт (ручной запуск)

```bash
# Создай отдельный .env для staging
cp .env.example .env.staging
# Заполни TG_BOT_TOKEN (другой тестовый бот), TG_ALLOWED_CHATS (свой chat_id)
# Измени пути логов, чтобы не пересекались с prod:
#   CLAUDE_LOG_FILE=~/claude_remcont/logs/listener-staging.log

# Запуск
env $(grep -v '^#' .env.staging | xargs) python tg_listener.py
```

## Через systemd (отдельный юнит)

Скопируй и переопредели переменные через drop-in:

```bash
sudo systemctl edit claude-tg-bridge@$USER
```

```ini
[Service]
EnvironmentFile=/home/%i/claude_remcont/.env.staging
```

Запусти как отдельный экземпляр с суффиксом:
```bash
# Нельзя запустить два template-экземпляра с одним именем пользователя.
# Вместо этого используй прямой запуск (см. выше) или отдельного системного пользователя.
```

## Переменные окружения для staging

| Переменная | Staging-значение |
|------------|-----------------|
| `TG_BOT_TOKEN` | Токен тестового бота (создать через @BotFather) |
| `TG_ALLOWED_CHATS` | Только твой личный chat_id |
| `CLAUDE_PROJECT_DIR` | Отдельный тестовый репозиторий или `/tmp/claude-test` |
| `CLAUDE_MAX_QUEUE_SIZE` | `5` (меньше для тестов) |
| `CLAUDE_PER_CHAT_QUEUE_LIMIT` | `2` |
| `CLAUDE_CB_THRESHOLD` | `2` (быстрее срабатывает circuit breaker) |
| `CLAUDE_TASK_TIMEOUT` | `60` (короткий таймаут) |

## Проверка конфига перед запуском

```bash
env $(grep -v '^#' .env.staging | xargs) python -c "
from config import validate_config
errors = validate_config()
if errors:
    for e in errors: print('ERROR:', e)
else:
    print('Config OK')
"
```
