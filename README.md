# USCIS Policy Memoranda Monitor

Monitors https://www.uscis.gov/laws-and-policy/policy-memoranda and sends a Telegram alert whenever a new policy memorandum is published.

## Setup

### 1. Fork / push to GitHub
Push this repo to your GitHub account.

### 2. Deploy to Render (free)

1. Go to https://render.com and sign in with GitHub
2. Click **New → Background Worker**
3. Connect your GitHub repo
4. Set these environment variables:
   - `TELEGRAM_TOKEN` — your Telegram bot token
   - `TELEGRAM_CHAT_ID` — your Telegram chat ID
   - `CHECK_INTERVAL_MINUTES` — how often to check (default: `30`)
5. Click **Deploy**

### 3. Deploy to Railway (alternative)

1. Go to https://railway.app and sign in with GitHub
2. Click **New Project → Deploy from GitHub repo**
3. Add environment variables (same as above)
4. Railway auto-detects the Procfile and deploys

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_TOKEN` | Yes | — | Your Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Yes | — | Your Telegram chat ID |
| `CHECK_INTERVAL_MINUTES` | No | `30` | How often to check USCIS (minutes) |

## Local development

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=your_token
export TELEGRAM_CHAT_ID=your_chat_id
python monitor.py
```
