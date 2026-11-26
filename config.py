import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or "0")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").strip().lstrip("@")
