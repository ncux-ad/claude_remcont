# Claude Code ↔ Telegram Bridge

Run Claude Code tasks from your phone via a Telegram bot. Designed for Linux servers where Dispatch is unavailable.

## Architecture

```
Telegram → tg_listener.py → task queue → claude -p
                                               ↓
                              Stop Hook → tg_notify.py → Telegram
```

- `tg_listener.py` — long-polling bot, queues tasks, runs `claude -p`
- `tg_notify.py` — Claude Code Stop Hook, sends result back to Telegram
- `session_manager.py` — persists session IDs, supports `--resume`
- `queue_manager.py` — atomic JSON queue with rate limiting

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
| `/sessions` | List all sessions |
| `/session ID` | Switch to a specific session |
| `/new` | Next task will start a new session |
| `/label ID Name` | Rename a session |
| `/status` | Show current queue status |
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

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TG_BOT_TOKEN` | — | Telegram bot token (required) |
| `TG_ALLOWED_CHATS` | — | Comma-separated chat IDs (required) |
| `CLAUDE_PROJECT_DIR` | `~/claude_remcont` | Working directory for Claude |
| `CLAUDE_BIN` | `claude` | Claude CLI binary |
| `CLAUDE_TASK_TIMEOUT` | `0` | Task timeout in seconds (0 = none) |
| `CLAUDE_MAX_QUEUE_SIZE` | `50` | Max queued tasks before rejection |
