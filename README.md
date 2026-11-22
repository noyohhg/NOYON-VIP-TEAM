# NOYON PYTHON HOSTING

Telegram bot worker deployed on Railway.

## Start command

```bash
python -u bot.py
```

## Railway variables (Secrets)

- `BOT_TOKEN` — Telegram bot token from BotFather.
- `ADMIN_IDS` — comma-separated admin Telegram user IDs (for example, `5742494294`).
- `PROVIDER_API_URL` — provider API v2 URL (defaults to `https://my.smmgen.com/api/v2`).
- `PROVIDER_API_KEY` — provider API key; keep private.
- `DB_PATH` — optional SQLite path (recommended: `/data/noyon_smm.sqlite3` when a persistent Railway volume is mounted at `/data`).

Do not commit bot tokens or API keys to this repository. Attach a persistent volume at `/data` if SQLite data must survive redeploys. Run only one worker instance for this bot token to avoid Telegram polling conflicts.
