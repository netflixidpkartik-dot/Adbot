import asyncio
import random
import string
import re
import json
from datetime import datetime, timedelta
from telethon import TelegramClient, functions, types, events
from telethon.sessions import StringSession
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
    PasswordHashInvalidError
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
        logging.FileHandler('logs/TecxoAds.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

print("Tecxo Bot Free Version Started. 🚀")

# Initialize encryption key with persistence
ENCRYPTION_KEY = getattr(config, 'ENCRYPTION_KEY', None)
KEY_FILE = 'encryption.key'
if not ENCRYPTION_KEY:
    logger.warning("No ENCRYPTION_KEY in config. Loading or generating from file.")
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, 'r') as f:
            ENCRYPTION_KEY = f.read().strip()
    else:
        ENCRYPTION_KEY = Fernet.generate_key().decode()
        with open(KEY_FILE, 'w') as f:
            f.write(ENCRYPTION_KEY)
        logger.info("Generated and saved new encryption key to encryption.key")
else:
    with open(KEY_FILE, 'w') as f:
        f.write(ENCRYPTION_KEY)
    logger.info("Using ENCRYPTION_KEY from config and saved to file.")

cipher_suite = Fernet(ENCRYPTION_KEY.encode())

# Initialize database
try:
    db = EnhancedDatabaseManager()
except Exception as e:
    logger.error(f"Failed to initialize database: {e}. Exiting.")
    print("Bot failed to start due to database error. Check logs/TecxoAds.log for details.")
    exit(1)

# Admin check
ADMIN_IDS = [config.ADMIN_ID]
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
pyro = PyroClient("TecxoAds", api_id=config.API_ID, api_hash=config.API_HASH, bot_token=config.BOT_TOKEN)
logger_client = PyroClient("logger_bot", api_id=config.API_ID, api_hash=config.API_HASH, bot_token=config.LOGGER_BOT_TOKEN)

# In-memory storage for broadcast tasks
user_tasks = {}

# In-memory storage for auto-reply Telethon clients: {owner_uid: {acc_id: tg_client}}
autoreply_clients = {}

# Errors that mean an account is banned/restricted — stop using it immediately
BAN_ERRORS = (
    "UserBannedInChannel",
    "ChatWriteForbidden",
    "UserDeactivated",
    "UserDeactivatedBan",
    "AuthKeyUnregistered",
    "AuthKeyDuplicated",
    "SessionRevoked",
    "AccountBanned",
    "SpamBot",
    "PeerFlood",
)

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


# Invisible Unicode chars to vary message fingerprint so each send is unique
INVISIBLE_CHARS = ["\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"]


def vary_message(msg: str) -> str:
    """Append a random invisible character so each send has a unique fingerprint."""
    if not msg:
        return ""
    return msg + random.choice(INVISIBLE_CHARS)


async def safe_reconnect(tg_client, session_str, phone):
    """Try to reconnect a dropped Telethon client. Returns True if successful."""
    try:
        if tg_client.is_connected():
            await tg_client.disconnect()
        await asyncio.sleep(random.uniform(3, 7))
        await tg_client.connect()
        if await tg_client.is_user_authorized():
            logger.info(f"Reconnected account {phone}")
            return True
        logger.warning(f"Account {phone} not authorized after reconnect")
        return False
    except Exception as e:
        logger.error(f"Reconnect failed for {phone}: {e}")
        return False

async def send_dm_log(user_id, log_message):
    if not db.get_logger_status(user_id):
        logger.info(f"User {user_id} has not started logger bot. Skipping DM log.")
        return
    try:
        await logger_client.resolve_peer(user_id)
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
    state = db.get_broadcast_state(uid)
    running = state.get("running", False)
    if not running:
        logger.info(f"No broadcast running for user {uid}")
        return False

    if uid in user_tasks:
        task = user_tasks[uid]
        try:
            task.cancel()
            await task
            logger.info(f"Cancelled broadcast task for {uid}")
        except asyncio.CancelledError:
            logger.info(f"Broadcast task for {uid} was cancelled successfully")
        except Exception as e:
            logger.error(f"Failed to cancel broadcast task for {uid}: {e}")
        finally:
            del user_tasks[uid]
    
    db.set_broadcast_state(uid, running=False)
    return True

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


async def run_broadcast(client, uid, account_ids=None):
    """
    Run the broadcast loop.
    account_ids: list of str account _id values to use, or None to use all.
    """
    try:
        sent_count = 0
        failed_count = 0
        cycle_count = 0

        db_msgs = db.get_user_ad_messages(uid)
        fallback_text       = db_msgs[0].get("message") if db_msgs else None
        fallback_photo      = db_msgs[0].get("photo_path") if db_msgs else None
        fallback_ad_type    = db_msgs[0].get("ad_type", "text") if db_msgs else "text"
        fallback_entities   = db_msgs[0].get("entities", []) if db_msgs else []
        fallback_from_chat  = db_msgs[0].get("from_chat_id") if db_msgs else None
        fallback_message_id = db_msgs[0].get("message_id") if db_msgs else None

        delay = db.get_user_ad_delay(uid)
        all_accounts = db.get_user_accounts(uid)

        # Filter to selected accounts if specified
        if account_ids:
            accounts = [a for a in all_accounts if str(a['_id']) in account_ids]
        else:
            accounts = all_accounts

        target_groups = db.get_target_groups(uid)
        group_ids = [g['group_id'] for g in target_groups] if target_groups else None

        group_cache = {}
        clients = {}
        banned_accounts = set()
        error_summary = []

        skip_group_ids = [config.MUSTJOIN_GROUP_ID]

        # ── Connect accounts with stagger delay ──────────────────────────────
        for i, acc in enumerate(accounts):
            try:
                session_str = cipher_suite.decrypt(acc['session_string'].encode()).decode()
                tg_client = TelegramClient(
                    StringSession(session_str),
                    config.API_ID,
                    config.API_HASH,
                    connection_retries=3,
                    retry_delay=5,
                )
                await tg_client.connect()

                # Check auth directly — no double-connect via validate_session
                if not await tg_client.is_user_authorized():
                    await tg_client.disconnect()
                    db.deactivate_account(acc['_id'])
                    logger.warning(f"Deactivated unauthorized session for {acc['phone_number']}")
                    await send_dm_log(uid, f"<b>⚠️ Account deactivated (unauthorized):</b> <code>{acc['phone_number']}</code>")
                    continue

                # Auto-join the ad source channel if not already in it
                try:
                    await tg_client(functions.channels.JoinChannelRequest(config.AD_SOURCE_CHANNEL))
                    logger.info(f"Account {acc['phone_number']} joined ad source channel")
                except Exception as e:
                    logger.info(f"Account {acc['phone_number']} already in channel or join skipped: {e}")

                # Fetch ALL groups, then shuffle order
                cached_groups = []
                async for dialog in tg_client.iter_dialogs(limit=None):
                    if dialog.is_group and (not group_ids or dialog.id in group_ids):
                        if dialog.id in skip_group_ids:
                            logger.info(f"Skipping protected group {dialog.name} ({dialog.id})")
                            continue
                        cached_groups.append((dialog.id, dialog.name))

                random.shuffle(cached_groups)
                group_cache[acc['_id']] = cached_groups
                clients[acc['_id']] = (tg_client, session_str, acc['phone_number'])

                logger.info(f"Account {acc['phone_number']} ready — {len(cached_groups)} groups")

                # Stagger: wait between connecting each account
                if i < len(accounts) - 1:
                    await asyncio.sleep(random.uniform(5, 12))

            except Exception as e:
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
        try:
            while db.get_broadcast_state(uid).get("running", False):

                # Re-read DB ad settings at start of each cycle (user may have updated them)
                db_msgs_fresh     = db.get_user_ad_messages(uid)
                active_ad_type    = db_msgs_fresh[0].get("ad_type", fallback_ad_type)    if db_msgs_fresh else fallback_ad_type
                active_entities_data = db_msgs_fresh[0].get("entities", fallback_entities) if db_msgs_fresh else fallback_entities
                active_from_chat  = db_msgs_fresh[0].get("from_chat_id", fallback_from_chat)   if db_msgs_fresh else fallback_from_chat
                active_message_id = db_msgs_fresh[0].get("message_id",  fallback_message_id)   if db_msgs_fresh else fallback_message_id

                # Fetch latest ad message/media from channel at the start of every cycle
                channel_caption, channel_media = None, None
                for acc_id, (tg_c, _, _ph) in clients.items():
                    channel_caption, channel_media = await get_channel_ad_message(tg_c)
                    if channel_caption or channel_media:
                        break

                # Determine active content based on ad_type
                if channel_caption or channel_media:
                    # Channel content: use as-is (channel messages don't carry entity data here)
                    active_caption = channel_caption
                    active_media = channel_media
                    active_entities_data = []  # channel content has no saved entities
                else:
                    # Use user-set DB content filtered by ad_type
                    if active_ad_type == "text":
                        active_caption = fallback_text
                        active_media = None
                    elif active_ad_type == "photo":
                        active_caption = ""  # no text
                        active_media = fallback_photo
                    else:  # 'both'
                        active_caption = fallback_text
                        active_media = fallback_photo

                if active_ad_type != "forward" and not active_caption and not active_media:
                    await client.send_message(uid, "No ad message or photo found. Please set an ad message/photo first.", parse_mode=ParseMode.HTML)
                    db.set_broadcast_state(uid, running=False)
                    break

                # Re-shuffle group order every cycle
                for acc_id in group_cache:
                    random.shuffle(group_cache[acc_id])

                for acc in accounts:
                    if acc['_id'] in banned_accounts:
                        continue

                    entry = clients.get(acc['_id'])
                    if not entry:
                        continue

                    tg_client, session_str, phone = entry
                    cached_groups = group_cache.get(acc['_id'], [])

                    for gid, group_name in cached_groups:
                        if not db.get_broadcast_state(uid).get("running", False):
                            raise asyncio.CancelledError("Broadcast stopped by user")

                        # Auto-reconnect if client dropped
                        if not tg_client.is_connected():
                            logger.warning(f"Client {phone} disconnected — reconnecting")
                            reconnected = await safe_reconnect(tg_client, session_str, phone)
                            if not reconnected:
                                banned_accounts.add(acc['_id'])
                                await send_dm_log(uid, f"<b>⚠️ Account {phone} dropped and could not reconnect. Skipping.</b>")
                                break

                        try:
                            varied_caption = vary_message(active_caption or "")
                            tl_entities = pyrogram_entities_to_telethon(active_entities_data)

                            # ── Send / Forward ──────────────────────────────────────────────────
                            if active_ad_type == "forward" and active_from_chat and active_message_id:
                                # FORWARD MODE: server-side forward — 100% preserves premium emojis
                                await tg_client.forward_messages(
                                    entity=gid,
                                    messages=active_message_id,
                                    from_peer=active_from_chat,
                                    drop_author=True,
                                )
                            elif active_media and active_ad_type in ("photo", "both"):
                                await tg_client.send_file(
                                    gid, file=active_media, caption=varied_caption,
                                    formatting_entities=tl_entities
                                )
                            elif active_caption:
                                await tg_client.send_message(
                                    gid, varied_caption,
                                    formatting_entities=tl_entities
                                )
                            elif active_media:
                                await tg_client.send_file(
                                    gid, file=active_media, caption=varied_caption,
                                    formatting_entities=tl_entities
                                )
                            else:
                                await tg_client.send_message(
                                    gid, varied_caption,
                                    formatting_entities=tl_entities
                                )

                                
                            sent_count += 1
                            db.increment_broadcast_stats(uid, True)
                            logger.info(f"Sent ad to {group_name} ({gid}) via {phone}")
                            await send_dm_log(uid, f"<b>✅ Sent to {group_name}</b> via {phone}")

                        except FloodWaitError as e:
                            wait = min(e.seconds, 600)
                            logger.warning(f"FloodWait {e.seconds}s for {phone} in {gid} — waiting {wait}s")
                            await send_dm_log(uid, f"<b>⚠️ Flood wait {e.seconds}s — {group_name}</b> via {phone}")
                            await asyncio.sleep(wait)
                            failed_count += 1
                            db.increment_broadcast_stats(uid, False)

                        except Exception as e:
                            err_str = str(e)
                            logger.error(f"Failed send to {gid} via {phone}: {err_str}")

                            # Detect ban-level errors — deactivate immediately
                            if any(ban in err_str for ban in BAN_ERRORS):
                                logger.warning(f"Account {phone} banned/restricted: {err_str}")
                                db.deactivate_account(acc['_id'])
                                banned_accounts.add(acc['_id'])
                                await send_dm_log(uid, f"<b>🚫 Account banned/restricted:</b> <code>{phone}</code>\n<i>{err_str}</i>")
                                break

                            failed_count += 1
                            db.increment_broadcast_stats(uid, False)
                            error_summary.append(f"{group_name}: {err_str}")
                            await send_dm_log(uid, f"<b>❌ Failed to send to {group_name}:</b> {err_str}")

                        # Safe per-group delay: 45–90 seconds
                        await asyncio.sleep(random.uniform(45, 90))

                        if not db.get_broadcast_state(uid).get("running", False):
                            raise asyncio.CancelledError("Broadcast stopped by user")

                cycle_count += 1
                db.increment_broadcast_cycle(uid)

                if error_summary:
                    logger.warning(f"Broadcast errors for user {uid}: {len(error_summary)} failures")
                    await send_dm_log(uid, f"<b>⚠️ Cycle {cycle_count} Errors:</b> {len(error_summary)} issues encountered")
                    error_summary = []

                await send_dm_log(
                    uid,
                    f"<b>✅ Cycle {cycle_count} done</b> — Sent: {sent_count} | Failed: {failed_count}\n"
                    f"Next cycle in {delay}s"
                )

                await asyncio.sleep(delay)

                if not db.get_broadcast_state(uid).get("running", False):
                    raise asyncio.CancelledError("Broadcast stopped by user")

        except asyncio.CancelledError:
            logger.info(f"Broadcast task cancelled for {uid}")
            raise

        finally:
            for acc_id, (tg_client, _, phone) in clients.items():
                try:
                    await tg_client.disconnect()
                except Exception as e:
                    logger.error(f"Disconnect error for {phone}: {e}")
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

                session_encrypted = cipher_suite.encrypt(session_str.encode()).decode()
                db.add_user_account(uid, phone, session_encrypted)

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
                temp_dict_2fa = {
                    "phone": phone,
                    "session_str": session_str
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
        
        ad_msg_status = "Not Set ⭕"
        if saved_msgs:
            msg_doc = saved_msgs[0]
            has_photo = bool(msg_doc.get("photo_path"))
            has_text = bool(msg_doc.get("message"))
            if has_photo and has_text:
                ad_msg_status = "Set (Photo + Text) 🖼️📝"
            elif has_photo:
                ad_msg_status = "Set (Photo Only) 🖼️"
            elif has_text:
                ad_msg_status = "Set (Text Only) 📝"

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
            f"•Advertising Status: <b>{broadcast_status}</b>\n"
            f"•Auto Reply: <b>{ar_status}</b>\n\n"
            "<blockquote>╰_╯Choose an action below to continue </blockquote>"
        )

        menu = [
            [InlineKeyboardButton("Add Accounts", callback_data="host_account"),
             InlineKeyboardButton("My Accounts", callback_data="view_accounts")],
            [InlineKeyboardButton("Set Ad Message/Photo 📸", callback_data="set_msg"),
             InlineKeyboardButton("Set Time Interval", callback_data="set_delay")],
            [InlineKeyboardButton("Start Ads▶️", callback_data="start_broadcast"),
             InlineKeyboardButton("Stop Ads⏸️", callback_data="stop_broadcast")],
            [InlineKeyboardButton(f"Auto Reply {ar_status}", callback_data="auto_reply_menu"),
             InlineKeyboardButton("Analytics", callback_data="analytics")],
            [InlineKeyboardButton("Delete Accounts", callback_data="delete_accounts")]
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
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.message.edit_caption(
            caption="""<blockquote><b>╰_╯NO ACCOUNTS HOSTED</b></blockquote>\n\n"""
                    """Add an account to start broadcasting!""",
            reply_markup=kb([[InlineKeyboardButton("Add Account 📱", callback_data="host_account"),
                            InlineKeyboardButton("Back 🔙", callback_data="menu_main")]]),
            parse_mode=ParseMode.HTML
        )
        return
    
    caption = "<blockquote><b>╰_╯HOSTED ACCOUNTS</b></blockquote>\n\n"
    buttons = []
    for i, acc in enumerate(accounts, 1):
        status = "Active ✅" if acc['is_active'] else "Inactive ❌"
        caption += f"{i}. <code>{acc['phone_number']}</code> - <i>{status}</i>\n"
        buttons.append([
            InlineKeyboardButton(f"{acc['phone_number']} ({status})", callback_data=f"view_acc_{acc['_id']}"),
            InlineKeyboardButton("Delete", callback_data=f"delete_acc_{acc['_id']}")
        ])
    
    caption += "\n<blockquote>╰_╯Choose an action:</blockquote>"
    buttons.append([InlineKeyboardButton("Add Account", callback_data="host_account")])
    buttons.append([InlineKeyboardButton("Back", callback_data="menu_main")])
    
    await cb.message.edit_caption(
        caption=caption,
        reply_markup=kb(buttons),
        parse_mode=ParseMode.HTML
    )

@pyro.on_callback_query(filters.regex("delete_accounts"))
async def delete_accounts(client, cb):
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        await cb.message.edit_caption(
            caption="""<blockquote><b>╰_╯NO ACCOUNTS TO DELETE</b></blockquote>\n\n"""
                    """Add an account to start Advertising!""",
            reply_markup=kb([[InlineKeyboardButton("Add Account", callback_data="host_account"),
                            InlineKeyboardButton("Back", callback_data="menu_main")]]),
            parse_mode=ParseMode.HTML
        )
        return
    
    caption = "<blockquote><b>╰_╯ DELETE ACCOUNTS</b></blockquote>\n\n"
    buttons = []
    for i, acc in enumerate(accounts, 1):
        status = "Active ✅" if acc['is_active'] else "Inactive ❌"
        caption += f"{i}. <code>{acc['phone_number']}</code> - <i>{status}</i>\n"
        buttons.append([
            InlineKeyboardButton(f"{acc['phone_number']} ({status})", callback_data=f"view_acc_{acc['_id']}"),
            InlineKeyboardButton("Delete", callback_data=f"delete_acc_{acc['_id']}")
        ])
    
    caption += "\n<blockquote>Choose an account to delete:</blockquote>"
    buttons.append([InlineKeyboardButton("Back", callback_data="menu_main")])
    
    await cb.message.edit_caption(
        caption=caption,
        reply_markup=kb(buttons),
        parse_mode=ParseMode.HTML
    )

@pyro.on_callback_query(filters.regex("delete_acc_"))
async def delete_account(client, cb):
    uid = cb.from_user.id
    acc_id = cb.data.replace("delete_acc_", "")
    try:
        db.delete_user_account(uid, acc_id)
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
    account = next((acc for acc in accounts if acc['_id'] == acc_id), None)
    if not account:
        await cb.answer("╰_╯Custom settings for Accounts is not available in this version, Update will come soon.", show_alert=True)
        return
    
    status = "Active ✅" if account['is_active'] else "Inactive ⭕"
    caption = (
        f"<blockquote><b>╰_╯ACCOUNT DETAILS</b></blockquote>\n\n"
        f"Phone: <code>{account['phone_number']}</code>\n"
        f"<b>Status:</b>{status}\n\n"
        f"<blockquote>Choose an action:</blockquote>"
    )
    
    await cb.message.edit_caption(
        caption=caption,
        reply_markup=kb([
            [InlineKeyboardButton("Delete Account", callback_data=f"delete_acc_{acc_id}")],
            [InlineKeyboardButton("Back", callback_data="delete_accounts")]
        ]),
        parse_mode=ParseMode.HTML
    )

@pyro.on_callback_query(filters.regex("^set_msg$"))
async def set_msg(client, cb):
    uid = cb.from_user.id
    saved_msgs = db.get_user_ad_messages(uid)

    current_status = "Not Set ⭕"
    if saved_msgs:
        msg_doc = saved_msgs[0]
        ad_type = msg_doc.get("ad_type", "text")
        if ad_type == "both":
            current_status = "Photo + Text 🖼️📝"
        elif ad_type == "photo":
            current_status = "Photo Only 🖼️"
        else:
            current_status = "Text Only 📝"

    await cb.message.edit_media(
        media=InputMediaPhoto(
            media=config.START_IMAGE,
            caption=(
                f"<blockquote>╰_╯ <b>SET YOUR AD TYPE</b></blockquote>\n\n"
                f"<b>Current:</b> {current_status}\n\n"
                f"Choose what type of ad you want to broadcast:"
            ),
            parse_mode=ParseMode.HTML
        ),
        reply_markup=kb([
            [InlineKeyboardButton("🖼️ Image Only", callback_data="adtype_photo")],
            [InlineKeyboardButton("📝 Text Only", callback_data="adtype_text")],
            [InlineKeyboardButton("🖼️📝 Image + Text (Both)", callback_data="adtype_both")],
            [InlineKeyboardButton("📨 Forward Mode (Premium Emojis)", callback_data="adtype_forward")],
            [InlineKeyboardButton("Back 🔙", callback_data="menu_main")]
        ])
    )


@pyro.on_callback_query(filters.regex("^adtype_forward$"))
async def adtype_forward(client, cb):
    uid = cb.from_user.id
    db.set_user_state(uid, "waiting_forward_ad")
    await cb.message.edit_caption(
        caption=(
            "<blockquote><b>╰_╯ FORWARD MODE 📨</b></blockquote>\n\n"
            "<b>How to set your ad:</b>\n\n"
            "1️⃣ Post your premium emoji ad to a <b>Telegram channel you own</b>\n"
            "2️⃣ Open that channel post\n"
            "3️⃣ Tap <b>Share → Forward</b> → select this bot\n\n"
            "<b>⚠️ Why not Saved Messages?</b>\n"
            "<i>Telegram hides the message ID for private chat forwards. "
            "Only channel post forwards expose the ID needed to re-broadcast.</i>\n\n"
            "Once set, the bot will forward that exact channel post to all groups — "
            "premium emojis, stickers, everything preserved ✅"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=kb([[InlineKeyboardButton("Cancel 🔙", callback_data="set_msg")]])
    )


@pyro.on_callback_query(filters.regex("^adtype_(photo|text|both)$"))
async def adtype_select(client, cb):
    uid = cb.from_user.id
    ad_type = cb.data.split("_")[1]  # 'photo', 'text', or 'both'

    saved_msgs = db.get_user_ad_messages(uid)
    if saved_msgs:
        # Preserve existing content, just update ad_type
        existing = saved_msgs[0]
        db.add_user_ad_message(
            uid,
            existing.get("message", ""),
            datetime.now(),
            photo_path=existing.get("photo_path"),
            ad_type=ad_type
        )

    type_labels = {"photo": "Image Only 🖼️", "text": "Text Only 📝", "both": "Image + Text 🖼️📝"}
    instructions = {
        "photo": "Now send a <b>photo</b> (caption is optional).",
        "text": "Now send your <b>text</b> ad message.",
        "both": "First send a <b>photo</b> with your text as the caption."
    }

    if ad_type == "text":
        db.set_user_state(uid, "waiting_broadcast_msg_text")
    else:
        db.set_user_state(uid, "waiting_broadcast_msg_photo")

    await cb.message.edit_caption(
        caption=(
            f"<blockquote><b>╰_╯ AD TYPE: {type_labels[ad_type]}</b></blockquote>\n\n"
            f"{instructions[ad_type]}\n\n"
            f"<i>Tips: Keep it concise, use emojis, include a clear call-to-action.</i>"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=kb([[InlineKeyboardButton("Back 🔙", callback_data="set_msg")]])
    )

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
        phone = acc['phone_number']
        is_on = acc_id in selected_ids
        status_icon = "✅" if is_on else "⬜"
        buttons.append([
            InlineKeyboardButton(
                f"{status_icon} {phone}",
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
        if db.get_broadcast_state(uid).get("running"):
            await cb.answer("╰_╯Broadcast already running!", show_alert=True)
            return

        accounts = db.get_user_accounts(uid)
        if not accounts:
            await cb.answer("╰_╯Baka! No accounts hosted yet!", show_alert=True)
            return

        if not db.get_logger_status(uid):
            try:
                await cb.message.edit_caption(
                    caption="<b>⚠️ Logger bot not started yet!</b>\n\n"
                            f"Please start @{config.LOGGER_BOT_USERNAME.lstrip('@')} to receive Advertising logs.\n"
                            "<i>After starting, return here to begin Advertising.</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([
                        [InlineKeyboardButton("Start Logger Bot 📩", url=f"https://t.me/{config.LOGGER_BOT_USERNAME.lstrip('@')}")],
                        [InlineKeyboardButton("Back", callback_data="menu_main")]
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
    selected_phones = [
        acc['phone_number'] for acc in accounts if str(acc['_id']) in selected
    ]
    await _launch_broadcast(client, uid, account_ids=selected)

    label = ", ".join(selected_phones[:3])
    if len(selected_phones) > 3:
        label += f" +{len(selected_phones) - 3} more"

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

@pyro.on_callback_query(filters.regex("analytics"))
async def analytics(client, cb):
    uid = cb.from_user.id
    user_stats = db.get_user_analytics(uid)
    accounts = db.get_user_accounts(uid)
    logger_failures = len(db.get_logger_failures(uid))
    
    analytics_text = (
        f"<blockquote><b>╰_╯@Tecxo ANALYTICS</b></blockquote>\n\n"
        f"<u>Broadcast Cycles Completed:</u> <code>{user_stats.get('total_cycles', 0)}</code>\n"
        f"<b>Messages Sent:</b> <i>{user_stats.get('total_sent', 0)}</i>\n"
        f"<u>Failed Sends:</u> <code>{user_stats.get('total_failed', 0)}</code>\n"
        f"<b>Logger Failures:</b> <i>{logger_failures}</i>\n"
        f"<b>Active Accounts:</b> <i>{len([a for a in accounts if a['is_active']])}</i>\n"
        f"<u>Avg Delay:</u> <code>{db.get_user_ad_delay(uid)}s</code>\n\n"
        f"<blockquote>Success Rate: {generate_progress_bar(user_stats.get('total_sent', 0), user_stats.get('total_sent', 0) + user_stats.get('total_failed', 0))}</blockquote>"
    )
    
    await cb.message.edit_caption(
        caption=analytics_text,
        reply_markup=kb([
            [InlineKeyboardButton("Detailed Report", callback_data="detailed_report")],
            [InlineKeyboardButton("Back", callback_data="menu_main")]
        ]),
        parse_mode=ParseMode.HTML
    )

@pyro.on_callback_query(filters.regex("detailed_report"))
async def detailed_report(client, cb):
    uid = cb.from_user.id
    user_stats = db.get_user_analytics(uid)
    accounts = db.get_user_accounts(uid)
    logger_failures = db.get_logger_failures(uid)
    
    detailed_text = (
        f"<blockquote><b>╰_╯ DETAILED ANALYTICS REPORT:</b></blockquote>\n\n"
        f"<u>Date:</u> <i>{datetime.now().strftime('%d/%m/%y')}</i>\n"
        f"<b>User ID:</b> <code>{uid}</code>\n\n"
        "<b>Broadcast Stats:</b>\n"
        f"- <u>Total Sent:</u> <code>{user_stats.get('total_sent', 0)}</code>\n"
        f"- <i>Total Failed:</i> <b>{user_stats.get('total_failed', 0)}</b>\n"
        f"- <u>Total Broadcasts:</u> <code>{user_stats.get('total_broadcasts', 0)}</code>\n\n"
        "<b>Logger Stats:</b>\n"
        f"- <u>Logger Failures:</u> <code>{len(logger_failures)}</code>\n"
        f"- <i>Last Failure:</i> <b>{logger_failures[-1]['error'] if logger_failures else 'None'}</b>\n\n"
        "<b>Account Stats:</b>\n"
        f"- <i>Total Accounts:</i> <u>{len(accounts)}</u>\n"
        f"- <b>Active Accounts:</b> <code>{len([a for a in accounts if a['is_active']])}</code> 🟢\n"
        f"- <u>Inactive Accounts:</u> <i>{len([a for a in accounts if not a['is_active']])}</i> 🔴\n\n"
        f"<blockquote><b>Current Delay:</b> <code>{db.get_user_ad_delay(uid)}s</code></blockquote>"
    )
    
    await cb.message.edit_caption(
        caption=detailed_text,
        reply_markup=kb([
            [InlineKeyboardButton("Back", callback_data="analytics")]
        ]),
        parse_mode=ParseMode.HTML
    )

@pyro.on_message(filters.command("stats") & filters.user(ALLOWED_BD_IDS))
async def admin_stats(client, m):
    try:
        stats = db.get_admin_stats()
        
        stats_text = (
            f"<blockquote><b>╰_╯ Tecxo Ads ADMIN DASHBOARD </b></blockquote>\n\n"
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
        """<blockquote><b>📢 Tecxo ADMIN BROADCAST</b></blockquote>\n\n"""
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
                    f"""<blockquote><b>📢 Tecxo ADMIN BROADCAST</b></blockquote>\n\n"""
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
        f"""<blockquote><b>✅ Tecxo ADMIN BROADCAST COMPLETED </b></blockquote>\n\n"""
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
        f"<blockquote><b>╰_╯ Tecxo FREE Ads bot</b></blockquote>\n\n"
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
                    caption="""<blockquote><b>╰_╯WELCOME TO @TECXO FREE ADS BOT</b></blockquote>\n\n"""
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

@pyro.on_message(filters.text & filters.regex(r"https?://t\.me/.*") & filters.private & ~filters.command(["start", "bd", "me", "stats", "stop"]))
async def handle_group_link(client, m):
    uid = m.from_user.id
    state = db.get_user_state(uid)
    if state != "waiting_group_link":
        return
    link = m.text.strip()
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

    try:
        db.add_user_ad_message(
            uid, "", datetime.now(),
            photo_path=None, ad_type="forward",
            entities=[],
            from_chat_id=fwd_chat_id,
            message_id=fwd_msg_id
        )
        db.set_user_state(uid, "")
        logger.info(f"Forward-mode ad set for {uid}: from_chat={fwd_chat_id} msg_id={fwd_msg_id}")
        await m.reply(
            "<blockquote><b>╰_╯ FORWARD AD SET! ✅</b></blockquote>\n\n"
            f"Source channel: <code>{fwd_chat_id}</code>\n"
            f"Message ID: <code>{fwd_msg_id}</code>\n\n"
            "<b>Broadcasting will forward this exact message</b> —\n"
            "premium emojis, stickers, and all formatting preserved! 🚀",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
        )
        await send_dm_log(uid, f"<b>📨 Forward-mode ad set:</b> chat <code>{fwd_chat_id}</code> msg <code>{fwd_msg_id}</code>")
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
            photo_file = await client.download_media(m.photo, file_name=f"downloads/{uid}_ad.jpg")
            caption_text = m.caption or ""

            # Determine ad_type from state
            if state == "waiting_broadcast_msg_photo":
                # Check current DB ad_type to keep 'both' vs 'photo'
                saved = db.get_user_ad_messages(uid)
                ad_type = saved[0].get("ad_type", "photo") if saved else "photo"
                if ad_type not in ("photo", "both"):
                    ad_type = "photo"
            else:
                ad_type = "both" if caption_text else "photo"

            # Capture caption entities (premium emojis, bold, etc.)
            caption_entities = entities_to_dict(m.caption_entities or [])

            db.add_user_ad_message(
                uid, caption_text, datetime.now(),
                photo_path=photo_file, ad_type=ad_type,
                entities=caption_entities
            )
            db.set_user_state(uid, "")

            type_label = "Photo + Text 🖼️📝" if (ad_type == "both" and caption_text) else "Photo Only 🖼️"
            caption_preview = f"<code>{caption_text}</code>" if caption_text else "<i>No text caption</i>"
            await m.reply(
                f"<blockquote><b>╰_╯ AD SET! ✅</b></blockquote>\n\n"
                f"<u>Type:</u> {type_label}\n"
                f"<u>Caption Preview:</u>\n{caption_preview}\n\n"
                f"<b>Ready to broadcast!</b>\n"
                f"<i>Start your campaign from the dashboard.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
            )
            await send_dm_log(uid, f"<b>🖼️ Ad Photo updated!</b> Type: {type_label} | Caption: <code>{caption_text[:50] if caption_text else 'None'}</code>")
            logger.info(f"Ad photo set for user {uid}: path={photo_file}, ad_type={ad_type}")
        except Exception as e:
            logger.error(f"Failed to add ad photo for user {uid}: {e}")
            db.set_user_state(uid, "")
            await m.reply(
                f"<blockquote><b>❌ Failed to save ad photo!</b></blockquote>\n\n"
                f"<u>Error:</u> <i>{str(e)}</i>\n"
                f"<b>Contact Support:</b> @{config.ADMIN_USERNAME}",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
            )
            await send_dm_log(uid, f"<b>❌ Failed to set ad photo:</b> {str(e)}")

@pyro.on_message(filters.text & filters.private & ~filters.command(["start", "bd", "me", "stats", "stop"]))
async def handle_text_message(client, m):
    uid = m.from_user.id
    state = db.get_user_state(uid)
    text = m.text.strip()

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
            logger.info(f"Forward-mode ad set for {uid}: from_chat={fwd_chat_id} msg_id={fwd_msg_id}")
            await m.reply(
                "<blockquote><b>╰_╯ FORWARD AD SET! ✅</b></blockquote>\n\n"
                f"Source chat: <code>{fwd_chat_id}</code>\n"
                f"Message ID: <code>{fwd_msg_id}</code>\n\n"
                "<b>Broadcasting will forward this exact message</b> —\n"
                "premium emojis, stickers, and all formatting preserved! 🚀",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
            )
            await send_dm_log(uid, f"<b>📨 Forward-mode ad set:</b> chat <code>{fwd_chat_id}</code> msg <code>{fwd_msg_id}</code>")
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

            db.add_user_ad_message(
                uid, text, datetime.now(),
                photo_path=None, ad_type=ad_type,
                entities=msg_entities,
                from_chat_id=from_chat_id,
                message_id=message_id
            )
            db.set_user_state(uid, "")
            entity_count = len(msg_entities)
            logger.info(f"Ad text set for {uid}: {len(text)} chars, {entity_count} entities, msg_id={message_id}")
            await m.reply(
                f"<blockquote><b>╰_╯ AD MESSAGE SET!✅</b></blockquote>\n\n"
                f"<u>Message Preview:</u>\n<code>{text}</code>\n\n"
                f"<b>Ready to broadcast!</b>\n"
                f"<i>Start your campaign from the dashboard.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Dashboard 🚪", callback_data="menu_main")]])
            )
            await send_dm_log(uid, f"<b>📝 Ad message updated:</b> <code>{text[:50]}{'...' if len(text) > 50 else ''}</code>")
            logger.info(f"Ad message set for user {uid}: {text[:50]}...")
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



async def start_auto_reply_listeners(uid, tg_client, phone):
    """Register a Telethon event handler on tg_client that auto-replies to private DMs."""
    @tg_client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def _auto_reply_handler(event):
        ar = db.get_auto_reply(uid)
        if not ar.get("enabled") or not ar.get("message"):
            return
        try:
            sender = await event.get_sender()
            if sender and getattr(sender, 'bot', False):
                return
            if event.sender_id == 777000:
                return
            await event.reply(ar["message"])
            logger.info(f"Auto-replied to {event.sender_id} via {phone} for user {uid}")
            await send_dm_log(uid, f"<b>💬 Auto-replied to</b> <code>{event.sender_id}</code> via {phone}")
        except Exception as e:
            logger.error(f"Auto-reply failed for {phone}: {e}")

    logger.info(f"Auto-reply listener registered for account {phone} (owner: {uid})")


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
        session_encrypted = cipher_suite.encrypt(session_str.encode()).decode()
        db.add_user_account(uid, phone, session_encrypted)
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


async def main():
    await pyro.start()
    await logger_client.start()
    try:
        await idle()
    except KeyboardInterrupt:
        for uid, task in list(user_tasks.items()):
            task.cancel()
        db.close()
        logger.info("Bot stopped gracefully")

if __name__ == "__main__":
    pyro.run(main())
