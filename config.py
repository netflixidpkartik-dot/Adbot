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
BOT_USERNAME = "TecxoAdsBot"
BOT_NAME = "Tecxo Ads 🚀"
LOGGER_BOT_USERNAME = "AdbotLogv7bot"

# Admin Settings
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_USERNAME = "TecxoChat"
ADMIN_IDS = [ADMIN_ID] if ADMIN_ID else []

# Permanent Group Folder Link
PERMANENT_GROUP_FOLDER = os.getenv("PERMANENT_GROUP_FOLDER", "https://t.me/addlist/Qt31BHYkGgk2MGFl")

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

# External Links
PRIVACY_POLICY_URL = "https://t.me/TecxoChat"
SUPPORT_GROUP_URL = "https://t.me/TecxoChat"
UPDATES_CHANNEL_URL = "https://t.me/TecxoChat"
GUIDE_URL = "https://t.me/TecxoChat"
PRIVATE_CHANNEL_INVITE = "https://t.me/TecxoChat"

# Encryption Key
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")

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
LOG_FILE = "logs/TecxoAds.log"

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
    "account_limit": "You've reached your account limit of 5! Get higher limit contact @TecxoChat",
    "invalid_phone": "Invalid phone number format! Use +1234567890",
    "otp_expired": "OTP has expired. Please restart hosting.",
    "invalid_otp": "Invalid OTP. Please try again.",
    "login_failed": "Failed to login to Telegram account!",
    "no_groups": "No groups found in your account!",
    "no_messages": "No messages found in Saved Messages!",
    "broadcast_limit": "Daily broadcast limit reached! Get higher limit contact @TecxoChat",
    "unauthorized": "You are not authorized to perform this action!",
    "force_join_required": "Join required channels to access this feature!"
}

# Session Storage
SESSION_STORAGE_PATH = "sessions/"

# Ad Source Channel
# All accounts will auto-use the latest message posted here
AD_SOURCE_CHANNEL = "https://t.me/+XIBMyi8VCF1jMjQ9"
