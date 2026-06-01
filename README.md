# Claude Code ↔ Telegram Bridge

Run Claude Code tasks from your phone via a Telegram bot. Designed for Linux servers where Dispatch is unavailable.

## Architecture

```
Telegram → tg_listener.py → SQLite queue → claude -p
                                               ↓
                              Stop Hook → tg_notify.py → Telegram
```

- `tg_listener.py` — long-polling bot, queues tasks, runs `claude -p`
- `tg_notify.py` — Claude Code Stop Hook, sends result back to Telegram
- `session_manager.py` — per-chat session isolation, SQLite storage, `--resume`
- `queue_manager.py` — SQLite queue with global + per-chat rate limiting
- `circuit_breaker.py` — blocks chat after N consecutive Claude failures
- `check_health.py` — cron healthcheck via heartbeat file

## Requirements

- Python 3.10+
- [Claude Code CLI](https://claude.ai/code) installed and authenticated
- Telegram bot token from [@BotFather](https://t.me/BotFather)

## Quick Start

```bash
# 1. Clone
git clone https://github.com/ncux-ad/claude_remcont.git
cd claude_remcont

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example ~/.env.claude-tg
nano ~/.env.claude-tg   # fill in TG_BOT_TOKEN, TG_ALLOWED_CHATS, CLAUDE_PROJECT_DIR

# 4. Run
source ~/.env.claude-tg
python3 tg_listener.py
```

## Bot Commands

| Command | Description |
|---|---|
| `<any text>` | Queue a task for Claude Code |
| `/new <text>` | Queue a task in a fresh session |
| `/sessions` | List sessions (10 per page) |
| `/sessions 2` | Page 2 of sessions |
| `/session ID` | Switch to a specific session |
| `/new` | Next task will start a new session |
| `/label ID Name` | Rename a session |
| `/status` | Current queue status |
| `/history` | Last 10 tasks for this chat |
| `/stats` | Queue counts, sessions, uptime |
| `/start` | Show help |

## Claude Code Stop Hook

Add to `~/.claude/settings.json` so Claude notifies Telegram when done:

```json
{
  "hooks": {
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "python3 /path/to/claude_remcont/tg_notify.py"
      }]
    }]
  }
}
```

## Production Deploy (systemd)

See [DEPLOY.md](DEPLOY.md) for full instructions.

```bash
sudo cp claude-tg-bridge.service /etc/systemd/system/claude-tg-bridge@.service
sudo systemctl enable --now claude-tg-bridge@$USER
sudo journalctl -fu claude-tg-bridge@$USER
```

## Log Rotation

```bash
sudo cp claude-tg-bridge.logrotate /etc/logrotate.d/claude-tg-bridge
```

## Healthcheck (cron)

```bash
# Check every 2 minutes; alert via Telegram if bot stops updating heartbeat
*/2 * * * * source ~/.env.claude-tg && python3 /path/to/check_health.py
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TG_BOT_TOKEN` | — | Telegram bot token (required) |
| `TG_ALLOWED_CHATS` | — | Comma-separated chat IDs (required) |
| `CLAUDE_PROJECT_DIR` | `~/claude_remcont` | Working directory for Claude |
| `CLAUDE_BIN` | `claude` | Claude CLI binary |
| `CLAUDE_TASK_TIMEOUT` | `0` | Task timeout in seconds (0 = none) |
| `CLAUDE_MAX_QUEUE_SIZE` | `50` | Global max queued tasks |
| `CLAUDE_PER_CHAT_QUEUE_LIMIT` | `5` | Max queued tasks per chat |
| `CLAUDE_CB_THRESHOLD` | `3` | Failures before circuit breaker opens |
| `CLAUDE_CB_COOLDOWN` | `300` | Seconds circuit breaker stays open |
| `CLAUDE_HEARTBEAT_MAX_AGE` | `120` | Seconds before healthcheck alerts |
| `CLAUDE_QUEUE_CLEANUP_DAYS` | `7` | Days before done/error tasks are deleted |
| `SESSION_CLEANUP_DAYS` | `30` | Days before unused sessions are deleted |

## Staging

See [docs/STAGING.md](docs/STAGING.md) for running a second bot instance for testing.
