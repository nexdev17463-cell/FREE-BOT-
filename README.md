# Host-only Telegram bot

A minimal Telegram bot with exactly one command: `/host`.

The command checks the configured bot token with Telegram's Bot API `getMe`
method and displays the bot's own ID, username, online status, and runtime.

## Railway setup

1. Create a Railway service from the `host-only` branch.
2. Add the secret `BOT_TOKEN` (the token from @BotFather) in Railway Variables.
   `TELEGRAM_BOT_TOKEN` is also accepted as an alternative variable name.
3. Deploy. Railway uses `railway.json` and starts `python main.py`.
4. Open the bot's private chat and send `/host`.

Only the bot token is needed. `API_ID`, `API_HASH`, MongoDB, and user-session
secrets are not used by this standalone service.

## Scope and security

This branch intentionally contains no user-account login flow. It does not
request Telegram OTPs or 2FA passwords and does not create, export, or store
personal Telegram sessions.
