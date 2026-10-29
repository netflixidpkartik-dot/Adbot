import asyncio
import sys

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())
import random
import string
import re
import json
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient, functions, types, events
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest, UpdateNotifySettingsRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions import chatlists, channels, messages
from telethon.tl.types import (
    chatlists as chatlist_types,
    InputChatlistDialogFilter,
    InputNotifyPeer,
    InputPeerNotifySettings,
)
from telethon.tl.types import (
    MessageEntityCustomEmoji,
    MessageEntityBold,
    MessageEntityItalic,
    MessageEntityCode,
    MessageEntityPre,
    MessageEntityTextUrl,
    MessageEntityMention,
    MessageEntityStrike,
    MessageEntityUnderline,
    MessageEntitySpoiler,
    MessageEntityBlockquote,
)
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    UpdateAppToLoginError,
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionExpiredError,
    SessionRevokedError,
    AuthKeyUnregisteredError,
    UserDeactivatedError,
    PasswordHashInvalidError,
    AboutTooLongError,
    ChannelsTooMuchError,
    InviteHashExpiredError,
    UserAlreadyParticipantError
)
from pyrogram import Client as PyroClient, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from pyrogram.errors import UserNotParticipant, PeerIdInvalid, ChatWriteForbidden, FloodWait, MessageNotModified
from pyrogram.enums import ParseMode, ChatType
import config
from database import EnhancedDatabaseManager
import os
import time
import sqlite3
import base64
import struct
import ipaddress
import zipfile
import shutil
import logging
from cryptography.fernet import Fernet

# Helper functions
def validate_phone_number(phone):
    pattern = r'^\+[1-9]\d{6,14}$'
    return bool(re.match(pattern, phone))


def get_account_display_name(acc):
    """Return friendly profile name for an account, falling back to phone number."""
    if not acc:
        return "Unknown Account"
    fn = (acc.get("first_name") or "").strip()
    ln = (acc.get("last_name") or "").strip()
    full_name = f"{fn} {ln}".strip()
    if full_name:
        return full_name
    return acc.get("phone_number") or "Unknown Account"


def entities_to_dict(entities):
    """Serialize Pyrogram message entities to plain-Python dicts safe for MongoDB."""
    if not entities:
        return []
    result = []
    for e in entities:
        try:
            # Always cast to plain str — Pyrogram enums are NOT BSON-serializable
            if hasattr(e, 'type'):
                raw_type = e.type
                etype = raw_type.value if hasattr(raw_type, 'value') else str(raw_type)
                etype = str(etype)   # guarantee plain str
            else:
                continue            # skip unknown entity shapes

            d = {
                "type": etype,
                "offset": int(e.offset),   # guarantee plain int
                "length": int(e.length),   # guarantee plain int
            }
            # custom_emoji_id can be a huge int — always store as str
            if hasattr(e, 'custom_emoji_id') and e.custom_emoji_id is not None:
                d["custom_emoji_id"] = str(e.custom_emoji_id)
            if hasattr(e, 'url') and e.url:
                d["url"] = str(e.url)
            result.append(d)
        except Exception as ex:
            logger.warning(f"Skipped un-serializable entity {e}: {ex}")
    return result


def pyrogram_entities_to_telethon(entities_data):
    """Convert saved entity dicts back to Telethon entity objects for sending."""
    if not entities_data:
        return None
    telethon_entities = []
    for e in entities_data:
        etype = e.get("type", "")
        offset = e.get("offset", 0)
        length = e.get("length", 0)
        try:
            if etype == "custom_emoji":
                doc_id = int(e.get("custom_emoji_id", 0))
                if doc_id:
                    telethon_entities.append(MessageEntityCustomEmoji(offset=offset, length=length, document_id=doc_id))
            elif etype == "bold":
                telethon_entities.append(MessageEntityBold(offset=offset, length=length))
            elif etype == "italic":
                telethon_entities.append(MessageEntityItalic(offset=offset, length=length))
            elif etype == "code":
                telethon_entities.append(MessageEntityCode(offset=offset, length=length))
            elif etype == "pre":
                telethon_entities.append(MessageEntityPre(offset=offset, length=length, language=""))
            elif etype == "text_link":
                telethon_entities.append(MessageEntityTextUrl(offset=offset, length=length, url=e.get("url", "")))
            elif etype == "mention":
                telethon_entities.append(MessageEntityMention(offset=offset, length=length))
            elif etype == "strikethrough":
                telethon_entities.append(MessageEntityStrike(offset=offset, length=length))
            elif etype == "underline":
                telethon_entities.append(MessageEntityUnderline(offset=offset, length=length))
            elif etype == "spoiler":
                telethon_entities.append(MessageEntitySpoiler(offset=offset, length=length))
            elif etype == "blockquote":
                telethon_entities.append(MessageEntityBlockquote(offset=offset, length=length))
        except Exception:
            pass
    return telethon_entities if telethon_entities else None



def generate_progress_bar(current, total, length=10):
    if total <= 0:
        return "[░░░░░░░░░░] 0%"
    percent = current / total
    filled = int(length * percent)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {int(percent * 100)}%"

# Logging setup
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/adbot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Optimize Telethon: disable supergroup/channel difference fetching to keep memory low and prevent OOM
try:
    import telethon._updates.messagebox as mb
    mb.MessageBox.get_channel_difference = lambda *args, **kwargs: None
    logger.info("Telethon channel difference polling disabled for optimal memory efficiency.")
except Exception as e:
    logger.debug(f"Telethon MessageBox patch notice: {e}")

class TelethonWrongSessionFilter(logging.Filter):
    """
    Suppresses rapid flooding of 'Server replied with a wrong session ID' warnings.
    Throttles it to at most 1 warning per 60s so unexpected packets never choke stdout or memory.
    """
    def __init__(self):
        super().__init__()
        self.last_logged = 0

    def filter(self, record):
        if "wrong session ID" in record.getMessage():
            now = time.time()
            if now - self.last_logged < 60:
                return False
            self.last_logged = now
        return True

logging.getLogger('telethon.network.mtprotosender').addFilter(TelethonWrongSessionFilter())

print("Adbot Started. 🚀")

# Initialize encryption key with persistence across containers
ENCRYPTION_KEY = getattr(config, 'ENCRYPTION_KEY', None)
if not ENCRYPTION_KEY and getattr(config, 'MONGO_URI', None):
    try:
        from pymongo import MongoClient
        _client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=4000)
        _db = _client[getattr(config, 'DB_NAME', 'adsbot_db')]
        _doc = _db.settings.find_one({"_id": "app_encryption_key"})
        if _doc and _doc.get("key"):
            ENCRYPTION_KEY = _doc["key"]
            logger.info("Retrieved ENCRYPTION_KEY from MongoDB settings.")
    except Exception as e:
        logger.warning(f"Could not load ENCRYPTION_KEY from MongoDB: {e}")

KEY_FILE = 'encryption.key'
if not ENCRYPTION_KEY:
    logger.warning("No ENCRYPTION_KEY in config or DB. Loading or generating from file.")
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, 'r') as f:
            ENCRYPTION_KEY = f.read().strip()
    else:
        ENCRYPTION_KEY = Fernet.generate_key().decode()
        with open(KEY_FILE, 'w') as f:
            f.write(ENCRYPTION_KEY)
        logger.info("Generated and saved new encryption key to encryption.key")
else:
    try:
        with open(KEY_FILE, 'w') as f:
            f.write(ENCRYPTION_KEY)
    except Exception:
        pass
    logger.info("Using ENCRYPTION_KEY.")

if ENCRYPTION_KEY and getattr(config, 'MONGO_URI', None):
    try:
        from pymongo import MongoClient
        _client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=4000)
        _db = _client[getattr(config, 'DB_NAME', 'adsbot_db')]
        _db.settings.update_one(
            {"_id": "app_encryption_key"},
            {"$set": {"key": ENCRYPTION_KEY, "updated_at": datetime.now()}},
            upsert=True
        )
    except Exception as e:
        logger.warning(f"Could not persist ENCRYPTION_KEY to MongoDB: {e}")

cipher_suite = Fernet(ENCRYPTION_KEY.encode())

# Initialize database
try:
    db = EnhancedDatabaseManager()
except Exception as e:
    logger.error(f"Failed to initialize database: {e}. Exiting.")
    print("Bot failed to start due to database error. Check logs/adbot.log for details.")
    exit(1)

# Admin check
ADMIN_IDS = [config.ADMIN_ID, 8104033602, 8835800450] if config.ADMIN_ID else [8104033602, 8835800450]
ALLOWED_BD_IDS = ADMIN_IDS

def is_owner(uid):
    return uid in ALLOWED_BD_IDS

# Inline keyboard helper
def kb(rows):
    if not isinstance(rows, list) or not all(isinstance(row, list) for row in rows):
        logger.error("Invalid rows format for InlineKeyboardMarkup")
        raise ValueError("Rows must be a list of lists")
    return InlineKeyboardMarkup(rows)

# Initialize Pyrogram clients
pyro = PyroClient("adbot", api_id=config.API_ID, api_hash=config.API_HASH, bot_token=config.BOT_TOKEN)
logger_client = PyroClient("logger_bot", api_id=config.API_ID, api_hash=config.API_HASH, bot_token=config.LOGGER_BOT_TOKEN)

# In-memory storage for broadcast tasks
user_tasks = {}

# Global persistent Telethon clients for 24/7 DM forwarding across all hosted accounts:
# {str(acc_id): {"client": tg_client, "phone": phone, "session_str": session_str, "user_id": uid}}
global_account_clients = {}
autoreply_clients = global_account_clients

# Account-level bans (the entire Telegram account / session is blocked or revoked)
ACCOUNT_BAN_ERRORS = (
    "UserDeactivated",
    "UserDeactivatedBan",
    "AuthKeyUnregistered",
    "AuthKeyDuplicated",
    "SessionRevoked",
    "AccountBanned",
)

# Group-level restrictions (the group muted the account, write permissions disabled, or admin-only)
GROUP_RESTRICTED_ERRORS = (
    "ChatWriteForbidden",
    "UserBannedInChannel",
    "ChannelPrivate",
    "ChatAdminRequired",
    "RightForbidden",
    "ChatRestricted",
    "ChatGuestSendForbidden",
    "ChatSendMediaForbidden",
    "ChatSendGifsForbidden",
    "ChatSendPlainForbidden",
    "SlowmodeWait",
    "ChannelInvalid",
    "You cannot write in this chat",
    "You're restricted from sending messages in this chat",
    "restricted from sending messages",
    "can't write in this chat",
    "cannot write in this chat",
    "banned from this group",
    "banned in channel",
    "muted in this group",
    "not allowed to speak",
    "write forbidden",
    "send messages in this group",
    "send messages in this channel",
    "restricted until",
    "CHAT_RESTRICTED",
    "CHAT_WRITE_FORBIDDEN",
    "USER_BANNED_IN_CHANNEL",
    "CHANNEL_PRIVATE",
    "USER_RESTRICTED",
    "forbidden",
    "restricted",
)

BAN_ERRORS = ACCOUNT_BAN_ERRORS

DC_IPS = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130"
}

async def load_session_from_file(file_path):
    """
    Load an authorized session from a .session file.
    Supports:
      1. Telethon SQLite .session files
      2. Pyrogram SQLite .session files (v1 and v2)
      3. Plain text string session files
    Returns (session_string, user_entity) or (None, None).
    """
    # ── Method 1: Universal SQLite parser (Telethon + Pyrogram) ─────────────
    try:
        conn = sqlite3.connect(file_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM sessions LIMIT 1")
        row = cur.fetchone()
        conn.close()

        if row:
            row_keys = row.keys()
            dc_id = int(row['dc_id'])
            
            # Extract auth_key (BLOB)
            auth_key = row['auth_key']
            if isinstance(auth_key, memoryview):
                auth_key = bytes(auth_key)
            elif not isinstance(auth_key, bytes):
                auth_key = bytes(auth_key)

            # Determine server IP and port
            if 'server_address' in row_keys and row['server_address']:
                server_ip = str(row['server_address'])
            else:
                server_ip = DC_IPS.get(dc_id, "149.154.167.51")

            port = int(row['port']) if ('port' in row_keys and row['port']) else 443

            ip_bytes = ipaddress.ip_address(server_ip).packed
            if len(ip_bytes) == 4:
                session_bytes = struct.pack('>B4sH', dc_id, ip_bytes, port) + auth_key
            else:
                session_bytes = struct.pack('>B16sH', dc_id, ip_bytes, port) + auth_key

            session_str = '1' + base64.urlsafe_b64encode(session_bytes).decode('ascii')

            tg = TelegramClient(StringSession(session_str), config.API_ID, config.API_HASH)
            await tg.connect()
            if await tg.is_user_authorized():
                me = await tg.get_me()
                saved_str = tg.session.save()
                await tg.disconnect()
                logger.info(f"Successfully loaded session for {getattr(me, 'phone', me.id)} via SQLite parser")
                return saved_str, me
            await tg.disconnect()
            logger.warning("Session extracted from SQLite but is_user_authorized returned False (account logged out/banned)")
    except Exception as e:
        logger.warning(f"Universal SQLite session extraction failed: {e}")

    # ── Method 2: Direct Telethon SQLite load fallback ───────────────────────
    try:
        session_base = file_path[:-8] if file_path.endswith('.session') else file_path
        tg = TelegramClient(session_base, config.API_ID, config.API_HASH)
        await tg.connect()
        if await tg.is_user_authorized():
            me = await tg.get_me()
            ss = StringSession()
            ss._dc_id = tg.session.dc_id
            ss._server_address = tg.session.server_address
            ss._port = tg.session.port
            ss._auth_key = tg.session.auth_key
            saved_str = ss.save()
            await tg.disconnect()
            logger.info(f"Successfully loaded session for {getattr(me, 'phone', me.id)} via Telethon fallback")
            return saved_str, me
        await tg.disconnect()
    except Exception as e:
        logger.warning(f"Telethon direct session file fallback failed: {e}")

    # ── Method 3: Text / StringSession in file fallback ──────────────────────
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read().strip()
        if content and len(content) > 50:
            saved_str, me = await load_session_from_string(content)
            if saved_str and me:
                logger.info(f"Successfully loaded session for {getattr(me, 'phone', me.id)} via text file fallback")
                return saved_str, me
    except Exception as e:
        logger.warning(f"Text file session load fallback failed: {e}")

    return None, None


async def load_session_from_string(session_str):
    """
    Load an authorized session from a string session.
    Supports Telethon StringSession and Pyrogram StringSession.
    Returns (session_string, user_entity) or (None, None).
    """
    raw_str = session_str.strip()
    
    # Try 1: Telethon StringSession
    try:
        tg = TelegramClient(StringSession(raw_str), config.API_ID, config.API_HASH)
        await tg.connect()
        if await tg.is_user_authorized():
            me = await tg.get_me()
            saved_str = tg.session.save()
            await tg.disconnect()
            return saved_str, me
        await tg.disconnect()
    except Exception as e:
        logger.warning(f"Telethon string session attempt failed: {e}")

    # Try 2: Pyrogram StringSession -> convert to Telethon
    try:
        pyro_client = PyroClient("temp_auth", api_id=config.API_ID, api_hash=config.API_HASH, session_string=raw_str, in_memory=True)
        await pyro_client.start()
        me_pyro = await pyro_client.get_me()
        auth_key = pyro_client.storage.auth_key.key
        dc_id = pyro_client.storage.dc_id
        await pyro_client.stop()
        
        server_ip = DC_IPS.get(dc_id, "149.154.167.51")
        ip = ipaddress.ip_address(server_ip).packed
        if len(ip) == 4:
            session_bytes = struct.pack('>B4sH', dc_id, ip, 443) + auth_key
        else:
            session_bytes = struct.pack('>B16sH', dc_id, ip, 443) + auth_key

        telethon_str = '1' + base64.urlsafe_b64encode(session_bytes).decode('ascii')
        tg = TelegramClient(StringSession(telethon_str), config.API_ID, config.API_HASH)
        await tg.connect()
        if await tg.is_user_authorized():
            me = await tg.get_me()
            saved_str = tg.session.save()
            await tg.disconnect()
            return saved_str, me
        await tg.disconnect()
    except Exception as e:
        logger.warning(f"Pyrogram string session conversion failed: {e}")

    return None, None


def normalize_tg_id(gid):
    """Normalize Telegram group/channel IDs across Pyrogram (-100...) and Telethon formats."""
    s = str(gid)
    if s.startswith("-100"):
        return int(s[4:])
    elif s.startswith("-"):
        return int(s[1:])
    try:
        return int(s)
    except Exception:
        return gid


# Invisible Unicode chars to vary message fingerprint so each send is unique
INVISIBLE_CHARS = ["\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"]


def parse_spintax(text: str) -> str:
    """
    Parse spintax patterns like {Hello|Hi|Hey} or nested {A|{B|C}} randomly.
    Returns randomly selected variation.
    """
    if not text:
        return ""
    pattern = re.compile(r'\{([^{}]+)\}')
    while True:
        match = pattern.search(text)
        if not match:
            break
        options = match.group(1).split('|')
        chosen = random.choice(options)
        text = text[:match.start()] + chosen + text[match.end():]
    return text


def vary_message(msg: str) -> str:
    """Expand spin-tax variants and append an invisible character for zero-duplicate fingerprint."""
    if not msg:
        return ""
    expanded = parse_spintax(msg)
    return expanded + random.choice(INVISIBLE_CHARS)


async def safe_reconnect(tg_client, session_str, phone):
    """Try to reconnect a dropped Telethon client safely without duplicate connections."""
    try:
        if tg_client.is_connected():
            return True
        sender = getattr(tg_client, '_sender', None)
        # If Telethon is already reconnecting in background, wait for it
        if sender and getattr(sender, '_reconnecting', False):
            for _ in range(5):
                await asyncio.sleep(2)
                if tg_client.is_connected():
                    return True
        await asyncio.sleep(random.uniform(2, 4))
        await tg_client.connect()
        if await tg_client.is_user_authorized():
            logger.info(f"Reconnected account {phone}")
            return True
        logger.warning(f"Account {phone} not authorized after reconnect")
        return False
    except Exception as e:
        logger.error(f"Reconnect failed for {phone}: {e}")
        return False

async def safe_leave_group(tg_client, gid, user_id=None):
    """Safely leave a group or channel via Telethon and clean from database."""
    left = False
    try:
        entity = await tg_client.get_entity(gid)
        await tg_client(functions.channels.LeaveChannelRequest(channel=entity))
        logger.info(f"Successfully left channel/megagroup {gid} via LeaveChannelRequest")
        left = True
    except Exception as e1:
        try:
            await tg_client.delete_dialog(gid)
            logger.info(f"Successfully left dialog {gid} via delete_dialog")
            left = True
        except Exception as e2:
            logger.warning(f"Could not leave group {gid}: {e1} | {e2}")

    if user_id:
        try:
            db.delete_target_group(user_id, gid)
        except Exception:
            pass
    return left


async def send_dm_log(user_id, log_message):
    if not db.get_logger_status(user_id):
        logger.info(f"User {user_id} has not started logger bot. Skipping DM log.")
        return
    try:
        await logger_client.resolve_peer(user_id)
        if len(log_message) > 4000:
            log_message = log_message[:3985] + "\n\n<i>[Truncated]</i>"
        await logger_client.send_message(user_id, log_message, parse_mode=ParseMode.HTML)
        logger.info(f"DM log sent to {user_id}: {log_message[:50]}...")
    except PeerIdInvalid:
        logger.error(f"DM log failed for {user_id}: Peer not found. User must start logger bot.")
        db.log_logger_failure(user_id, "PeerIdInvalid: User must start logger bot")
        try:
            await pyro.send_message(
                user_id,
                "<b>⚠️ Logger bot not started!</b>\n\n"
                f"Please start @{config.LOGGER_BOT_USERNAME} to receive broadcast logs. 🌟",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Start Logger Bot 📩", url=f"https://t.me/{config.LOGGER_BOT_USERNAME.lstrip('@')}")]])
            )
        except Exception as e:
            logger.error(f"Failed to notify user {user_id} to start logger bot: {e}")
    except Exception as e:
        logger.error(f"DM log failed for {user_id}: {e} - Message: {log_message[:50]}...")
        db.log_logger_failure(user_id, str(e))

@logger_client.on_message(filters.command(["start"]))
async def logger_start(client, m):
    uid = m.from_user.id
    username = m.from_user.username or "Unknown"
    first_name = m.from_user.first_name or "User"
    
    db.create_user(uid, username, first_name)
    if is_owner(uid):
        db.db.users.update_one({"user_id": uid}, {"$set": {"accounts_limit": "unlimited"}})
    db.set_logger_status(uid, is_active=True)
    await m.reply(
        f"<b>╰_╯Welcome to Adbot Logger Bot! </b>\n\n"
        f"Logs for your ad broadcasts will be sent here.\n"
        f"Start the main bot to begin broadcasting! 🌟",
        parse_mode=ParseMode.HTML
    )
    logger.info(f"Logger bot started by user {uid}")

async def is_joined(client, uid, chat_id):
    max_retries = 3
    retry_delay = 2
    for attempt in range(max_retries):
        try:
            logger.info(f"Checking membership for user {uid} in chat_id {chat_id} (Attempt {attempt + 1})")
            await client.get_chat_member(chat_id, uid)
            logger.info(f"User {uid} is a member of chat_id {chat_id}")
            return True
        except UserNotParticipant:
            logger.info(f"User {uid} is not a member of chat_id {chat_id}")
            return False
        except Exception as e:
            logger.error(f"Attempt {attempt + 1}: Failed to check join status for {uid} in chat_id {chat_id}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
                continue
            logger.error(f"All retries failed for user {uid} in chat_id {chat_id}: {e}")
            return False
    return False

async def is_joined_all(client, uid):
    channel_joined = await is_joined(client, uid, config.MUST_JOIN_CHANNEL_ID)
    await asyncio.sleep(0.5)
    group_joined = await is_joined(client, uid, config.MUSTJOIN_GROUP_ID)
    logger.info(f"User {uid} - Channel ({config.MUST_JOIN_CHANNEL_ID}) joined: {channel_joined}, Group ({config.MUSTJOIN_GROUP_ID}) joined: {group_joined}")
    if not channel_joined:
        logger.info(f"User {uid} has not joined channel {config.MUST_JOIN_CHANNEL_ID}")
    if not group_joined:
        logger.info(f"User {uid} has not joined group {config.MUSTJOIN_GROUP_ID}")
    return channel_joined and group_joined

async def validate_session(session_str):
    try:
        tg_client = TelegramClient(StringSession(session_str), config.API_ID, config.API_HASH)
        await tg_client.connect()
        is_valid = await tg_client.is_user_authorized()
        await tg_client.disconnect()
        return is_valid
    except Exception as e:
        logger.error(f"Session validation failed: {e}")
        return False

async def stop_broadcast_task(uid):
    task = user_tasks.pop(uid, None)
    if task:
        try:
            task.cancel()
            await task
            logger.info(f"Cancelled broadcast task for {uid}")
        except asyncio.CancelledError:
            logger.info(f"Broadcast task for {uid} was cancelled successfully")
        except Exception as e:
            logger.error(f"Failed to cancel broadcast task for {uid}: {e}")
    
    state = db.get_broadcast_state(uid)
    was_running = state.get("running", False)
    db.set_broadcast_state(uid, running=False)
    return was_running or (task is not None)

async def get_channel_ad_message(tg_client):
    """Fetch the latest message (text or media) from the owner's ad source channel."""
    try:
        channel = await tg_client.get_entity(config.AD_SOURCE_CHANNEL)
        messages = await tg_client.get_messages(channel, limit=1)
        if messages:
            msg = messages[0]
            # Return tuple: (text/caption, media_obj)
            caption = msg.text or ""
            media = msg.media if msg.media else None
            if caption or media:
                return caption, media
        logger.warning("No message found in AD_SOURCE_CHANNEL")
        return None, None
    except Exception as e:
        logger.error(f"Failed to fetch ad message from channel: {e}")
        return None, None


async def send_ad_doc(tg_client, gid, ad_doc):
    """Send an ad message document to a group entity (supports text, photo, both, forward)."""
    ad_type = ad_doc.get("ad_type", "text")
    caption = ad_doc.get("message", "")
    media = ad_doc.get("photo_path")
    photo_data = ad_doc.get("photo_data")
    if (not media or not os.path.exists(media)) and photo_data:
        try:
            os.makedirs("downloads", exist_ok=True)
            ad_id_str = str(ad_doc.get("_id", "temp"))
            restored_path = os.path.join("downloads", f"restored_ad_{ad_id_str}.jpg")
            with open(restored_path, "wb") as f:
                f.write(photo_data)
            media = restored_path
            logger.info(f"Restored ad photo from MongoDB to {restored_path}")
        except Exception as rest_err:
            logger.warning(f"Failed to restore photo_data for ad {ad_doc.get('_id')}: {rest_err}")

    entities_data = ad_doc.get("entities", [])
    from_chat = ad_doc.get("from_chat_id")
    msg_id = ad_doc.get("message_id")

    varied_caption = vary_message(caption or "") if caption else ""
    tl_entities = pyrogram_entities_to_telethon(entities_data) if entities_data else None

    media_exists = True
    if media and isinstance(media, str) and not media.startswith(("http://", "https://")):
        media_exists = os.path.exists(media)

    if ad_type == "forward" and from_chat and msg_id:
        await tg_client.forward_messages(
            entity=gid,
            messages=msg_id,
            from_peer=from_chat,
            drop_author=True,
        )
    elif media and media_exists and ad_type in ("photo", "both"):
        try:
            await tg_client.send_file(
                gid, file=media, caption=varied_caption,
                formatting_entities=tl_entities
            )
        except Exception as media_err:
            m_err_str = str(media_err).lower()
            if caption and any(k in m_err_str for k in ("sendmedia", "send_media", "chat_send_media_forbidden", "chatsendmediaforbidden", "photos_forbidden", "media_forbidden", "userbannedinchannel")):
                logger.warning(f"Media send blocked in {gid} ({media_err}). Automatically falling back to text-only send...")
                await tg_client.send_message(
                    gid, varied_caption,
                    formatting_entities=tl_entities
                )
            else:
                raise media_err
    elif caption:
        await tg_client.send_message(
            gid, varied_caption,
            formatting_entities=tl_entities
        )
    elif media and media_exists:
        try:
            await tg_client.send_file(
                gid, file=media, caption=varied_caption,
                formatting_entities=tl_entities
            )
        except Exception as media_err:
            m_err_str = str(media_err).lower()
            if caption and any(k in m_err_str for k in ("sendmedia", "send_media", "chat_send_media_forbidden", "chatsendmediaforbidden", "photos_forbidden", "media_forbidden", "userbannedinchannel")):
                logger.warning(f"Media send blocked in {gid} ({media_err}). Automatically falling back to text-only send...")
                await tg_client.send_message(
                    gid, varied_caption,
                    formatting_entities=tl_entities
                )
            else:
                raise media_err
    else:
        await tg_client.send_message(
            gid, varied_caption,
            formatting_entities=tl_entities
        )


# Track when an account last appealed to @SpamBot to avoid spamming the bot
spambot_appeals_last_time = {}

# In-memory storage for SpamBot restricted accounts: {acc_id: {"phone": phone, "expiry_ts": expiry_ts, "user_id": uid}}
spambot_restricted_accounts = {}
# In-memory storage for resting / cooldown accounts: {acc_id: expiry_ts}
account_cooldowns = {}

async def check_spambot_status(tg_client):
    """SpamBot check disabled per user instruction."""
    return False, 0, "SpamBot check disabled"


async def appeal_spambot(tg_client, phone, user_id, acc_id=None):
    """SpamBot checking and pausing disabled per user instruction."""
    return


def check_entity_restricted(entity):
    """
    Check if an entity is restricted, muted, or write-forbidden.
    Returns: (is_restricted, should_leave, reason)
    Policy:
    - If mute duration is <= 48 hours: should_leave = False (temporary mute/wait, ignore/stay).
    - If permanent or longer than 48 hours: should_leave = True (leave group).
    """
    if not entity:
        return False, False, None

    now = datetime.now(timezone.utc)

    def parse_rem_seconds(until_date):
        if not until_date:
            return None
        if isinstance(until_date, datetime):
            if until_date.tzinfo is None:
                until_date = until_date.replace(tzinfo=timezone.utc)
            return (until_date - now).total_seconds()
        elif isinstance(until_date, (int, float)):
            return float(until_date) - time.time()
        return None

    # 1. Account specific banned_rights in this chat
    banned_rights = getattr(entity, 'banned_rights', None)
    if banned_rights and getattr(banned_rights, 'send_messages', False):
        until_date = getattr(banned_rights, 'until_date', None)
        rem_seconds = parse_rem_seconds(until_date)
        if rem_seconds is not None:
            if 0 < rem_seconds <= 48 * 3600:
                hours_left = round(rem_seconds / 3600, 1)
                return True, False, f"Temporary mute ({hours_left}h left, <= 48h)"
            else:
                return True, True, "Muted longer than 48h or permanent"
        else:
            return True, True, "Permanently muted by group admin"

    # 2. General channel restriction flag
    if getattr(entity, 'restricted', False):
        return True, True, "Channel restricted by Telegram"

    # 3. Default banned rights (group write closed for normal members)
    default_rights = getattr(entity, 'default_banned_rights', None)
    admin_rights = getattr(entity, 'admin_rights', None)
    if default_rights and getattr(default_rights, 'send_messages', False) and not admin_rights:
        until_date = getattr(default_rights, 'until_date', None)
        rem_seconds = parse_rem_seconds(until_date)
        if rem_seconds is not None:
            if 0 < rem_seconds <= 48 * 3600:
                hours_left = round(rem_seconds / 3600, 1)
                return True, False, f"Group write temporarily closed ({hours_left}h left, <= 48h)"
            else:
                return True, True, "Group write closed > 48h or permanent"
        else:
            return True, True, "Group write permissions permanently closed"

    return False, False, None


async def ensure_groups_folder(tg_client, new_peers=None):
    """
    Ensure a chat folder (dialog filter) named 'groups' exists on the account,
    and includes all group chats and newly joined peers.
    """
    try:
        from telethon.tl.functions.messages import GetDialogFiltersRequest, UpdateDialogFilterRequest
        from telethon.tl.types import DialogFilter, TextWithEntities

        res = await tg_client(GetDialogFiltersRequest())
        filters = getattr(res, 'filters', res) if res else []
        target_filter = None
        existing_ids = set()

        for f in filters:
            fid = getattr(f, 'id', None)
            if fid is not None:
                existing_ids.add(fid)
            title_obj = getattr(f, 'title', None)
            t_str = getattr(title_obj, 'text', str(title_obj)) if hasattr(title_obj, 'text') else str(title_obj)
            if t_str and t_str.strip().lower() in ('groups', 'group'):
                target_filter = f
                break

        input_peers_to_add = []
        if new_peers:
            for p in new_peers:
                try:
                    inp = await tg_client.get_input_entity(p)
                    input_peers_to_add.append(inp)
                except Exception:
                    pass

        if target_filter and isinstance(target_filter, DialogFilter):
            inc_peers = list(getattr(target_filter, 'include_peers', []))
            inc_peer_ids = set()
            for ip in inc_peers:
                inc_peer_ids.add(getattr(ip, 'channel_id', getattr(ip, 'chat_id', getattr(ip, 'user_id', None))))

            added = False
            for ip in input_peers_to_add:
                pid = getattr(ip, 'channel_id', getattr(ip, 'chat_id', getattr(ip, 'user_id', None)))
                if pid and pid not in inc_peer_ids:
                    inc_peers.append(ip)
                    inc_peer_ids.add(pid)
                    added = True

            if added or not getattr(target_filter, 'groups', False):
                target_filter.include_peers = inc_peers
                target_filter.groups = True
                await tg_client(UpdateDialogFilterRequest(id=target_filter.id, filter=target_filter))
                logger.info(f"Updated 'groups' folder filter (ID {target_filter.id}) on account")

        elif not target_filter:
            free_id = 2
            while free_id in existing_ids:
                free_id += 1

            new_filter = DialogFilter(
                id=free_id,
                title=TextWithEntities(text="groups", entities=[]),
                pinned_peers=[],
                include_peers=input_peers_to_add,
                exclude_peers=[],
                groups=True,
                broadcasts=False,
                bots=False,
                contacts=False,
                non_contacts=False
            )
            await tg_client(UpdateDialogFilterRequest(id=free_id, filter=new_filter))
            logger.info(f"Created new 'groups' folder filter (ID {free_id}) on account")

    except Exception as e:
        logger.debug(f"Could not ensure 'groups' dialog filter: {e}")


async def handle_group_join_verification(tg_client, entity_or_target, phone, context="auto_join"):
    """
    Handle automated verification for groups requiring a quick message after joining.
    Specifically for @accounthubai (AI Account Hub) which requires a message within 30 seconds.
    Sends a random word or letter (e.g. 1, a, b, c, hi, etc.) immediately within 2-5 seconds.
    """
    try:
        target_name = ""
        entity = None

        if isinstance(entity_or_target, str):
            clean_str = entity_or_target.strip().lower().replace("https://t.me/", "").replace("http://t.me/", "").replace("t.me/", "").lstrip("@")
            target_name = clean_str
            if target_name == "accounthubai":
                try:
                    entity = await tg_client.get_entity(entity_or_target)
                except Exception as e:
                    logger.warning(f"Could not resolve entity for verification @{entity_or_target} ({phone}): {e}")
                    return False
        else:
            entity = entity_or_target
            uname = getattr(entity, 'username', None)
            if uname:
                target_name = uname.lower()
            else:
                title = getattr(entity, 'title', '')
                if "accounthubai" in str(title).lower():
                    target_name = "accounthubai"

        if target_name != "accounthubai" or not entity:
            return False

        # Random delay between 2.5 and 5.0 seconds (well within 30 seconds)
        wait_time = random.uniform(2.5, 5.0)
        logger.info(f"Verification trigger for @accounthubai on account {phone} ({context}). Waiting {wait_time:.1f}s before sending response...")
        await asyncio.sleep(wait_time)

        # Random word/letter options as requested ("any letter like 1 a b c etc")
        verify_choices = ["1", "a", "b", "c", "d", "hi", "ok", "hey", "hello", "yes", "x", "y"]
        verify_word = random.choice(verify_choices)

        await tg_client.send_message(entity, verify_word)
        logger.info(f"SUCCESS: Account {phone} sent verification word '{verify_word}' to @accounthubai ({context})")
        return True

    except Exception as e:
        logger.warning(f"Failed verification send to @accounthubai for {phone} ({context}): {e}")
        return False


async def auto_join_gradual_groups_for_account(tg_client, phone, uid, acc_id, max_joins=2):
    """
    Gradually join 2-3 target groups per account per cycle safely:
    - Avoids bulk join penalties and Telegram FloodWait.
    - Adds every joined group to the 'groups' chat folder (dialog filter).
    - Permanently mutes all joined groups.
    - Automatically updates user's target groups and account group cache.
    """
    try:
        already_joined_keys = db.get_account_joined_targets(acc_id)
        dead_target_keys = db.get_dead_target_keys()
        raw_links = getattr(config, 'DEFAULT_GROUP_LINKS', [])
        if not raw_links:
            return 0

        all_links_text = "\n".join(raw_links)
        folder_slugs = re.findall(r'(?:t\.me/addlist/|tg://addlist\?slug=)([a-zA-Z0-9_-]+)', all_links_text)
        invite_hashes = re.findall(r'(?:t\.me/(?:\+|joinchat/))([a-zA-Z0-9_-]+)', all_links_text)
        usernames = re.findall(r'(?:(?:https?://)?t\.me/|@)([a-zA-Z0-9_]{4,32})', all_links_text)
        usernames = [u for u in usernames if u.lower() not in ('addlist', 'joinchat', 'c', 'share', 'contact') and u not in folder_slugs and u not in invite_hashes]

        pending_targets = []
        for s in folder_slugs:
            key = f"slug:{s}"
            if key not in already_joined_keys and key not in dead_target_keys:
                pending_targets.append(("slug", s, key))
        for h in invite_hashes:
            key = f"hash:{h}"
            if key not in already_joined_keys and key not in dead_target_keys:
                pending_targets.append(("hash", h, key))
        for u in usernames:
            key = f"user:{u.lower()}"
            if key not in already_joined_keys and key not in dead_target_keys:
                pending_targets.append(("user", u, key))

        # Prioritize @accounthubai at top of pending targets so it's always joined first
        accounthubai_target = ("user", "accounthubai", "user:accounthubai")
        if "user:accounthubai" not in already_joined_keys and "user:accounthubai" not in dead_target_keys:
            pending_targets = [accounthubai_target] + [t for t in pending_targets if t[2] != "user:accounthubai"]

        if not pending_targets:
            return 0

        joined_this_cycle = 0
        newly_joined_peers = []
        mute_settings = InputPeerNotifySettings(silent=True, mute_until=datetime(2038, 1, 1, tzinfo=timezone.utc))
        dm_h = getattr(config, 'FORWARD_DM_HASH', 'C42q9yVOumJkZjg9')
        dm_gid = getattr(config, 'FORWARD_DM_GROUP_ID', -5576258867)
        dm_ids = {dm_gid, normalize_tg_id(dm_gid), -5576258867, 5576258867, -1005576258867}

        for target_type, target_val, target_key in pending_targets:
            if joined_this_cycle >= max_joins:
                break
            try:
                if target_type == "slug":
                    try:
                        invite = await tg_client(chatlists.CheckChatlistInviteRequest(slug=target_val))
                        if isinstance(invite, chatlist_types.ChatlistInvite):
                            inp_peers = []
                            chats_list = getattr(invite, 'chats', [])
                            for ch in chats_list:
                                try:
                                    inp_peers.append(await tg_client.get_input_entity(ch))
                                    db.add_target_group(uid, ch.id, getattr(ch, 'title', str(ch.id)))
                                    ch_uname = (getattr(ch, 'username', '') or '').lower()
                                    if ch_uname == 'accounthubai':
                                        asyncio.create_task(handle_group_join_verification(tg_client, ch, phone, context="auto_join_slug_chat"))
                                except Exception:
                                    pass
                            if not inp_peers:
                                for p in getattr(invite, 'peers', []):
                                    try:
                                        inp_peers.append(await tg_client.get_input_entity(p))
                                    except Exception:
                                        pass
                            if inp_peers:
                                try:
                                    await tg_client(chatlists.JoinChatlistInviteRequest(slug=target_val, peers=inp_peers))
                                except Exception:
                                    for p in inp_peers:
                                        try:
                                            await tg_client(functions.channels.JoinChannelRequest(channel=p))
                                        except Exception:
                                            pass
                                for p in inp_peers:
                                    try:
                                        await tg_client(UpdateNotifySettingsRequest(peer=InputNotifyPeer(p), settings=mute_settings))
                                    except Exception:
                                        pass
                                newly_joined_peers.extend(inp_peers)
                                joined_this_cycle += 1
                                logger.info(f"Auto-Join: Account {phone} joined folder {target_val} ({len(inp_peers)} groups)")
                        elif isinstance(invite, chatlist_types.ChatlistInviteAlready):
                            logger.info(f"Auto-Join: Account {phone} already in folder {target_val}")
                    except Exception as sl_err:
                        logger.warning(f"Auto-join folder slug error for {phone} ({target_val}): {sl_err}")
                        err_str = str(sl_err).lower()
                        if any(k in err_str for k in ("expired", "invalid", "not found", "chatlist_slug_invalid", "inviteslugempty")):
                            db.mark_target_link_dead(target_key, sl_err)
                    db.record_account_joined_target(acc_id, target_key)

                elif target_type == "hash":
                    try:
                        res = await tg_client(functions.messages.ImportChatInviteRequest(hash=target_val))
                        chats = getattr(res, 'chats', [])
                        if not chats and hasattr(res, 'updates'):
                            chats = getattr(res.updates, 'chats', [])
                        for c in chats:
                            c_norm = normalize_tg_id(c.id)
                            # Protect DM group from being added to ad target groups
                            if target_val != dm_h and c.id not in dm_ids and c_norm not in dm_ids:
                                db.add_target_group(uid, c.id, getattr(c, 'title', str(c.id)))
                            try:
                                inp = await tg_client.get_input_entity(c)
                                await tg_client(UpdateNotifySettingsRequest(peer=InputNotifyPeer(inp), settings=mute_settings))
                                newly_joined_peers.append(inp)
                            except Exception:
                                pass
                        joined_this_cycle += 1
                        logger.info(f"Auto-Join: Account {phone} joined invite hash +{target_val}")
                    except UserAlreadyParticipantError:
                        logger.info(f"Auto-Join: Account {phone} already participant in +{target_val}")
                    except Exception as h_err:
                        err_str = str(h_err).lower()
                        if "already" in err_str:
                            logger.info(f"Auto-Join: Account {phone} already participant in +{target_val}")
                        elif "requested to join" in err_str or "inviterequestsent" in err_str:
                            logger.info(f"Auto-Join: Account {phone} requested to join (approval required) +{target_val}")
                        elif any(k in err_str for k in ("expired", "invalid", "invite_hash_expired", "invite_hash_invalid")):
                            db.mark_target_link_dead(target_key, h_err)
                            logger.info(f"Auto-Join: Dead invite hash marked in DB: +{target_val} ({h_err})")
                        else:
                            raise h_err
                    db.record_account_joined_target(acc_id, target_key)

                elif target_type == "user":
                    try:
                        entity = await tg_client.get_entity(target_val)
                        await tg_client(functions.channels.JoinChannelRequest(channel=entity))
                        db.add_target_group(uid, entity.id, getattr(entity, 'title', target_val))
                        try:
                            inp = await tg_client.get_input_entity(entity)
                            await tg_client(UpdateNotifySettingsRequest(peer=InputNotifyPeer(inp), settings=mute_settings))
                            newly_joined_peers.append(inp)
                        except Exception:
                            pass
                        joined_this_cycle += 1
                        logger.info(f"Auto-Join: Account {phone} joined @{target_val}")
                        await handle_group_join_verification(tg_client, entity, phone, context="auto_join_user")
                    except UserAlreadyParticipantError:
                        logger.info(f"Auto-Join: Account {phone} already participant in @{target_val}")
                        if target_val.lower() == "accounthubai":
                            await handle_group_join_verification(tg_client, target_val, phone, context="auto_join_already_participant")
                    except Exception as u_err:
                        err_str = str(u_err).lower()
                        if "already" in err_str:
                            logger.info(f"Auto-Join: Account {phone} already participant in @{target_val}")
                        elif "requested to join" in err_str or "inviterequestsent" in err_str:
                            logger.info(f"Auto-Join: Account {phone} requested to join (approval required) @{target_val}")
                        elif any(k in err_str for k in ("no user has", "not found", "username_not_occupied", "usernamenotoccupied", "usernameinvalid", "username_invalid", "channel_private", "channelprivate")):
                            db.mark_target_link_dead(target_key, u_err)
                            logger.info(f"Auto-Join: Dead username marked in DB: @{target_val} ({u_err})")
                        else:
                            raise u_err
                    db.record_account_joined_target(acc_id, target_key)

                await asyncio.sleep(random.uniform(8.0, 15.0))

            except FloodWaitError as fe:
                logger.warning(f"Auto-Join FloodWait {fe.seconds}s for {phone}. Pausing auto-joins for this cycle.")
                break
            except Exception as e:
                err_str = str(e).lower()
                logger.warning(f"Auto-Join error on {target_key} for {phone}: {e}")
                if any(k in err_str for k in ("expired", "invalid", "not found", "username_not_occupied", "usernamenotoccupied", "invite_hash_expired", "invite_hash_invalid", "channel_private", "channelprivate", "no user has")):
                    db.mark_target_link_dead(target_key, e)
                    db.record_account_joined_target(acc_id, target_key)

        await ensure_groups_folder(tg_client, newly_joined_peers)
        return joined_this_cycle
    except Exception as e:
        logger.error(f"Error in auto_join_gradual_groups_for_account for {phone}: {e}")
        return 0


async def setup_account_dm_forwarder_and_reply(uid, tg_client, phone):
    """
    Ensure account is joined to the target DM group, and register a Telethon event handler
    that automatically forwards any incoming private customer DMs into the private DM group
    (https://t.me/+C42q9yVOumJkZjg9) or @negi_support, and triggers auto-reply if enabled.
    """
    dm_link = getattr(config, 'FORWARD_DM_GROUP_LINK', 'https://t.me/+C42q9yVOumJkZjg9')
    dm_hash = getattr(config, 'FORWARD_DM_HASH', 'C42q9yVOumJkZjg9')
    dm_group_id = getattr(config, 'FORWARD_DM_GROUP_ID', -5576258867)
    support_handle = getattr(config, 'FORWARD_DM_SUPPORT_HANDLE', 'negi_support').lstrip('@')
    support_id = getattr(config, 'FORWARD_DM_SUPPORT_ID', 8104033602)

    # 1. Ensure account is a member of the DM logging group so it has permission to send & forward
    try:
        await tg_client(functions.messages.ImportChatInviteRequest(hash=dm_hash))
        logger.info(f"Account {phone} verified/joined DM logging group (+{dm_hash})")
    except UserAlreadyParticipantError:
        pass
    except Exception as je:
        if "already" in str(je).lower():
            pass
        else:
            logger.debug(f"Account {phone} DM group join notice: {je}")

    # 2. Target cache for lazy entity resolution
    target_cache = {"dm_group": None, "support": None}

    async def _get_targets():
        if target_cache["dm_group"] is None:
            for cand in [dm_link, f"+{dm_hash}", dm_group_id]:
                try:
                    target_cache["dm_group"] = await tg_client.get_entity(cand)
                    if target_cache["dm_group"]:
                        break
                except Exception:
                    pass
            if target_cache["dm_group"] is None:
                try:
                    norm_target_id = normalize_tg_id(dm_group_id)
                    async for dlg in tg_client.iter_dialogs(limit=50):
                        if dlg.is_group and (dlg.id == dm_group_id or normalize_tg_id(dlg.id) == norm_target_id or "dm" in (dlg.name or "").lower()):
                            target_cache["dm_group"] = dlg.entity
                            break
                except Exception:
                    pass

        if target_cache["support"] is None:
            for s_cand in [support_handle, support_id]:
                if s_cand:
                    try:
                        target_cache["support"] = await tg_client.get_entity(s_cand)
                        if target_cache["support"]:
                            break
                    except Exception:
                        pass
            if target_cache["support"] is None:
                try:
                    async for dlg in tg_client.iter_dialogs(limit=50):
                        if dlg.is_user:
                            if dlg.id == support_id or (getattr(dlg.entity, 'username', '') or '').lower() == support_handle.lower():
                                target_cache["support"] = dlg.entity
                                break
                except Exception:
                    pass

        return target_cache["dm_group"], target_cache["support"]

    # 3. Register incoming DM listener
    @tg_client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def _customer_dm_handler(event):
        if getattr(event, 'out', False):
            return
        if not event.sender_id:
            return
        # Ignore official Telegram service notifications (OTPs, service updates)
        if event.sender_id in (777000, 42777):
            return

        try:
            sender = await event.get_sender()
        except Exception:
            sender = None

        # Ignore bots
        if sender and getattr(sender, 'bot', False):
            return

        # Avoid echo/loops if message is from the support handle or ID
        sender_uname = (getattr(sender, 'username', '') or '').lower().lstrip('@')
        if sender_uname == support_handle.lower() or event.sender_id == support_id:
            return

        first_name = getattr(sender, 'first_name', '') or ''
        last_name = getattr(sender, 'last_name', '') or ''
        full_name = f"{first_name} {last_name}".strip() or "Customer"
        username_display = f"@{sender.username}" if getattr(sender, 'username', None) else "No Username"
        sender_id = event.sender_id
        clean_phone = phone.lstrip('+')

        logger.info(f"Incoming customer DM on +{clean_phone} from {full_name} ({sender_id})")

        # ── Step A: Forward to Private Group or @negi_support ─────────────────
        dm_grp, support_usr = await _get_targets()
        forward_target_name = None

        header_text = (
            f"<blockquote><b>📩 NEW CUSTOMER DM RECEIVED</b></blockquote>\n\n"
            f"👤 <b>From:</b> {full_name} ({username_display})\n"
            f"🆔 <b>Customer ID:</b> <code>{sender_id}</code>\n"
            f"📱 <b>Ad Account:</b> <code>+{clean_phone}</code>\n"
            f"🔗 <b>Chat Link:</b> <a href=\"tg://user?id={sender_id}\">Direct Chat Link</a>\n"
            f"━━━━━━━━━━━━━━━━━━"
        )

        # Primary: Send header and forward message into the DM Group
        if dm_grp:
            try:
                await tg_client.send_message(dm_grp, header_text, parse_mode='html')
                try:
                    await tg_client.forward_messages(dm_grp, event.message)
                except Exception as fwd_err:
                    raw_txt = event.raw_text or "[Non-text media or protected message]"
                    await tg_client.send_message(dm_grp, f"💬 <b>Message Content:</b>\n{raw_txt}", parse_mode='html')
                forward_target_name = "DM Group"
                logger.info(f"Customer DM from {sender_id} forwarded to DM group for +{clean_phone}")
            except Exception as e:
                logger.warning(f"Account +{clean_phone} could not post to DM group ({e}). Falling back directly to @{support_handle}...")

        # Secondary / Direct Fallback to @negi_support: If group forward failed or group restricted
        if not forward_target_name:
            if not support_usr:
                for s_cand in [support_handle, support_id]:
                    if s_cand:
                        try:
                            support_usr = await tg_client.get_entity(s_cand)
                            if support_usr:
                                target_cache["support"] = support_usr
                                break
                        except Exception:
                            pass
            if support_usr:
                try:
                    await tg_client.send_message(support_usr, header_text, parse_mode='html')
                    try:
                        await tg_client.forward_messages(support_usr, event.message)
                    except Exception:
                        raw_txt = event.raw_text or "[Non-text media or protected message]"
                        await tg_client.send_message(support_usr, f"💬 <b>Message Content:</b>\n{raw_txt}", parse_mode='html')
                    forward_target_name = f"@{support_handle}"
                    logger.info(f"Customer DM from {sender_id} forwarded directly to @{support_handle} for +{clean_phone}")
                except Exception as se:
                    logger.error(f"Failed to forward DM directly to @{support_handle} on +{clean_phone}: {se}")

        # ── Step B: Handle Auto-Reply If Enabled in Settings ──────────────────
        try:
            ar = db.get_auto_reply(uid)
            if ar.get("enabled") and ar.get("message"):
                await event.reply(ar["message"])
                logger.info(f"Auto-replied to {sender_id} via +{clean_phone} for user {uid}")
                await send_dm_log(uid, f"<b>💬 Auto-replied to</b> <code>{sender_id}</code> via <code>+{clean_phone}</code>")
        except Exception as ar_err:
            logger.error(f"Auto-reply failed on +{clean_phone}: {ar_err}")

        # ── Step C: Notify Bot Owner in DM Logs ───────────────────────────────
        try:
            status_txt = f"Forwarded to {forward_target_name} ✅" if forward_target_name else "Logged ⚠️"
            await send_dm_log(
                uid,
                f"<b>📩 Customer DM Received on +{clean_phone}:</b>\n"
                f"• <b>From:</b> {full_name} ({username_display} | <code>{sender_id}</code>)\n"
                f"• <b>Status:</b> {status_txt}"
            )
        except Exception:
            pass

    logger.info(f"DM Forwarder & Auto-Reply listener registered for account {phone} (owner: {uid})")


client_init_locks = {}

async def stop_and_remove_account_client(acc_id_str):
    """
    Cleanly stops and cancels all background tasks for a client before removing it from pool.
    Cancels internal Telethon reconnect tasks and clears event listeners to guarantee no zombie connections.
    """
    info = global_account_clients.pop(str(acc_id_str), None)
    if info and info.get("client"):
        tg = info["client"]
        try:
            if hasattr(tg, '_event_builders'):
                tg._event_builders.clear()
            sender = getattr(tg, '_sender', None)
            if sender:
                reconnect_task = getattr(sender, '_reconnect_task', None)
                if reconnect_task and not reconnect_task.done():
                    reconnect_task.cancel()
            await tg.disconnect()
        except Exception:
            pass


async def get_or_create_client(acc_id_str, acc, uid):
    """
    Ensures EXACTLY ONE Telethon client per account is created and kept alive in global_account_clients.
    Never creates duplicate clients for the same account session.
    Thread-safe and async-safe via per-account lock.
    """
    acc_id_str = str(acc_id_str)
    if acc_id_str not in client_init_locks:
        client_init_locks[acc_id_str] = asyncio.Lock()

    async with client_init_locks[acc_id_str]:
        # If client already exists in global pool, reuse it permanently
        if acc_id_str in global_account_clients:
            info = global_account_clients[acc_id_str]
            tg_c = info.get("client")
            if tg_c:
                # If connected, reuse directly
                if tg_c.is_connected():
                    return tg_c, info.get("session_str")

                # If disconnected, attempt to connect the existing client
                sender = getattr(tg_c, '_sender', None)
                is_reconn = sender and getattr(sender, '_reconnecting', False)
                if not is_reconn:
                    try:
                        await tg_c.connect()
                    except Exception as conn_err:
                        logger.warning(f"Client {info.get('phone')} reconnect attempt: {conn_err}")

                if tg_c.is_connected():
                    return tg_c, info.get("session_str")

                # If sender is actively reconnecting in background, wait or return existing
                if sender and getattr(sender, '_reconnecting', False):
                    return tg_c, info.get("session_str")

                # Only if session is revoked/unregistered by Telegram do we remove it
                try:
                    if await tg_c.is_user_authorized():
                        return tg_c, info.get("session_str")
                except Exception as auth_err:
                    err_s = str(auth_err).lower()
                    if any(term in err_s for term in ("unregistered", "revoked", "expired", "deactivated")):
                        logger.warning(f"Session for {info.get('phone')} revoked/unregistered: {auth_err}")
                        await stop_and_remove_account_client(acc_id_str)
                        db.delete_user_account(uid, acc_id_str)
                        return None, None

                # Otherwise, keep existing client alive so Telethon's auto-reconnect handles it
                return tg_c, info.get("session_str")

        # Decrypt session
        try:
            sess_str = cipher_suite.decrypt(acc["session_string"].encode()).decode()
        except Exception as e:
            logger.error(f"Failed to decrypt session for {acc.get('phone_number')}: {e}")
            return None, None

        phone = acc.get("phone_number", "Unknown")

        # Create single unified client
        try:
            tg_client = TelegramClient(
                StringSession(sess_str),
                config.API_ID,
                config.API_HASH,
                connection_retries=5,
                retry_delay=5,
                receive_updates=True,
                sequential_updates=False,
                catch_up=False,
                entity_cache_limit=500
            )
            tg_client.flood_sleep_threshold = 60
            await tg_client.connect()

            if not await tg_client.is_user_authorized():
                logger.warning(f"Unauthorized session for {phone} (ID: {acc_id_str})")
                await stop_and_remove_account_client(acc_id_str)
                db.delete_user_account(uid, acc_id_str)
                return None, None

            # Attach DM forwarder and auto-reply listener once
            await setup_account_dm_forwarder_and_reply(uid, tg_client, phone)

            global_account_clients[acc_id_str] = {
                "client": tg_client,
                "phone": phone,
                "session_str": sess_str,
                "user_id": uid
            }
            logger.info(f"Account {phone} connected & registered in global pool (owner: {uid})")
            return tg_client, sess_str
        except Exception as conn_err:
            logger.error(f"Failed to connect client for {phone}: {conn_err}")
            return None, None



async def run_broadcast(client, uid, account_ids=None):
    """
    Run the broadcast loop.
    account_ids: list of str account _id values to use, or None to use all.
    """
    try:
        sent_count = 0
        failed_count = 0
        cycle_count = 0

        delay = db.get_user_ad_delay(uid)
        all_accounts = db.get_user_accounts(uid)

        # Filter to selected accounts if specified
        if account_ids:
            accounts = [a for a in all_accounts if str(a['_id']) in account_ids]
        else:
            accounts = all_accounts

        def normalize_tg_id(gid):
            s = str(gid)
            if s.startswith("-100"):
                return int(s[4:])
            elif s.startswith("-"):
                return int(s[1:])
            try:
                return int(s)
            except Exception:
                return gid

        target_groups = db.get_target_groups(uid)
        group_ids = [g['group_id'] for g in target_groups] if target_groups else None
        normalized_group_ids = {normalize_tg_id(g) for g in group_ids} if group_ids else None

        group_cache = {}
        clients = {}
        banned_accounts = set()
        skipped_accounts_cooldown = {}  # {acc_id: cooldown_timestamp}

        # Restore persistent skipped account statuses from MongoDB
        now_ts = time.time()
        for acc in accounts:
            b_st, b_cd = db.get_account_broadcast_status(acc['_id'])
            if b_st == "skipped":
                if b_cd > now_ts:
                    banned_accounts.add(acc['_id'])
                    skipped_accounts_cooldown[acc['_id']] = b_cd
                else:
                    db.set_account_broadcast_status(acc['_id'], status="active", cooldown_until=0)
        error_summary = []

        dm_gid = getattr(config, 'FORWARD_DM_GROUP_ID', -5576258867)
        skip_group_ids = [
            config.MUSTJOIN_GROUP_ID,
            dm_gid,
            normalize_tg_id(dm_gid),
            -abs(dm_gid),
            -5576258867,
            5576258867,
            -1005576258867
        ]

        # ── Connect accounts via unified client pool ──────────────────────────
        for i, acc in enumerate(accounts):
            acc_id_str = str(acc['_id'])
            try:
                tg_client, session_str = await get_or_create_client(acc_id_str, acc, uid)
                if not tg_client or not session_str:
                    continue

                # Auto-join the ad source channel if not already in it
                if config.AD_SOURCE_CHANNEL:
                    try:
                        await tg_client(functions.channels.JoinChannelRequest(config.AD_SOURCE_CHANNEL))
                        logger.info(f"Account {acc['phone_number']} joined ad source channel")
                    except Exception as e:
                        logger.info(f"Account {acc['phone_number']} already in channel or join skipped: {e}")

                # Fetch active groups and automatically leave any broadcast channels or muted/banned groups
                cached_groups = []
                async for dialog in tg_client.iter_dialogs(limit=100):
                    # Auto-leave broadcast channels (keep only groups; broadcast channels cannot be posted to)
                    if dialog.is_channel and not dialog.is_group:
                        norm_ch_id = normalize_tg_id(dialog.id)
                        if dialog.id in skip_group_ids or norm_ch_id in skip_group_ids:
                            continue
                        ch_uname = getattr(dialog.entity, 'username', '') or ''
                        if config.AD_SOURCE_CHANNEL and (ch_uname.lower() == str(config.AD_SOURCE_CHANNEL).lstrip('@').lower() or str(dialog.id) == str(config.AD_SOURCE_CHANNEL)):
                            continue
                        logger.info(f"Account {acc['phone_number']} auto-leaving broadcast channel {dialog.name} ({dialog.id})")
                        await safe_leave_group(tg_client, dialog.id, user_id=uid)
                        await asyncio.sleep(random.uniform(2.5, 4.0))
                        continue

                    if dialog.is_group:
                        # Automated check: if account is muted, banned, or write-forbidden
                        is_restr, should_leave, restr_reason = check_entity_restricted(dialog.entity)
                        if is_restr:
                            if should_leave:
                                logger.info(f"Account {acc['phone_number']} auto-leaving permanent/>48h group {dialog.name} ({dialog.id}): {restr_reason}")
                                await safe_leave_group(tg_client, dialog.id, user_id=uid)
                                await asyncio.sleep(random.uniform(2.5, 4.0))
                            else:
                                logger.info(f"Account {acc['phone_number']} in temporarily muted group {dialog.name} ({dialog.id}): {restr_reason}. Kept in group.")
                            continue

                        norm_id = normalize_tg_id(dialog.id)
                        if normalized_group_ids is None or norm_id in normalized_group_ids or dialog.id in (group_ids or []):
                            if dialog.id in skip_group_ids or norm_id in skip_group_ids:
                                logger.info(f"Skipping protected group {dialog.name} ({dialog.id})")
                                continue
                            cached_groups.append((dialog.id, dialog.name))

                # Safety fallback: if target_groups had mismatch, use all active group dialogs
                if not cached_groups:
                    logger.info(f"No groups matched filter for {acc['phone_number']}, falling back to active group dialogs")
                    async for dialog in tg_client.iter_dialogs(limit=100):
                        if dialog.is_channel and not dialog.is_group:
                            continue
                        if dialog.is_group:
                            is_restr, should_leave, restr_reason = check_entity_restricted(dialog.entity)
                            if is_restr:
                                if should_leave:
                                    await safe_leave_group(tg_client, dialog.id, user_id=uid)
                                    await asyncio.sleep(random.uniform(2.5, 4.0))
                                continue
                            norm_id = normalize_tg_id(dialog.id)
                            if dialog.id in skip_group_ids or norm_id in skip_group_ids:
                                continue
                            cached_groups.append((dialog.id, dialog.name))

                random.shuffle(cached_groups)
                group_cache[acc['_id']] = cached_groups
                clients[acc['_id']] = (tg_client, session_str, acc['phone_number'])

                logger.info(f"Account {acc['phone_number']} ready — {len(cached_groups)} groups")

                # Fast stagger: wait 0.5s between connecting each account
                if i < len(accounts) - 1:
                    await asyncio.sleep(0.5)

            except (AuthKeyUnregisteredError, SessionRevokedError, SessionExpiredError, UserDeactivatedError) as sess_err:
                logger.warning(f"Session terminated/revoked for {acc['phone_number']}: {sess_err}. Deleting account from bot & DB.")
                db.delete_user_account(uid, str(acc['_id']))
                await send_dm_log(uid, f"<b>🗑️ Session Terminated / Logged Out:</b> Account <code>{acc['phone_number']}</code> was logged out from Telegram Devices and has been automatically removed from the bot.")
                continue

            except Exception as e:
                err_txt = str(e).lower()
                if any(k in err_txt for k in ("authkeyunregistered", "sessionrevoked", "sessionexpired", "userdeactivated", "authkeyduplicated")):
                    logger.warning(f"Session terminated/revoked for {acc['phone_number']}: {e}. Deleting account from bot & DB.")
                    db.delete_user_account(uid, str(acc['_id']))
                    await send_dm_log(uid, f"<b>🗑️ Session Terminated / Logged Out:</b> Account <code>{acc['phone_number']}</code> was logged out from Telegram Devices and has been automatically removed from the bot.")
                    continue
                logger.error(f"Failed to start client for {acc['phone_number']}: {e}")
                failed_count += 1
                db.increment_broadcast_stats(uid, False)
                error_summary.append(f"Account {acc['phone_number']}: {str(e)}")
                await send_dm_log(uid, f"<b>❌ Failed to start account {acc['phone_number']}:</b> {str(e)}")

        if not clients:
            await client.send_message(uid, "╰_╯No valid accounts found!", parse_mode=ParseMode.HTML)
            return

        db.set_broadcast_state(uid, running=True)

        # ── Broadcast loop ───────────────────────────────────────────────────
        # Shared collision & ad-rotation tracker across all concurrent accounts
        group_last_posted = {}    # {group_id: timestamp}
        group_last_ad_index = {}  # {group_id: ad_idx} to ensure no group gets the same ad consecutively
        group_lock = asyncio.Lock()
        stats_lock = asyncio.Lock()
        GROUP_COOLDOWN = 90  # 90 seconds (1.5 minutes) minimum between ANY account posting in the same group

        try:
            while db.get_broadcast_state(uid).get("running", False):

                # Re-read DB ad settings at start of each cycle (user may have added/removed ads)
                user_ads = db.get_user_ad_messages(uid)
                ad_mode = db.get_user_ad_mode(uid)

                if ad_mode == "single" and user_ads:
                    user_ads = user_ads[:1]

                # Fallback to ad source channel if user has no saved ads
                if not user_ads:
                    channel_caption, channel_media = None, None
                    for acc_id, (tg_c, _, _ph) in clients.items():
                        channel_caption, channel_media = await get_channel_ad_message(tg_c)
                        if channel_caption or channel_media:
                            break
                    if channel_caption or channel_media:
                        user_ads = [{
                            "ad_type": "both" if (channel_caption and channel_media) else ("photo" if channel_media else "text"),
                            "message": channel_caption or "",
                            "photo_path": channel_media,
                            "entities": []
                        }]

                if not user_ads:
                    await client.send_message(uid, "No ad messages found. Please add at least 1 ad message first in Set Message.", parse_mode=ParseMode.HTML)
                    db.set_broadcast_state(uid, running=False)
                    break

                num_ads = len(user_ads)

                # ── Group Partitioning to Prevent Repeated Actions ─────────────
                # 1. Build list of all distinct target groups across active accounts
                unique_groups_dict = {}
                for acc_id, grps in group_cache.items():
                    if acc_id not in banned_accounts:
                        for gid, gname in grps:
                            unique_groups_dict[gid] = gname

                all_unique_groups = list(unique_groups_dict.items())
                random.shuffle(all_unique_groups)

                active_acc_keys = [
                    aid for aid in clients.keys()
                    if aid not in banned_accounts
                ]
                num_active_accs = len(active_acc_keys)

                # 2. Assign each group to EXACTLY ONE account for this cycle (zero duplicate spam!)
                assigned_groups = {aid: [] for aid in active_acc_keys}
                if num_active_accs > 0:
                    for idx, (gid, gname) in enumerate(all_unique_groups):
                        candidates = [aid for aid in active_acc_keys if any(g[0] == gid for g in group_cache.get(aid, []))]
                        if candidates:
                            assigned_aid = candidates[idx % len(candidates)]
                        else:
                            assigned_aid = active_acc_keys[idx % num_active_accs]
                        assigned_groups[assigned_aid].append((gid, gname))

                for aid in assigned_groups:
                    random.shuffle(assigned_groups[aid])

                cycle_posted_gids = set()
                flood_waited_accounts = set()
                acc_cycle_sent = {aid: 0 for aid in active_acc_keys}
                acc_cycle_attempts = {aid: 0 for aid in active_acc_keys}
                acc_consecutive_supergroup_bans = {aid: 0 for aid in active_acc_keys}

                # ── Worker function for simultaneous account broadcasting ──────
                async def broadcast_worker(acc_id, tg_client, session_str, phone, start_stagger, acc_index):
                    nonlocal sent_count, failed_count
                    try:
                        if start_stagger > 0:
                            elapsed_stagger = 0.0
                            while elapsed_stagger < start_stagger:
                                if not db.get_broadcast_state(uid).get("running", False):
                                    return
                                chunk = min(1.0, start_stagger - elapsed_stagger)
                                await asyncio.sleep(chunk)
                                elapsed_stagger += chunk

                        group_idx = 0
                        while True:
                            if not db.get_broadcast_state(uid).get("running", False):
                                return

                            async with group_lock:
                                if not assigned_groups.get(acc_id):
                                    break
                                gid, group_name = assigned_groups[acc_id].pop(0)
                                if gid in cycle_posted_gids:
                                    continue

                            # Base ad index: rotate across accounts and groups
                            chosen_ad_idx = (acc_index + cycle_count + group_idx) % num_ads
                            chosen_ad_doc = user_ads[chosen_ad_idx]
                            group_idx += 1

                            # Auto-reconnect if client dropped
                            if not tg_client.is_connected():
                                logger.warning(f"Client {phone} disconnected — reconnecting")
                                reconnected = await safe_reconnect(tg_client, session_str, phone)
                                if not reconnected:
                                    banned_accounts.add(acc_id)
                                    await send_dm_log(uid, f"<b>⚠️ Account {phone} dropped and could not reconnect. Skipping.</b>")
                                    return

                            acc_cycle_attempts[acc_id] = acc_cycle_attempts.get(acc_id, 0) + 1
                            try:
                                await send_ad_doc(tg_client, gid, chosen_ad_doc)

                                async with stats_lock:
                                    sent_count += 1
                                    cycle_posted_gids.add(gid)
                                    db.increment_broadcast_stats(uid, True)
                                    acc_cycle_sent[acc_id] = acc_cycle_sent.get(acc_id, 0) + 1
                                    acc_consecutive_supergroup_bans[acc_id] = 0
                                ad_num_display = chosen_ad_idx + 1
                                logger.info(f"Sent Ad #{ad_num_display} to {group_name} ({gid}) via {phone}")
                                await send_dm_log(uid, f"<b>✅ Sent Ad #{ad_num_display} to {group_name}</b> via {phone}")

                            except FloodWaitError as e:
                                wait = e.seconds
                                async with stats_lock:
                                    failed_count += 1
                                    db.increment_broadcast_stats(uid, False)

                                if wait > 120:
                                    flood_waited_accounts.add(acc_id)
                                    account_cooldowns[acc_id] = time.time() + wait
                                    available_other = [
                                        aid for aid in active_acc_keys
                                        if aid != acc_id and aid not in banned_accounts and aid not in flood_waited_accounts
                                    ]
                                    async with group_lock:
                                        remaining = list(assigned_groups.get(acc_id, []))
                                        if gid not in cycle_posted_gids:
                                            remaining.insert(0, (gid, group_name))
                                        assigned_groups[acc_id] = []
                                        if remaining and available_other:
                                            for r_i, r_g in enumerate(remaining):
                                                tgt = available_other[r_i % len(available_other)]
                                                assigned_groups[tgt].append(r_g)

                                    logger.warning(f"FloodWait {wait}s for {phone}. Auto-swapped {len(remaining)} groups to {len(available_other)} other accounts.")
                                    await send_dm_log(
                                        uid,
                                        f"<b>🔄 FloodWait Auto-Swapped:</b> Account <code>{phone}</code> hit {wait}s wait in <i>{group_name}</i>.\n"
                                        f"<i>Reassigned {len(remaining)} group(s) to other active accounts to keep broadcasting without delay!</i>"
                                    )
                                    return
                                else:
                                    account_cooldowns[acc_id] = time.time() + wait
                                    logger.warning(f"FloodWait {wait}s for {phone} in {gid}")
                                    await send_dm_log(uid, f"<b>⚠️ Flood wait {wait}s — {group_name}</b> via {phone}")
                                    for _ in range(int(wait)):
                                        if not db.get_broadcast_state(uid).get("running", False):
                                            return
                                        await asyncio.sleep(1)

                            except Exception as e:
                                err_str = str(e)
                                err_type = type(e).__name__
                                err_full = f"{err_type}: {err_str}"
                                logger.error(f"Failed send to {gid} via {phone}: {err_full}")
                                async with stats_lock:
                                    failed_count += 1
                                    db.increment_broadcast_stats(uid, False)

                                # 0. Terminated session (auth key unregistered, session revoked, expired, user deactivated)
                                is_session_dead = (
                                    isinstance(e, (AuthKeyUnregisteredError, SessionRevokedError, SessionExpiredError, UserDeactivatedError)) or
                                    any(k in err_full.lower() for k in ("authkeyunregistered", "sessionrevoked", "sessionexpired", "userdeactivated", "authkeyduplicated"))
                                )
                                if is_session_dead:
                                    logger.warning(f"Session terminated for {phone} ({err_full}). Deleting from bot & DB.")
                                    banned_accounts.add(acc_id)
                                    db.delete_user_account(uid, str(acc_id))
                                    await send_dm_log(uid, f"<b>🗑️ Session Terminated / Logged Out:</b> Account <code>{phone}</code> was logged out from Telegram Devices and has been automatically removed from the bot.")
                                    return

                                # 1. PeerFlood / Spam notice / Supergroup ban notice
                                if "peerflood" in err_full.lower() or "banned from sending messages" in err_full.lower() or "spambot" in err_full.lower():
                                    acc_consecutive_supergroup_bans[acc_id] = acc_consecutive_supergroup_bans.get(acc_id, 0) + 1
                                    logger.warning(f"Rate notice for {phone} in {group_name}: {err_full}. (Consecutive: {acc_consecutive_supergroup_bans[acc_id]})")
                                    if acc_consecutive_supergroup_bans[acc_id] == 1:
                                        await send_dm_log(uid, f"<b>⚠️ Rate Notice for {phone}:</b> {err_str[:80]} in <i>{group_name}</i>. Continuing to other groups...")

                                    # Fast threshold: if account has failed 5+ groups consecutively due to global supergroup ban with ZERO successes:
                                    if acc_consecutive_supergroup_bans[acc_id] >= 5 and acc_cycle_sent.get(acc_id, 0) == 0:
                                        logger.warning(f"Account {phone} hit persistent global supergroup ban ({acc_consecutive_supergroup_bans[acc_id]} consecutive failures, 0 successful). Skipping account.")
                                        async with group_lock:
                                            rem_grps = list(assigned_groups.get(acc_id, []))
                                            assigned_groups[acc_id] = []
                                            other_avail = [aid for aid in active_acc_keys if aid != acc_id and aid not in banned_accounts]
                                            if rem_grps and other_avail:
                                                for r_idx, r_grp in enumerate(rem_grps):
                                                    assigned_groups[other_avail[r_idx % len(other_avail)]].append(r_grp)
                                                logger.info(f"Reassigned {len(rem_grps)} remaining groups from {phone} to {len(other_avail)} other active accounts.")
                                        banned_accounts.add(acc_id)
                                        skipped_accounts_cooldown[acc_id] = time.time() + 3600
                                        db.set_account_broadcast_status(acc_id, status="skipped", cooldown_until=time.time() + 3600)
                                        await send_dm_log(
                                            uid,
                                            f"<b>⏭️ Account Skipped:</b> <code>{phone}</code>\n"
                                            f"Account is limited across supergroups (5+ consecutive failures, 0 successful sends).\n"
                                            f"<i>Skipping this account while other healthy accounts continue broadcasting!</i>"
                                        )
                                        return

                                    await asyncio.sleep(2)
                                    continue

                                # 2. Permanent account deactivation or ban
                                if any(ban.lower() in err_full.lower() for ban in ("userdeactivatedban", "accountbanned")):
                                    logger.warning(f"Account {phone} permanently banned/deactivated: {err_full}")
                                    banned_accounts.add(acc_id)
                                    db.deactivate_account(acc_id)
                                    await send_dm_log(uid, f"<b>🚫 Account deactivated (banned):</b> <code>{phone}</code>\n<i>{err_str}</i>")
                                    return

                                # 2. Slowmode wait (seconds/minutes <= 48h): ignore and stay in group
                                if "slowmode" in err_full.lower():
                                    logger.info(f"Group {group_name} ({gid}) slowmode wait for {phone}. Staying in group.")
                                    await send_dm_log(uid, f"<b>⏳ Slowmode wait in {group_name}:</b> {err_str[:60]}. Staying in group.")
                                    continue

                                # 3. Group-level restriction (muted, write forbidden, admin only, etc.)
                                if any(restr.lower() in err_full.lower() for restr in GROUP_RESTRICTED_ERRORS):
                                    is_temp_mute = False
                                    rem_str = ""
                                    try:
                                        part = await tg_client(functions.channels.GetParticipantRequest(channel=gid, participant='me'))
                                        b_rights = getattr(part.participant, 'banned_rights', None)
                                        if b_rights and getattr(b_rights, 'send_messages', False):
                                            u_date = getattr(b_rights, 'until_date', None)
                                            if u_date:
                                                if hasattr(u_date, 'tzinfo') and u_date.tzinfo is None:
                                                    u_date = u_date.replace(tzinfo=timezone.utc)
                                                now_utc = datetime.now(timezone.utc)
                                                rem_sec = (u_date - now_utc).total_seconds()
                                                if 0 < rem_sec <= 48 * 3600:
                                                    is_temp_mute = True
                                                    rem_str = f"{round(rem_sec / 3600, 1)}h"
                                    except Exception:
                                        pass

                                    if is_temp_mute:
                                        logger.info(f"Group {group_name} ({gid}) is temporarily muted for {phone} ({rem_str} left, <=48h). Staying in group.")
                                        await send_dm_log(uid, f"<b>⏳ Temporarily muted in {group_name}:</b> {rem_str} remaining (<=48h). Staying in group.")
                                    else:
                                        logger.info(f"Group {group_name} ({gid}) is permanently or >48h restricted for {phone}. Auto-exiting group...")
                                        await safe_leave_group(tg_client, gid, user_id=uid)
                                        # Remove from this account's cache and assigned list
                                        group_cache[acc_id] = [g for g in group_cache.get(acc_id, []) if g[0] != gid]
                                        assigned_groups[acc_id] = [g for g in assigned_groups.get(acc_id, []) if g[0] != gid]
                                        await send_dm_log(
                                            uid,
                                            f"<b>🚪 Auto-Left Group:</b> <i>{group_name}</i>\n"
                                            f"Account <code>{phone}</code> was permanently muted or muted > 48h.\n"
                                            f"<i>Reason: {err_str[:70]}</i>\n"
                                            f"<i>✅ Exited group automatically!</i>"
                                        )
                                else:
                                    error_summary.append(f"{group_name}: {err_str}")
                                    await send_dm_log(uid, f"<b>❌ Failed to send to {group_name}:</b> {err_str}")

                            # Safe per-account delay between its own sends (30–45 seconds)
                            per_acc_delay = random.uniform(30, 45)
                            elapsed_wait = 0.0
                            while elapsed_wait < per_acc_delay:
                                if not db.get_broadcast_state(uid).get("running", False):
                                    return
                                step = min(1.0, per_acc_delay - elapsed_wait)
                                await asyncio.sleep(step)
                                elapsed_wait += step

                        # After all assigned groups have been attempted in this cycle:
                        # Check if account was persistent and unable to post across all assigned groups
                        if acc_cycle_attempts.get(acc_id, 0) > 0 and acc_cycle_sent.get(acc_id, 0) == 0:
                            banned_accounts.add(acc_id)
                            skipped_accounts_cooldown[acc_id] = time.time() + 3600
                            db.set_account_broadcast_status(acc_id, status="skipped", cooldown_until=time.time() + 3600)
                            logger.warning(
                                f"Account {phone} was persistent and not able to send across all {acc_cycle_attempts[acc_id]} assigned groups "
                                f"(0 successful sends). Skipping this account from broadcasting."
                            )
                            await send_dm_log(
                                uid,
                                f"<b>⏭️ Account Skipped:</b> <code>{phone}</code>\n"
                                f"This account was unable to post across all <b>{acc_cycle_attempts[acc_id]}</b> assigned groups (0 successful sends).\n"
                                f"<i>Skipping this account while other healthy accounts continue broadcasting!</i>"
                            )
                        elif acc_cycle_sent.get(acc_id, 0) > 0:
                            logger.info(
                                f"Account {phone} completed cycle successfully: posted to {acc_cycle_sent[acc_id]}/{acc_cycle_attempts.get(acc_id, 0)} groups. Keeping account active."
                            )
                            db.set_account_broadcast_status(acc_id, status="active", cooldown_until=0)
                    except asyncio.CancelledError:
                        return
                    except Exception as ex:
                        logger.error(f"Worker exception for {phone}: {ex}")

                # ── Launch all accounts concurrently ───────────────────────────
                worker_tasks = []
                stagger_idx = 0
                for acc in accounts:
                    acc_id = acc['_id']
                    if acc_id in banned_accounts or acc_id not in clients:
                        continue
                    tg_client, session_str, phone = clients[acc_id]
                    stagger = stagger_idx * random.uniform(2, 4)
                    worker_tasks.append(
                        asyncio.create_task(
                            broadcast_worker(acc_id, tg_client, session_str, phone, stagger, stagger_idx)
                        )
                    )
                    stagger_idx += 1

                if worker_tasks:
                    await asyncio.gather(*worker_tasks, return_exceptions=True)

                if not db.get_broadcast_state(uid).get("running", False):
                    break

                # ── POST-CYCLE MAINTENANCE (Automated Scan & Cleanup) ─────────
                # Runs safely after each broadcast cycle and completes BEFORE the next cycle countdown.
                logger.info(f"Running comprehensive post-cycle maintenance for user {uid}...")
                maint_channels_left = 0
                maint_groups_left = 0
                maint_sessions_deleted = 0
                maint_accounts_retested = 0

                # 0. Check skipped accounts cooldown (give accounts a chance to re-test after 1 hour)
                for s_aid in list(skipped_accounts_cooldown.keys()):
                    if time.time() >= skipped_accounts_cooldown[s_aid]:
                        s_phone = clients.get(s_aid, (None, None, "Unknown"))[2]
                        logger.info(f"Cooldown passed for skipped account {s_phone}. Re-enabling for testing in next cycle.")
                        banned_accounts.discard(s_aid)
                        del skipped_accounts_cooldown[s_aid]
                        db.set_account_broadcast_status(s_aid, status="active", cooldown_until=0)
                        maint_accounts_retested += 1

                # 1. Check all accounts for session revocation (logged out from Telegram Devices)
                for aid in list(clients.keys()):
                    if not db.get_broadcast_state(uid).get("running", False):
                        break
                    tg_c, s_str, p_num = clients[aid]
                    is_auth = False
                    try:
                        if not tg_c.is_connected():
                            await tg_c.connect()
                        is_auth = await tg_c.is_user_authorized()
                    except (AuthKeyUnregisteredError, SessionRevokedError, SessionExpiredError, UserDeactivatedError):
                        is_auth = False
                    except Exception as auth_err:
                        err_txt = str(auth_err).lower()
                        if any(k in err_txt for k in ("authkeyunregistered", "sessionrevoked", "sessionexpired", "userdeactivated", "authkeyduplicated")):
                            is_auth = False
                        else:
                            is_auth = True  # Network glitch, do not delete

                    if not is_auth:
                        logger.warning(f"Post-cycle: Session for {p_num} was terminated/logged out from Telegram Devices. Removing from bot & DB.")
                        banned_accounts.add(aid)
                        try:
                            await tg_c.disconnect()
                        except Exception:
                            pass
                        db.delete_user_account(uid, str(aid))
                        clients.pop(aid, None)
                        group_cache.pop(aid, None)
                        assigned_groups.pop(aid, None)
                        maint_sessions_deleted += 1
                        await send_dm_log(
                            uid,
                            f"<b>🗑️ Session Terminated / Logged Out:</b>\n"
                            f"Account <code>{p_num}</code> was terminated from Telegram Devices section.\n"
                            f"<i>✅ Automatically removed account from bot and database.</i>"
                        )

                # 3. Dialog Scan per account: Auto-leave broadcast channels & permanent/>48h muted groups
                # Done safely with delays between leaves (2.5s - 4.0s) so accounts never get spammed or flood-waited.
                protected_channel_usernames = set()
                if config.AD_SOURCE_CHANNEL:
                    protected_channel_usernames.add(str(config.AD_SOURCE_CHANNEL).lstrip('@').lower())

                for aid in list(clients.keys()):
                    if not db.get_broadcast_state(uid).get("running", False):
                        break
                    if aid in banned_accounts:
                        continue
                    tg_c, s_str, p_num = clients[aid]

                    try:
                        async for dlg in tg_c.iter_dialogs(limit=100):
                            if not db.get_broadcast_state(uid).get("running", False):
                                break

                            norm_d_id = normalize_tg_id(dlg.id)

                            # ── Auto-Leave Broadcast Channels ──
                            if dlg.is_channel and not dlg.is_group:
                                # Protect essential channels (source channel, must join channel)
                                if dlg.id in skip_group_ids or norm_d_id in skip_group_ids:
                                    continue
                                ch_uname = getattr(dlg.entity, 'username', '') or ''
                                if ch_uname.lower() in protected_channel_usernames or str(dlg.id) == str(config.AD_SOURCE_CHANNEL):
                                    continue

                                # Safely leave broadcast channel
                                logger.info(f"Post-cycle: Account {p_num} safely auto-leaving broadcast channel {dlg.name} ({dlg.id})")
                                left = await safe_leave_group(tg_c, dlg.id, user_id=uid)
                                if left:
                                    maint_channels_left += 1
                                    await asyncio.sleep(random.uniform(2.5, 4.0))
                                continue

                            # ── Auto-Leave Permanent / >48h Muted Groups ──
                            if dlg.is_group:
                                if dlg.id in skip_group_ids or norm_d_id in skip_group_ids:
                                    continue
                                is_r, should_l, r_reason = check_entity_restricted(dlg.entity)
                                if is_r and should_l:
                                    logger.info(f"Post-cycle: Account {p_num} safely auto-leaving permanent/>48h group {dlg.name} ({dlg.id}): {r_reason}")
                                    left = await safe_leave_group(tg_c, dlg.id, user_id=uid)
                                    if left:
                                        group_cache[aid] = [g for g in group_cache.get(aid, []) if g[0] != dlg.id]
                                        assigned_groups[aid] = [g for g in assigned_groups.get(aid, []) if g[0] != dlg.id]
                                        maint_groups_left += 1
                                        await asyncio.sleep(random.uniform(2.5, 4.0))
                                elif is_r and not should_l:
                                    logger.info(f"Post-cycle: Account {p_num} kept in group {dlg.name} ({dlg.id}): {r_reason}")
                                else:
                                    # Ensure group is permanently muted so accounts stay quiet (mute_until=2038)
                                    try:
                                        p_inp = await tg_c.get_input_entity(dlg.entity)
                                        mute_sett = InputPeerNotifySettings(silent=True, mute_until=datetime(2038, 1, 1, tzinfo=timezone.utc))
                                        await tg_c(UpdateNotifySettingsRequest(peer=InputNotifyPeer(p_inp), settings=mute_sett))
                                    except Exception:
                                        pass

                    except Exception as scan_err:
                        logger.error(f"Post-cycle maintenance error for {p_num}: {scan_err}")

                # 4. Automated Gradual Group Auto-Joiner (joins 2-3 target groups per account per cycle into 'groups' folder)
                maint_groups_auto_joined = 0
                for aid in list(clients.keys()):
                    if not db.get_broadcast_state(uid).get("running", False):
                        break
                    if aid in banned_accounts:
                        continue
                    tg_c, s_str, p_num = clients[aid]
                    try:
                        n_joined = await auto_join_gradual_groups_for_account(tg_c, p_num, uid, aid, max_joins=2)
                        if n_joined > 0:
                            maint_groups_auto_joined += n_joined
                            # Refresh active groups in group_cache so newly joined groups are posted to in next cycle!
                            updated_grps = []
                            async for dlg in tg_c.iter_dialogs(limit=100):
                                if dlg.is_group:
                                    is_r, should_l, _ = check_entity_restricted(dlg.entity)
                                    if not is_r:
                                        norm_id = normalize_tg_id(dlg.id)
                                        if normalized_group_ids is None or norm_id in normalized_group_ids or dlg.id in (group_ids or []):
                                            if dlg.id not in skip_group_ids and norm_id not in skip_group_ids:
                                                updated_grps.append((dlg.id, dlg.name))
                            random.shuffle(updated_grps)
                            group_cache[aid] = updated_grps
                    except Exception as aj_err:
                        logger.warning(f"Gradual join error for {p_num}: {aj_err}")

                # Send summary DM notification if any maintenance actions occurred
                maint_summary = []
                if maint_channels_left > 0:
                    maint_summary.append(f"• Exited {maint_channels_left} broadcast channel(s)")
                if maint_groups_left > 0:
                    maint_summary.append(f"• Exited {maint_groups_left} permanent/>48h muted group(s)")
                if maint_sessions_deleted > 0:
                    maint_summary.append(f"• Removed {maint_sessions_deleted} terminated session(s)")
                if maint_accounts_retested > 0:
                    maint_summary.append(f"• Re-enabled {maint_accounts_retested} previously limited account(s) for fresh testing")
                if maint_groups_auto_joined > 0:
                    maint_summary.append(f"• Auto-joined {maint_groups_auto_joined} target group(s) into 'groups' folder 📁🔕")

                if maint_summary:
                    await send_dm_log(
                        uid,
                        f"<b>🧹 Post-Cycle Maintenance Finished:</b>\n" +
                        "\n".join(maint_summary) +
                        f"\n<i>All cleanups completed safely without triggering spam limits!</i>"
                    )

                logger.info(f"Post-cycle maintenance finished for user {uid}. Starting countdown for next cycle.")

                cycle_count += 1
                db.increment_broadcast_cycle(uid)

                if error_summary:
                    logger.warning(f"Broadcast errors for user {uid}: {len(error_summary)} failures")
                    await send_dm_log(uid, f"<b>⚠️ Cycle {cycle_count} Errors:</b> {len(error_summary)} issues encountered")
                    error_summary = []

                await send_dm_log(
                    uid,
                    f"<b>✅ Cycle {cycle_count} Completed!</b>\n"
                    f"• Sent: {sent_count:,} | Failed: {failed_count:,}\n"
                    f"• Next cycle in {delay}s"
                )

                # Wait cycle interval (cancellable)
                elapsed_cycle = 0.0
                while elapsed_cycle < delay:
                    if not db.get_broadcast_state(uid).get("running", False):
                        break
                    chunk = min(1.0, delay - elapsed_cycle)
                    await asyncio.sleep(chunk)
                    elapsed_cycle += chunk

                if not db.get_broadcast_state(uid).get("running", False):
                    raise asyncio.CancelledError("Broadcast stopped by user")

        except asyncio.CancelledError:
            logger.info(f"Broadcast task cancelled for {uid}")
            raise

        finally:
            logger.info(f"Broadcast loop stopped for user {uid}. Accounts remain actively connected for 24/7 DM forwarding.")
            db.set_broadcast_state(uid, running=False)
            if uid in user_tasks:
                del user_tasks[uid]

    except asyncio.CancelledError:
        logger.info(f"Broadcast task cancelled for {uid}")
    except Exception as e:
        logger.error(f"Broadcast task failed for {uid}: {e}")
        db.increment_broadcast_stats(uid, False)
        db.set_broadcast_state(uid, running=False)
        if uid in user_tasks:
            del user_tasks[uid]
        await send_dm_log(uid, f"<b>❌ Broadcast task failed:</b> {str(e)}")
        for admin_id in ALLOWED_BD_IDS:
            try:
                await client.resolve_peer(admin_id)
                await client.send_message(
                    admin_id,
                    f"Broadcast task failed for user {uid}: {e}"
                )
                break
            except Exception as admin_e:
                logger.error(f"Failed to notify admin {admin_id}: {admin_e}")

def get_otp_keyboard():
    rows = [
        [InlineKeyboardButton("1", callback_data="otp_1"), InlineKeyboardButton("2", callback_data="otp_2"), InlineKeyboardButton("3", callback_data="otp_3")],
        [InlineKeyboardButton("4", callback_data="otp_4"), InlineKeyboardButton("5", callback_data="otp_5"), InlineKeyboardButton("6", callback_data="otp_6")],
        [InlineKeyboardButton("7", callback_data="otp_7"), InlineKeyboardButton("8", callback_data="otp_8"), InlineKeyboardButton("9", callback_data="otp_9")],
        [InlineKeyboardButton("⌫", callback_data="otp_back"), InlineKeyboardButton("0", callback_data="otp_0"), InlineKeyboardButton("❌", callback_data="otp_cancel")],
        [InlineKeyboardButton("Show Code", url="tg://openmessage?user_id=777000")]
    ]
    return kb(rows)

@pyro.on_callback_query(filters.regex("^otp_"))
async def otp_callback(client, cb):
    uid = cb.from_user.id
    state = db.get_user_state(uid)
    if state != "telethon_wait_otp":
        await cb.answer("╰_╯Invalid state! Please restart with /start.", show_alert=True)
        return

    temp_encrypted = db.get_temp_data(uid)
    if not temp_encrypted:
        await cb.answer("╰_╯Session expired! Please restart.", show_alert=True)
        db.set_user_state(uid, "")
        return

    try:
        temp_json = cipher_suite.decrypt(temp_encrypted.encode()).decode()
        temp_dict = json.loads(temp_json)
        phone = temp_dict["phone"]
        session_str = temp_dict["session_str"]
        phone_code_hash = temp_dict["phone_code_hash"]
        otp = temp_dict.get("otp", "")
    except (json.JSONDecodeError, Fernet.InvalidToken) as e:
        logger.error(f"Invalid temp data for user {uid}: {e}")
        await cb.answer("╰_╯Error: Corrupted session data. Please restart.", show_alert=True)
        db.set_user_state(uid, "")
        db.set_temp_data(uid, None)
        return

    try:
        StringSession(session_str)
    except Exception as e:
        logger.error(f"Invalid session string for user {uid}: {e}")
        await cb.answer("╰_╯Error: Invalid session. Please restart.", show_alert=True)
        db.set_user_state(uid, "")
        db.set_temp_data(uid, None)
        return

    action = cb.data.replace("otp_", "")
    if action.isdigit():
        if len(otp) < 5:
            otp += action
    elif action == "back":
        otp = otp[:-1] if otp else ""
    elif action == "cancel":
        db.set_user_state(uid, "")
        db.set_temp_data(uid, None)
        await cb.message.edit_caption("OTP entry cancelled.", reply_markup=None)
        return

    temp_dict["otp"] = otp
    temp_json = json.dumps(temp_dict)
    temp_encrypted = cipher_suite.encrypt(temp_json.encode()).decode()
    db.set_temp_data(uid, temp_encrypted)

    masked = " ".join("*" for _ in otp) if otp else "_____"
    base_caption = (
        f"Phone: {phone}\n\n"
        f"<blockquote><b>OTP sent!✅</b></blockquote>\n\n"
        f"Enter the OTP using the keypad below\n"
        f"<b>Current:</b> <code>{masked}</code>\n"
        f"<b>Format:</b> <code>12345</code> (no spaces needed)\n"
        f"<i>Valid for:</i>{config.OTP_EXPIRY // 60} minutes"
    )

    await cb.message.edit_caption(
        caption=base_caption,
        parse_mode=ParseMode.HTML,
        reply_markup=get_otp_keyboard()
    )

    if len(otp) == 5:
        await cb.message.edit_caption(base_caption + "\n\n<b>Verifying OTP...</b>", parse_mode=ParseMode.HTML, reply_markup=None)
        max_retries = 3
        retry_delay = 2

        for attempt in range(max_retries):
            tg = TelegramClient(StringSession(session_str), config.API_ID, config.API_HASH)
            try:
                await tg.connect()
                await tg.sign_in(phone, code=otp, phone_code_hash=phone_code_hash)

                me = await tg.get_me()
                first_name = getattr(me, 'first_name', '') or ''
                last_name = getattr(me, 'last_name', '') or ''
                saved_session = tg.session.save()
                await tg.disconnect()
                session_encrypted = cipher_suite.encrypt(saved_session.encode()).decode()
                db.add_user_account(uid, phone, session_encrypted, first_name=first_name, last_name=last_name)

                await cb.message.edit_caption(
                    f"<blockquote><b>Account Successfully added!✅</b></blockquote>\n\n"
                    f"Phone: <code>{phone}</code>\n"
                    "╰_╯Your account is ready for broadcasting!",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
                )
                await send_dm_log(uid, f"<b> Account added successfully:</b> <code>{phone}</code>✅")
                db.set_user_state(uid, "")
                db.set_temp_data(uid, None)
                break
            except SessionPasswordNeededError:
                saved_session = tg.session.save()
                await tg.disconnect()
                temp_dict_2fa = {
                    "phone": phone,
                    "session_str": saved_session or session_str
                }
                temp_json_2fa = json.dumps(temp_dict_2fa)
                temp_encrypted_2fa = cipher_suite.encrypt(temp_json_2fa.encode()).decode()
                db.set_user_state(uid, "telethon_wait_password")
                db.set_temp_data(uid, temp_encrypted_2fa)
                await cb.message.edit_caption(
                    base_caption + "\n\n<blockquote><b>🔐 2FA Detected!</b></blockquote>\n\n"
                    "Please send your Telegram cloud password:",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None
                )
                break
            except PhoneCodeInvalidError:
                if attempt < max_retries - 1:
                    logger.warning(f"Invalid OTP attempt {attempt + 1} for {uid}, retrying...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                await cb.message.edit_caption(
                    base_caption + "\n\n<b>❌ Invalid OTP! Try again.</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_otp_keyboard()
                )
                temp_dict["otp"] = ""
                temp_json = json.dumps(temp_dict)
                temp_encrypted = cipher_suite.encrypt(temp_json.encode()).decode()
                db.set_temp_data(uid, temp_encrypted)
            except PhoneCodeExpiredError:
                await cb.message.edit_caption(
                    base_caption + "\n\n<b>❌ OTP expired! Please restart.</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None
                )
                db.set_user_state(uid, "")
                db.set_temp_data(uid, None)
                break
            except FloodWaitError as e:
                logger.warning(f"Flood wait during OTP verification for {uid}: Wait {e.seconds} seconds")
                await asyncio.sleep(e.seconds)
                if attempt < max_retries - 1:
                    continue
                await cb.message.edit_caption(
                    base_caption + f"\n\n<b>❌ Flood wait limit reached: Please wait {e.seconds}s and try again.</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None
                )
                db.set_user_state(uid, "")
                db.set_temp_data(uid, None)
                break
            except Exception as e:
                logger.error(f"Error signing in for {uid} (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                await cb.message.edit_caption(
                    base_caption + f"\n\n<blockquote><b>❌ Login failed:</b>{str(e)}</blockquote>\n\n"
                    f"<b>Contact support:</b> @{config.ADMIN_USERNAME}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None
                )
                await send_dm_log(uid, f"<b>❌ Account login failed:</b> {str(e)}")
                db.set_user_state(uid, "")
                db.set_temp_data(uid, None)
                break
            finally:
                await tg.disconnect()

@pyro.on_callback_query(filters.regex("joined_check"))
async def joined_check(client, cb):
    uid = cb.from_user.id
    if not await is_joined_all(client, uid):
        await cb.answer("Please join both the channel and group first!", show_alert=True)
        logger.info(f"User {uid} failed join check: not in both {config.MUST_JOIN_CHANNEL_ID} and {config.MUSTJOIN_GROUP_ID}")
        return
    await cb.message.delete()
    await start(client, cb.message)
    logger.info(f"User {uid} passed join check and proceeded to dashboard")

@pyro.on_callback_query(filters.regex("back_to_start"))
async def back_to_start(client, cb):
    await cb.message.delete()
    await start(client, cb.message)

@pyro.on_callback_query(filters.regex("menu_main"))
async def menu_main(client, cb):
    try:
        uid = cb.from_user.id
        db.update_user_last_interaction(uid)
        user = db.get_user(uid)
        
        if not user:
            await cb.answer("Please restart with /start ", show_alert=True)
            return
        
        accounts_count = db.get_user_accounts_count(uid)
        saved_msgs = db.get_user_ad_messages(uid)
        ad_mode = db.get_user_ad_mode(uid)
        
        ad_count = len(saved_msgs)
        if ad_count == 0:
            ad_msg_status = "Not Set ⭕"
        elif ad_mode == "single" or ad_count == 1:
            atype = saved_msgs[0].get("ad_type", "text")
            type_label = {"photo": "Photo 🖼️", "both": "Photo+Text 🖼️📝", "forward": "Forward 📨"}.get(atype, "Text 📝")
            ad_msg_status = f"1 Ad ({type_label} - Single) ✅"
        else:
            ad_msg_status = f"{ad_count} Ads (Rotating) 🔄"

        current_delay = db.get_user_ad_delay(uid)
        broadcast_state = db.get_broadcast_state(uid)
        running = broadcast_state.get("running", False)
        broadcast_status = "Running 🚀" if running else "Paused ⏸️"
        
        auto_reply_data = db.get_auto_reply(uid)
        ar_status = "ON ✅" if auto_reply_data.get("enabled") else "OFF ❌"

        selected_acc_ids = db.get_selected_broadcast_accounts(uid)
        if selected_acc_ids is None:
            bcast_acc_label = f"All ({accounts_count})"
        elif selected_acc_ids:
            bcast_acc_label = f"{len(selected_acc_ids)} of {accounts_count} selected"
        else:
            bcast_acc_label = f"None selected"

        dashboard_caption = (
            f"<blockquote><b>╰_╯ ADS DASHBOARD</b></blockquote>\n\n"
            f"•Hosted Accounts: <code>{accounts_count}</code> (Unlimited ♾️)\n"
            f"•Broadcasting With: <code>{bcast_acc_label}</code>\n"
            f"•Ad Message: {ad_msg_status}\n"
            f"•Cycle Interval: {current_delay}s\n"
            f"•Advertising Status: <b>{broadcast_status}</b>\n\n"
            "<blockquote>╰_╯Choose an action below to continue </blockquote>"
        )

        menu = [
            [InlineKeyboardButton("Add Accounts", callback_data="host_account"),
             InlineKeyboardButton("My Accounts", callback_data="view_accounts")],
            [InlineKeyboardButton("Set Ad Message/Photo 📸", callback_data="set_msg"),
             InlineKeyboardButton("Set Time Interval", callback_data="set_delay")],
            [InlineKeyboardButton("Start Ads▶️", callback_data="start_broadcast"),
             InlineKeyboardButton("Stop Ads⏸️", callback_data="stop_broadcast")],
            [InlineKeyboardButton("Analytics 📊", callback_data="analytics"),
             InlineKeyboardButton("Delete Accounts 🗑️", callback_data="delete_accounts")],
            [InlineKeyboardButton("🎭 Chinese Identity Reset (Names/Pics/Posts) 🇨🇳", callback_data="reset_identity_menu")],
            [InlineKeyboardButton("✏️ Change Bio for All Accounts 📝", callback_data="change_bio_menu")]
        ]
        
        try:
            await cb.message.edit_text(
                text=dashboard_caption,
                reply_markup=kb(menu),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Error editing message in menu_main: {e}")
            await cb.answer("Error loading dashboard. Try /start.", show_alert=True)
        logger.info(f"Menu main accessed by user {uid}, callback_data: {cb.data}")
    except Exception as e:
        logger.error(f"Error in menu_main for user {uid}: {e}")
        await cb.answer("Error loading dashboard. Try /start.", show_alert=True)

@pyro.on_callback_query(filters.regex(r"^host_account$"))
async def host_account(client, cb):
    uid = cb.from_user.id
    user = db.get_user(uid)
    
    if not user:
        await cb.answer("Please restart with /start", show_alert=True)
        return
    
    accounts_count = db.get_user_accounts_count(uid)
    limit = user.get("accounts_limit", 5)
    if isinstance(limit, str):
        if limit.lower() == "unlimited":
            limit = 999
        else:
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                limit = 5
    
    if not is_owner(uid) and accounts_count >= limit:
        await cb.answer(f"Account limit reached ({accounts_count}/{limit}).", show_alert=True)
        return
    
    menu = [
        [InlineKeyboardButton("📱 Via Phone Number + OTP", callback_data="host_by_phone")],
        [InlineKeyboardButton("📁 Via .session File (Direct)", callback_data="host_by_file")],
        [InlineKeyboardButton("🔑 Via String Session", callback_data="host_by_string")],
        [InlineKeyboardButton("Back 🔙", callback_data="menu_main")]
    ]
    
    caption = (
        "<blockquote><b>╰_╯ HOST NEW TELEGRAM ACCOUNT</b></blockquote>\n\n"
        "Choose your preferred login method:\n\n"
        "• 📱 <b>Phone + OTP:</b> Login with phone number and receive OTP code\n"
        "• 📁 <b>.session File:</b> Upload a Telethon or Pyrogram <code>.session</code> file directly\n"
        "• 🔑 <b>String Session:</b> Paste your session string\n\n"
        f"<b>Hosted Accounts:</b> <code>{accounts_count}</code>\n"
        "<blockquote>All sessions are AES-256 encrypted and secure 🔒</blockquote>"
    )
    
    try:
        await cb.message.edit_media(
            media=InputMediaPhoto(
                media=config.FORCE_JOIN_IMAGE,
                caption=caption,
                parse_mode=ParseMode.HTML
            ),
            reply_markup=kb(menu)
        )
    except Exception:
        await cb.message.edit_caption(
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb(menu)
        )

@pyro.on_callback_query(filters.regex(r"^host_by_phone$"))
async def host_by_phone(client, cb):
    uid = cb.from_user.id
    try:
        db.set_user_state(uid, "telethon_wait_phone")
        db.set_temp_data(uid, None)
    except Exception as e:
        logger.error(f"Failed to set user state for {uid}: {e}")
        await cb.answer("Error. Try again.", show_alert=True)
        return

    caption = (
        "<blockquote><b>📱 HOST VIA PHONE NUMBER</b></blockquote>\n\n"
        "Enter your Telegram phone number with country code:\n\n"
        "<blockquote>Example: <code>+1234567890</code></blockquote>\n\n"
        "You will receive an OTP code in your Telegram app."
    )
    try:
        await cb.message.edit_media(
            media=InputMediaPhoto(
                media=config.FORCE_JOIN_IMAGE,
                caption=caption,
                parse_mode=ParseMode.HTML
            ),
            reply_markup=kb([[InlineKeyboardButton("Back 🔙", callback_data="host_account")]])
        )
    except Exception:
        await cb.message.edit_caption(
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Back 🔙", callback_data="host_account")]])
        )

@pyro.on_callback_query(filters.regex(r"^host_by_file$"))
async def host_by_file(client, cb):
    uid = cb.from_user.id
    try:
        db.set_user_state(uid, "telethon_wait_file")
        db.set_temp_data(uid, None)
    except Exception as e:
        logger.error(f"Failed to set user state for {uid}: {e}")
        await cb.answer("Error. Try again.", show_alert=True)
        return

    caption = (
        "<blockquote><b>📁 HOST VIA .SESSION FILE</b></blockquote>\n\n"
        "Send your <code>.session</code> file as a document in this chat.\n\n"
        "• Supports Telethon & Pyrogram <code>.session</code> files\n"
        "• Instant login without OTP / phone code\n"
        "• You can send files one-by-one\n\n"
        "<blockquote>Send the file now ⬇️</blockquote>"
    )
    try:
        await cb.message.edit_media(
            media=InputMediaPhoto(
                media=config.FORCE_JOIN_IMAGE,
                caption=caption,
                parse_mode=ParseMode.HTML
            ),
            reply_markup=kb([[InlineKeyboardButton("Back 🔙", callback_data="host_account")]])
        )
    except Exception:
        await cb.message.edit_caption(
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Back 🔙", callback_data="host_account")]])
        )

@pyro.on_callback_query(filters.regex(r"^host_by_string$"))
async def host_by_string(client, cb):
    uid = cb.from_user.id
    try:
        db.set_user_state(uid, "telethon_wait_string")
        db.set_temp_data(uid, None)
    except Exception as e:
        logger.error(f"Failed to set user state for {uid}: {e}")
        await cb.answer("Error. Try again.", show_alert=True)
        return

    caption = (
        "<blockquote><b>🔑 HOST VIA STRING SESSION</b></blockquote>\n\n"
        "Please paste and send your Telethon / Pyrogram String Session text in this chat.\n\n"
        "<blockquote>Example: <code>1BQAAAA...</code></blockquote>"
    )
    try:
        await cb.message.edit_media(
            media=InputMediaPhoto(
                media=config.FORCE_JOIN_IMAGE,
                caption=caption,
                parse_mode=ParseMode.HTML
            ),
            reply_markup=kb([[InlineKeyboardButton("Back 🔙", callback_data="host_account")]])
        )
    except Exception:
        await cb.message.edit_caption(
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Back 🔙", callback_data="host_account")]])
        )

@pyro.on_callback_query(filters.regex("view_accounts"))
async def view_accounts(client, cb):
    try:
        uid = cb.from_user.id
        accounts = db.get_user_accounts(uid)
        if not accounts:
            try:
                await cb.message.edit_caption(
                    caption="""<blockquote><b>╰_╯NO ACCOUNTS HOSTED</b></blockquote>\n\n"""
                            """Add an account to start broadcasting!""",
                    reply_markup=kb([[InlineKeyboardButton("Add Account 📱", callback_data="host_account"),
                                    InlineKeyboardButton("Back 🔙", callback_data="menu_main")]]),
                    parse_mode=ParseMode.HTML
                )
            except MessageNotModified:
                pass
            await cb.answer()
            return
        
        caption = "<blockquote><b>╰_╯HOSTED ACCOUNTS</b></blockquote>\n\n"
        buttons = []
        for i, acc in enumerate(accounts, 1):
            status = "Active ✅" if acc['is_active'] else "Inactive ❌"
            acc_name = get_account_display_name(acc)
            phone = acc.get('phone_number', '')
            caption += f"{i}. <b>{acc_name}</b> (<code>{phone}</code>) - <i>{status}</i>\n"
            buttons.append([
                InlineKeyboardButton(f"{acc_name} ({status})", callback_data=f"view_acc_{acc['_id']}"),
                InlineKeyboardButton("Delete", callback_data=f"delete_acc_{acc['_id']}")
            ])
        
        caption += "\n<blockquote>╰_╯Choose an action:</blockquote>"
        buttons.append([InlineKeyboardButton("🎭 Reset Identity (Chinese Names & Wipe) 🇨🇳", callback_data="reset_identity_menu")])
        buttons.append([InlineKeyboardButton("✏️ Change Bio for All Accounts 📝", callback_data="change_bio_menu")])
        buttons.append([InlineKeyboardButton("Add Account ➕", callback_data="host_account")])
        if is_owner(uid):
            buttons.append([InlineKeyboardButton("📦 Backup All Accounts (Sessions)", callback_data="backup_accounts")])
        buttons.append([InlineKeyboardButton("Back 🔙", callback_data="menu_main")])
        
        try:
            await cb.message.edit_caption(
                caption=caption,
                reply_markup=kb(buttons),
                parse_mode=ParseMode.HTML
            )
        except MessageNotModified:
            pass
        await cb.answer()
    except Exception as e:
        logger.error(f"Error in view_accounts: {e}")
        try:
            await cb.answer()
        except Exception:
            pass

@pyro.on_callback_query(filters.regex("delete_accounts"))
async def delete_accounts(client, cb):
    try:
        uid = cb.from_user.id
        accounts = db.get_user_accounts(uid)
        if not accounts:
            try:
                await cb.message.edit_caption(
                    caption="""<blockquote><b>╰_╯NO ACCOUNTS TO DELETE</b></blockquote>\n\n"""
                            """Add an account to start Advertising!""",
                    reply_markup=kb([[InlineKeyboardButton("Add Account", callback_data="host_account"),
                                    InlineKeyboardButton("Back", callback_data="menu_main")]]),
                    parse_mode=ParseMode.HTML
                )
            except MessageNotModified:
                pass
            await cb.answer()
            return
        
        caption = "<blockquote><b>╰_╯ DELETE ACCOUNTS</b></blockquote>\n\n"
        buttons = []
        for i, acc in enumerate(accounts, 1):
            status = "Active ✅" if acc['is_active'] else "Inactive ❌"
            acc_name = get_account_display_name(acc)
            phone = acc.get('phone_number', '')
            caption += f"{i}. <b>{acc_name}</b> (<code>{phone}</code>) - <i>{status}</i>\n"
            buttons.append([InlineKeyboardButton(f"Delete {acc_name}", callback_data=f"delete_acc_{acc['_id']}")])
        
        buttons.append([InlineKeyboardButton("Delete All Accounts ⚠️", callback_data="delete_all_accounts")])
        buttons.append([InlineKeyboardButton("Back 🔙", callback_data="view_accounts")])
        
        try:
            await cb.message.edit_caption(
                caption=caption,
                reply_markup=kb(buttons),
                parse_mode=ParseMode.HTML
            )
        except MessageNotModified:
            pass
        await cb.answer()
    except Exception as e:
        logger.error(f"Error in delete_accounts: {e}")
        try:
            await cb.answer()
        except Exception:
            pass

@pyro.on_callback_query(filters.regex("delete_acc_"))
async def delete_account(client, cb):
    uid = cb.from_user.id
    acc_id = cb.data.replace("delete_acc_", "")
    try:
        db.delete_user_account(uid, acc_id)
        # Immediately cleanly disconnect from pool and forwarder
        await stop_and_remove_account_client(acc_id)
        await cb.message.edit_caption(
            caption="""<blockquote><b> Account deleted!</b></blockquote>\n\n"""
                    """Account removed successfully.✅""",
            reply_markup=kb([[InlineKeyboardButton("Back 🔙", callback_data="delete_accounts")]]),
            parse_mode=ParseMode.HTML
        )
        await send_dm_log(uid, f"<b>Account deleted:</b> ID {acc_id}")
        logger.info(f"Account {acc_id} deleted by user {uid}")
    except Exception as e:
        logger.error(f"Failed to delete account {acc_id} for user {uid}: {e}")
        await cb.answer("Error deleting account. Try again.", show_alert=True)
        await send_dm_log(uid, f"<b>❌ Failed to delete account:</b> {str(e)}")

@pyro.on_callback_query(filters.regex("view_acc_"))
async def view_account(client, cb):
    uid = cb.from_user.id
    acc_id = cb.data.replace("view_acc_", "")
    accounts = db.get_user_accounts(uid)
    account = next((acc for acc in accounts if str(acc['_id']) == str(acc_id)), None)
    if not account:
        await cb.answer("Account not found.", show_alert=True)
        return
    
    status = "Active ✅" if account.get('is_active') else "Inactive ⭕"
    current_bio = account.get('bio', 'Not set')
    acc_name = get_account_display_name(account)
    caption = (
        f"<blockquote><b>╰_╯ACCOUNT DETAILS</b></blockquote>\n\n"
        f"• <b>Name:</b> <b>{acc_name}</b>\n"
        f"• <b>Phone:</b> <code>{account['phone_number']}</code>\n"
        f"• <b>Status:</b> {status}\n"
        f"• <b>Bio:</b> <code>{current_bio}</code>\n\n"
        f"<blockquote>Choose an action:</blockquote>"
    )
    
    await cb.message.edit_caption(
        caption=caption,
        reply_markup=kb([
            [InlineKeyboardButton("🎭 Reset to Chinese Identity (Name/Pics/Posts) 🇨🇳", callback_data=f"reset_identity_acc_{acc_id}")],
            [InlineKeyboardButton("✏️ Change Bio", callback_data=f"edit_bio_acc_{acc_id}")],
            [InlineKeyboardButton("Delete Account 🗑️", callback_data=f"delete_acc_{acc_id}")],
            [InlineKeyboardButton("Back 🔙", callback_data="view_accounts")]
        ]),
        parse_mode=ParseMode.HTML
    )


# ─────────────────────────────────────────────────────────────────────────────
# MANUAL BIO CHANGE & FOLDER JOIN HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

@pyro.on_callback_query(filters.regex(r"^change_bio_menu$"))
async def change_bio_menu(client, cb):
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.answer("Please add accounts first!", show_alert=True)
        return

    caption = (
        "<blockquote><b>╰_╯ MANAGE ACCOUNT BIOS ✏️</b></blockquote>\n\n"
        "You can update the profile <b>Bio (About)</b> of your hosted accounts directly from here.\n\n"
        "• <b>Bulk update:</b> Change bio for all accounts in one go\n"
        "• <b>Single account:</b> Change bio for an individual account\n\n"
        "<i>Standard Telegram bio limit: 70 characters (140 for Premium).</i>\n\n"
        "<blockquote>Select an option below:</blockquote>"
    )

    buttons = [
        [InlineKeyboardButton("🌐 Update Bio for ALL Accounts", callback_data="edit_bio_all")],
        [InlineKeyboardButton("👤 Choose Specific Account", callback_data="change_bio_choose")],
        [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="menu_main")]
    ]

    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        else:
            await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning(f"Error in change_bio_menu: {e}")
        await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)


@pyro.on_callback_query(filters.regex(r"^change_bio_choose$"))
async def change_bio_choose(client, cb):
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.answer("Please add accounts first!", show_alert=True)
        return

    caption = (
        "<blockquote><b>👤 SELECT ACCOUNT TO CHANGE BIO</b></blockquote>\n\n"
        "Select the account you want to update the bio for:"
    )
    buttons = []
    row = []
    for acc in accounts:
        acc_name = get_account_display_name(acc)
        acc_id = str(acc["_id"])
        row.append(InlineKeyboardButton(f"✏️ {acc_name}", callback_data=f"edit_bio_acc_{acc_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("Back 🔙", callback_data="change_bio_menu")])

    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        else:
            await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning(f"Error in change_bio_choose: {e}")
        await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)


@pyro.on_callback_query(filters.regex(r"^edit_bio_all$"))
async def edit_bio_all_cb(client, cb):
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.answer("No accounts available!", show_alert=True)
        return

    db.set_user_state(uid, "waiting_bio_all")
    caption = (
        f"<blockquote><b>✏️ SET BIO FOR ALL ACCOUNTS</b></blockquote>\n\n"
        f"Please send the new Bio text below. It will be applied to <b>all {len(accounts)} hosted accounts</b>.\n\n"
        f"<i>• Max 70 characters (140 for Premium)</i>\n"
        f"<i>• Send <code>/clear</code> to remove bio from all accounts</i>\n"
        f"<i>• Send <code>/cancel</code> to abort</i>"
    )
    buttons = [
        [InlineKeyboardButton("Cancel ❌", callback_data="change_bio_menu")],
        [InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]
    ]

    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        else:
            await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception:
        await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)


@pyro.on_callback_query(filters.regex(r"^edit_bio_acc_"))
async def edit_bio_single_cb(client, cb):
    uid = cb.from_user.id
    acc_id = cb.data.replace("edit_bio_acc_", "")
    accounts = db.get_user_accounts(uid)
    account = next((a for a in accounts if str(a["_id"]) == str(acc_id)), None)
    if not account:
        await cb.answer("Account not found!", show_alert=True)
        return

    phone = account.get("phone_number", "Unknown")
    db.set_user_state(uid, "waiting_bio_single")
    db.set_user_temp_data(uid, "target_bio_acc", {"acc_id": str(acc_id), "phone": phone})

    caption = (
        f"<blockquote><b>✏️ SET BIO FOR {phone}</b></blockquote>\n\n"
        f"Please send the new Bio text for this account below:\n\n"
        f"<i>• Max 70 characters (140 for Premium)</i>\n"
        f"<i>• Send <code>/clear</code> to remove bio</i>\n"
        f"<i>• Send <code>/cancel</code> to abort</i>"
    )
    buttons = [[InlineKeyboardButton("Cancel ❌", callback_data=f"view_acc_{acc_id}")]]

    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        else:
            await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception:
        await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)


async def perform_bio_update(client, uid, new_bio, account_ids=None):
    new_bio = new_bio.strip()
    if new_bio.lower() == "/clear":
        new_bio = ""

    if len(new_bio) > 140:
        await client.send_message(
            uid,
            f"<blockquote><b>❌ Bio too long!</b></blockquote>\n\n"
            f"Telegram allows max <b>70 characters</b> (140 for Premium).\n"
            f"Your text has <b>{len(new_bio)} characters</b>.\n\n"
            f"Please try again with a shorter message.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Back to Bio Menu", callback_data="change_bio_menu")]])
        )
        return

    all_accounts = db.get_user_accounts(uid)
    if not all_accounts:
        await client.send_message(uid, "❌ No accounts found.", reply_markup=kb([[InlineKeyboardButton("Dashboard", callback_data="menu_main")]]))
        return

    if account_ids:
        targets = [a for a in all_accounts if str(a['_id']) in [str(x) for x in account_ids]]
    else:
        targets = [a for a in all_accounts if a.get('is_active', True)]

    if not targets:
        await client.send_message(uid, "❌ No active accounts to update.", reply_markup=kb([[InlineKeyboardButton("Back", callback_data="change_bio_menu")]]))
        return

    bio_display = f"<code>{new_bio}</code>" if new_bio else "<i>[Cleared]</i>"
    status_msg = await client.send_message(
        uid,
        f"⏳ <b>Updating Bio across {len(targets)} account(s)...</b>\n\n"
        f"<b>Target Bio:</b> {bio_display}\n"
        f"<i>Please wait, connecting accounts...</i>",
        parse_mode=ParseMode.HTML
    )

    success_list = []
    fail_list = []

    for idx, acc in enumerate(targets, 1):
        phone = acc.get('phone_number', 'Unknown')
        name = get_account_display_name(acc)
        acc_id = acc['_id']
        tg_client = None
        try:
            tg_client, _ = await get_or_create_client(str(acc_id), acc, uid)
            if not tg_client or not await tg_client.is_user_authorized():
                fail_list.append(f"• <b>{name}</b> (<code>{phone}</code>): ❌ Session logged out / removed from bot")
                continue

            me = await tg_client.get_me()
            await tg_client(UpdateProfileRequest(
                first_name=me.first_name or "",
                last_name=me.last_name or "",
                about=new_bio
            ))

            verified_bio = new_bio
            try:
                full = await tg_client(GetFullUserRequest('me'))
                verified_bio = (getattr(full.full_user, 'about', '') or "").strip()
            except Exception:
                pass

            db.update_account_bio(acc_id, new_bio)

            if verified_bio == new_bio.strip() or (not new_bio and not verified_bio):
                success_list.append(f"• <b>{name}</b> (<code>{phone}</code>): ✅ Bio updated & verified")
                logger.info(f"Bio updated & verified for {phone}: {new_bio}")
            else:
                success_list.append(f"• <b>{name}</b> (<code>{phone}</code>): ⚠️ Saved (Telegram got: '{verified_bio[:30]}')")
                logger.warning(f"Bio discrepancy for {phone}: expected '{new_bio}', got '{verified_bio}'")
        except FloodWaitError as e:
            fail_list.append(f"• <b>{name}</b> (<code>{phone}</code>): ⏳ FloodWait ({e.seconds}s)")
            logger.warning(f"FloodWait updating bio for {phone}: {e.seconds}s")
        except AboutTooLongError:
            fail_list.append(f"• <b>{name}</b> (<code>{phone}</code>): ❌ Exceeds 70-character limit")
        except Exception as e:
            import traceback
            err_msg = str(e)
            if "about too long" in err_msg.lower():
                err_msg = "Exceeds 70-character limit"
            fail_list.append(f"• <b>{name}</b> (<code>{phone}</code>): ❌ {err_msg[:80]}")
            logger.error(f"Failed to update bio for {phone}: {traceback.format_exc()}")


        if len(targets) > 1 and idx % 2 == 0:
            try:
                await status_msg.edit_text(
                    f"⏳ <b>Updating Bio...</b> ({idx}/{len(targets)})\n\n"
                    f"✅ Success: {len(success_list)} | ❌ Issues: {len(fail_list)}",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
        await asyncio.sleep(0.6)

    report = (
        f"<blockquote><b>╰_╯ BIO UPDATE COMPLETED! ✅</b></blockquote>\n\n"
        f"<b>New Bio:</b> {bio_display}\n\n"
        f"<b>Summary:</b>\n"
        f"• Total Accounts: {len(targets)}\n"
        f"• ✅ Success: {len(success_list)}\n"
        f"• ❌ Failed: {len(fail_list)}\n\n"
    )
    details = success_list + fail_list
    if len(details) <= 12:
        report += "\n".join(details)
    else:
        report += "\n".join(details[:12]) + f"\n<i>...and {len(details) - 12} more</i>"

    await status_msg.edit_text(
        report,
        parse_mode=ParseMode.HTML,
        reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
    )
    await send_dm_log(uid, f"<b>✏️ Bio update:</b> {len(success_list)}/{len(targets)} accounts updated.")


# ─────────────────────────────────────────────────────────────────────────────
# 330+ AUTHENTIC CHINESE NAMES POOL & ACCOUNT IDENTITY RESET HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

CHINESE_NAMES_POOL = [
    # Classic / Common Chinese Names in Pinyin
    "Lǐ Wěi", "Wáng Wěi", "Wáng Fāng", "Lǐ Xiùyīng", "Lǐ Qiáng", "Wáng Xiùyīng", "Zhāng Mǐn", "Lǐ Mǐn", "Wáng Jìng", "Zhāng Lì",
    "Wáng Lì", "Lǐ Jìng", "Zhāng Qiáng", "Zhāng Xiùyīng", "Lǐ Jūn", "Wáng Mǐn", "Zhāng Yǒng", "Lǐ Yàn", "Wáng Yàn", "Zhāng Jié",
    "Zhāng Lěi", "Wáng Qiáng", "Liú Yáng", "Wáng Lěi", "Lǐ Yǒng", "Wáng Jūn", "Zhāng Yàn", "Zhāng Tāo", "Lǐ Lěi", "Wáng Yǒng",
    "Liú Wěi", "Zhāng Píng", "Wáng Píng", "Lǐ Gāng", "Wáng Gāng", "Zhāng Fēng", "Lǐ Jié", "Liú Fāng", "Zhāng Jūn", "Lǐ Juān",
    "Wáng Juān", "Lǐ Míng", "Wáng Míng", "Zhāng Míng", "Lǐ Jiànguó", "Wáng Jiànguó", "Zhāng Jiàn", "Lǐ Huá", "Wáng Jiànhuá", "Zhāng Jiànhuá",
    "Liú Xiùyīng", "Lǐ Guìyīng", "Wáng Guìyīng", "Liú Mǐn", "Liú Jìng", "Liú Lì", "Liú Qiáng", "Liú Jūn", "Liú Yàn", "Liú Yǒng",
    "Liú Jié", "Liú Lěi", "Liú Píng", "Liú Gāng", "Liú Fēng", "Liú Juān", "Liú Míng", "Liú Jiànhuá", "Liú Jiàn", "Liú Guìyīng",
    "Chén Wěi", "Chén Fāng", "Chén Xiùyīng", "Chén Mǐn", "Chén Jìng", "Chén Lì", "Chén Qiáng", "Chén Jūn", "Chén Yàn", "Chén Yǒng",
    "Chén Jié", "Chén Lěi", "Chén Píng", "Chén Gāng", "Chén Fēng", "Chén Juān", "Chén Míng", "Chén Jiànhuá", "Chén Jiàn", "Chén Guìyīng",
    "Yáng Wěi", "Yáng Fāng", "Yáng Xiùyīng", "Yáng Mǐn", "Yáng Jìng", "Yáng Lì", "Yáng Qiáng", "Yáng Jūn", "Yáng Yàn", "Yáng Yǒng",
    "Yáng Jié", "Yáng Lěi", "Yáng Píng", "Yáng Gāng", "Yáng Fēng", "Yáng Juān", "Yáng Míng", "Yáng Jiànhuá", "Yáng Jiàn", "Yáng Guìyīng",
    "Zhào Wěi", "Zhào Fāng", "Zhào Xiùyīng", "Zhào Mǐn", "Zhào Jìng", "Zhào Lì", "Zhào Qiáng", "Zhào Jūn", "Zhào Yàn", "Zhào Yǒng",
    "Zhào Jié", "Zhào Lěi", "Zhào Píng", "Zhào Gāng", "Zhào Fēng", "Zhào Juān", "Zhào Míng", "Zhào Jiànhuá", "Zhào Jiàn", "Zhào Guìyīng",
    "Huáng Wěi", "Huáng Fāng", "Huáng Xiùyīng", "Huáng Mǐn", "Huáng Jìng", "Huáng Lì", "Huáng Qiáng", "Huáng Jūn", "Huáng Yàn", "Huáng Yǒng",
    "Huáng Jié", "Huáng Lěi", "Huáng Píng", "Huáng Gāng", "Huáng Fēng", "Huáng Juān", "Huáng Míng", "Huáng Jiànhuá", "Huáng Jiàn", "Huáng Guìyīng",
    "Zhōu Wěi", "Zhōu Fāng", "Zhōu Xiùyīng", "Zhōu Mǐn", "Zhōu Jìng", "Zhōu Lì", "Zhōu Qiáng", "Zhōu Jūn", "Zhōu Yàn", "Zhōu Yǒng",
    "Zhōu Jié", "Zhōu Lěi", "Zhōu Píng", "Zhōu Gāng", "Zhōu Fēng", "Zhōu Juān", "Zhōu Míng", "Zhōu Jiànhuá", "Zhōu Jiàn", "Zhōu Guìyīng",
    "Wú Wěi", "Wú Fāng", "Wú Xiùyīng", "Wú Mǐn", "Wú Jìng", "Wú Lì", "Wú Qiáng", "Wú Jūn", "Wú Yàn", "Wú Yǒng",
    "Wú Jié", "Wú Lěi", "Wú Píng", "Wú Gāng", "Wú Fēng", "Wú Juān", "Wú Míng", "Wú Jiànhuá", "Wú Jiàn", "Wú Guìyīng",
    "Xú Wěi", "Xú Fāng", "Xú Xiùyīng", "Xú Mǐn", "Xú Jìng", "Xú Lì", "Xú Qiáng", "Xú Jūn", "Xú Yàn", "Xú Yǒng",
    "Xú Jié", "Xú Lěi", "Xú Píng", "Xú Gāng", "Xú Fēng", "Xú Juān", "Xú Míng", "Xú Jiànhuá", "Xú Jiàn", "Xú Guìyīng",
    "Sūn Wěi", "Sūn Fāng", "Sūn Xiùyīng", "Sūn Mǐn", "Sūn Jìng", "Sūn Lì", "Sūn Qiáng", "Sūn Jūn", "Sūn Yàn", "Sūn Yǒng",
    "Sūn Jié", "Sūn Lěi", "Sūn Píng", "Sūn Gāng", "Sūn Fēng", "Sūn Juān", "Sūn Míng", "Sūn Jiànhuá", "Sūn Jiàn", "Sūn Guìyīng",
    # Modern / Business / Trendy Chinese Names in Pinyin
    "Lín Zǐxuān", "Lín Hàoyǔ", "Lín Xīnyí", "Lín Shīhán", "Lín Jùnxī", "Lín Sīyǐng", "Lín Yǔtóng", "Lín Bówén", "Lín Yìhán", "Lín Zǐxuān",
    "Mǎ Hóngbó", "Mǎ Chénxī", "Mǎ Jiāníng", "Mǎ Ruìbó", "Mǎ Míngyuǎn", "Mǎ Zhènyǔ", "Mǎ Jiāháo", "Mǎ Xiǎofēng", "Mǎ Tiānyòu", "Mǎ Sīcōng",
    "Zhū Yǔxiáng", "Zhū Jǐnchéng", "Zhū Hàorán", "Zhū Qǐwén", "Zhū Yìfēi", "Zhū Jiāchéng", "Zhū Bóruì", "Zhū Wénbīn", "Zhū Zékǎi", "Zhū Hǎikuò",
    "Hú Tiānyǔ", "Hú Xīngchén", "Hú Jìngyuǎn", "Hú Zhìháng", "Hú Jùnkǎi", "Hú Hàotiān", "Hú Yànbīn", "Hú Xuěfēng", "Hú Rùnzé", "Hú Jiāháo",
    "Guō Zǐhán", "Guō Míngxuān", "Guō Yìchén", "Guō Xiūyuǎn", "Guō Hóngyì", "Guō Hǎitāo", "Guō Shàoyóu", "Guō Zhènhuá", "Guō Yúnpéng", "Guō Chényáng",
    "Hé Jǐngtiān", "Hé Rùnbó", "Hé Yìfán", "Hé Chǔtiān", "Hé Chéngdé", "Hé Zhìyuǎn", "Hé Guānlín", "Hé Xiàngyáng", "Hé Hǎifēng", "Hé Mínghuī",
    "Gāo Bóyáng", "Gāo Jùnyì", "Gāo Péngzhǎn", "Gāo Tiānxiáng", "Gāo Zǐmò", "Gāo Zhìyuǎn", "Gāo Jǐnháo", "Gāo Hàosēn", "Gāo Yǔdá", "Gāo Shìháo",
    "Zhèng Zǐhéng", "Zhèng Sīchéng", "Zhèng Zéyǔ", "Zhèng Wénbó", "Zhèng Hóngdá", "Zhèng Kāitài", "Zhèng Xùdōng", "Zhèng Jiāhuī", "Zhèng Zhǎnpéng", "Zhèng Hàorán",
    "Xiè Hóngwén", "Xiè Yǔkūn", "Xiè Ānrán", "Xiè Tiānzé", "Xiè Zhìyuǎn", "Xiè Hàotiān", "Xiè Wénzhuó", "Xiè Jiāháo", "Xiè Jǐngpíng", "Xiè Yǔzé",
    "Hán Ruòbīng", "Hán Sīyuǎn", "Hán Jǐngchéng", "Hán Zǐqí", "Hán Zhènjiāng", "Hán Tiānlè", "Hán Míngxuān", "Hán Jiāxiáng", "Hán Hǎifēng", "Hán Jùnxióng"
]


async def wipe_account_profile_photos(tg_client):
    """Deletes all existing profile photos for the account."""
    photos_deleted = 0
    try:
        photos_res = await tg_client(functions.photos.GetUserPhotosRequest(user_id='me', offset=0, max_id=0, limit=100))
        input_photos = []
        for p in getattr(photos_res, 'photos', []):
            try:
                ip = utils.get_input_photo(p)
                if ip and not isinstance(ip, types.InputPhotoEmpty):
                    input_photos.append(ip)
            except Exception:
                pass
        if input_photos:
            await tg_client(functions.photos.DeletePhotosRequest(id=input_photos))
            photos_deleted = len(input_photos)
            logger.info(f"Wiped {photos_deleted} profile photos from account")
    except Exception as e:
        logger.warning(f"Could not wipe profile photos: {e}")
    return photos_deleted


async def wipe_account_posts_and_stories(tg_client):
    """Deletes personal broadcast channels created by the account, stories, and saved messages."""
    posts_cleared = 0
    # 1. Clear stories if any exist
    try:
        pinned = await tg_client(functions.stories.GetPinnedStoriesRequest(peer='me', offset_id=0, limit=100))
        story_ids = [s.id for s in getattr(pinned, 'stories', []) if hasattr(s, 'id')]
        if story_ids:
            await tg_client(functions.stories.DeleteStoriesRequest(peer='me', id=story_ids))
            posts_cleared += len(story_ids)
    except Exception as e:
        logger.debug(f"Story clear skipped: {e}")

    # 2. Check for personal broadcast channels where this user is creator/owner
    try:
        async for dialog in tg_client.iter_dialogs(limit=50):
            if dialog.is_channel and not dialog.is_group:
                entity = dialog.entity
                if getattr(entity, 'creator', False):
                    try:
                        await tg_client(functions.channels.DeleteChannelRequest(channel=entity))
                        posts_cleared += 1
                        logger.info(f"Deleted personal channel {dialog.name} ({dialog.id})")
                    except Exception as ce:
                        logger.debug(f"Could not delete channel {dialog.id}: {ce}")
    except Exception as e:
        logger.debug(f"Channel cleanup skipped: {e}")

    # 3. Clear Saved Messages (self chat)
    try:
        saved_msgs = await tg_client.get_messages('me', limit=100)
        if saved_msgs:
            msg_ids = [m.id for m in saved_msgs]
            await tg_client.delete_messages('me', msg_ids)
            posts_cleared += len(msg_ids)
    except Exception as e:
        logger.debug(f"Saved messages clear skipped: {e}")

    return posts_cleared


async def perform_account_identity_reset(tg_client, acc, uid, chosen_name):
    """
    Sets an authentic Chinese name, wipes all profile pictures, clears bio,
    and removes personal posts/stories for the given account.
    """
    acc_id = acc['_id']
    phone = acc.get('phone_number', 'Unknown')
    old_name = get_account_display_name(acc)

    # 1. Update Profile in Telegram (Name and clear Bio)
    await tg_client(functions.account.UpdateProfileRequest(
        first_name=chosen_name,
        last_name="",
        about=""
    ))

    # 2. Wipe Profile Pictures
    photos_wiped = await wipe_account_profile_photos(tg_client)

    # 3. Wipe Personal Posts and Stories
    posts_wiped = await wipe_account_posts_and_stories(tg_client)

    # 4. Update Database
    db.update_account_name(acc_id, chosen_name, "")
    db.update_account_bio(acc_id, "")

    logger.info(f"Identity reset for {phone}: '{old_name}' -> '{chosen_name}', photos wiped: {photos_wiped}, posts wiped: {posts_wiped}")
    return {
        "acc_id": str(acc_id),
        "phone": phone,
        "old_name": old_name,
        "new_name": chosen_name,
        "photos_wiped": photos_wiped,
        "posts_wiped": posts_wiped
    }


@pyro.on_callback_query(filters.regex(r"^reset_identity_menu$"))
async def reset_identity_menu(client, cb):
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.answer("Please add accounts first!", show_alert=True)
        return

    caption = (
        "<blockquote><b>╰_╯ CHINESE IDENTITY RESET 🎭🇨🇳</b></blockquote>\n\n"
        "Transform your hosted accounts with a completely fresh, authentic Chinese identity:\n\n"
        "• 🏷️ <b>Chinese Names (Pinyin):</b> Randomly assigned from <b>330+ curated Pinyin names</b> (e.g. <i>Lín Hàoyǔ, Wáng Wěi, Mǎ Chénxī</i>)\n"
        "• 🖼️ <b>Profile Pictures:</b> Deletes all previous profile photos completely\n"
        "• 📝 <b>Profile Bio:</b> Cleared clean (zero old traces)\n"
        "• 📜 <b>Posts & Stories:</b> Wipes all previous stories & personal channels\n\n"
        f"<b>Currently Hosted Accounts:</b> <code>{len(accounts)}</code>\n\n"
        "<i>Select an option below to proceed:</i>"
    )

    buttons = [
        [InlineKeyboardButton(f"🇨🇳 Reset ALL Accounts Now ({len(accounts)})", callback_data="reset_identity_all_confirm")],
        [InlineKeyboardButton("🎯 Choose Single Account", callback_data="reset_identity_choose")],
        [InlineKeyboardButton("Back 🔙", callback_data="view_accounts")]
    ]

    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        else:
            await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception:
        await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)


@pyro.on_callback_query(filters.regex(r"^reset_identity_choose$"))
async def reset_identity_choose(client, cb):
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.answer("No accounts found!", show_alert=True)
        return

    caption = (
        "<blockquote><b>🎯 SELECT ACCOUNT TO RESET</b></blockquote>\n\n"
        "Choose an account below to assign a random Chinese name, wipe its profile pictures, and clear previous posts/stories:\n"
    )

    buttons = []
    row = []
    for acc in accounts:
        name = get_account_display_name(acc)
        acc_id = str(acc["_id"])
        row.append(InlineKeyboardButton(f"🎭 {name}", callback_data=f"reset_identity_acc_{acc_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("Back 🔙", callback_data="reset_identity_menu")])

    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        else:
            await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception:
        await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)


@pyro.on_callback_query(filters.regex(r"^reset_identity_all_confirm$"))
async def reset_identity_all_confirm_cb(client, cb):
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.answer("No accounts to reset!", show_alert=True)
        return

    active_targets = [a for a in accounts if a.get("is_active", True)]
    if not active_targets:
        await cb.answer("No active accounts found!", show_alert=True)
        return

    await cb.answer("⏳ Starting Chinese identity transformation...", show_alert=False)

    status_msg = await cb.message.reply(
        f"⏳ <b>Resetting Identities for {len(active_targets)} Account(s)...</b>\n\n"
        "• <i>Assigning random Chinese Pinyin names (e.g. Lín Hàoyǔ)...</i>\n"
        "• <i>Removing profile photos...</i>\n"
        "• <i>Clearing old posts, stories, and bios...</i>\n\n"
        "<i>Please wait a moment...</i>",
        parse_mode=ParseMode.HTML
    )

    # Sample unique random Chinese names for each account
    names_sample = random.sample(CHINESE_NAMES_POOL, min(len(active_targets), len(CHINESE_NAMES_POOL)))

    success_results = []
    fail_results = []
    total_photos_wiped = 0

    for idx, (acc, c_name) in enumerate(zip(active_targets, names_sample), 1):
        acc_id = str(acc["_id"])
        phone = acc.get("phone_number", "Unknown")
        old_name = get_account_display_name(acc)

        try:
            tg_client, _ = await get_or_create_client(acc_id, acc, uid)
            if not tg_client or not await tg_client.is_user_authorized():
                fail_results.append(f"• <b>{old_name}</b> (<code>{phone}</code>): ❌ Session unauthorized")
                continue

            res = await perform_account_identity_reset(tg_client, acc, uid, c_name)
            total_photos_wiped += res["photos_wiped"]
            success_results.append(
                f"• <code>{phone}</code>: <s>{old_name}</s> ➔ <b>{c_name}</b> (Photos: {res['photos_wiped']} 🗑️)"
            )
            await asyncio.sleep(random.uniform(1.5, 3.0))

        except FloodWaitError as fe:
            fail_results.append(f"• <code>{phone}</code>: ⏳ Rate limited ({fe.seconds}s)")
            logger.warning(f"FloodWait resetting identity for {phone}: {fe.seconds}s")
        except Exception as err:
            logger.error(f"Failed to reset identity for {phone}: {err}")
            fail_results.append(f"• <code>{phone}</code>: ❌ {str(err)[:50]}")

    report = (
        "<blockquote><b>╰_╯ CHINESE IDENTITY RESET COMPLETE! 🎭🇨🇳</b></blockquote>\n\n"
        f"• <b>Total Accounts Transformed:</b> <code>{len(success_results)}/{len(active_targets)}</code>\n"
        f"• <b>Profile Pictures Removed:</b> <code>{total_photos_wiped}</code>\n"
        f"• <b>Stories & Posts:</b> Cleared Clean ✅\n"
        f"• <b>Names Applied:</b> Authentic Chinese Pinyin Names (e.g. Lín Hàoyǔ)\n\n"
    )

    if success_results:
        report += "<b>Updated Identities:</b>\n" + "\n".join(success_results[:15])
        if len(success_results) > 15:
            report += f"\n<i>...and {len(success_results) - 15} more accounts updated!</i>"
        report += "\n\n"

    if fail_results:
        report += "<b>Issues:</b>\n" + "\n".join(fail_results[:5]) + "\n\n"

    report += "<i>All accounts now have brand-new identities and are ready for broadcasting! 🚀</i>"

    buttons = [
        [InlineKeyboardButton("My Accounts 📱", callback_data="view_accounts")],
        [InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]
    ]

    try:
        await status_msg.edit_text(report, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception:
        await cb.message.reply(report, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)

    await send_dm_log(uid, f"<b>🎭 Chinese Identity Reset:</b> {len(success_results)} accounts updated with new Chinese names & photos wiped.")


@pyro.on_callback_query(filters.regex(r"^reset_identity_acc_"))
async def reset_identity_single_acc_cb(client, cb):
    uid = cb.from_user.id
    acc_id = cb.data.replace("reset_identity_acc_", "")
    accounts = db.get_user_accounts(uid)
    account = next((a for a in accounts if str(a["_id"]) == str(acc_id)), None)
    if not account:
        await cb.answer("Account not found!", show_alert=True)
        return

    phone = account.get("phone_number", "Unknown")
    old_name = get_account_display_name(account)
    chosen_name = random.choice(CHINESE_NAMES_POOL)

    await cb.answer("⏳ Resetting identity...", show_alert=False)

    try:
        tg_client, _ = await get_or_create_client(str(acc_id), account, uid)
        if not tg_client or not await tg_client.is_user_authorized():
            await cb.answer("❌ Session unauthorized or logged out!", show_alert=True)
            return

        res = await perform_account_identity_reset(tg_client, account, uid, chosen_name)

        caption = (
            f"<blockquote><b>╰_╯ IDENTITY RESET COMPLETE! 🎭🇨🇳</b></blockquote>\n\n"
            f"• <b>Phone:</b> <code>{phone}</code>\n"
            f"• <b>Old Name:</b> <s>{old_name}</s>\n"
            f"• <b>New Chinese Name (Pinyin):</b> <b>{chosen_name}</b> ✅\n"
            f"• <b>Profile Pictures Removed:</b> <code>{res['photos_wiped']}</code> 🗑️\n"
            f"• <b>Posts / Stories / Bio:</b> Cleared Clean ✨\n\n"
            f"<i>Account is ready with its new Chinese identity! 🚀</i>"
        )

        buttons = [
            [InlineKeyboardButton("View Account 👤", callback_data=f"view_acc_{acc_id}")],
            [InlineKeyboardButton("Reset Another 🎭", callback_data="reset_identity_choose")],
            [InlineKeyboardButton("Back 🔙", callback_data="view_accounts")]
        ]

        try:
            if cb.message.photo or cb.message.caption:
                await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
            else:
                await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        except Exception:
            await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)

        await send_dm_log(uid, f"<b>🎭 Identity Reset for {phone}:</b> <s>{old_name}</s> ➔ <b>{chosen_name}</b> (Photos wiped: {res['photos_wiped']})")

    except Exception as e:
        logger.error(f"Single identity reset failed for {phone}: {e}")
        await cb.answer(f"Failed: {str(e)[:50]}", show_alert=True)





@pyro.on_callback_query(filters.regex(r"^join_folder_menu$"))
async def join_folder_menu(client, cb):
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.answer("Please add accounts first!", show_alert=True)
        return

    caption = (
        "<blockquote><b>╰_╯ JOIN GROUP FOLDER / CHATLIST 📁</b></blockquote>\n\n"
        "Automatically make your hosted accounts join all groups from a <b>Telegram Shareable Chat Folder</b> link!\n\n"
        "<b>Supported Formats:</b>\n"
        "• <b>Folder link:</b> <code>https://t.me/addlist/...</code> or <code>tg://addlist?slug=...</code>\n"
        "• <b>Group invite link:</b> <code>https://t.me/+...</code> or <code>https://t.me/joinchat/...</code>\n"
        "• <b>Group usernames:</b> <code>@groupname</code> (one or multiple)\n\n"
        "<blockquote>Select target accounts:</blockquote>"
    )

    perm_folder = getattr(config, 'PERMANENT_GROUP_FOLDER', '')
    buttons = []
    if perm_folder:
        buttons.append([InlineKeyboardButton("⚡ Join Saved Folder (ALL IDs)", callback_data="join_folder_saved_all")])
    buttons.append([InlineKeyboardButton("🌐 Custom Link (ALL Accounts)", callback_data="join_folder_all")])

    row = []
    for acc in accounts:
        acc_name = get_account_display_name(acc)
        acc_id = str(acc["_id"])
        row.append(InlineKeyboardButton(f"📁 {acc_name}", callback_data=f"join_folder_acc_{acc_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("Back 🔙", callback_data="menu_main")])

    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        else:
            await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning(f"Error in join_folder_menu: {e}")
        await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)


@pyro.on_callback_query(filters.regex(r"^join_folder_saved_all$"))
async def join_folder_saved_all_cb(client, cb):
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.answer("No accounts available!", show_alert=True)
        return
    default_links = getattr(config, 'DEFAULT_GROUP_LINKS', [])
    perm_folder = getattr(config, 'PERMANENT_GROUP_FOLDER', '')
    payload = "\n".join(default_links) if default_links else perm_folder
    if not payload:
        await cb.answer("No saved links/folders configured!", show_alert=True)
        return
    await cb.answer("Joining saved folders & groups for all accounts...")
    await perform_join_folder(client, uid, payload, account_ids=None)


@pyro.on_callback_query(filters.regex(r"^join_folder_all$"))
async def join_folder_all_cb(client, cb):
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.answer("No accounts available!", show_alert=True)
        return

    db.set_user_state(uid, "waiting_folder_all")
    perm_folder = getattr(config, 'PERMANENT_GROUP_FOLDER', '')
    caption = (
        f"<blockquote><b>📁 JOIN FOLDER FOR ALL ACCOUNTS</b></blockquote>\n\n"
        f"Please send the <b>Telegram Folder Link</b> below:\n"
        f"<code>https://t.me/addlist/XXXXXX</code>\n\n"
        + (f"<i>Permanent folder:</i> <code>{perm_folder}</code>\n\n" if perm_folder else "")
        + f"<i>All {len(accounts)} hosted accounts will join all groups in that folder automatically.</i>\n\n"
        f"<i>• Send <code>/cancel</code> to abort</i>"
    )
    buttons = []
    if perm_folder:
        buttons.append([InlineKeyboardButton("⚡ Use Saved Folder", callback_data="join_folder_saved_all")])
    buttons.append([InlineKeyboardButton("Cancel ❌", callback_data="join_folder_menu")])

    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        else:
            await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception:
        await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)


@pyro.on_callback_query(filters.regex(r"^join_folder_saved_acc_"))
async def join_folder_saved_single_cb(client, cb):
    uid = cb.from_user.id
    acc_id = cb.data.replace("join_folder_saved_acc_", "")
    accounts = db.get_user_accounts(uid)
    account = next((a for a in accounts if str(a["_id"]) == str(acc_id)), None)
    if not account:
        await cb.answer("Account not found!", show_alert=True)
        return
    default_links = getattr(config, 'DEFAULT_GROUP_LINKS', [])
    perm_folder = getattr(config, 'PERMANENT_GROUP_FOLDER', '')
    payload = "\n".join(default_links) if default_links else perm_folder
    if not payload:
        await cb.answer("No saved links/folders configured!", show_alert=True)
        return
    await cb.answer("Joining saved folders & groups for this account...")
    await perform_join_folder(client, uid, payload, account_ids=[acc_id])


@pyro.on_callback_query(filters.regex(r"^join_folder_acc_"))
async def join_folder_single_cb(client, cb):
    uid = cb.from_user.id
    acc_id = cb.data.replace("join_folder_acc_", "")
    accounts = db.get_user_accounts(uid)
    account = next((a for a in accounts if str(a["_id"]) == str(acc_id)), None)
    if not account:
        await cb.answer("Account not found!", show_alert=True)
        return

    phone = account.get("phone_number", "Unknown")
    db.set_user_state(uid, "waiting_folder_single")
    db.set_user_temp_data(uid, "target_folder_acc", {"acc_id": str(acc_id), "phone": phone})

    perm_folder = getattr(config, 'PERMANENT_GROUP_FOLDER', '')
    caption = (
        f"<blockquote><b>📁 JOIN FOLDER FOR {phone}</b></blockquote>\n\n"
        f"Please send the <b>Telegram Folder Link</b> below:\n"
        f"<code>https://t.me/addlist/XXXXXX</code>\n\n"
        + (f"<i>Permanent folder:</i> <code>{perm_folder}</code>\n\n" if perm_folder else "")
        + f"<i>This account will join all groups in that folder automatically.</i>\n\n"
        f"<i>• Send <code>/cancel</code> to abort</i>"
    )
    buttons = []
    if perm_folder:
        buttons.append([InlineKeyboardButton("⚡ Use Saved Folder", callback_data=f"join_folder_saved_acc_{acc_id}")])
    buttons.append([InlineKeyboardButton("Cancel ❌", callback_data=f"view_acc_{acc_id}")])

    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        else:
            await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception:
        await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)


async def perform_join_folder(client, uid, input_text, account_ids=None):
    input_text = input_text.strip()

    folder_slugs = list(dict.fromkeys(re.findall(r'(?:t\.me/addlist/|tg://addlist\?slug=)([a-zA-Z0-9_-]+)', input_text)))
    invite_hashes = list(dict.fromkeys(re.findall(r'(?:t\.me/(?:\+|joinchat/))([a-zA-Z0-9_-]+)', input_text)))
    raw_usernames = re.findall(r'(?:(?:https?://)?t\.me/|@)([a-zA-Z0-9_]{4,32})', input_text)
    usernames = list(dict.fromkeys([
        u for u in raw_usernames
        if u.lower() not in ('addlist', 'joinchat', 'c', 'share', 'contact')
        and u not in folder_slugs
        and u not in invite_hashes
    ]))

    if not folder_slugs and not invite_hashes and not usernames:
        await client.send_message(
            uid,
            "<blockquote><b>❌ Invalid Link!</b></blockquote>\n\n"
            "Please provide a valid <b>Telegram Folder Link</b>:\n"
            "• <code>https://t.me/addlist/XXXXXX</code>\n\n"
            "<i>Or send regular group invite links (https://t.me/+...) or @usernames.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Try Again 🔄", callback_data="join_folder_menu"),
                              InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
        )
        return

    all_accounts = db.get_user_accounts(uid)
    if not all_accounts:
        await client.send_message(uid, "❌ No accounts found.", reply_markup=kb([[InlineKeyboardButton("Dashboard", callback_data="menu_main")]]))
        return

    if account_ids:
        targets = [a for a in all_accounts if str(a['_id']) in [str(x) for x in account_ids]]
    else:
        targets = [a for a in all_accounts if a.get('is_active', True)]

    if not targets:
        await client.send_message(uid, "❌ No active accounts to process.", reply_markup=kb([[InlineKeyboardButton("Back", callback_data="join_folder_menu")]]))
        return

    status_msg = await client.send_message(
        uid,
        f"⏳ <b>Starting Group/Folder Join across {len(targets)} account(s)...</b>\n\n"
        f"<i>Connecting accounts and joining chats. Please wait...</i>",
        parse_mode=ParseMode.HTML
    )

    success_list = []
    fail_list = []

    for idx, acc in enumerate(targets, 1):
        phone = acc.get('phone_number', 'Unknown')
        name = get_account_display_name(acc)
        acc_id = acc['_id']
        try:
            tg_client, _ = await get_or_create_client(str(acc_id), acc, uid)
            if not tg_client or not await tg_client.is_user_authorized():
                fail_list.append(f"• <b>{name}</b> (<code>{phone}</code>): ❌ Session logged out / removed from bot")
                continue

            account_joined_count = 0
            account_muted_count = 0
            newly_joined_peers = []
            mute_settings = InputPeerNotifySettings(silent=True, mute_until=datetime(2038, 1, 1, tzinfo=timezone.utc))

            # 1. Process Folders
            for slug in folder_slugs:
                target_key = f"slug:{slug}"
                try:
                    invite = await tg_client(chatlists.CheckChatlistInviteRequest(slug=slug))
                    if isinstance(invite, chatlist_types.ChatlistInvite):
                        inp_peers = []
                        chats_list = getattr(invite, 'chats', [])
                        for ch in chats_list:
                            try:
                                inp_peers.append(await tg_client.get_input_entity(ch))
                                db.add_target_group(uid, ch.id, getattr(ch, 'title', str(ch.id)))
                                ch_uname = (getattr(ch, 'username', '') or '').lower()
                                if ch_uname == 'accounthubai':
                                    asyncio.create_task(handle_group_join_verification(tg_client, ch, phone, context="perform_join_folder_chat"))
                            except Exception:
                                pass
                        if not inp_peers:
                            for p in getattr(invite, 'peers', []):
                                try:
                                    inp_peers.append(await tg_client.get_input_entity(p))
                                except Exception:
                                    pass

                        if inp_peers:
                            try:
                                await tg_client(chatlists.JoinChatlistInviteRequest(slug=slug, peers=inp_peers))
                            except Exception as e:
                                logger.warning(f"JoinChatlistInviteRequest error: {e}, falling back to JoinChannel")
                                for p in inp_peers:
                                    try:
                                        await tg_client(functions.channels.JoinChannelRequest(channel=p))
                                    except Exception:
                                        pass

                            for p in inp_peers:
                                try:
                                    await tg_client(UpdateNotifySettingsRequest(peer=InputNotifyPeer(p), settings=mute_settings))
                                    account_muted_count += 1
                                except Exception:
                                    pass
                            newly_joined_peers.extend(inp_peers)
                            account_joined_count += len(inp_peers)
                        db.record_account_joined_target(acc_id, target_key)

                    elif isinstance(invite, chatlist_types.ChatlistInviteAlready):
                        all_chats = getattr(invite, 'chats', [])
                        for ch in all_chats:
                            try:
                                db.add_target_group(uid, ch.id, getattr(ch, 'title', str(ch.id)))
                                p = await tg_client.get_input_entity(ch)
                                await tg_client(UpdateNotifySettingsRequest(peer=InputNotifyPeer(p), settings=mute_settings))
                                account_muted_count += 1
                            except Exception:
                                pass
                        missing = getattr(invite, 'missing_peers', [])
                        if missing:
                            for p in missing:
                                try:
                                    inp = await tg_client.get_input_entity(p)
                                    try:
                                        await tg_client(chatlists.JoinChatlistUpdatesRequest(
                                            chatlist=InputChatlistDialogFilter(filter_id=invite.filter_id),
                                            peers=[inp]
                                        ))
                                        account_joined_count += 1
                                    except Exception:
                                        await tg_client(functions.channels.JoinChannelRequest(channel=inp))
                                        account_joined_count += 1
                                    await tg_client(UpdateNotifySettingsRequest(peer=InputNotifyPeer(inp), settings=mute_settings))
                                    account_muted_count += 1
                                    newly_joined_peers.append(inp)
                                except Exception as ex:
                                    logger.warning(f"Failed to join missing peer {p}: {ex}")
                        db.record_account_joined_target(acc_id, target_key)
                except FloodWaitError as e:
                    logger.warning(f"FloodWait in folder {slug} for {phone}: {e.seconds}s")
                    break
                except Exception as e:
                    logger.warning(f"Folder join error for {phone} ({slug}): {e}")

            # 2. Process Invite Hashes
            dm_h = getattr(config, 'FORWARD_DM_HASH', 'C42q9yVOumJkZjg9')
            dm_gid = getattr(config, 'FORWARD_DM_GROUP_ID', -5576258867)
            dm_ids = {dm_gid, normalize_tg_id(dm_gid), -5576258867, 5576258867, -1005576258867}
            for h in invite_hashes:
                target_key = f"hash:{h}"
                try:
                    res = await tg_client(functions.messages.ImportChatInviteRequest(hash=h))
                    chats = getattr(res, 'chats', [])
                    if not chats and hasattr(res, 'updates'):
                        chats = getattr(res.updates, 'chats', [])
                    for c in chats:
                        c_norm = normalize_tg_id(c.id)
                        # Protect DM group from being added to ad target groups
                        if h != dm_h and c.id not in dm_ids and c_norm not in dm_ids:
                            db.add_target_group(uid, c.id, getattr(c, 'title', str(c.id)))
                        try:
                            inp = await tg_client.get_input_entity(c)
                            await tg_client(UpdateNotifySettingsRequest(peer=InputNotifyPeer(inp), settings=mute_settings))
                            account_muted_count += 1
                            newly_joined_peers.append(inp)
                        except Exception:
                            pass
                    account_joined_count += 1
                    db.record_account_joined_target(acc_id, target_key)
                    await asyncio.sleep(1)
                except UserAlreadyParticipantError:
                    account_joined_count += 1
                    db.record_account_joined_target(acc_id, target_key)
                except FloodWaitError as e:
                    logger.warning(f"FloodWait in hash +{h} for {phone}: {e.seconds}s")
                    break
                except Exception as e:
                    err_str = str(e).lower()
                    if "already" in err_str:
                        account_joined_count += 1
                        db.record_account_joined_target(acc_id, target_key)
                    elif any(k in err_str for k in ("expired", "invalid")):
                        db.record_account_joined_target(acc_id, target_key)
                    logger.info(f"Join hash error for {phone} (+{h}): {e}")

            # 3. Process Usernames (prioritize accounthubai)
            if any(u.lower() == "accounthubai" for u in usernames):
                usernames = ["accounthubai"] + [u for u in usernames if u.lower() != "accounthubai"]

            for u in usernames:
                target_key = f"user:{u.lower()}"
                try:
                    entity = await tg_client.get_entity(u)
                    await tg_client(functions.channels.JoinChannelRequest(channel=entity))
                    db.add_target_group(uid, entity.id, getattr(entity, 'title', u))
                    try:
                        inp = await tg_client.get_input_entity(entity)
                        await tg_client(UpdateNotifySettingsRequest(peer=InputNotifyPeer(inp), settings=mute_settings))
                        account_muted_count += 1
                        newly_joined_peers.append(inp)
                    except Exception:
                        pass
                    account_joined_count += 1
                    db.record_account_joined_target(acc_id, target_key)
                    await handle_group_join_verification(tg_client, entity, phone, context="perform_join_user")
                    await asyncio.sleep(1)
                except UserAlreadyParticipantError:
                    account_joined_count += 1
                    db.record_account_joined_target(acc_id, target_key)
                    if u.lower() == "accounthubai":
                        await handle_group_join_verification(tg_client, u, phone, context="perform_join_already_participant")
                except FloodWaitError as e:
                    logger.warning(f"FloodWait in @{u} for {phone}: {e.seconds}s")
                    break
                except Exception as e:
                    err_str = str(e).lower()
                    if "already" in err_str:
                        account_joined_count += 1
                        db.record_account_joined_target(acc_id, target_key)
                    elif any(k in err_str for k in ("invalid", "not found", "username_not_occupied")):
                        db.record_account_joined_target(acc_id, target_key)
                    logger.info(f"Join username error for {phone} (@{u}): {e}")

            # 4. Ensure 'groups' folder filter on account
            await ensure_groups_folder(tg_client, newly_joined_peers)

            logger.info(f"Finished group join for {phone}: {account_joined_count} joined/synced, {account_muted_count} muted")

            if account_joined_count > 0 or account_muted_count > 0:
                success_list.append(f"• <b>{name}</b> (<code>{phone}</code>): ✅ Synced {account_joined_count} groups, {account_muted_count} muted 🔕 (organized in 'groups' folder 📁)")
            else:
                success_list.append(f"• <b>{name}</b> (<code>{phone}</code>): ℹ️ Processed (all targets up to date)")

        except Exception as e:
            fail_list.append(f"• <b>{name}</b> (<code>{phone}</code>): ❌ {str(e)}")
            logger.error(f"Error during folder join for {phone}: {e}")

        if len(targets) > 1 and idx % 2 == 0:
            try:
                await status_msg.edit_text(
                    f"⏳ <b>Joining Folder/Groups...</b> ({idx}/{len(targets)})\n\n"
                    f"✅ Processed: {len(success_list)} | ❌ Issues: {len(fail_list)}",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
        await asyncio.sleep(1.2)

    report = (
        f"<blockquote><b>╰_╯ FOLDER & GROUP JOIN COMPLETE! ✅</b></blockquote>\n\n"
        f"<b>Summary:</b>\n"
        f"• Total Accounts: {len(targets)}\n"
        f"• ✅ Success: {len(success_list)}\n"
        f"• ❌ Failed: {len(fail_list)}\n\n"
    )
    details = success_list + fail_list
    if len(details) <= 12:
        report += "\n".join(details)
    else:
        report += "\n".join(details[:12]) + f"\n<i>...and {len(details) - 12} more</i>"

    report += "\n\n<i>All groups are automatically muted and organized into your 'groups' chat folder! 🚀</i>"

    await status_msg.edit_text(
        report,
        parse_mode=ParseMode.HTML,
        reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
    )
    await send_dm_log(uid, f"<b>📁 Folder & Group join:</b> {len(success_list)}/{len(targets)} accounts processed.")


@pyro.on_callback_query(filters.regex(r"^backup_accounts$"))
async def backup_accounts_cb(client, cb):
    uid = cb.from_user.id
    if not is_owner(uid):
        await cb.answer("Admin only feature!", show_alert=True)
        return

    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.answer("No accounts hosted to backup!", show_alert=True)
        return

    await cb.answer("📦 Preparing encrypted account backup...", show_alert=False)
    backup_data = {
        "backup_created_at": datetime.now(timezone.utc).isoformat(),
        "exported_by_user_id": uid,
        "total_accounts": len(accounts),
        "accounts": []
    }

    for acc in accounts:
        decrypted_session = ""
        try:
            decrypted_session = cipher_suite.decrypt(acc['session_string'].encode()).decode()
        except Exception:
            decrypted_session = "DECRYPT_ERROR"

        backup_data["accounts"].append({
            "account_id": str(acc.get("_id", "")),
            "name": get_account_display_name(acc),
            "phone_number": acc.get("phone_number", ""),
            "first_name": acc.get("first_name", ""),
            "last_name": acc.get("last_name", ""),
            "is_active": acc.get("is_active", True),
            "session_string": decrypted_session,
            "created_at": str(acc.get("created_at", ""))
        })

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"adbot_accounts_backup_{timestamp_str}.json"
    temp_filepath = os.path.join(os.getcwd(), backup_filename)

    try:
        with open(temp_filepath, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)

        await client.send_document(
            chat_id=uid,
            document=temp_filepath,
            caption=(
                f"<b>📦 AD-BOT ACCOUNT SESSIONS BACKUP</b>\n\n"
                f"• <b>Total Accounts:</b> {len(accounts)}\n"
                f"• <b>Export Date:</b> <code>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</code>\n\n"
                f"<i>⚠️ Keep this file strictly confidential! It contains working login sessions for all your hosted accounts.</i>"
            ),
            parse_mode=ParseMode.HTML
        )
        await cb.answer("✅ Backup file sent directly to your DM!", show_alert=True)
        await send_dm_log(uid, f"<b>📦 Account Backup Generated:</b> Exported {len(accounts)} accounts safely.")
    except Exception as e:
        logger.error(f"Backup export error: {e}")
        await cb.answer(f"Backup failed: {str(e)[:50]}", show_alert=True)
    finally:
        if os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except Exception:
                pass


@pyro.on_callback_query(filters.regex(r"^sync_groups_all$"))
async def sync_groups_all_cb(client, cb):
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.answer("No accounts hosted!", show_alert=True)
        return

    active_accounts = [a for a in accounts if a.get("is_active", True)]
    if len(active_accounts) < 2:
        await cb.answer("You need at least 2 active accounts to sync groups between them!", show_alert=True)
        return

    await cb.answer("⏳ Starting cross-account group synchronization...", show_alert=False)

    status_msg = await cb.message.reply(
        "⏳ <b>Cross-Account Group Sync Started...</b>\n\n"
        f"Scanning groups across <b>{len(active_accounts)}</b> hosted accounts.\n"
        "<i>Compiling master group list and syncing missing groups. Please wait...</i>",
        parse_mode=ParseMode.HTML
    )

    # Step 1: Connect accounts and scan their groups
    account_groups = {}
    connected_clients = {}

    for acc in active_accounts:
        acc_id = str(acc["_id"])
        ph = acc.get("phone_number", "Unknown")
        name = get_account_display_name(acc)
        try:
            tg_c, _ = await get_or_create_client(acc_id, acc, uid)
            if tg_c and await tg_c.is_user_authorized():
                connected_clients[acc_id] = (tg_c, ph, name)
                acc_grps = []
                async for dlg in tg_c.iter_dialogs(limit=100):
                    if dlg.is_group:
                        acc_grps.append((dlg.id, dlg.name, getattr(dlg.entity, 'username', None)))
                account_groups[acc_id] = acc_grps
        except Exception as e:
            logger.warning(f"Sync: could not connect {ph}: {e}")

    if not connected_clients:
        await status_msg.edit_text("❌ Failed to connect any accounts for synchronization.")
        return

    # If permanent folder is configured, also join folder on any accounts that missed it
    perm_folder = getattr(config, 'PERMANENT_GROUP_FOLDER', '')
    if perm_folder:
        try:
            folder_slug = perm_folder.strip().split('/')[-1].replace('+', '').replace('addlist?slug=', '')
            for acc_id, (tg_c, ph, name) in connected_clients.items():
                try:
                    invite = await tg_c(chatlists.CheckChatlistInviteRequest(slug=folder_slug))
                    if isinstance(invite, chatlist_types.ChatlistInvite) and hasattr(invite, 'missing_peers') and invite.missing_peers:
                        await tg_c(chatlists.JoinChatlistInviteRequest(slug=folder_slug, peers=invite.missing_peers))
                        logger.info(f"Sync: Account {ph} joined {len(invite.missing_peers)} missing peers from saved folder")
                except Exception as fe:
                    logger.debug(f"Sync folder check for {ph}: {fe}")
                await asyncio.sleep(1.0)
        except Exception as fe_all:
            logger.warning(f"Sync folder joining error: {fe_all}")

    # Build master list of unique groups with usernames or public links
    master_public_groups = {}
    for acc_id, grps in account_groups.items():
        for gid, gname, uname in grps:
            if uname:
                master_public_groups[uname.lower()] = (uname, gname)

    total_joined = 0
    synced_details = []

    # Step 2: For each account, join any missing public groups from the master list (safely capped)
    for acc_id, (tg_c, ph, name) in connected_clients.items():
        joined_this_acc = 0
        current_unames = {g[2].lower() for g in account_groups.get(acc_id, []) if g[2]}
        missing_unames = [u_data for u_lower, u_data in master_public_groups.items() if u_lower not in current_unames]

        for u_orig, gname in missing_unames[:10]:
            try:
                await tg_c(functions.channels.JoinChannelRequest(channel=u_orig))
                joined_this_acc += 1
                total_joined += 1
                if u_orig.lower().lstrip('@') == "accounthubai":
                    await handle_group_join_verification(tg_c, u_orig, ph, context="sync_groups")
                await asyncio.sleep(random.uniform(3.5, 6.0))
            except Exception as join_err:
                logger.debug(f"Sync: {ph} join {u_orig} skipped: {join_err}")

        if joined_this_acc > 0:
            synced_details.append(f"• <b>{name}</b> (<code>{ph}</code>): Joined +{joined_this_acc} missing group(s)")

    summary_text = (
        "<blockquote><b>╰_╯ GROUP SYNC COMPLETE! 🔄</b></blockquote>\n\n"
        f"• <b>Accounts Synced:</b> {len(connected_clients)}\n"
        f"• <b>Unique Groups Tracked:</b> {len(master_public_groups)}\n"
        f"• <b>Missing Groups Joined:</b> {total_joined}\n\n"
    )
    if synced_details:
        summary_text += "<b>Details:</b>\n" + "\n".join(synced_details) + "\n\n"
    else:
        summary_text += "<i>All accounts were already in sync and sharing all available groups!</i>\n\n"

    summary_text += "<i>All synced groups are ready for broadcasting! 🚀</i>"

    await status_msg.edit_text(
        summary_text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb([[InlineKeyboardButton("Back to Accounts 🔙", callback_data="view_accounts")]])
    )
    await send_dm_log(uid, f"<b>🔄 Groups Synced:</b> Joined {total_joined} missing groups across {len(connected_clients)} accounts.")


@pyro.on_callback_query(filters.regex(r"^leave_muted_groups_all$"))
async def leave_muted_groups_all_cb(client, cb):
    """Scan all hosted accounts and automatically leave any broadcast channels or groups where the account is permanently muted or cannot write."""
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.answer("No accounts hosted!", show_alert=True)
        return

    await cb.answer("⏳ Scanning accounts for channels & muted groups...", show_alert=False)

    status_msg = await cb.message.reply(
        "⏳ <b>Auto-Cleanup Started...</b>\n\n"
        f"Scanning <b>{len(accounts)}</b> account(s) for useless broadcast channels and muted groups.\n"
        "<i>Accounts will safely exit any channel or permanently muted group. Please wait...</i>",
        parse_mode=ParseMode.HTML
    )

    dm_gid = getattr(config, 'FORWARD_DM_GROUP_ID', -5576258867)
    skip_group_ids = [
        getattr(config, 'MUSTJOIN_GROUP_ID', None),
        dm_gid,
        normalize_tg_id(dm_gid),
        -abs(dm_gid),
        -5576258867,
        5576258867,
        -1005576258867
    ]
    protected_channel_usernames = set()
    if getattr(config, 'AD_SOURCE_CHANNEL', ''):
        protected_channel_usernames.add(str(config.AD_SOURCE_CHANNEL).lstrip('@').lower())
    if getattr(config, 'MUST_JOIN_CHANNEL', ''):
        protected_channel_usernames.add(str(config.MUST_JOIN_CHANNEL).lstrip('@').lower())

    total_left = 0
    account_results = []

    for acc in accounts:
        phone = acc.get("phone_number", "Unknown")
        name = get_account_display_name(acc)
        if not acc.get("is_active", True):
            continue

        try:
            tg_client, _ = await get_or_create_client(str(acc['_id']), acc, uid)
            if not tg_client or not await tg_client.is_user_authorized():
                continue

            acc_left = 0
            async for dialog in tg_client.iter_dialogs(limit=150):
                entity = dialog.entity
                norm_d_id = normalize_tg_id(dialog.id)

                # 1. Safely auto-leave broadcast channels (non-groups)
                if dialog.is_channel and not dialog.is_group:
                    if dialog.id in skip_group_ids or norm_d_id in skip_group_ids:
                        continue
                    ch_uname = getattr(entity, 'username', '') or ''
                    if ch_uname.lower() in protected_channel_usernames or str(dialog.id) == str(config.AD_SOURCE_CHANNEL):
                        continue
                    left = await safe_leave_group(tg_client, dialog.id, user_id=uid)
                    if left:
                        acc_left += 1
                        total_left += 1
                        logger.info(f"Account {name} ({phone}) auto-left broadcast channel {dialog.name} ({dialog.id})")
                        await asyncio.sleep(random.uniform(2.5, 4.0))
                    continue

                # 2. Safely auto-leave permanent or >48h muted groups (temporary <=48h stays)
                if dialog.is_group:
                    if dialog.id in skip_group_ids or norm_d_id in skip_group_ids:
                        continue
                    is_r, should_l, r_reason = check_entity_restricted(entity)
                    if is_r and should_l:
                        left = await safe_leave_group(tg_client, dialog.id, user_id=uid)
                        if left:
                            acc_left += 1
                            total_left += 1
                            logger.info(f"Account {name} ({phone}) auto-left group {dialog.name} ({dialog.id}): {r_reason}")
                            await asyncio.sleep(random.uniform(2.5, 4.0))
                    elif is_r and not should_l:
                        logger.info(f"Account {name} ({phone}) kept in group {dialog.name} ({dialog.id}): {r_reason}")

            account_results.append(f"• <b>{name}</b> (<code>{phone}</code>): Left <b>{acc_left}</b> group(s)/channel(s)")

        except Exception as e:
            logger.error(f"Error checking muted groups for {phone}: {e}")
            account_results.append(f"• <b>{name}</b>: ❌ Error ({str(e)[:30]})")

    report = (
        f"<blockquote><b>🚪 AUTO-CLEANUP COMPLETE! ✅</b></blockquote>\n\n"
        f"<b>Total Channels & Muted Groups Left:</b> <code>{total_left}</code>\n"
        f"<b>Accounts Scanned:</b> <code>{len(accounts)}</code>\n\n"
        f"<b>Account Details:</b>\n"
    )
    report += "\n".join(account_results[:15])
    report += "\n\n<i>All useless channels and permanent muted groups were cleanly exited to protect account health! 🛡️</i>"

    try:
        await status_msg.edit_text(
            report,
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Back to Accounts 🔙", callback_data="view_accounts")]])
        )
    except Exception:
        await cb.message.reply(report, parse_mode=ParseMode.HTML)

    await send_dm_log(uid, f"<b>🚪 Auto-Cleanup:</b> Left {total_left} channels/muted groups across {len(accounts)} accounts.")


@pyro.on_callback_query(filters.regex(r"^set_msg$"))
async def set_msg(client, cb):
    uid = cb.from_user.id
    saved_msgs = db.get_user_ad_messages(uid)
    count = len(saved_msgs)
    ad_mode = db.get_user_ad_mode(uid)

    if count == 0:
        status_text = "<b>Current Ad Status:</b> <i>Not Set ⭕</i>\n<i>No ad messages saved yet. Choose an option below:</i>"
    elif ad_mode == "single" or count == 1:
        m_doc = saved_msgs[0]
        atype = m_doc.get("ad_type", "text")
        type_badge = {
            "text": "📝 Text Only",
            "photo": "🖼️ Photo Only",
            "both": "🖼️📝 Photo + Text",
            "forward": "📨 Forward (Channel Post)"
        }.get(atype, atype)
        prev = m_doc.get("message", "")
        if prev:
            prev = prev.replace("\n", " ")
            if len(prev) > 40:
                prev = prev[:37] + "..."
        elif atype == "forward":
            prev = f"Channel post ({m_doc.get('from_chat_id')}:{m_doc.get('message_id')})"
        else:
            prev = "[Photo File]"
        status_text = (
            f"<b>Current Mode:</b> 📝 <b>Single Ad Mode (No Rotation)</b>\n"
            f"• <b>Type:</b> {type_badge}\n"
            f"• <b>Preview:</b> <code>{prev}</code>"
        )
    else:
        status_text = (
            f"<b>Current Mode:</b> 🔄 <b>Multi-Ad Rotation ({count} Ads Active)</b>\n"
            f"• <i>Ad rotation and group anti-collision are enabled!</i>"
        )

    caption = (
        f"<blockquote>╰_╯ <b>SET AD MESSAGE 📢</b></blockquote>\n\n"
        f"{status_text}\n\n"
        f"<b>Choose an option:</b>\n"
        f"1️⃣ <b>Single Text Ad:</b> Set only 1 text ad (No rotation)\n"
        f"2️⃣ <b>Photo Ad:</b> Set 1 photo ad (with or without caption)\n"
        f"3️⃣ <b>Multi-Ad Rotation:</b> Add multiple ads & start ad rotation\n"
        f"4️⃣ <b>Forward Ad:</b> Channel post forward (premium emojis & stickers)\n\n"
        f"💡 <i>Tip: Use Spin-Tax like <code>{{Best|Top}} {{offers|deals}}</code> for unique variations every post!</i>\n\n"
        f"<i>Select an option below:</i>"
    )

    rot_btn_label = f"3️⃣ 🔄 Multi-Ad Rotation ({count} Ads)" if count > 1 else "3️⃣ 🔄 Multi-Ad Rotation (Start Rotation)"
    buttons = [
        [InlineKeyboardButton("1️⃣ 📝 Single Text Ad (1 Msg Only)", callback_data="adtype_text_single")],
        [InlineKeyboardButton("2️⃣ 🖼️ Photo Ad", callback_data="adtype_photo_single")],
        [InlineKeyboardButton(rot_btn_label, callback_data="manage_ads")],
        [InlineKeyboardButton("4️⃣ 📨 Forward Ad (Channel Post)", callback_data="adtype_forward_single")],
        [InlineKeyboardButton("Back 🔙", callback_data="menu_main")]
    ]

    try:
        await cb.message.edit_media(
            media=InputMediaPhoto(
                media=config.START_IMAGE,
                caption=caption,
                parse_mode=ParseMode.HTML
            ),
            reply_markup=kb(buttons)
        )
    except Exception:
        try:
            await cb.message.edit_caption(
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=kb(buttons)
            )
        except Exception:
            try:
                await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
            except Exception:
                await cb.message.reply(caption, parse_mode=ParseMode.HTML, reply_markup=kb(buttons))


@pyro.on_callback_query(filters.regex(r"^(manage_ads|add_ad_choice)$"))
async def manage_ads(client, cb):
    uid = cb.from_user.id
    db.set_user_ad_mode(uid, "rotation")  # Entering Multi-Ad Rotation activates rotation mode!
    saved_msgs = db.get_user_ad_messages(uid)
    count = len(saved_msgs)

    if count == 0:
        ads_list = "<i>• No rotation ads added yet. Tap '➕ Add Ad to Rotation' below to add your ads!</i>\n"
    else:
        ads_list = f"<b>Configured Rotation Ads ({count}):</b>\n\n"
        for idx, m_doc in enumerate(saved_msgs, 1):
            atype = m_doc.get("ad_type", "text")
            type_badge = {
                "text": "📝 Text",
                "photo": "🖼️ Photo",
                "both": "🖼️📝 Photo+Text",
                "forward": "📨 Forward"
            }.get(atype, atype)

            preview = m_doc.get("message", "")
            if preview:
                preview_clean = preview.replace("\n", " ")
                if len(preview_clean) > 35:
                    preview_clean = preview_clean[:32] + "..."
            elif atype == "forward":
                preview_clean = f"Channel post ({m_doc.get('from_chat_id')}:{m_doc.get('message_id')})"
            elif atype == "photo":
                preview_clean = "[Photo File]"
            else:
                preview_clean = "None"

            ads_list += f"• <b>Ad #{idx}</b> [{type_badge}]: <code>{preview_clean}</code>\n"

    caption = (
        f"<blockquote>╰_╯ <b>3️⃣ MULTI-AD ROTATION MANAGER 🔄</b></blockquote>\n\n"
        f"<b>Status:</b> 🔄 Rotation Mode Activated ✅\n\n"
        f"{ads_list}\n"
        f"<b>Smart Rotation Rules:</b>\n"
        f"• <b>Ad Rotation Active:</b> Since you chose this option, ad rotation will start when broadcasting!\n"
        f"• <b>Cycle Shift:</b> Cycle 1: Acc 1 ➔ Ad #1, Acc 2 ➔ Ad #2. Cycle 2: Acc 1 ➔ Ad #2, Acc 2 ➔ Ad #3, and so on.\n"
        f"• <b>Anti-Collision:</b> If multiple accounts post in the same group, they alternate ads so that no group ever receives duplicate consecutive ads!"
    )

    buttons = [
        [InlineKeyboardButton("➕ Add Ad to Rotation", callback_data="add_rot_choice")]
    ]

    # Individual delete buttons for each ad
    if saved_msgs:
        del_row = []
        for idx, m_doc in enumerate(saved_msgs, 1):
            ad_id = str(m_doc["_id"])
            del_row.append(InlineKeyboardButton(f"🗑️ Del #{idx}", callback_data=f"del_ad_{ad_id}"))
            if len(del_row) == 3:
                buttons.append(del_row)
                del_row = []
        if del_row:
            buttons.append(del_row)
        buttons.append([InlineKeyboardButton("❌ Clear All Rotation Ads", callback_data="clear_all_ads")])

    buttons.append([InlineKeyboardButton("Back 🔙", callback_data="set_msg")])

    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        else:
            await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception:
        try:
            await cb.message.edit_media(
                media=InputMediaPhoto(media=config.START_IMAGE, caption=caption, parse_mode=ParseMode.HTML),
                reply_markup=kb(buttons)
            )
        except Exception:
            await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)


@pyro.on_callback_query(filters.regex(r"^add_rot_choice$"))
async def add_rot_choice_cb(client, cb):
    uid = cb.from_user.id
    saved_msgs = db.get_user_ad_messages(uid)
    next_num = len(saved_msgs) + 1

    caption = (
        f"<blockquote><b>╰_╯ ADD ROTATION AD #{next_num} ➕</b></blockquote>\n\n"
        f"Choose format for Rotation Ad #{next_num}:\n\n"
        f"• <b>Text Ad:</b> Plain or styled text message\n"
        f"• <b>Photo Ad:</b> Photo with optional text caption\n"
        f"• <b>Forward Ad:</b> Channel post forward (premium emojis & stickers)\n\n"
        f"<i>This ad will be added to your rotation queue and cycle automatically!</i>"
    )
    buttons = [
        [InlineKeyboardButton("📝 Text Ad", callback_data="rot_add_text")],
        [InlineKeyboardButton("🖼️ Photo Ad", callback_data="rot_add_photo")],
        [InlineKeyboardButton("📨 Forward Ad (Channel Post)", callback_data="rot_add_forward")],
        [InlineKeyboardButton("Cancel 🔙", callback_data="manage_ads")]
    ]
    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        else:
            await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception:
        await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)


@pyro.on_callback_query(filters.regex(r"^rot_add_(text|photo|forward)$"))
async def rot_add_select(client, cb):
    uid = cb.from_user.id
    ad_type = cb.data.split("_")[2]  # 'text', 'photo', or 'forward'

    db.set_user_temp_data(uid, "new_ad_type", {"ad_type": ad_type, "is_rotation": True})

    if ad_type == "text":
        db.set_user_state(uid, "waiting_broadcast_msg_text")
        prompt = "Now send your <b>text ad message</b> to add to rotation.\n\n• <i>Send <code>/cancel</code> to abort</i>"
    elif ad_type == "photo":
        db.set_user_state(uid, "waiting_broadcast_msg_photo")
        prompt = "Now send a <b>photo</b> (with optional text caption) to add to rotation.\n\n• <i>Send <code>/cancel</code> to abort</i>"
    else:
        db.set_user_state(uid, "waiting_forward_ad")
        prompt = (
            "Now <b>forward a channel post</b> from a channel you own to add to rotation.\n\n"
            "• <i>Telegram channel forwards preserve all premium emojis & stickers.</i>\n"
            "• <i>Send <code>/cancel</code> to abort</i>"
        )

    caption = (
        f"<blockquote><b>╰_╯ ADD ROTATION AD: {ad_type.upper()} 🔄</b></blockquote>\n\n"
        f"{prompt}"
    )
    buttons = [[InlineKeyboardButton("Cancel 🔙", callback_data="manage_ads")]]
    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        else:
            await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception:
        await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)


@pyro.on_callback_query(filters.regex(r"^del_ad_"))
async def del_ad_cb(client, cb):
    uid = cb.from_user.id
    ad_id = cb.data.replace("del_ad_", "")
    ok = db.delete_user_ad_message(uid, ad_id)
    if ok:
        await cb.answer("Ad message deleted!", show_alert=False)
    else:
        await cb.answer("Ad message not found!", show_alert=True)
    await manage_ads(client, cb)


@pyro.on_callback_query(filters.regex(r"^clear_all_ads$"))
async def clear_all_ads_cb(client, cb):
    uid = cb.from_user.id
    cnt = db.clear_user_ad_messages(uid)
    await cb.answer(f"Cleared {cnt} ad message(s)!", show_alert=True)
    await manage_ads(client, cb)


@pyro.on_callback_query(filters.regex(r"^(adtype_forward|adtype_forward_single)$"))
async def adtype_forward(client, cb):
    uid = cb.from_user.id
    db.set_user_temp_data(uid, "new_ad_type", {"ad_type": "forward", "is_rotation": False})
    db.set_user_state(uid, "waiting_forward_ad")
    caption = (
        "<blockquote><b>╰_╯ 4️⃣ SINGLE FORWARD AD 📨</b></blockquote>\n\n"
        "<b>How to set your single forward ad:</b>\n\n"
        "1️⃣ Post your premium emoji ad to a <b>Telegram channel you own</b>\n"
        "2️⃣ Open that channel post\n"
        "3️⃣ Tap <b>Share → Forward</b> → select this bot\n\n"
        "<b>⚠️ Why channel posts?</b>\n"
        "<i>Telegram hides the message ID for private chat forwards. "
        "Channel post forwards expose the ID needed to re-broadcast.</i>\n\n"
        "• <i>This sets 1 single forward ad for broadcasting (No rotation).</i>\n"
        "• <i>Send <code>/cancel</code> to abort</i>"
    )
    buttons = [[InlineKeyboardButton("Cancel 🔙", callback_data="set_msg")]]
    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        else:
            await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception:
        await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)


@pyro.on_callback_query(filters.regex(r"^(adtype_text|adtype_text_single)$"))
async def adtype_text_single(client, cb):
    uid = cb.from_user.id
    db.set_user_temp_data(uid, "new_ad_type", {"ad_type": "text", "is_rotation": False})
    db.set_user_state(uid, "waiting_broadcast_msg_text")

    caption = (
        "<blockquote><b>╰_╯ 1️⃣ SINGLE TEXT AD (1 MSG ONLY) 📝</b></blockquote>\n\n"
        "Now send your <b>text ad message</b> in this chat.\n\n"
        "• <i>This will set 1 single ad message (No rotation).</i>\n"
        "• <i>All broadcast accounts will send this exact message.</i>\n"
        "• <i>Formatting (bold, italic, links, monospace) will be preserved!</i>\n"
        "• <i>Send <code>/cancel</code> to abort</i>"
    )
    buttons = [[InlineKeyboardButton("Cancel 🔙", callback_data="set_msg")]]
    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        else:
            await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception:
        await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)


@pyro.on_callback_query(filters.regex(r"^(adtype_photo|adtype_photo_single|adtype_both)$"))
async def adtype_photo_single(client, cb):
    uid = cb.from_user.id
    db.set_user_temp_data(uid, "new_ad_type", {"ad_type": "photo", "is_rotation": False})
    db.set_user_state(uid, "waiting_broadcast_msg_photo")

    caption = (
        "<blockquote><b>╰_╯ 2️⃣ PHOTO AD 🖼️</b></blockquote>\n\n"
        "Now send a <b>photo</b> in this chat.\n\n"
        "• <i>You can include a text caption with the photo or send photo only!</i>\n"
        "• <i>This sets 1 photo ad message for broadcasting (No rotation).</i>\n"
        "• <i>Send <code>/cancel</code> to abort</i>"
    )
    buttons = [[InlineKeyboardButton("Cancel 🔙", callback_data="set_msg")]]
    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.edit_caption(caption=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
        else:
            await cb.message.edit_text(text=caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)
    except Exception:
        await cb.message.reply(caption, reply_markup=kb(buttons), parse_mode=ParseMode.HTML)

@pyro.on_callback_query(filters.regex("set_delay"))
async def set_delay(client, cb):
    uid = cb.from_user.id
    current_delay = db.get_user_ad_delay(uid)
    
    await cb.message.edit_media(
        media=InputMediaPhoto(
            media=config.START_IMAGE,
            caption=f"""<blockquote><b>╰_╯SET BROADCAST CYCLE INTERVAL</b></blockquote>\n\n"""
                    f"<u>Current Interval:</u> <code>{current_delay} seconds</code>\n\n"
                    f"<b>Recommended Intervals:</b>\n"
                    f"•300s - Aggressive (5 min) 🔴\n"
                    f"•600s - Safe & Balanced (10 min) 🟡\n"
                    f"•1200s - Conservative (20 min) 🟢\n\n"
                    f"<blockquote>To set custom time interval Send a number (in seconds):\n\n(Note: using short time interval for broadcasting can get your Account on high risk.)</blockquote>",
            parse_mode=ParseMode.HTML
        ),
        reply_markup=kb([
            [InlineKeyboardButton("20min 🟢", callback_data="quick_delay_1200"),
             InlineKeyboardButton("5min 🔴", callback_data="quick_delay_300"),
             InlineKeyboardButton("10min 🟡", callback_data="quick_delay_600")],
            [InlineKeyboardButton("Back 🔙", callback_data="menu_main")]
        ])
    )
    db.set_user_state(uid, "waiting_broadcast_delay")

@pyro.on_callback_query(filters.regex("quick_delay_"))
async def quick_delay(client, cb):
    uid = cb.from_user.id
    delay = int(cb.data.split("_")[-1])
    
    try:
        db.set_user_ad_delay(uid, delay)
    except Exception as e:
        logger.error(f"Failed to set ad delay for user {uid}: {e}")
        await cb.answer("Error setting delay. Try again.", show_alert=True)
        return
    
    mode = "Aggressive" if delay >= 300 else "Balanced" if delay >= 600 else "Conservative" if delay >= 1200 else "Custom"
    
    await cb.message.edit_caption(
        caption=f"""<blockquote><b>╰_╯CYCLE INTERVAL UPDATED!</b></blockquote>\n\n"""
                f"<u>New Interval:</u> <code>{delay} seconds</code> \n"
                f"<b>Mode:</b> <i>{mode}</i>\n\n"
                f"<blockquote>Ready for broadcasting!</blockquote>",
        reply_markup=kb([[InlineKeyboardButton("Back", callback_data="menu_main")]]),
        parse_mode=ParseMode.HTML
    )
    await send_dm_log(uid, f"<b> Broadcast interval updated:</b> {delay} seconds ({mode})")
    db.set_user_state(uid, "")

# ─────────────────────────────────────────────────────────────────────────────
# ACCOUNT SELECTION + BROADCAST CONTROLS
# ─────────────────────────────────────────────────────────────────────────────

def _build_account_selection_menu(accounts, selected_ids):
    """Build inline keyboard rows for account selection."""
    buttons = []
    for acc in accounts:
        acc_id = str(acc['_id'])
        acc_name = get_account_display_name(acc)
        is_on = acc_id in selected_ids
        status_icon = "✅" if is_on else "⬜"
        buttons.append([
            InlineKeyboardButton(
                f"{status_icon} {acc_name}",
                callback_data=f"toggle_bcast_acc_{acc_id}"
            )
        ])

    # Control buttons
    has_selection = bool(selected_ids)
    buttons.append([
        InlineKeyboardButton("▶️ Start Selected" if has_selection else "(Select accounts above)",
                             callback_data="start_bcast_selected" if has_selection else "noop_no_selection"),
    ])
    buttons.append([
        InlineKeyboardButton("🚀 Start ALL Accounts", callback_data="start_bcast_all")
    ])
    buttons.append([
        InlineKeyboardButton("Back 🔙", callback_data="menu_main")
    ])
    return buttons


@pyro.on_callback_query(filters.regex("^start_broadcast$"))
async def start_broadcast(client, cb):
    """Show account-selection screen before starting broadcast."""
    uid = cb.from_user.id
    try:
        bcast_state = db.get_broadcast_state(uid)
        is_running_in_db = bcast_state.get("running", False)
        active_task = user_tasks.get(uid)
        is_task_alive = active_task is not None and not active_task.done()

        if is_running_in_db and is_task_alive:
            await cb.answer("╰_╯Broadcast already running!", show_alert=True)
            return
        elif is_running_in_db and not is_task_alive:
            # Ghost/zombie state: marked running in DB but no active async task exists
            logger.info(f"Clearing stale broadcast state for user {uid} (task was not running).")
            db.set_broadcast_state(uid, running=False)
            user_tasks.pop(uid, None)

        accounts = db.get_user_accounts(uid)
        if not accounts:
            await cb.answer("╰_╯Baka! No accounts hosted yet!", show_alert=True)
            return

        if not db.get_logger_status(uid):
            try:
                await logger_client.resolve_peer(uid)
                db.set_logger_status(uid, is_active=True)
            except Exception:
                pass

        if not db.get_logger_status(uid):
            if is_owner(uid):
                db.set_logger_status(uid, is_active=True)
            else:
                try:
                    logger_username = config.LOGGER_BOT_USERNAME.lstrip('@')
                    await cb.message.edit_caption(
                        caption="<b>⚠️ Logger bot not started yet!</b>\n\n"
                                f"Please start @{logger_username} to receive Advertising logs.\n\n"
                                "<i>After starting the logger bot, click 'I Started It' below!</i>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb([
                            [InlineKeyboardButton("Start Logger Bot 📩", url=f"https://t.me/{logger_username}")],
                            [InlineKeyboardButton("✅ I Started It / Continue", callback_data="confirm_logger_start")],
                            [InlineKeyboardButton("Back 🔙", callback_data="menu_main")]
                        ])
                    )
                except Exception as e:
                    logger.error(f"Failed to edit logger bot message for {uid}: {e}")
                    await cb.answer("╰_╯Error: Please try again.", show_alert=True)
                return

        # Load previously-selected accounts (defaults to empty = none pre-selected)
        prev_selected = db.get_selected_broadcast_accounts(uid) or []
        selected_ids = set(prev_selected)

        menu = _build_account_selection_menu(accounts, selected_ids)
        count = len(accounts)
        caption = (
            f"<blockquote><b>╰_╯ SELECT ACCOUNTS TO BROADCAST</b></blockquote>\n\n"
            f"<b>{count}</b> account(s) available.\n\n"
            "Tap an account to toggle it <b>ON ✅</b> or <b>OFF ⬜</b>\n"
            "Then tap <b>Start Selected</b> or <b>Start ALL</b>."
        )
        await cb.message.edit_caption(
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb(menu)
        )
    except Exception as e:
        logger.error(f"Error in start_broadcast for {uid}: {e}")
        await cb.answer("Error. Try again.", show_alert=True)


@pyro.on_callback_query(filters.regex("^confirm_logger_start$"))
async def confirm_logger_start_cb(client, cb):
    """Callback when user confirms they started the logger bot."""
    uid = cb.from_user.id
    try:
        await logger_client.resolve_peer(uid)
    except Exception:
        pass
    db.set_logger_status(uid, is_active=True)
    await cb.answer("Logger status updated! Proceeding... ✅", show_alert=False)
    await start_broadcast(client, cb)


@pyro.on_callback_query(filters.regex("^toggle_bcast_acc_"))
async def toggle_bcast_acc(client, cb):
    """Toggle a single account in/out of the broadcast selection."""
    uid = cb.from_user.id
    acc_id = cb.data.replace("toggle_bcast_acc_", "")

    prev = db.get_selected_broadcast_accounts(uid) or []
    selected = set(prev)

    if acc_id in selected:
        selected.discard(acc_id)
    else:
        selected.add(acc_id)

    db.set_selected_broadcast_accounts(uid, list(selected))

    accounts = db.get_user_accounts(uid)
    menu = _build_account_selection_menu(accounts, selected)
    count = len(accounts)
    caption = (
        f"<blockquote><b>╰_╯ SELECT ACCOUNTS TO BROADCAST</b></blockquote>\n\n"
        f"<b>{count}</b> account(s) available. <b>{len(selected)}</b> selected.\n\n"
        "Tap an account to toggle it <b>ON ✅</b> or <b>OFF ⬜</b>\n"
        "Then tap <b>Start Selected</b> or <b>Start ALL</b>."
    )
    try:
        await cb.message.edit_caption(
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb(menu)
        )
    except Exception:
        pass  # Message unchanged, ignore
    await cb.answer()


@pyro.on_callback_query(filters.regex("^noop_no_selection$"))
async def noop_no_selection(client, cb):
    await cb.answer("Select at least one account first!", show_alert=True)


async def _launch_broadcast(client, uid, account_ids=None):
    """Internal helper: cancel any existing task and launch a new broadcast."""
    current_task = user_tasks.get(uid)
    if current_task:
        try:
            current_task.cancel()
            await current_task
        except Exception:
            pass
        finally:
            user_tasks.pop(uid, None)

    task = asyncio.create_task(run_broadcast(client, uid, account_ids=account_ids))
    user_tasks[uid] = task
    db.set_broadcast_state(uid, running=True)


@pyro.on_callback_query(filters.regex("^start_bcast_selected$"))
async def start_bcast_selected(client, cb):
    """Start broadcast using only the selected accounts."""
    uid = cb.from_user.id
    selected = db.get_selected_broadcast_accounts(uid) or []
    if not selected:
        await cb.answer("Select at least one account first!", show_alert=True)
        return

    accounts = db.get_user_accounts(uid)
    selected_names = [
        get_account_display_name(acc) for acc in accounts if str(acc['_id']) in selected
    ]
    await _launch_broadcast(client, uid, account_ids=selected)

    label = ", ".join(selected_names[:3])
    if len(selected_names) > 3:
        label += f" +{len(selected_names) - 3} more"

    try:
        await cb.message.edit_caption(
            caption=f"<blockquote><b>╰_╯BROADCAST ON! 🚀</b></blockquote>\n\n"
                    f"Broadcasting with <b>{len(selected)}</b> account(s):\n"
                    f"<code>{label}</code>\n\n"
                    f"Logs → @{config.LOGGER_BOT_USERNAME.lstrip('@')}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Back 🔙", callback_data="menu_main")]])
        )
    except Exception as e:
        logger.error(f"Failed to edit caption for start_bcast_selected {uid}: {e}")
    await cb.answer("Broadcast started! ▶️", show_alert=True)
    await send_dm_log(uid, f"<b>🚀 Broadcast started</b> with <b>{len(selected)}</b> selected account(s): {label}")
    logger.info(f"Broadcast started for {uid} with selected accounts: {selected}")


@pyro.on_callback_query(filters.regex("^start_bcast_all$"))
async def start_bcast_all(client, cb):
    """Start broadcast using ALL hosted accounts."""
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.answer("No accounts hosted!", show_alert=True)
        return

    # Clear selection — None means 'all'
    db.set_selected_broadcast_accounts(uid, None)
    await _launch_broadcast(client, uid, account_ids=None)

    try:
        await cb.message.edit_caption(
            caption=f"<blockquote><b>╰_╯BROADCAST ON! 🚀</b></blockquote>\n\n"
                    f"Broadcasting with <b>ALL {len(accounts)}</b> account(s).\n\n"
                    f"Logs → @{config.LOGGER_BOT_USERNAME.lstrip('@')}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Back 🔙", callback_data="menu_main")]])
        )
    except Exception as e:
        logger.error(f"Failed to edit caption for start_bcast_all {uid}: {e}")
    await cb.answer("All accounts broadcasting! 🚀", show_alert=True)
    await send_dm_log(uid, f"<b>🚀 Broadcast started with ALL {len(accounts)} accounts</b>")
    logger.info(f"Broadcast started for {uid} with ALL accounts")


@pyro.on_callback_query(filters.regex("^stop_broadcast$"))
async def stop_broadcast(client, cb):
    uid = cb.from_user.id
    stopped = await stop_broadcast_task(uid)
    if not stopped:
        await cb.answer("╰_╯No broadcast running!", show_alert=True)
        return
    
    await cb.answer("Broadcast stopped! ⏸️", show_alert=True)
    try:
        await cb.message.edit_caption(
            caption="""<blockquote><b>╰_╯BROADCAST STOPPED! ✨</b></blockquote>\n\n"""
                    """Your broadcast has been stopped.\n"""
                    """Check analytics for final stats.""",
            reply_markup=kb([[InlineKeyboardButton("Back", callback_data="menu_main")]]),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to edit BROADCAST STOPPED message for {uid}: {e}")
        await client.send_photo(
            chat_id=uid,
            photo=config.START_IMAGE,
            caption="""<blockquote><b>╰_╯BROADCAST STOPPED!</b></blockquote>\n\n"""
                    """Your broadcast has been stopped.\n"""
                    """Check analytics for final stats.""",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Back", callback_data="menu_main")]])
        )
    await send_dm_log(uid, f"<b>╰_╯ Broadcast stopped!</b>")
    logger.info(f"Broadcast stopped via callback for user {uid}")

@pyro.on_callback_query(filters.regex("^analytics$"))
async def analytics(client, cb):
    uid = cb.from_user.id
    user_stats = db.get_user_analytics(uid)
    accounts = db.get_user_accounts(uid)
    logger_failures = len(db.get_logger_failures(uid))

    active_healthy = 0
    skipped_count = 0
    resting_count = 0
    inactive_count = 0

    now = time.time()
    for acc in accounts:
        acc_id = str(acc["_id"])
        b_st, b_cd = db.get_account_broadcast_status(acc_id)
        if not acc.get("is_active", True):
            inactive_count += 1
        elif b_st == "skipped" and b_cd > now:
            skipped_count += 1
        elif acc_id in account_cooldowns and account_cooldowns[acc_id] > now:
            resting_count += 1
        else:
            active_healthy += 1

    analytics_text = (
        f"<blockquote><b>📊 ANALYTICS & HEALTH</b></blockquote>\n\n"
        f"<u>Broadcast Cycles Completed:</u> <code>{user_stats.get('total_cycles', 0)}</code>\n"
        f"<b>Messages Sent:</b> <i>{user_stats.get('total_sent', 0)}</i>\n"
        f"<u>Failed Sends:</u> <code>{user_stats.get('total_failed', 0)}</code>\n"
        f"<b>Logger Failures:</b> <i>{logger_failures}</i>\n"
        f"<u>Avg Delay:</u> <code>{db.get_user_ad_delay(uid)}s</code>\n\n"
        f"<blockquote><b>🏥 Account Health Breakdown:</b>\n"
        f"🟢 <b>Active & Healthy:</b> <code>{active_healthy}</code>\n"
        f"⏭️ <b>Temporarily Skipped:</b> <code>{skipped_count}</code>\n"
        f"💤 <b>Resting / Cooldown:</b> <code>{resting_count}</code>\n"
        f"🔴 <b>Terminated / Logged Out:</b> <code>{inactive_count}</code></blockquote>\n\n"
        f"<blockquote>Success Rate: {generate_progress_bar(user_stats.get('total_sent', 0), user_stats.get('total_sent', 0) + user_stats.get('total_failed', 0))}</blockquote>"
    )

    await cb.message.edit_caption(
        caption=analytics_text,
        reply_markup=kb([
            [InlineKeyboardButton("🩺 Live Health Check", callback_data="account_health_check")],
            [InlineKeyboardButton("Detailed Report", callback_data="detailed_report")],
            [InlineKeyboardButton("Back", callback_data="menu_main")]
        ]),
        parse_mode=ParseMode.HTML
    )


@pyro.on_callback_query(filters.regex("^detailed_report$"))
async def detailed_report(client, cb):
    uid = cb.from_user.id
    user_stats = db.get_user_analytics(uid)
    accounts = db.get_user_accounts(uid)
    logger_failures = db.get_logger_failures(uid)

    active_healthy = 0
    skipped_count = 0
    resting_count = 0
    inactive_count = 0

    now = time.time()
    for acc in accounts:
        acc_id = str(acc["_id"])
        b_st, b_cd = db.get_account_broadcast_status(acc_id)
        if not acc.get("is_active", True):
            inactive_count += 1
        elif b_st == "skipped" and b_cd > now:
            skipped_count += 1
        elif acc_id in account_cooldowns and account_cooldowns[acc_id] > now:
            resting_count += 1
        else:
            active_healthy += 1

    detailed_text = (
        f"<blockquote><b>📊 DETAILED ANALYTICS REPORT:</b></blockquote>\n\n"
        f"<u>Date:</u> <i>{datetime.now().strftime('%d/%m/%y')}</i>\n"
        f"<b>User ID:</b> <code>{uid}</code>\n\n"
        "<b>Broadcast Stats:</b>\n"
        f"- <u>Total Sent:</u> <code>{user_stats.get('total_sent', 0)}</code>\n"
        f"- <i>Total Failed:</i> <b>{user_stats.get('total_failed', 0)}</b>\n"
        f"- <u>Total Broadcasts:</u> <code>{user_stats.get('total_broadcasts', 0)}</code>\n\n"
        "<b>Logger Stats:</b>\n"
        f"- <u>Logger Failures:</u> <code>{len(logger_failures)}</code>\n"
        f"- <i>Last Failure:</i> <b>{logger_failures[-1]['error'] if logger_failures else 'None'}</b>\n\n"
        "<b>Account Health Breakdown:</b>\n"
        f"- <i>Total Accounts:</i> <u>{len(accounts)}</u>\n"
        f"- 🟢 <b>Active & Healthy:</b> <code>{active_healthy}</code>\n"
        f"- ⏭️ <b>Temporarily Skipped:</b> <code>{skipped_count}</code>\n"
        f"- 💤 <b>Resting / Cooldown:</b> <code>{resting_count}</code>\n"
        f"- 🔴 <b>Terminated / Logged Out:</b> <code>{inactive_count}</code>\n\n"
        f"<blockquote><b>Current Delay:</b> <code>{db.get_user_ad_delay(uid)}s</code></blockquote>"
    )

    await cb.message.edit_caption(
        caption=detailed_text,
        reply_markup=kb([
            [InlineKeyboardButton("Back", callback_data="analytics")]
        ]),
        parse_mode=ParseMode.HTML
    )


@pyro.on_callback_query(filters.regex("^account_health_check$"))
async def account_health_check_cb(client, cb):
    """
    Live Account Health Check:
    Concurrently verifies Telethon session validity, device revocations,
    skipped account cooldowns, and resting timers for every hosted account.
    """
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.answer("No accounts hosted!", show_alert=True)
        return

    await cb.answer("🩺 Running live health check...", show_alert=False)

    try:
        await cb.message.edit_caption(
            caption=(
                "<blockquote><b>🩺 LIVE HEALTH CHECK IN PROGRESS...</b></blockquote>\n\n"
                f"Scanning <b>{len(accounts)}</b> accounts...\n"
                "<i>Checking Telegram sessions & authorization.</i>\n\n"
                "⏳ Please wait a moment..."
            ),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.debug(f"Failed to edit caption for health check progress: {e}")

    active_healthy_count = 0
    skipped_count = 0
    resting_count = 0
    terminated_count = 0

    sem = asyncio.Semaphore(5)

    async def check_single_account(acc):
        nonlocal active_healthy_count, skipped_count, resting_count, terminated_count
        acc_id = str(acc["_id"])
        phone = acc.get("phone_number", "Unknown")
        name = get_account_display_name(acc)
        is_active = acc.get("is_active", True)

        now = time.time()
        b_st, b_cd = db.get_account_broadcast_status(acc_id)
        if b_st == "skipped" and b_cd > now:
            rem_m = max(1, int((b_cd - now) / 60))
            skipped_count += 1
            return f"• <b>{name}</b>: ⏭️ Skipped ({rem_m}m cooldown left)"

        if acc_id in account_cooldowns and account_cooldowns[acc_id] > now:
            rem_wait = int(account_cooldowns[acc_id] - now)
            resting_count += 1
            return f"• <b>{name}</b>: 💤 Resting ({rem_wait}s)"

        if not is_active:
            terminated_count += 1
            return f"• <b>{name}</b>: 🔴 Inactive"

        acc_id_str = str(acc_id)
        if acc_id_str in global_account_clients:
            info = global_account_clients[acc_id_str]
            tg = info.get("client")
            if tg and tg.is_connected():
                active_healthy_count += 1
                return f"• <b>{name}</b>: 🟢 Healthy & Ready"

        async with sem:
            try:
                tg_c, _ = await get_or_create_client(acc_id, acc, uid)
                if not tg_c or not await tg_c.is_user_authorized():
                    terminated_count += 1
                    db.deactivate_account(acc_id)
                    return f"• <b>{name}</b>: 🔴 Logged Out"

                active_healthy_count += 1
                return f"• <b>{name}</b>: 🟢 Healthy & Ready"

            except Exception as e:
                err_msg = str(e)
                if any(err in err_msg.lower() for err in ["deactivated", "session_revoked", "auth_key", "unregistered"]):
                    terminated_count += 1
                    db.deactivate_account(acc_id)
                    return f"• <b>{name}</b>: 🔴 Session Revoked"
                else:
                    if acc.get("is_active", True):
                        active_healthy_count += 1
                        return f"• <b>{name}</b>: 🟢 Healthy"
                    else:
                        terminated_count += 1
                        return f"• <b>{name}</b>: 🔴 Inactive"

    scan_tasks = [check_single_account(acc) for acc in accounts]
    account_results = await asyncio.gather(*scan_tasks)

    report_text = (
        f"<blockquote><b>🩺 LIVE ACCOUNT HEALTH DASHBOARD</b></blockquote>\n\n"
        f"<b>Health Summary ({len(accounts)} Accounts):</b>\n"
        f"🟢 <b>Active & Healthy:</b> <code>{active_healthy_count}</code>\n"
        f"⏭️ <b>Temporarily Skipped:</b> <code>{skipped_count}</code>\n"
        f"💤 <b>Resting / Cooldown:</b> <code>{resting_count}</code>\n"
        f"🔴 <b>Terminated / Logged Out:</b> <code>{terminated_count}</code>\n\n"
        f"<b>Account Status Breakdown:</b>\n"
    )

    # Ensure final caption is strictly under 1000 characters to satisfy Telegram media limit
    lines_to_add = []
    current_len = len(report_text)
    for res_line in account_results:
        if current_len + len(res_line) + 40 > 980:
            lines_to_add.append(f"<i>...and {len(account_results) - len(lines_to_add)} more accounts</i>")
            break
        lines_to_add.append(res_line)
        current_len += len(res_line) + 1

    report_text += "\n".join(lines_to_add)

    try:
        await cb.message.edit_caption(
            caption=report_text,
            reply_markup=kb([
                [InlineKeyboardButton("🔄 Re-Scan Health", callback_data="account_health_check")],
                [InlineKeyboardButton("Back to Analytics", callback_data="analytics")]
            ]),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to edit caption for health check report: {e}")


@pyro.on_message(filters.command("status") & filters.private)
async def user_status_command(client, m):
    """Live status command: displays current broadcasting state, account health, and skipped accounts."""
    uid = m.from_user.id
    accounts = db.get_user_accounts(uid)
    bcast_state = db.get_broadcast_state(uid)
    is_running = bcast_state.get("running", False)
    user_stats = db.get_user_analytics(uid)

    active_healthy = 0
    skipped_count = 0
    resting_count = 0
    inactive_count = 0
    now = time.time()
    skipped_details = []

    for acc in accounts:
        acc_id = str(acc["_id"])
        b_st, b_cd = db.get_account_broadcast_status(acc_id)
        name = get_account_display_name(acc)
        if not acc.get("is_active", True):
            inactive_count += 1
        elif b_st == "skipped" and b_cd > now:
            skipped_count += 1
            rem_m = max(1, int((b_cd - now) / 60))
            skipped_details.append(f"• <b>{name}</b>: ⏭️ Skipped ({rem_m}m cooldown left)")
        elif acc_id in account_cooldowns and account_cooldowns[acc_id] > now:
            resting_count += 1
        else:
            active_healthy += 1

    status_badge = "🟢 Running" if is_running else "⏸️ Stopped"
    text = (
        f"<blockquote><b>📡 LIVE ADBOT STATUS</b></blockquote>\n\n"
        f"• <b>Broadcasting:</b> {status_badge}\n"
        f"• <b>Total Accounts:</b> <code>{len(accounts)}</code>\n"
        f"• <b>Total Sent:</b> <code>{user_stats.get('total_sent', 0)}</code>\n"
        f"• <b>Cycle Interval:</b> <code>{db.get_user_ad_delay(uid)}s</code>\n\n"
        f"<blockquote><b>🏥 Account Health Breakdown:</b>\n"
        f"🟢 <b>Active & Healthy:</b> <code>{active_healthy}</code>\n"
        f"⏭️ <b>Temporarily Skipped:</b> <code>{skipped_count}</code>\n"
        f"💤 <b>Resting / Cooldown:</b> <code>{resting_count}</code>\n"
        f"🔴 <b>Terminated / Logged Out:</b> <code>{inactive_count}</code></blockquote>"
    )
    if skipped_details:
        text += "\n\n<b>Skipped Accounts:</b>\n" + "\n".join(skipped_details[:5])

    await m.reply(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb([
            [InlineKeyboardButton("🩺 Live Health Check", callback_data="account_health_check")],
            [InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]
        ])
    )


@pyro.on_message(filters.command("stats") & filters.user(ALLOWED_BD_IDS))
async def admin_stats(client, m):
    try:
        stats = db.get_admin_stats()
        
        stats_text = (
            f"<blockquote><b>╰_╯ ADMIN DASHBOARD </b></blockquote>\n\n"
            f"<u>Report Date:</u> <i>{datetime.now().strftime('%d/%m/%y • %I:%M %p')}</i>\n\n"
            "<b>USER STATISTICS</b>\n"
            f"• <u>Total Users:</u> <code>{stats.get('total_users', 0)}</code>\n"
            f"• <b>Hosted Accounts:</b> <code>{stats.get('total_accounts', 0)}</code>\n"
            f"• <u>Total Forwards:</u> <i>{stats.get('total_forwards', 0)}</i>\n"
            f"• <b>Active Logger Users:</b> <code>{stats.get('active_logger_users', 0)}</code>\n"
        )
        
        await m.reply_photo(
            photo=config.START_IMAGE,
            caption=stats_text,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await m.reply(f"╰_╯Error generating stats: {str(e)}", parse_mode=ParseMode.HTML)

@pyro.on_message(filters.command("stats") & ~filters.user(ALLOWED_BD_IDS))
async def non_admin_stats(client, m):
    await m.reply("╰_╯Baka! This is an admin command you are not allowed to do this.")

@pyro.on_message(filters.command("bd") & filters.user(ALLOWED_BD_IDS))
async def admin_broadcast(client, m):
    uid = m.from_user.id
    if not is_owner(uid):
        await m.reply("╰_╯Baka! This is an Admin only command.", parse_mode=ParseMode.HTML)
        return
    
    if not m.reply_to_message:
        await m.reply("╰_╯Reply to a message to broadcast it.", parse_mode=ParseMode.HTML)
        return
    
    all_users = db.get_all_users(limit=0)  # Fetch all users without limit
    if not all_users:
        await m.reply("╰_╯No users found.", parse_mode=ParseMode.HTML)
        return
    
    total_users = len(all_users)
    status_msg = await m.reply(
        """<blockquote><b>📢 ADMIN BROADCAST</b></blockquote>\n\n"""
        "<u>Status: Initializing...</u>",
        parse_mode=ParseMode.HTML
    )
    
    sent_count = 0
    failed_count = 0
    
    reply_msg = m.reply_to_message
    media = None
    caption = reply_msg.caption or reply_msg.text or ""
    
    if reply_msg.photo:
        media = reply_msg.photo.file_id
    elif reply_msg.document:
        media = reply_msg.document.file_id
    elif reply_msg.video:
        media = reply_msg.video.file_id
    
    for user in all_users:
        user_id = user['user_id']
        try:
            await client.resolve_peer(user_id)
            if media:
                await client.send_photo(
                    chat_id=user_id,
                    photo=media,
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )
            else:
                await client.send_message(
                    chat_id=user_id,
                    text=caption,
                    parse_mode=ParseMode.HTML
                )
            sent_count += 1
        except PeerIdInvalid:
            logger.error(f"Failed to send broadcast to user {user_id}: PeerIdInvalid")
            failed_count += 1
            await send_dm_log(user_id, f"<b>⚠️ Admin broadcast failed:</b> PeerIdInvalid")
        except FloodWait as e:
            logger.warning(f"Flood wait for user {user_id}: Wait {e.seconds} seconds")
            await asyncio.sleep(e.seconds)
            try:
                if media:
                    await client.send_photo(chat_id=user_id, photo=media, caption=caption, parse_mode=ParseMode.HTML)
                else:
                    await client.send_message(chat_id=user_id, text=caption, parse_mode=ParseMode.HTML)
                sent_count += 1
            except Exception:
                failed_count += 1
                await send_dm_log(user_id, f"<b>⚠️ Admin broadcast failed after wait:</b> {str(e)} ")
        except Exception as e:
            logger.error(f"Failed to send broadcast to user {user_id}: {e}")
            failed_count += 1
            await send_dm_log(user_id, f"<b>⚠️ Admin broadcast failed:</b> {str(e)} ")
        if (sent_count + failed_count) % 10 == 0 or (sent_count + failed_count) == total_users:
            try:
                await status_msg.edit_text(
                    f"""<blockquote><b>📢 ADMIN BROADCAST</b></blockquote>\n\n"""
                    f"<u>Status: In Progress...</u> \n"
                    f"<b>Sent:</b> <code>{sent_count}/{total_users}</code>\n"
                    f"<i>Failed:</i> <u>{failed_count}</u>\n"
                    f"<blockquote>Progress: {generate_progress_bar(sent_count + failed_count, total_users)} </blockquote>",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Failed to update broadcast status: {e}")
        await asyncio.sleep(0.5)
    
    await status_msg.edit_text(
        f"""<blockquote><b>✅ ADMIN BROADCAST COMPLETED </b></blockquote>\n\n"""
        f"<u>Sent:</u> <code>{sent_count}/{total_users}</code>\n"
        f"<b>Failed:</b> <i>{failed_count}</i> ⚠️\n"
        f"<blockquote>Success Rate: {generate_progress_bar(sent_count, total_users)} 💹</blockquote>",
        parse_mode=ParseMode.HTML
    )
    await send_dm_log(uid, f"<b>🏁 Admin broadcast completed:</b> Sent {sent_count}/{total_users}, Failed {failed_count} ✨")

@pyro.on_message(filters.command("bd") & ~filters.user(ALLOWED_BD_IDS))
async def non_admin_bd(client, m):
    await m.reply("╰_╯Baka! command is for admins only, you are not allowed to use it.")

@pyro.on_message(filters.command("stop"))
async def stop_command(client, m):
    uid = m.from_user.id
    stopped = await stop_broadcast_task(uid)
    if stopped:
        await m.reply("<blockquote><b>⏹️ Broadcast stopped! </b></blockquote>", parse_mode=ParseMode.HTML)
        await send_dm_log(uid, "<b>⏹️ Broadcast stopped! </b>")
    else:
        await m.reply("╰_╯No broadcast running!", parse_mode=ParseMode.HTML)

@pyro.on_message(filters.command("me"))
async def user_info(client, m):
    uid = m.from_user.id
    user = db.get_user(uid)
    
    if not user:
        await m.reply("╰_╯You're not registered. Please /start first.", parse_mode=ParseMode.HTML)
        return
    
    accounts_count = db.get_user_accounts_count(uid)
    
    status_text = (
        f"<blockquote><b>╰_╯ Ads Bot</b></blockquote>\n\n"
        f"<u>User ID:</u> <code>{uid}</code>\n"
        f"<b>Username:</b> <i>@{user.get('username', 'N/A')}</i>\n"
        "<blockquote><b>Status: FREE USER </b></blockquote>\n"
        f"Hosted Accounts: <u>{accounts_count}/5 \n"
        f"<b>Logger Active:</b>{'Yes ✅' if db.get_logger_status(uid) else 'No ❌'}\n"
        "<b>Features:</b>\n"
        "•Up to 5 account hosting\n"
        "•Automated broadcasting (Text & Photos)\n"
        "•Group targeting\n"
        "•Real-time analytics\n"
        "•DM logging via logger bot\n"
    )
    
    status_buttons = [
        [InlineKeyboardButton("Dashboard", callback_data="menu_main")],
        [InlineKeyboardButton("Support 💬", url=config.SUPPORT_GROUP_URL)]
    ]
    
    await m.reply_photo(
        photo=config.START_IMAGE,
        caption=status_text,
        reply_markup=InlineKeyboardMarkup(status_buttons),
        parse_mode=ParseMode.HTML
    )

@pyro.on_message(filters.command(["start"]))
async def start(client, m):
    uid = m.from_user.id
    username = m.from_user.username or "Unknown"
    first_name = m.from_user.first_name or "User"
    
    db.create_user(uid, username, first_name)
    # Ensure all users have unlimited account slots (migrates existing users too)
    db.db.users.update_one({"user_id": uid}, {"$set": {"accounts_limit": "unlimited"}})
    db.update_user_last_interaction(uid)
    
    if config.ENABLE_FORCE_JOIN:
        if not await is_joined_all(client, uid):
            try:
                await m.reply_photo(
                    photo=config.FORCE_JOIN_IMAGE,
                    caption="""<blockquote><b>╰_╯ WELCOME TO YOUR ADS BOT</b></blockquote>\n\n"""
                            """To unlock the full <b>Theodron</b> experience, please join our official channel and group first!\n\n"""
                            """<i>Tip: Click the buttons below to join both. After joining, click 'Try Again' to proceed.</i>\n\n"""
                            """Your <i>Free premium automation journey</i> starts here""",
                    reply_markup=kb([
                        [InlineKeyboardButton("Join Channel", url=config.MUST_JOIN_CHANNEL_URL)],
                        [InlineKeyboardButton("Join Group", url=config.MUSTJOIN_GROUP_URL)],
                        [InlineKeyboardButton("Try again", callback_data="joined_check")]
                    ]),
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"Sent force join message to user {uid}")
            except Exception as e:
                logger.error(f"Failed to send force join message to {uid}: {e}")
                await m.reply("╰_╯Please join my channel and group to proceed. Contact support you are having any issues...")
            return
    
    try:
        await m.reply(
            "<blockquote><b>╰_╯ Welcome! Your Ads Bot is ready.</b></blockquote>",
            reply_markup=kb([
                [InlineKeyboardButton("Dashboard", callback_data="menu_main")]
            ]),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"╰_╯Failed to send start message to {uid}: {e}")
        await m.reply("╰_╯Error starting bot. Please try again or contact support.")


# ─────────────────────────────────────────────────────────────────────────────
# FORWARD-AD HANDLER — runs in group -1 so it has priority over all group 0
# handlers (photo, text, etc.). If state is NOT waiting_forward_ad it returns
# immediately and Pyrogram falls through to group 0 handlers as normal.
# ─────────────────────────────────────────────────────────────────────────────

@pyro.on_message(filters.private & ~filters.command(["start", "bd", "me", "stats", "stop"]), group=-1)
async def handle_forward_ad(client, m):
    uid = m.from_user.id
    state = db.get_user_state(uid)
    if state != "waiting_forward_ad":
        return  # not our state — fall through to group 0 (photo/text handlers)

    from pyrogram import StopPropagation

    fwd_chat_id = None
    fwd_msg_id  = None

    # ── Pyrogram v2: MessageOriginChannel is the only origin with a message_id ──
    fwd_origin = getattr(m, "forward_origin", None)
    if fwd_origin:
        origin_type = type(fwd_origin).__name__
        logger.info(f"Forward origin type for {uid}: {origin_type}")
        if hasattr(fwd_origin, "message_id") and fwd_origin.message_id:
            fwd_msg_id = int(fwd_origin.message_id)
            if hasattr(fwd_origin, "chat") and fwd_origin.chat:
                fwd_chat_id = int(fwd_origin.chat.id)

    # ── Pyrogram v1 legacy fields ───────────────────────────────────────────────
    if not fwd_chat_id and getattr(m, "forward_from_chat", None):
        fwd_chat_id = int(m.forward_from_chat.id)
        fwd_msg_id  = int(m.forward_from_message_id or 0) or None

    # ── Nothing found ───────────────────────────────────────────────────────────
    if not fwd_chat_id or not fwd_msg_id:
        await m.reply(
            "<blockquote><b>❌ Can't read forward origin!</b></blockquote>\n\n"
            "Forwarding from <b>Saved Messages</b> doesn't expose the original message ID "
            "— Telegram hides it for private chats.\n\n"
            "<b>✅ How to fix:</b>\n"
            "1️⃣ Post your premium emoji ad to a <b>Telegram channel you own</b>\n"
            "2️⃣ Open that channel post → tap <b>Share → Forward</b> → select this bot\n\n"
            "<i>Only channel post forwards expose the message ID needed for broadcasting.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Try Again 🔄", callback_data="adtype_forward"),
                              InlineKeyboardButton("Cancel", callback_data="set_msg")]])
        )
        raise StopPropagation

    temp_info = db.get_user_temp_data(uid, "new_ad_type") or {}
    is_rotation = temp_info.get("is_rotation", False)

    try:
        if not is_rotation:
            # Single ad mode: clear previous ads, save this 1 forward ad
            db.clear_user_ad_messages(uid)
            db.add_user_ad_message(
                uid, "", datetime.now(),
                photo_path=None, ad_type="forward",
                entities=[],
                from_chat_id=fwd_chat_id,
                message_id=fwd_msg_id
            )
            db.set_user_ad_mode(uid, "single")
            db.set_user_state(uid, "")
            db.set_user_temp_data(uid, "new_ad_type", None)
            logger.info(f"Single forward ad set for {uid}: from_chat={fwd_chat_id} msg_id={fwd_msg_id}")
            await m.reply(
                f"<blockquote><b>╰_╯ SINGLE FORWARD AD SET! ✅</b></blockquote>\n\n"
                f"• <b>Mode:</b> Single Message (1 Ad Only - No Rotation)\n"
                f"• <b>Source Channel:</b> <code>{fwd_chat_id}</code>\n"
                f"• <b>Message ID:</b> <code>{fwd_msg_id}</code>\n\n"
                "Broadcasting will forward this exact message across all accounts with no rotation! 🚀",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([
                    [InlineKeyboardButton("3️⃣ 🔄 Multi-Ad Rotation", callback_data="manage_ads")],
                    [InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]
                ])
            )
            await send_dm_log(uid, f"<b>📨 Single forward ad set:</b> chat <code>{fwd_chat_id}</code> msg <code>{fwd_msg_id}</code>")
        else:
            # Multi-ad rotation mode: append to queue
            db.add_user_ad_message(
                uid, "", datetime.now(),
                photo_path=None, ad_type="forward",
                entities=[],
                from_chat_id=fwd_chat_id,
                message_id=fwd_msg_id
            )
            db.set_user_ad_mode(uid, "rotation")
            db.set_user_state(uid, "")
            db.set_user_temp_data(uid, "new_ad_type", None)
            total_ads = len(db.get_user_ad_messages(uid))
            logger.info(f"Rotation forward ad #{total_ads} added for {uid}: from_chat={fwd_chat_id} msg_id={fwd_msg_id}")
            await m.reply(
                f"<blockquote><b>╰_╯ ROTATION AD #{total_ads} ADDED! ✅</b></blockquote>\n\n"
                f"• <b>Mode:</b> 🔄 Multi-Ad Rotation Active\n"
                f"• <b>Source Channel:</b> <code>{fwd_chat_id}</code>\n"
                f"• <b>Message ID:</b> <code>{fwd_msg_id}</code>\n"
                f"• <b>Total Rotating Ads:</b> <code>{total_ads}</code>\n\n"
                "Broadcasting will forward this message in rotation — premium emojis preserved! 🚀",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([
                    [InlineKeyboardButton("➕ Add Another Ad to Rotation", callback_data="add_rot_choice"),
                     InlineKeyboardButton("📋 View Rotation Queue", callback_data="manage_ads")],
                    [InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]
                ])
            )
            await send_dm_log(uid, f"<b>📨 Rotation forward ad #{total_ads} added:</b> chat <code>{fwd_chat_id}</code> msg <code>{fwd_msg_id}</code>")
    except Exception as e:
        logger.error(f"Failed to save forward-mode ad for {uid}: {e}")
        db.set_user_state(uid, "")
        await m.reply(f"<b>❌ Error:</b> {str(e)}", parse_mode=ParseMode.HTML,
                      reply_markup=kb([[InlineKeyboardButton("Back", callback_data="set_msg")]]))
    raise StopPropagation


@pyro.on_message(filters.photo & filters.private)
async def handle_photo_message(client, m):

    uid = m.from_user.id
    state = db.get_user_state(uid)
    # Accept photo in both old 'waiting_broadcast_msg' and new typed states
    if state in ("waiting_broadcast_msg", "waiting_broadcast_msg_photo"):
        try:
            os.makedirs("downloads", exist_ok=True)
            timestamp = int(time.time())
            photo_file = await client.download_media(m.photo, file_name=f"downloads/{uid}_ad_{timestamp}_{random.randint(100, 999)}.jpg")
            caption_text = m.caption or ""

            temp_info = db.get_user_temp_data(uid, "new_ad_type") or {}
            is_rotation = temp_info.get("is_rotation", False)
            ad_type = temp_info.get("ad_type")
            if not ad_type or ad_type == "photo":
                ad_type = "both" if caption_text else "photo"

            caption_entities = entities_to_dict(m.caption_entities or [])

            if not is_rotation:
                # Single photo ad mode: clear previous ads, save this 1 photo ad
                db.clear_user_ad_messages(uid)
                db.add_user_ad_message(
                    uid, caption_text, datetime.now(),
                    photo_path=photo_file, ad_type=ad_type,
                    entities=caption_entities
                )
                db.set_user_ad_mode(uid, "single")
                db.set_user_state(uid, "")
                db.set_user_temp_data(uid, "new_ad_type", None)

                type_label = "Photo + Text 🖼️📝" if (ad_type == "both" and caption_text) else "Photo Only 🖼️"
                caption_preview = f"<code>{caption_text}</code>" if caption_text else "<i>No text caption</i>"
                await m.reply(
                    f"<blockquote><b>╰_╯ SINGLE PHOTO AD SET! ✅</b></blockquote>\n\n"
                    f"• <b>Mode:</b> Single Message (1 Ad Only - No Rotation)\n"
                    f"• <b>Type:</b> {type_label}\n"
                    f"• <b>Caption:</b> {caption_preview}\n\n"
                    f"<i>Broadcasting will send this 1 photo ad across all accounts with no rotation!</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([
                        [InlineKeyboardButton("3️⃣ 🔄 Multi-Ad Rotation", callback_data="manage_ads")],
                        [InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]
                    ])
                )
                await send_dm_log(uid, f"<b>🖼️ Single Photo Ad set:</b> {type_label}")
                logger.info(f"Single ad photo set for user {uid}: path={photo_file}, ad_type={ad_type}")
            else:
                # Multi-ad rotation mode: append to queue
                db.add_user_ad_message(
                    uid, caption_text, datetime.now(),
                    photo_path=photo_file, ad_type=ad_type,
                    entities=caption_entities
                )
                db.set_user_ad_mode(uid, "rotation")
                db.set_user_state(uid, "")
                db.set_user_temp_data(uid, "new_ad_type", None)

                total_ads = len(db.get_user_ad_messages(uid))
                type_label = "Photo + Text 🖼️📝" if (ad_type == "both" and caption_text) else "Photo Only 🖼️"
                caption_preview = f"<code>{caption_text}</code>" if caption_text else "<i>No text caption</i>"
                await m.reply(
                    f"<blockquote><b>╰_╯ ROTATION AD #{total_ads} ADDED! ✅</b></blockquote>\n\n"
                    f"• <b>Mode:</b> 🔄 Multi-Ad Rotation Active\n"
                    f"• <b>Type:</b> {type_label}\n"
                    f"• <b>Total Rotating Ads:</b> <code>{total_ads}</code>\n"
                    f"• <b>Caption:</b> {caption_preview}\n\n"
                    f"<i>This ad has been added to your rotation queue! Ad rotation will run when broadcasting.</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([
                        [InlineKeyboardButton("➕ Add Another Ad to Rotation", callback_data="add_rot_choice"),
                         InlineKeyboardButton("📋 View Rotation Queue", callback_data="manage_ads")],
                        [InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]
                    ])
                )
                await send_dm_log(uid, f"<b>🖼️ Rotation Ad #{total_ads} added:</b> {type_label}")
                logger.info(f"Ad photo #{total_ads} added for user {uid}: path={photo_file}, ad_type={ad_type}")
        except Exception as e:
            logger.error(f"Failed to add ad photo for user {uid}: {e}")
            db.set_user_state(uid, "")
            await m.reply(
                f"<blockquote><b>❌ Failed to save ad photo!</b></blockquote>\n\n"
                f"<u>Error:</u> <i>{str(e)}</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Back", callback_data="set_msg")]])
            )
            await send_dm_log(uid, f"<b>❌ Failed to set ad photo:</b> {str(e)}")

@pyro.on_message(filters.text & filters.private & ~filters.command(["start", "bd", "me", "stats", "stop", "status"]))
async def handle_text_message(client, m):
    uid = m.from_user.id
    state = db.get_user_state(uid)
    text = m.text.strip()

    # ── Global /cancel handling ──────────────────────────────────────────────
    if text.lower() == "/cancel":
        if state:
            db.set_user_state(uid, "")
            db.set_temp_data(uid, None)
            await m.reply(
                "<blockquote><b>🚫 Action cancelled.</b></blockquote>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
            )
            return

    # ── Bio Update States ────────────────────────────────────────────────────
    if state == "waiting_bio_all":
        db.set_user_state(uid, "")
        await perform_bio_update(client, uid, text, account_ids=None)
        return

    if state == "waiting_bio_single":
        temp_data = db.get_user_temp_data(uid, "target_bio_acc") or {}
        acc_id = temp_data.get("acc_id")
        db.set_user_state(uid, "")
        if not acc_id:
            await m.reply("❌ Error: Target account not found. Please try again.", reply_markup=kb([[InlineKeyboardButton("Back", callback_data="change_bio_menu")]]))
            return
        await perform_bio_update(client, uid, text, account_ids=[acc_id])
        return

    # ── Folder / Group Link Join States ──────────────────────────────────────
    if state == "waiting_folder_all":
        db.set_user_state(uid, "")
        await perform_join_folder(client, uid, text, account_ids=None)
        return

    if state == "waiting_folder_single":
        temp_data = db.get_user_temp_data(uid, "target_folder_acc") or {}
        acc_id = temp_data.get("acc_id")
        db.set_user_state(uid, "")
        if not acc_id:
            await m.reply("❌ Error: Target account not found. Please try again.", reply_markup=kb([[InlineKeyboardButton("Back", callback_data="join_folder_menu")]]))
            return
        await perform_join_folder(client, uid, text, account_ids=[acc_id])
        return

    # ── Legacy Single Group Link State ───────────────────────────────────────
    if state == "waiting_group_link":
        link = text
        try:
            tg_client = TelegramClient(StringSession(), config.API_ID, config.API_HASH)
            await tg_client.connect()
            chat = await tg_client.get_entity(link)
            db.add_target_group(uid, chat.id, chat.title)
            await m.reply(f"<blockquote><b>✅ Group <i>{chat.title}</i> added! ✨</b></blockquote>", parse_mode=ParseMode.HTML)
            await send_dm_log(uid, f"<b>🎯 Group added:</b> <i>{chat.title}</i> ✨")
            db.set_user_state(uid, "")
            await tg_client.disconnect()
        except Exception as e:
            await m.reply(f"<blockquote><b>❌ Failed to add group:</b> <i>{str(e)}</i> 😔</blockquote>", parse_mode=ParseMode.HTML)
            await send_dm_log(uid, f"<b>❌ Failed to add group:</b> {str(e)} 😔")
            logger.error(f"Failed to add group for {uid}: {e}")
        return

    # ── Auto-reply message setting ───────────────────────────────────────────
    if state == "waiting_auto_reply":
        try:
            db.set_auto_reply(uid, message=text)
            db.set_user_state(uid, "")
            await m.reply(
                f"<blockquote><b>╰_╯ AUTO REPLY SET! ✅</b></blockquote>\n\n"
                f"<u>Your Auto Reply Message:</u>\n<code>{text}</code>\n\n"
                f"<i>Anyone who DMs your hosted accounts will automatically receive this message.</i>\n"
                f"<b>Enable it from the Auto Reply menu.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Auto Reply Menu", callback_data="auto_reply_menu"),
                                  InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
            )
            await send_dm_log(uid, f"<b>💬 Auto reply message set:</b> <code>{text[:50]}</code>")
            logger.info(f"Auto reply set for user {uid}: {text[:50]}")
        except Exception as e:
            logger.error(f"Failed to set auto reply for {uid}: {e}")
            db.set_user_state(uid, "")
            await m.reply(f"<b>❌ Failed to set auto reply:</b> {str(e)}",
                          parse_mode=ParseMode.HTML,
                          reply_markup=kb([[InlineKeyboardButton("Back", callback_data="auto_reply_menu")]]))
        return

    # ── Forward-mode ad: user forwards a message, bot saves origin ────────────────
    if state == "waiting_forward_ad":
        try:
            # Check if this message is a forward
            fwd = m.forward_origin if hasattr(m, 'forward_origin') and m.forward_origin else None
            fwd_chat_id  = None
            fwd_msg_id   = None

            if fwd:
                # Pyrogram v2 forward_origin object
                if hasattr(fwd, 'chat') and fwd.chat:
                    fwd_chat_id = fwd.chat.id
                elif hasattr(fwd, 'sender_chat') and fwd.sender_chat:
                    fwd_chat_id = fwd.sender_chat.id
                if hasattr(fwd, 'message_id'):
                    fwd_msg_id = fwd.message_id
            elif m.forward_from_chat:
                # Legacy Pyrogram v1 fields
                fwd_chat_id = m.forward_from_chat.id
                fwd_msg_id  = m.forward_from_message_id

            if not fwd_chat_id or not fwd_msg_id:
                await m.reply(
                    "<blockquote><b>❌ That's not a forwarded message!</b></blockquote>\n\n"
                    "Please <b>forward</b> your ad message here.\n"
                    "<i>Open Saved Messages → long press message → Forward → select this bot.</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([[InlineKeyboardButton("Try Again", callback_data="adtype_forward"),
                                      InlineKeyboardButton("Cancel", callback_data="set_msg")]])
                )
                return

            # Save as 'forward' ad type with origin info
            existing_text = m.text or m.caption or ""
            db.add_user_ad_message(
                uid, existing_text, datetime.now(),
                photo_path=None, ad_type="forward",
                entities=[],
                from_chat_id=int(fwd_chat_id),
                message_id=int(fwd_msg_id)
            )
            db.set_user_state(uid, "")
            total_ads = len(db.get_user_ad_messages(uid))
            logger.info(f"Forward-mode ad #{total_ads} set for {uid}: from_chat={fwd_chat_id} msg_id={fwd_msg_id}")
            await m.reply(
                f"<blockquote><b>╰_╯ FORWARD AD #{total_ads} ADDED! ✅</b></blockquote>\n\n"
                f"• <b>Source Chat:</b> <code>{fwd_chat_id}</code>\n"
                f"• <b>Message ID:</b> <code>{fwd_msg_id}</code>\n"
                f"• <b>Total Rotation Ads:</b> <code>{total_ads}</code>\n\n"
                "<b>Broadcasting will forward this exact message</b> —\n"
                "premium emojis, stickers, and all formatting preserved! 🚀",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([
                    [InlineKeyboardButton("➕ Add Another Ad", callback_data="set_msg"),
                     InlineKeyboardButton("📋 View All Ads", callback_data="manage_ads")],
                    [InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]
                ])
            )
            await send_dm_log(uid, f"<b>📨 Forward-mode ad #{total_ads} added:</b> chat <code>{fwd_chat_id}</code> msg <code>{fwd_msg_id}</code>")
        except Exception as e:
            logger.error(f"Failed to set forward-mode ad for {uid}: {e}")
            db.set_user_state(uid, "")
            await m.reply(
                f"<b>❌ Error:</b> {str(e)}",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Back", callback_data="set_msg")]])
            )
        return

    # ── Ad text message setting ─────────────────────────────────────────────────
    if state in ("waiting_broadcast_msg", "waiting_broadcast_msg_text"):
        try:
            # Determine ad_type from state
            if state == "waiting_broadcast_msg_text":
                ad_type = "text"
            else:
                ad_type = "text"  # fallback for old state

            # Capture message entities (premium emojis, bold, etc.)
            msg_entities = entities_to_dict(m.entities or [])
            # Save message origin so broadcast can forward it (preserves ALL formatting server-side)
            from_chat_id = m.chat.id
            message_id   = m.id

            temp_info = db.get_user_temp_data(uid, "new_ad_type") or {}
            is_rotation = temp_info.get("is_rotation", False)

            if not is_rotation:
                # 1st Option: ONLY add 1 msg! Clear previous and set 1 single ad
                db.clear_user_ad_messages(uid)
                db.add_user_ad_message(
                    uid, text, datetime.now(),
                    photo_path=None, ad_type=ad_type,
                    entities=msg_entities,
                    from_chat_id=from_chat_id,
                    message_id=message_id
                )
                db.set_user_ad_mode(uid, "single")
                db.set_user_state(uid, "")
                db.set_user_temp_data(uid, "new_ad_type", None)
                total_ads = 1
                entity_count = len(msg_entities)
                logger.info(f"Single ad text set for {uid}: {len(text)} chars, {entity_count} entities, msg_id={message_id}")
                await m.reply(
                    f"<blockquote><b>╰_╯ SINGLE AD MESSAGE SET! ✅</b></blockquote>\n\n"
                    f"• <b>Mode:</b> 📝 Single Message (1 Ad Only - No Rotation)\n"
                    f"• <b>Preview:</b>\n<code>{text[:200]}{'...' if len(text) > 200 else ''}</code>\n\n"
                    f"<i>Broadcasting will send this 1 message across all accounts with no rotation!</i>\n"
                    f"<i>To broadcast multiple rotating messages, select Option 3 (Multi-Ad Rotation).</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([
                        [InlineKeyboardButton("3️⃣ 🔄 Multi-Ad Rotation", callback_data="manage_ads")],
                        [InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]
                    ])
                )
                await send_dm_log(uid, f"<b>📝 Single Ad message set:</b> <code>{text[:50]}{'...' if len(text) > 50 else ''}</code>")
            else:
                # 3rd Option: Add to rotation queue!
                db.add_user_ad_message(
                    uid, text, datetime.now(),
                    photo_path=None, ad_type=ad_type,
                    entities=msg_entities,
                    from_chat_id=from_chat_id,
                    message_id=message_id
                )
                db.set_user_ad_mode(uid, "rotation")
                db.set_user_state(uid, "")
                db.set_user_temp_data(uid, "new_ad_type", None)
                total_ads = len(db.get_user_ad_messages(uid))
                entity_count = len(msg_entities)
                logger.info(f"Rotation ad text #{total_ads} set for {uid}: {len(text)} chars, {entity_count} entities, msg_id={message_id}")
                await m.reply(
                    f"<blockquote><b>╰_╯ ROTATION AD #{total_ads} ADDED! ✅</b></blockquote>\n\n"
                    f"• <b>Mode:</b> 🔄 Multi-Ad Rotation Active\n"
                    f"• <b>Type:</b> 📝 Text Only\n"
                    f"• <b>Total Rotating Ads:</b> <code>{total_ads}</code>\n"
                    f"• <b>Preview:</b>\n<code>{text[:200]}{'...' if len(text) > 200 else ''}</code>\n\n"
                    f"<i>This ad has been added to your rotation queue! Ad rotation will start when broadcasting.</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([
                        [InlineKeyboardButton("➕ Add Another Ad to Rotation", callback_data="add_rot_choice"),
                         InlineKeyboardButton("📋 View Rotation Queue", callback_data="manage_ads")],
                        [InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]
                    ])
                )
                await send_dm_log(uid, f"<b>📝 Rotation Ad #{total_ads} added:</b> <code>{text[:50]}{'...' if len(text) > 50 else ''}</code>")
            logger.info(f"Ad message #{total_ads} set for user {uid}: {text[:50]}...")
        except Exception as e:
            logger.error(f"Failed to add ad message for user {uid}: {e}")
            db.set_user_state(uid, "")
            await m.reply(
                f"<blockquote><b>❌ Failed to save ad message!</b></blockquote>\n\n"
                f"<u>Error:</u> <i>{str(e)}</i>\n"
                f"<b>Contact Support:</b> @{config.ADMIN_USERNAME}",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
            )
            await send_dm_log(uid, f"<b>❌ Failed to set ad message:</b> {str(e)}")
        return

    if state == "waiting_broadcast_delay":
        try:
            delay = int(text)
            if delay < 120:
                await m.reply(
                    f"<blockquote><b>❌ Invalid interval!</b></blockquote>\n\n"
                    f"Minimum interval is 120 seconds.\n"
                    f"Please enter a valid number",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([[InlineKeyboardButton("Back", callback_data="menu_main")]])
                )
                return
            if delay > 86400:
                await m.reply(
                    f"<blockquote><b>❌ Invalid interval!</b></blockquote>\n\n"
                    f"Maximum interval is 86400 seconds (24 hours).\n"
                    f"Please enter a valid number",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([[InlineKeyboardButton("Back", callback_data="menu_main")]])
                )
                return
            db.set_user_ad_delay(uid, delay)
            db.set_user_state(uid, "")
            mode = "Aggressive" if delay >= 300 else "Balanced" if delay >= 600 else "Conservative" if delay >= 1200 else "Custom"
            await m.reply(
                f"<blockquote><b>╰_╯CYCLE INTERVAL UPDATED! ✅</b></blockquote>\n\n"
                f"<u>New Interval:</u> <code>{delay} seconds</code>\n"
                f"<b>Mode:</b> <i>{mode}</i>\n\n"
                f"<blockquote>Ready for broadcasting!</blockquote>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
            )
            await send_dm_log(uid, f"<b>⏱️ Broadcast interval updated:</b> {delay} seconds ({mode})")
            logger.info(f"Broadcast delay set for user {uid}: {delay}s")
        except ValueError:
            await m.reply(
                f"<blockquote><b>❌ Invalid input!</b></blockquote>\n\n"
                f"<u>Please enter a number (in seconds).</u>\n"
                f"<i>Example: <code>300</code> for 5 minutes.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Back", callback_data="menu_main")]])
            )
        except Exception as e:
            logger.error(f"Failed to set broadcast delay for user {uid}: {e}")
            db.set_user_state(uid, "")
            await m.reply(
                f"<blockquote><b>❌ Failed to set interval!</b></blockquote>\n\n"
                f"<u>Error:</u> <i>{str(e)}</i>\n"
                f"<b>Contact support:</b> @{config.ADMIN_USERNAME}",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Dashboard", callback_data="menu_main")]])
            )
            await send_dm_log(uid, f"<b>❌ Failed to set broadcast interval:</b> {str(e)}")

    # ── Telethon account login states ───────────────────────────────────────────
    elif state == "telethon_wait_phone":
        await _handle_telethon_phone(uid, text, m)
    elif state == "telethon_wait_password":
        await _handle_telethon_password(uid, text, m)
    elif state == "telethon_wait_string":
        await _handle_telethon_string(uid, text, m)


# ─────────────────────────────────────────────────────────────────────────────
# AUTO-REPLY CALLBACKS
# ─────────────────────────────────────────────────────────────────────────────

@pyro.on_callback_query(filters.regex("^auto_reply_menu$"))
async def auto_reply_menu(client, cb):
    uid = cb.from_user.id
    ar = db.get_auto_reply(uid)
    enabled = ar.get("enabled", False)
    message = ar.get("message", "")

    status_label = "ON ✅" if enabled else "OFF ❌"
    msg_preview = f"<code>{message[:100]}</code>" if message else "<i>Not set yet</i>"

    caption = (
        f"<blockquote><b>╰_╯ AUTO REPLY</b></blockquote>\n\n"
        f"<b>Status:</b> {status_label}\n"
        f"<b>Reply Message:</b>\n{msg_preview}\n\n"
        f"<i>When enabled, your hosted accounts will automatically reply\n"
        f"to anyone who DMs them with the message above.</i>"
    )

    toggle_label = "🔴 Disable" if enabled else "🟢 Enable"
    await cb.message.edit_caption(
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=kb([
            [InlineKeyboardButton("✏️ Set Reply Message", callback_data="set_auto_reply_msg")],
            [InlineKeyboardButton(toggle_label, callback_data="toggle_auto_reply")],
            [InlineKeyboardButton("Back 🔙", callback_data="menu_main")]
        ])
    )


@pyro.on_callback_query(filters.regex("^set_auto_reply_msg$"))
async def set_auto_reply_msg_cb(client, cb):
    uid = cb.from_user.id
    db.set_user_state(uid, "waiting_auto_reply")
    await cb.message.edit_caption(
        caption=(
            "<blockquote><b>╰_╯ SET AUTO REPLY MESSAGE</b></blockquote>\n\n"
            "Send the message your hosted accounts should auto-reply with when someone DMs them.\n\n"
            "<i>Example: Hi! I'm currently busy. Visit @YourChannel for updates.</i>"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=kb([[InlineKeyboardButton("Cancel", callback_data="auto_reply_menu")]])
    )


@pyro.on_callback_query(filters.regex("^toggle_auto_reply$"))
async def toggle_auto_reply(client, cb):
    uid = cb.from_user.id
    ar = db.get_auto_reply(uid)
    new_state = not ar.get("enabled", False)

    if new_state and not ar.get("message"):
        await cb.answer("Set a reply message first!", show_alert=True)
        return

    db.set_auto_reply(uid, enabled=new_state)
    status = "enabled ✅" if new_state else "disabled ❌"
    await cb.answer(f"Auto reply {status}!", show_alert=True)
    await send_dm_log(uid, f"<b>💬 Auto reply {status}</b>")
    # Refresh the menu
    await auto_reply_menu(client, cb)



# Maintain alias for compatibility
start_auto_reply_listeners = setup_account_dm_forwarder_and_reply


async def persistent_dm_forwarder_service():
    """
    24/7 Persistent DM Forwarder Background Service:
    Maintains all active hosted accounts connected in the background, listening
    for customer DMs and forwarding them into the DM group or to @negi_support,
    regardless of whether an advertising broadcast is active or stopped.
    Uses the unified get_or_create_client function so NO duplicate clients are ever created.
    """
    logger.info("Starting 24/7 Persistent DM Forwarder Background Service...")
    await asyncio.sleep(2)

    while True:
        try:
            active_accounts = list(db.db.accounts.find({"is_active": True}))
            current_active_ids = {str(a["_id"]) for a in active_accounts}

            # 1. Clean up removed or deactivated accounts
            for aid in list(global_account_clients.keys()):
                if str(aid) not in current_active_ids:
                    await stop_and_remove_account_client(aid)
                    logger.info(f"24/7 DM Forwarder: Disconnected inactive account {aid}")

            # 2. Ensure each active account is connected via unified pool
            for acc in active_accounts:
                acc_id_str = str(acc["_id"])
                uid = acc.get("user_id")
                await get_or_create_client(acc_id_str, acc, uid)
                await asyncio.sleep(0.5)

        except Exception as loop_err:
            logger.error(f"Error in persistent_dm_forwarder_service: {loop_err}")

        await asyncio.sleep(60)


async def _handle_telethon_phone(uid, text, m):
    """Handle telethon_wait_phone state."""
    if not validate_phone_number(text):
        await m.reply(
            f"<blockquote><b>❌ Invalid phone number!</b></blockquote>\n\n"
            f"<u>Please use international format.</u>\n"
            f"<i>Example: <code>+1234567890</code></i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Back", callback_data="menu_main")]])
        )
        return
    status_msg = await m.reply(
        f"<blockquote><b>⏳ Hold! We're trying to OTP...</b></blockquote>\n\n"
        f"<u>Phone:</u> <code>{text}</code> \n"
        f"<i>Please wait a moment.</i> ",
        parse_mode=ParseMode.HTML
    )
    try:
        tg = TelegramClient(StringSession(), config.API_ID, config.API_HASH)
        await tg.connect()
        sent_code = await tg.send_code_request(text)
        session_str = tg.session.save()

        temp_dict = {
            "phone": text,
            "session_str": session_str,
            "phone_code_hash": sent_code.phone_code_hash,
            "otp": ""
        }

        temp_json = json.dumps(temp_dict)
        temp_encrypted = cipher_suite.encrypt(temp_json.encode()).decode()
        db.set_temp_data(uid, temp_encrypted)
        db.set_user_state(uid, "telethon_wait_otp")

        base_caption = (
            f"<blockquote><b>╰_╯ OTP sent to <code>{text}</code>! ✅</b></blockquote>\n\n"
            f"Enter the OTP using the keypad below\n"
            f"<b>Current:</b> <code>_____</code>\n"
            f"<b>Format:</b> <code>12345</code> (no spaces needed)\n"
            f"<i>Valid for:</i> <u>{config.OTP_EXPIRY // 60} minutes</u>"
        )

        await status_msg.edit_caption(
            base_caption,
            parse_mode=ParseMode.HTML,
            reply_markup=get_otp_keyboard()
        )
        await send_dm_log(uid, f"<b>╰_╯ OTP requested for phone number:</b> <code>{text}</code>")
    except PhoneNumberInvalidError:
        await status_msg.edit_caption(
            f"<blockquote><b>❌ Invalid phone number! </b></blockquote>\n\n"
            f"<u>Please check the number and try again.</u>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Back", callback_data="menu_main")]])
        )
    except Exception as e:
        logger.error(f"Failed to send OTP for {uid}: {e}")
        db.set_user_state(uid, "")
        await status_msg.edit_caption(
            f"<blockquote><b>❌ Failed to send OTP!</b></blockquote>\n\n"
            f"<u>Error:</u> <i>{str(e)}</i>\n"
            f"<b>Contact support:</b> @{config.ADMIN_USERNAME}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Back", callback_data="menu_main")]])
        )
        await send_dm_log(uid, f"<b>❌ Failed to send OTP for phone:</b> {str(e)}")
    finally:
        await tg.disconnect()


async def _handle_telethon_password(uid, text, m):
    """Handle telethon_wait_password state."""
    temp_encrypted = db.get_temp_data(uid)
    if not temp_encrypted:
        await m.reply(
            f"<blockquote><b>❌ Session expired!</b></blockquote>\n\n"
            f"<u>Please restart the process.</u>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Back", callback_data="menu_main")]])
        )
        db.set_user_state(uid, "")
        return

    try:
        temp_json = cipher_suite.decrypt(temp_encrypted.encode()).decode()
        temp_dict = json.loads(temp_json)
        phone = temp_dict["phone"]
        session_str = temp_dict["session_str"]
    except (json.JSONDecodeError, Fernet.InvalidToken) as e:
        logger.error(f"Invalid temp data for user {uid} in 2FA: {e}")
        await m.reply(
            f"<blockquote><b>❌ Corrupted session data!</b></blockquote>\n\n"
            f"<b>Please restart the process.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Back", callback_data="menu_main")]])
        )
        db.set_user_state(uid, "")
        db.set_temp_data(uid, None)
        return

    tg = TelegramClient(StringSession(session_str), config.API_ID, config.API_HASH)
    try:
        await tg.connect()
        await tg.sign_in(password=text)
        me = await tg.get_me()
        first_name = getattr(me, 'first_name', '') or ''
        last_name = getattr(me, 'last_name', '') or ''
        saved_session = tg.session.save()
        await tg.disconnect()
        session_encrypted = cipher_suite.encrypt(saved_session.encode()).decode()
        db.add_user_account(uid, phone, session_encrypted, first_name=first_name, last_name=last_name)
        await m.reply(
            f"<blockquote><b>╰_╯Account added!✅ </b></blockquote>\n\n"
            f"<u>Phone:</u> <code>{phone}</code>\n"
            "•Account is ready for broadcasting!",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Dashboard", callback_data="menu_main")]])
        )
        await send_dm_log(uid, f"<b>╰_╯Account added successfully ✅:</b> <code>{phone}</code> ✨")
        db.set_user_state(uid, "")
        db.set_temp_data(uid, None)
    except PasswordHashInvalidError:
        await m.reply(
            f"<blockquote><b>⚠️ Invalid password!</b></blockquote>\n\n"
            f"<u>Please try again.</u>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Back 🔙", callback_data="menu_main")]])
        )
    except Exception as e:
        logger.error(f"Failed to sign in with password for {uid}: {e}")
        db.set_user_state(uid, "")
        db.set_temp_data(uid, None)
        await m.reply(
            f"<blockquote><b>❌ Login failed!</b></blockquote>\n\n"
            f"<u>Error:</u> <i>{str(e)}</i>\n"
            f"<b>Contact support:</b> @{config.ADMIN_USERNAME}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
        )
        await send_dm_log(uid, f"<b>╰_╯Account login failed:❌</b> {str(e)}")
    finally:
        await tg.disconnect()


async def _handle_telethon_string(uid, text, m):
    """Handle telethon_wait_string state."""
    user = db.get_user(uid)
    accounts_count = db.get_user_accounts_count(uid)
    limit = user.get("accounts_limit", 5) if user else 5
    if isinstance(limit, str) and limit.lower() == "unlimited":
        limit = 999
    else:
        try: limit = int(limit)
        except Exception: limit = 5

    if not is_owner(uid) and accounts_count >= limit:
        await m.reply(f"<blockquote><b>❌ Account Limit Reached!</b></blockquote>\n\nYou already have <code>{accounts_count}/{limit}</code> accounts hosted.", parse_mode=ParseMode.HTML)
        return

    status_msg = await m.reply("<blockquote><b>⏳ Verifying String Session...</b></blockquote>", parse_mode=ParseMode.HTML)
    try:
        session_str, me = await load_session_from_string(text)
        if not session_str or not me:
            await status_msg.edit_text(
                "<blockquote><b>❌ Invalid or Expired String Session!</b></blockquote>\n\nPlease check your session string and try again.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Try Again 🔄", callback_data="host_by_string"),
                                 InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
            )
            return

        phone = getattr(me, 'phone', None) or f"+{me.id}"
        first_name = getattr(me, 'first_name', '') or ''
        last_name = getattr(me, 'last_name', '') or ''
        username_display = f"@{me.username}" if getattr(me, 'username', None) else "None"

        session_encrypted = cipher_suite.encrypt(session_str.encode()).decode()
        db.add_user_account(
            uid,
            phone,
            session_encrypted,
            first_name=first_name,
            last_name=last_name
        )
        db.set_user_state(uid, "")

        await status_msg.edit_text(
            f"<blockquote><b>Account Successfully Hosted via String Session! ✅</b></blockquote>\n\n"
            f"• <b>Phone / ID:</b> <code>{phone}</code>\n"
            f"• <b>Name:</b> {first_name} {last_name}\n"
            f"• <b>Username:</b> {username_display}\n\n"
            "╰_╯Your account is ready for auto broadcasting!",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([
                [InlineKeyboardButton("Add Another Account ➕", callback_data="host_account")],
                [InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]
            ])
        )
        await send_dm_log(uid, f"<b>🔑 Account added via String Session:</b> <code>{phone}</code> ({first_name}) ✅")
        logger.info(f"Account added via String Session for user {uid}: {phone}")
    except Exception as e:
        logger.error(f"Error validating string session for user {uid}: {e}")
        await status_msg.edit_text(
            f"<blockquote><b>❌ Error validating session:</b></blockquote>\n\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
        )


@pyro.on_message(filters.document & filters.private & ~filters.command(["start", "bd", "me", "stats", "stop"]))
async def handle_document_message(client, m):
    uid = m.from_user.id
    user = db.get_user(uid)
    if not user:
        await m.reply("Please /start the bot first.", parse_mode=ParseMode.HTML)
        return

    doc = m.document
    file_name = doc.file_name or "account.session"
    state = db.get_user_state(uid)
    is_session_file = file_name.lower().endswith(".session")
    is_zip_file = file_name.lower().endswith(".zip")
    
    if state != "telethon_wait_file" and not (is_session_file or is_zip_file):
        return
        
    accounts_count = db.get_user_accounts_count(uid)
    limit = user.get("accounts_limit", 5)
    if isinstance(limit, str) and limit.lower() == "unlimited":
        limit = 999
    else:
        try: limit = int(limit)
        except Exception: limit = 5
        
    if not is_owner(uid) and accounts_count >= limit:
        await m.reply(f"<blockquote><b>❌ Account Limit Reached!</b></blockquote>\n\nYou already have <code>{accounts_count}/{limit}</code> accounts hosted.", parse_mode=ParseMode.HTML)
        return

    status_msg = await m.reply("<blockquote><b>⏳ Downloading & processing file...</b></blockquote>", parse_mode=ParseMode.HTML)
    
    os.makedirs("sessions/temp", exist_ok=True)
    temp_path = f"sessions/temp/{uid}_{int(time.time())}_{file_name}"
    
    try:
        downloaded = await client.download_media(m.document, file_name=temp_path)
        
        # ── Check if ZIP archive ─────────────────────────────────────────────
        if is_zip_file:
            extract_dir = f"sessions/temp/{uid}_{int(time.time())}_extracted"
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(downloaded, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            # Find all .session files
            session_files = []
            for root, dirs, files in os.walk(extract_dir):
                for f in files:
                    if f.lower().endswith(".session"):
                        session_files.append(os.path.join(root, f))
            
            if not session_files:
                shutil.rmtree(extract_dir, ignore_errors=True)
                if os.path.exists(downloaded):
                    try: os.remove(downloaded)
                    except Exception: pass
                await status_msg.edit_text(
                    "<blockquote><b>❌ No .session files found inside the ZIP!</b></blockquote>\n\n"
                    "Please make sure your ZIP archive contains <code>.session</code> file(s).",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
                )
                return

            added_count = 0
            failed_count = 0
            added_phones = []

            for s_file in session_files:
                session_str, me = await load_session_from_file(s_file)
                if session_str and me:
                    phone = getattr(me, 'phone', None) or f"+{me.id}"
                    first_name = getattr(me, 'first_name', '') or ''
                    last_name = getattr(me, 'last_name', '') or ''
                    session_encrypted = cipher_suite.encrypt(session_str.encode()).decode()
                    db.add_user_account(
                        uid,
                        phone,
                        session_encrypted,
                        first_name=first_name,
                        last_name=last_name
                    )
                    added_count += 1
                    added_phones.append(f"<code>{phone}</code> ({first_name})")
                    await send_dm_log(uid, f"<b>📁 Account added from ZIP:</b> <code>{phone}</code> ({first_name}) ✅")
                else:
                    failed_count += 1

            shutil.rmtree(extract_dir, ignore_errors=True)
            if os.path.exists(downloaded):
                try: os.remove(downloaded)
                except Exception: pass

            db.set_user_state(uid, "")
            
            if added_count > 0:
                phones_list = "\n".join(f"• {p}" for p in added_phones[:10])
                if len(added_phones) > 10:
                    phones_list += f"\n...and {len(added_phones) - 10} more"
                
                await status_msg.edit_text(
                    f"<blockquote><b>ZIP Archive Processed! ✅</b></blockquote>\n\n"
                    f"• <b>Added Successfully:</b> <code>{added_count}</code> account(s)\n"
                    f"• <b>Failed / Invalid:</b> <code>{failed_count}</code>\n\n"
                    f"<b>Accounts Added:</b>\n{phones_list}\n\n"
                    "╰_╯All accounts are ready for auto broadcasting!",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([
                        [InlineKeyboardButton("Add More ➕", callback_data="host_account")],
                        [InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]
                    ])
                )
            else:
                await status_msg.edit_text(
                    "<blockquote><b>❌ Failed to load any valid accounts from ZIP!</b></blockquote>\n\n"
                    "Please check your session files and try again.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
                )
            return

        # ── Single .session file handling ────────────────────────────────────
        session_str, me = await load_session_from_file(downloaded)
        
        # Clean up temporary downloaded file
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except Exception: pass
        if downloaded and os.path.exists(downloaded):
            try: os.remove(downloaded)
            except Exception: pass
            
        if not session_str or not me:
            await status_msg.edit_text(
                "<blockquote><b>❌ Invalid or Expired .session file!</b></blockquote>\n\n"
                "Could not connect to Telegram with this session file. Please ensure the account is active and not banned.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Try Again 🔄", callback_data="host_by_file"),
                                 InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
            )
            return

        phone = getattr(me, 'phone', None) or f"+{me.id}"
        first_name = getattr(me, 'first_name', '') or ''
        last_name = getattr(me, 'last_name', '') or ''
        username_display = f"@{me.username}" if getattr(me, 'username', None) else "None"

        session_encrypted = cipher_suite.encrypt(session_str.encode()).decode()
        
        db.add_user_account(
            uid,
            phone,
            session_encrypted,
            first_name=first_name,
            last_name=last_name
        )
        db.set_user_state(uid, "")
        
        await status_msg.edit_text(
            f"<blockquote><b>Account Successfully Hosted via .session File! ✅</b></blockquote>\n\n"
            f"• <b>Phone / ID:</b> <code>{phone}</code>\n"
            f"• <b>Name:</b> {first_name} {last_name}\n"
            f"• <b>Username:</b> {username_display}\n\n"
            "╰_╯Your account is ready for auto broadcasting!",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([
                [InlineKeyboardButton("Add Another Account ➕", callback_data="host_account")],
                [InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]
            ])
        )
        await send_dm_log(uid, f"<b>📁 Account added via .session file:</b> <code>{phone}</code> ({first_name}) ✅")
        logger.info(f"Account added via session file for user {uid}: {phone}")
    except Exception as e:
        logger.error(f"Error handling document for user {uid}: {e}")
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except Exception: pass
        await status_msg.edit_text(
            f"<blockquote><b>❌ Error handling file:</b></blockquote>\n\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
        )


async def auto_resume_active_broadcasts():
    """On bot startup or server reboot, automatically resume any broadcasts that were running."""
    # Give the bot 6 seconds to establish its connections and start DM forwarder
    await asyncio.sleep(6)
    try:
        running_uids = db.get_all_running_broadcast_users()
        if not running_uids:
            logger.info("Auto-resume: No active broadcasts to resume on startup.")
            return

        logger.info(f"Auto-resuming active broadcast(s) for user(s): {running_uids}")
        for uid in running_uids:
            if uid in user_tasks and not user_tasks[uid].done():
                continue

            accounts = db.get_user_accounts(uid)
            if not accounts:
                logger.warning(f"Auto-resume: User {uid} has running=True but no accounts. Setting running=False.")
                db.set_broadcast_state(uid, running=False)
                continue

            selected = db.get_selected_broadcast_accounts(uid)
            logger.info(f"Auto-resuming broadcast for user {uid} (selected: {selected})...")
            await _launch_broadcast(pyro, uid, account_ids=selected)
            await send_dm_log(
                uid,
                "<b>🔄 Server Reboot Auto-Recovery:</b>\n"
                "<i>Bot has safely resumed your ad broadcast in the background! ✅</i>"
            )
    except Exception as e:
        logger.error(f"Error in auto_resume_active_broadcasts: {e}")


async def periodic_memory_cleanup_daemon():
    """
    Runs periodically to collect garbage, clear unreferenced objects,
    and release freed memory back to the OS to maintain a low RAM footprint.
    """
    import gc
    while True:
        try:
            await asyncio.sleep(300)  # every 5 minutes
            collected = gc.collect()
            logger.debug(f"[GC] Periodic memory cleanup freed {collected} objects.")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"[GC] Error in memory cleanup: {e}")


async def main():
    # Launch Periodic Memory Garbage Collection Daemon
    asyncio.create_task(periodic_memory_cleanup_daemon())

    await pyro.start()
    await logger_client.start()
    try:
        me = await pyro.get_me()
        if me and me.username:
            config.BOT_USERNAME = me.username
            logger.info(f"Main bot connected: @{me.username} (ID: {me.id})")
        logger_me = await logger_client.get_me()
        if logger_me and logger_me.username:
            config.LOGGER_BOT_USERNAME = logger_me.username
            logger.info(f"Logger bot connected: @{logger_me.username} (ID: {logger_me.id})")
    except Exception as e:
        logger.warning(f"Failed to fetch bot usernames dynamically: {e}")

    # Launch 24/7 Persistent Background DM Forwarder Service
    dm_forwarder_task = asyncio.create_task(persistent_dm_forwarder_service())

    # Auto-resume broadcasts that were running before server reboot
    asyncio.create_task(auto_resume_active_broadcasts())

    try:
        await idle()
    except (KeyboardInterrupt, asyncio.CancelledError, Exception) as exit_err:
        logger.info(f"Main loop interrupted: {exit_err}")
    finally:
        for uid, task in list(user_tasks.items()):
            try: task.cancel()
            except Exception: pass
        if not dm_forwarder_task.done():
            try: dm_forwarder_task.cancel()
            except Exception: pass
        for aid, info in list(global_account_clients.items()):
            try:
                await info["client"].disconnect()
            except Exception:
                pass
        try: db.close()
        except Exception: pass
        logger.info("Bot stopped gracefully")

if __name__ == "__main__":
    pyro.run(main())
