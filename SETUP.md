# Mass Report Bot — Updated Project

## GitHub Actions secrets

Add these repository secrets under **Settings → Secrets and variables → Actions**:

- `BOT_TOKEN` — Telegram bot token
- `API_ID` — Telegram API ID
- `API_HASH` — Telegram API hash
- `MONGO_URI` — MongoDB connection URI
- `OWNER_ID` — numeric Telegram user ID of the bot owner
- `OWNER_USERNAME` — owner username, with or without `@`

The Owner ID is required for Owner/Sudo access controls. If it is missing, no account will be recognized as Owner.

## Access levels

- **Owner:** full access and Sudo management (`/sudo USER_ID`, `/removesudo USER_ID`).
- **Sudo:** full bot access and `/sudoinfo`.
- **Standard user:** `/start`, `/host`, `/cancel`, and `/help`.

## Session status

The Status panel is sent as a normal Telegram message (not a code block). Its Refresh action checks saved Telethon sessions. A confirmed unauthorized session is marked expired; transient connection errors leave its previous status unchanged.

## Run

Run the `Mass Report Bot 24/7` workflow from GitHub Actions. The workflow uses the existing 5h 50m runner rotation.
