# Community Bot — Test Branch

This branch contains a Bot API-only community assistant. Personal Telegram account login, OTP prompts, 2FA collection, session-string storage, and mass-report automation are not included.

## GitHub Actions secrets

Add these under **Settings → Secrets and variables → Actions**:

- `BOT_TOKEN` — token from @BotFather
- `OWNER_ID` — optional numeric Telegram user ID for owner admin access
- `OWNER_USERNAME` — optional display/contact username

No personal-account `API_ID`, `API_HASH`, `MONGO_URI`, OTP, 2FA password, or session string is required.

## Features

- `/start`, `/help`, `/id`, `/rules`
- Automatic welcome message for new group members
- Admin-only `/setwelcome`, `/setrules`, and `/settings`
- SQLite storage for each group's welcome message and rules

Welcome placeholders: `{first_name}` and `{username}`.

## Setup

1. Add the secrets above.
2. Add the bot to your group and grant permission to send messages.
3. Run **Community Bot (Test)** from GitHub Actions.

The workflow runs a syntax/dependency check before starting the bot. Keep this bot's database file backed up if you need to preserve settings between temporary runners.
