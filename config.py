import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Telegram API Credentials
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
LOGGER_BOT_TOKEN = os.getenv("LOGGER_BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "gcfhgyhujikuyjgfdcgvhbnjkmbot")
BOT_NAME = os.getenv("BOT_NAME", "adbot")
LOGGER_BOT_USERNAME = os.getenv("LOGGER_BOT_USERNAME", "rftyghuijkoijuytfrghjklkbot")

# Admin Settings
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_IDS = [ADMIN_ID] if ADMIN_ID else []

# Permanent Group Folder Link
PERMANENT_GROUP_FOLDER = os.getenv("PERMANENT_GROUP_FOLDER", "https://t.me/addlist/Qt31BHYkGgk2MGFl")

# Master Auto-Join Group Links (Folders, Private Invite Links & Public Groups)
DEFAULT_GROUP_LINKS = [
    "https://t.me/addlist/oA8W6ECFJV9kNjBl",
    "https://t.me/addlist/xmTsNdurof8wNDQ1",
    "https://t.me/All_country_kyc",
    "https://t.me/xmzf88",
    "https://t.me/RRYI456456",
    "https://t.me/GKBNotice",
    "https://t.me/kevcGroup",
    "https://t.me/Nigeriagroup1",
    "https://t.me/ngMessage",
    "https://t.me/gmailselling01",
    "https://t.me/AnjoCloud",
    "https://t.me/escrowbuyingselling",
    "https://t.me/shakyInfoFi",
    "https://t.me/usdt_chinaa",
    "https://t.me/chipperuu",
    "https://t.me/group_fuyou",
    "https://t.me/deatamsbunny",
    "https://t.me/xiaoyangg772",
    "https://t.me/addlist/gfuBZH23WB00Nzll",
    "https://t.me/+5awfQBjtDrE4YjQ5",
    "https://t.me/+4_l5EAm196YzNGNh",
    "https://t.me/+_kM5DymPn5U2YTNh",
    "https://t.me/+hjpS3mW9xkhhMmI1",
    "https://t.me/claudegiftlinkcursor",
    "https://t.me/GR_UPO",
    "https://t.me/+l8jV1bsbMq8wMDE1",
    "https://t.me/admtpo",
    "https://t.me/asoftwarez",
    "https://t.me/claudeaino1",
]

# Image URLs
START_IMAGE = "https://graph.org/file/9878e0f9785f390f5b5a3-2ae056edc4003b40e2.jpg"
BROADCAST_IMAGE = "https://graph.org/file/9878e0f9785f390f5b5a3-2ae056edc4003b40e2.jpg"
FORCE_JOIN_IMAGE = "https://graph.org/file/9878e0f9785f390f5b5a3-2ae056edc4003b40e2.jpg"

# Force Join Settings
ENABLE_FORCE_JOIN = False
MUST_JOIN_CHANNEL_ID = 0
MUSTJOIN_GROUP_ID = 0
MUST_JOIN_CHANNEL_URL = ""
MUSTJOIN_GROUP_URL = ""

# Channel and Group IDs
SETUP_GROUP_ID = 0
TECH_LOG_CHANNEL_ID = 0
GROUP_ID = 0

# External Links (set these via env vars or leave empty)
PRIVACY_POLICY_URL = os.getenv("PRIVACY_POLICY_URL", "")
SUPPORT_GROUP_URL = os.getenv("SUPPORT_GROUP_URL", "")
UPDATES_CHANNEL_URL = os.getenv("UPDATES_CHANNEL_URL", "")
GUIDE_URL = os.getenv("GUIDE_URL", "")
PRIVATE_CHANNEL_INVITE = os.getenv("PRIVATE_CHANNEL_INVITE", "")

# Encryption Key
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "ncWfmLS_S2IjSjW3HSK4FdEBkdyVImNPR-kcmRY9r14=")

# Database Configuration
MONGO_URI = os.getenv("MONGO_URI", "")
DB_NAME = "adsbot_db"

# Broadcast Settings
DEFAULT_DELAY = 600
MIN_DELAY = 60
MAX_DELAY = 86400

# OTP Settings
OTP_LENGTH = 5
OTP_EXPIRY = 300

# Logging Configuration
LOG_LEVEL = "INFO"
LOG_FILE = "logs/adbot.log"

# Feature Toggles
ENABLE_FORCE_JOIN = False
ENABLE_OTP_VERIFICATION = True
ENABLE_BROADCASTING = True
ENABLE_ANALYTICS = True

# Success Messages
SUCCESS_MESSAGES = {
    "account_added": "Account added successfully!",
    "otp_sent": "OTP sent to your phone number!",
    "broadcast_started": "Broadcast started successfully!",
    "broadcast_completed": "Broadcast completed successfully!",
    "accounts_deleted": "All accounts deleted successfully!"
}

# Error Messages
ERROR_MESSAGES = {
    "account_limit": "You've reached your account limit!",
    "invalid_phone": "Invalid phone number format! Use +1234567890",
    "otp_expired": "OTP has expired. Please restart hosting.",
    "invalid_otp": "Invalid OTP. Please try again.",
    "login_failed": "Failed to login to Telegram account!",
    "no_groups": "No groups found in your account!",
    "no_messages": "No messages found in Saved Messages!",
    "broadcast_limit": "Daily broadcast limit reached!",
    "unauthorized": "You are not authorized to perform this action!",
    "force_join_required": "Join required channels to access this feature!"
}

# Ad Source Channel
AD_SOURCE_CHANNEL = os.getenv("AD_SOURCE_CHANNEL", "")
