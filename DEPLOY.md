# Deployment Guide

## Prerequisites

- Linux server with systemd
- Python 3.10+
- [Claude Code CLI](https://claude.ai/code) installed and authenticated (`claude --version`)
- Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your Telegram chat ID (message [@userinfobot](https://t.me/userinfobot))

## 1. Clone the Repository

```bash
git clone https://github.com/ncux-ad/claude_remcont.git ~/claude_remcont
cd ~/claude_remcont
```

## 2. Install Dependencies

```bash
pip install -r requirements.txt
```

## 3. Configure Environment

```bash
cp .env.example ~/.env.claude-tg
nano ~/.env.claude-tg
```

Fill in:
- `TG_BOT_TOKEN` — your bot token
- `TG_ALLOWED_CHATS` — your Telegram chat ID
- `CLAUDE_PROJECT_DIR` — path to the project Claude will work on

## 4. Test Run (before systemd)

```bash
source ~/.env.claude-tg
python3 tg_listener.py
```

Send `/start` to your bot. If it replies — everything works. Press Ctrl+C to stop.

## 5. Configure Claude Code Stop Hook

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "python3 ~/claude_remcont/tg_notify.py"
      }]
    }]
  }
}
```

## 6. Install as systemd Service

```bash
# Install service template
sudo cp claude-tg-bridge.service /etc/systemd/system/claude-tg-bridge@.service

# Reload systemd and enable for your user
sudo systemctl daemon-reload
sudo systemctl enable --now claude-tg-bridge@$USER

# Verify it's running
sudo systemctl status claude-tg-bridge@$USER
```

## 7. Set Up Log Rotation

```bash
sudo cp claude-tg-bridge.logrotate /etc/logrotate.d/claude-tg-bridge
# Test rotation config
sudo logrotate --debug /etc/logrotate.d/claude-tg-bridge
```

## 8. Set Up Healthcheck (optional but recommended)

The bot writes a heartbeat file every 30 seconds. `check_health.py` reads it and sends a Telegram alert if the bot stops responding.

```bash
# Edit crontab
crontab -e
```

Add:
```
*/2 * * * * source ~/.env.claude-tg && python3 ~/claude_remcont/check_health.py
```

## 9. Verify Everything Works

```bash
# Check service status
sudo systemctl status claude-tg-bridge@$USER

# Follow live logs
journalctl -fu claude-tg-bridge@$USER

# Or read log file directly
tail -f ~/claude_remcont/logs/listener.log
```

Send a task to your bot and verify you get a reply.

## Useful Commands

```bash
# Stop the service
sudo systemctl stop claude-tg-bridge@$USER

# Restart after config change
sudo systemctl restart claude-tg-bridge@$USER

# View last 100 log lines
journalctl -u claude-tg-bridge@$USER -n 100 --no-pager
```

## Rollback Plan

```bash
# 1. Stop service
sudo systemctl stop claude-tg-bridge@$USER

# 2. Backup state files (SQLite databases)
cp ~/claude_remcont/logs/.queue.db ~/.queue.db.backup
cp ~/claude_remcont/logs/.sessions.db ~/.sessions.db.backup

# 3. Revert code
cd ~/claude_remcont
git log --oneline -5      # find the target commit
git checkout <commit>     # or: git revert HEAD

# 4. Restart
sudo systemctl start claude-tg-bridge@$USER

# 5. Verify
sudo systemctl status claude-tg-bridge@$USER
tail -20 ~/claude_remcont/logs/listener.log
```

State files (`.queue.db`, `.sessions.db`) are not touched by git — data is preserved across rollbacks.

## Running Tests

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests/ -v
```
