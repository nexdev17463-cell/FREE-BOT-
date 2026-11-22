import os

API_ID       = int(os.environ["API_ID"])
API_HASH     = os.environ["API_HASH"]
BOT_TOKEN    = os.environ["BOT_TOKEN"]
MONGO_URI    = os.environ["MONGO_URI"]

PROXY_ROTATE_INTERVAL = 1  # seconds

OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "@owner")
