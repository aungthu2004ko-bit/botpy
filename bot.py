import asyncio
import json
import os
import random
import re
import string
import time
import datetime
import html
import unicodedata
import uuid
import asyncpg
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputMediaPhoto, InputMediaVideo, InlineQueryResultCachedPhoto, InlineQueryResultCachedVideo, ChatPermissions, CopyTextButton, Bot, Message, CallbackQuery
from telegram.error import TelegramError, NetworkError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    InlineQueryHandler,
    BaseUpdateProcessor,
    ApplicationHandlerStop,
)


# Convert visible Latin letters and digits to Telegram-friendly Unicode Monospace.
# HTML tags/entities are preserved so existing formatting and parse modes keep working.
_MONO_TABLE = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    "𝙰𝙱𝙲𝙳𝙴𝙵𝙶𝙷𝙸𝙹𝙺𝙻𝙼𝙽𝙾𝙿𝚀𝚁𝚂𝚃𝚄𝚅𝚆𝚇𝚈𝚉"
    "𝚊𝚋𝚌𝚍𝚎𝚏𝚐𝚑𝚒𝚓𝚔𝚕𝚖𝚗𝚘𝚙𝚚𝚛𝚜𝚝𝚞𝚟𝚠𝚡𝚢𝚣"
    "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿"
)

_PLAIN_OPEN = "\x01PLAIN:"
_PLAIN_CLOSE = ":PLAIN\x02"

def _plain_text(value):
    """Mark a dynamic text segment to bypass the global Mono conversion."""
    return f"{_PLAIN_OPEN}{value}{_PLAIN_CLOSE}"

def _mono_visible_text(value):
    """Use Mono styling only for the explicitly approved command names."""
    if not isinstance(value, str):
        return value

    # Only these command labels should appear in Unicode monospace.
    # Every other message, command, name, link, and button label stays plain.
    mono_commands = re.compile(
        r'(?<![A-Za-z0-9_])/(?:kc|set|kgift|card|kachi|kbox|pre|khmode|kprofile)(?![A-Za-z0-9_])',
        re.IGNORECASE,
    )
    protected_pattern = re.compile(
        r'(<a\b[^>]*>.*?</a>'
        r'|https?://[^\s<>()]+'
        r'|<[^>]*>'
        r'|&(?:#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z]+);)',
        re.IGNORECASE | re.DOTALL,
    )

    # Keep explicitly marked plain-text segments unchanged.
    marker_pattern = re.compile(re.escape(_PLAIN_OPEN) + r"(.*?)" + re.escape(_PLAIN_CLOSE), re.DOTALL)
    protected_value = marker_pattern.sub(lambda m: "\x03" + m.group(1).replace("\x03", "") + "\x04", value)

    parts = protected_pattern.split(protected_value)
    converted_parts = []
    for part in parts:
        if protected_pattern.fullmatch(part):
            converted_parts.append(part)
        else:
            converted_parts.append(
                mono_commands.sub(lambda m: m.group(0).translate(_MONO_TABLE), part)
            )
    converted = ''.join(converted_parts)
    return re.sub(r"\x03(.*?)\x04", lambda m: m.group(1), converted, flags=re.DOTALL)

# Apply Mono styling to outgoing bot messages and inline keyboard labels only.
# Commands, callback_data, IDs, and database values are never modified.
_original_bot_send_message = Bot.send_message
async def _mono_bot_send_message(self, *args, **kwargs):
    if len(args) >= 2:
        args = (args[0], _mono_visible_text(args[1]), *args[2:])
    elif "text" in kwargs:
        kwargs["text"] = _mono_visible_text(kwargs["text"])
    return await _original_bot_send_message(self, *args, **kwargs)
Bot.send_message = _mono_bot_send_message

_original_bot_edit_message_text = Bot.edit_message_text
async def _mono_bot_edit_message_text(self, *args, **kwargs):
    if len(args) >= 2:
        args = (args[0], _mono_visible_text(args[1]), *args[2:])
    elif "text" in kwargs:
        kwargs["text"] = _mono_visible_text(kwargs["text"])
    return await _original_bot_edit_message_text(self, *args, **kwargs)
Bot.edit_message_text = _mono_bot_edit_message_text

_original_message_reply_text = Message.reply_text
async def _mono_message_reply_text(self, *args, **kwargs):
    if args:
        args = (_mono_visible_text(args[0]), *args[1:])
    elif "text" in kwargs:
        kwargs["text"] = _mono_visible_text(kwargs["text"])
    return await _original_message_reply_text(self, *args, **kwargs)
Message.reply_text = _mono_message_reply_text

_original_callback_edit_message_text = CallbackQuery.edit_message_text
async def _mono_callback_edit_message_text(self, *args, **kwargs):
    if args:
        args = (_mono_visible_text(args[0]), *args[1:])
    elif "text" in kwargs:
        kwargs["text"] = _mono_visible_text(kwargs["text"])
    return await _original_callback_edit_message_text(self, *args, **kwargs)
CallbackQuery.edit_message_text = _mono_callback_edit_message_text

_original_inline_button_init = InlineKeyboardButton.__init__
def _mono_inline_button_init(self, *args, **kwargs):
    if args:
        args = (_mono_visible_text(args[0]), *args[1:])
    elif "text" in kwargs:
        kwargs["text"] = _mono_visible_text(kwargs["text"])
    return _original_inline_button_init(self, *args, **kwargs)
InlineKeyboardButton.__init__ = _mono_inline_button_init


# Keep captions on outgoing media in the same Mono style as text messages.
def _wrap_caption_method(cls, method_name):
    original = getattr(cls, method_name, None)
    if original is None:
        return
    async def wrapped(self, *args, **kwargs):
        if "caption" in kwargs and kwargs["caption"] is not None:
            kwargs["caption"] = _mono_visible_text(kwargs["caption"])
        return await original(self, *args, **kwargs)
    setattr(cls, method_name, wrapped)

for _method_name in ("send_photo", "send_video", "send_animation", "send_document",
                     "send_audio", "send_voice", "edit_message_caption"):
    _wrap_caption_method(Bot, _method_name)

for _method_name in ("reply_photo", "reply_video", "reply_animation", "reply_document",
                     "reply_audio", "reply_voice"):
    _wrap_caption_method(Message, _method_name)

# Reply to the triggering command when it still exists; if the user deleted it,
# Telegram sends the bot response as a normal standalone message instead.
async def _safe_reply_text(message, *args, **kwargs):
    kwargs["allow_sending_without_reply"] = True
    return await message.reply_text(*args, **kwargs)

async def _safe_reply_photo(message, *args, **kwargs):
    kwargs["allow_sending_without_reply"] = True
    return await message.reply_photo(*args, **kwargs)

async def _safe_reply_video(message, *args, **kwargs):
    kwargs["allow_sending_without_reply"] = True
    return await message.reply_video(*args, **kwargs)


# Environment Variables
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7940553702"))
DATABASE_URL = os.getenv("DATABASE_URL")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID", "@beyondpoe")
CARD_LOG_CHANNEL_ID = "@beyondpoe"
TIGER_DRAGON_CHANNEL_ID = "@beyondpoe"
CHARACTER_CATCHER_LOGS_USERNAME = "character_catcher_logs"
CHARACTER_CATCHER_IMPORT_LOG_CHANNEL = "@brnyaraa"

# Main Group Link for /kbox, /klot, /kdaily, and /pre restriction
MAIN_GROUP_LINK = "https://t.me/Kachi_sGroup"

# Global Memory Stores (Non-blocking per user/action)
group_threshold = {}
pending_gifts = {}
active_card_drops = {}
active_high_rarity_quizzes = {}  # quiz message_id -> card + random correct choice
pending_catch_targets = {}  # (chat_id, user_id) -> spawned card message ID after .k reveal
card_drop_locks = {}
card_claim_locks = {}  # Serialize /mm claims per chat, including DB fallback.
tg_game_task = None
tg_stop_requested = False
tg_current_round = None
tg_rounds = {}
tg_pending_bets = {}
tg_game_lock = asyncio.Lock()
pending_card_adds = {}
pending_cc_channel_adds = {}
pending_kachi_transfers = {}
pending_card_buys = {}
pending_group_sends = {}
active_kbox_users = set()  # Track ongoing Kbox sessions per user
kshop_prices = {}  # Track shop card prices per user session
active_kdic_games = {}  # (chat_id, user_id) -> private dice-game state
kdic_cooldowns = {}  # user_id -> timestamp of the last completed/cancelled dice game

# Cooldown Stores for /kbox, and /kslot
kbox_cooldowns = {}
kslot_cooldowns = {}
kslot_is_cancelled = {}
active_kslot_sessions = {}  # Track active/pending kslot sessions per user

global_default_threshold = 70
bot_warned_groups = set()

# Anti-Spam Tracking Stores
user_message_timestamps = {}
spam_ignores = {}

# Caching Sets to Avoid Redundant Heavy DB Writes on Every Message
seen_registered_users = set()
seen_registered_groups = set()

# Process updates from different users concurrently, while keeping each user's
# updates ordered. This protects user-scoped in-memory state from same-user races.
class UserSerialUpdateProcessor(BaseUpdateProcessor):
    """
    Keep commands from the same user ordered, but do not make a command wait
    behind ordinary chat messages from that user. This reduces command delays
    when a user is active in busy groups while retaining command-to-command
    ordering for the bot's stateful commands.
    """
    def __init__(self, max_concurrent_updates: int = 24):
        super().__init__(max_concurrent_updates=max_concurrent_updates)
        self._user_locks = {}
        self._locks_guard = asyncio.Lock()

    async def initialize(self):
        return

    async def shutdown(self):
        self._user_locks.clear()

    async def do_process_update(self, update, coroutine):
        user = update.effective_user
        chat = update.effective_chat

        if user is not None:
            owner_key = ("user", user.id)
        elif chat is not None:
            owner_key = ("chat", chat.id)
        else:
            owner_key = ("update", update.update_id)

        message = update.effective_message
        message_text = ""
        if message is not None:
            message_text = message.text or message.caption or ""

        # Commands get their own ordered lane so group chatter cannot hold
        # up a user's /commands. Other updates remain ordered with each other.
        is_command = update.callback_query is None and message_text.lstrip().startswith("/")
        lane = "command" if is_command else "other"
        lock_key = (owner_key, lane)

        async with self._locks_guard:
            entry = self._user_locks.get(lock_key)
            if entry is None:
                entry = {"lock": asyncio.Lock(), "users": 0}
                self._user_locks[lock_key] = entry
            entry["users"] += 1
            lock = entry["lock"]

        try:
            async with lock:
                await coroutine
        finally:
            async with self._locks_guard:
                current = self._user_locks.get(lock_key)
                if current is entry:
                    current["users"] -= 1
                    if current["users"] <= 0 and not current["lock"].locked():
                        self._user_locks.pop(lock_key, None)


# Database Connection Pool: start small and allow measured concurrency.
db_pool = None

async def _init_db_once():
    global db_pool, global_default_threshold
    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=20,
        command_timeout=30,
        timeout=30,
        max_inactive_connection_lifetime=60
    )
    
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                name TEXT,
                coins BIGINT DEFAULT 1000,
                selected_card_id TEXT,
                is_card_admin BOOLEAN DEFAULT FALSE,
                started_in_pm BOOLEAN DEFAULT FALSE,
                banned BOOLEAN DEFAULT FALSE,
                ban_reason TEXT,
                muted_until BIGINT DEFAULT 0,
                mute_reason TEXT,
                last_kdaily BIGINT DEFAULT 0,
                pre_count INT DEFAULT 0,
                last_pre_reset BIGINT DEFAULT 0,
                kbox_count INT DEFAULT 0,
                last_kbox_reset BIGINT DEFAULT 0,
                kc_rarity_mode TEXT DEFAULT NULL,
                referred_by BIGINT,
                referral_count INT DEFAULT 0,
                daily_kg_win BIGINT DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS groups (
                chat_id BIGINT PRIMARY KEY,
                title TEXT,
                added_by_id BIGINT,
                added_by_name TEXT,
                threshold INT DEFAULT 70,
                msg_count INT DEFAULT 0,
                admin_warned BOOLEAN DEFAULT FALSE
            );
            CREATE TABLE IF NOT EXISTS group_members (
                chat_id BIGINT,
                user_id BIGINT,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS cards (
                card_id TEXT PRIMARY KEY,
                name TEXT,
                type TEXT,
                file_id TEXT,
                rarity TEXT,
                collection TEXT,
                is_locked BOOLEAN DEFAULT FALSE
            );
            CREATE TABLE IF NOT EXISTS user_cards (
                user_id BIGINT,
                card_id TEXT,
                amount INT DEFAULT 1,
                PRIMARY KEY (user_id, card_id)
            );
            CREATE TABLE IF NOT EXISTS daily_spawn_claims (
                user_id BIGINT,
                claim_date DATE,
                count INT NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, claim_date)
            );
            CREATE TABLE IF NOT EXISTS send_message_likes (
                message_key TEXT PRIMARY KEY,
                total_likes BIGINT NOT NULL DEFAULT 0,
                chat_id BIGINT,
                message_id BIGINT
            );
            ALTER TABLE send_message_likes ADD COLUMN IF NOT EXISTS chat_id BIGINT;
            ALTER TABLE send_message_likes ADD COLUMN IF NOT EXISTS message_id BIGINT;
            CREATE TABLE IF NOT EXISTS send_message_like_users (
                message_key TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                PRIMARY KEY (message_key, user_id)
            );
            CREATE TABLE IF NOT EXISTS send_message_reactions (
                message_key TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                reaction TEXT NOT NULL,
                PRIMARY KEY (message_key, user_id)
            );
            ALTER TABLE send_message_reactions
                DROP CONSTRAINT IF EXISTS send_message_reactions_reaction_check;
            ALTER TABLE send_message_reactions
                ADD CONSTRAINT send_message_reactions_reaction_check
                CHECK (reaction IN ('like', 'chee', 'haha'));
            INSERT INTO send_message_reactions(message_key, user_id, reaction)
            SELECT message_key, user_id, 'like' FROM send_message_like_users
            ON CONFLICT (message_key, user_id) DO NOTHING;
            CREATE TABLE IF NOT EXISTS active_card_drops (
                chat_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                card_id TEXT,
                card_name TEXT NOT NULL,
                card_type TEXT,
                file_id TEXT,
                rarity TEXT,
                collection TEXT,
                expires_at DOUBLE PRECISION NOT NULL,
                winner_id BIGINT,
                winner_name TEXT,
                ended BOOLEAN NOT NULL DEFAULT FALSE,
                PRIMARY KEY (chat_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_active_card_drops_chat_expiry
                ON active_card_drops(chat_id, expires_at);
            CREATE TABLE IF NOT EXISTS card_add_activity (
                activity_id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                card_id TEXT NOT NULL,
                added_on DATE NOT NULL DEFAULT CURRENT_DATE
            );
            CREATE INDEX IF NOT EXISTS idx_card_add_activity_user_date ON card_add_activity(user_id, added_on);
            CREATE TABLE IF NOT EXISTS cad_daily_claims (
                user_id BIGINT NOT NULL,
                claim_date DATE NOT NULL DEFAULT CURRENT_DATE,
                count INT NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, claim_date)
            );
            CREATE TABLE IF NOT EXISTS card_add_sessions (
                user_id BIGINT PRIMARY KEY,
                session_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS spam_restrictions (
                user_id BIGINT PRIMARY KEY,
                until_ts BIGINT NOT NULL,
                reason TEXT DEFAULT 'Spam'
            );
            CREATE INDEX IF NOT EXISTS idx_user_cards_user_id ON user_cards(user_id);
            CREATE INDEX IF NOT EXISTS idx_user_cards_card_id ON user_cards(card_id);
            CREATE INDEX IF NOT EXISTS idx_group_members_chat_user ON group_members(chat_id, user_id);
        """)

        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS selected_card_id TEXT;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_card_admin BOOLEAN DEFAULT FALSE;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS started_in_pm BOOLEAN DEFAULT FALSE;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS banned BOOLEAN DEFAULT FALSE;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS ban_reason TEXT;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS muted_until BIGINT DEFAULT 0;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mute_reason TEXT;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_kdaily BIGINT DEFAULT 0;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS pre_count INT DEFAULT 0;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_pre_reset BIGINT DEFAULT 0;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS kbox_count INT DEFAULT 0;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_kbox_reset BIGINT DEFAULT 0;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS kc_rarity_mode TEXT DEFAULT NULL;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_count INT DEFAULT 0;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_kg_win BIGINT DEFAULT 0;")
            await conn.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS added_by_id BIGINT;")
            await conn.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS added_by_name TEXT;")
            await conn.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS threshold INT DEFAULT 70;")
            await conn.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS msg_count INT DEFAULT 0;")
            await conn.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS admin_warned BOOLEAN DEFAULT FALSE;")
            await conn.execute("ALTER TABLE user_cards ADD COLUMN IF NOT EXISTS amount INT DEFAULT 1;")
            await conn.execute("ALTER TABLE cards ADD COLUMN IF NOT EXISTS rarity TEXT;")
            await conn.execute("ALTER TABLE cards ADD COLUMN IF NOT EXISTS collection TEXT;")
            await conn.execute("ALTER TABLE cards ADD COLUMN IF NOT EXISTS is_locked BOOLEAN DEFAULT FALSE;")
        except Exception:
            pass

        db_groups = await conn.fetch("SELECT chat_id, threshold FROM groups")
        for g in db_groups:
            if g['threshold'] is not None:
                group_threshold[g['chat_id']] = g['threshold']

        # Video cards belong exclusively to Kryora; Kryora must contain videos only.
        await conn.execute("UPDATE cards SET rarity = 'Kryora' WHERE LOWER(COALESCE(type, '')) = 'video'")
        await conn.execute("UPDATE cards SET rarity = 'Raiden' WHERE rarity = 'Kryora' AND LOWER(COALESCE(type, '')) <> 'video'")

        existing_cards = await conn.fetch("SELECT card_id, type FROM cards WHERE rarity IS NULL")
        for c in existing_cards:
            r = random.choices(["Shinsei", "Kiwami", "Kami", "Hoshi", "Ten"], weights=[1, 30, 30, 30, 30], k=1)[0]
            await conn.execute("UPDATE cards SET rarity = $1 WHERE card_id = $2", r, c['card_id'])

        collections_list = [
            "🎞 Ope Ope No Mi [ 🩺]",
            "🎞 Hito Hito No Mi [🌕]",
            "🎞 Mera Mera No Mi [🔥]",
            "🎞 Hana Hana No Mi [🌸]",
            "🎞 Yomi Yomi No Mi [💀]",
            "🎞 Tori Tori No Mi [🦅]",
            "🎞 Yami Yami No Mi [🌑]",
            "🎞 Sora Sora No Mi [☁️]",
            "🎞 Kaze Kaze No Mi [🌪️]",
            "🎞 Mizu Mizu No Mi [💧]",
            "🎞 Kori Kori No Mi [❄️]",
            "🎞 Tsuchi Tsuchi No Mi [🪨]",
            "🎞 Kumo Kumo No Mi [🕸️]",
            "🎞 Hikari Hikari No Mi [✨]",
            "🎞 Kage Kage No Mi [🌘]",
            "🎞 Rai Rai No Mi [⚡]",
            "🎞 Niji Niji No Mi [🌈]",
            "🎞 Tsuki Tsuki No Mi [🌙]",
            "🎞 Taiyo Taiyo No Mi [☀️]",
            "🎞 Mori Mori No Mi [🌳]",
            "🎞 Bara Bara No Mi [🎈]",
            "🎞 Toge Toge No Mi [🌵]",
            "🎞 Hanaumi No Mi [🪷]",
            "🎞 Kiba Kiba No Mi [🦷]",
            "🎞 Hebi Hebi No Mi [🐍]",
            "🎞 Neko Neko No Mi [🐈]",
            "🎞 Inu Inu No Mi [🐕]",
            "🎞 Kuma Kuma No Mi [🐻]",
            "🎞 Kitsune Kitsune No Mi [🦊]",
            "🎞 Tatsu Tatsu No Mi [🐉]",
            "🎞 Kame Kame No Mi [🐢]",
            "🎞 Hoshi Hoshi No Mi [⭐]",
            "🎞 Umi Umi No Mi [🌊]",
            "🎞 Shio Shio No Mi [🧂]",
            "🎞 Ame Ame No Mi [🌧️]",
            "🎞 Yuki Yuki No Mi [🌨️]",
            "🎞 Kiri Kiri No Mi [🌫️]",
            "🎞 Goro Goro No Mi [🔔]",
            "🎞 Hibana Hibana No Mi [🧨]",
            "🎞 Kemuri Kemuri No Mi [💨]",
            "🎞 Mushi Mushi No Mi [🦋]",
            "🎞 Tama Tama No Mi [🥚]",
            "🎞 Kuri Kuri No Mi [🌰]",
            "🎞 Suna Suna No Mi [🏜️]",
            "🎞 Ishi Ishi No Mi [💎]",
            "🎞 Gami Gami No Mi [📜]",
            "🎞 Oni Oni No Mi [👹]",
            "🎞 Tenshi Tenshi No Mi [👼]",
            "🎞 Shishi Shishi No Mi [🦁]",
            "🎞 Ryu Ryu No Mi [🦖]",
            "🎞 Fukurou Fukurou No Mi [🦉]",
            "🎞 Chou Chou No Mi [🐝]",
            "🎞 Sakana Sakana No Mi [🐟]",
            "🎞 Tako Tako No Mi [🐙]",
            "🎞 Kani Kani No Mi [🦀]",
            "🎞 Shima Shima No Mi [🏝️]",
            "🎞 Yume Yume No Mi [💭]"
        ]
        unassigned_cards = await conn.fetch("SELECT card_id FROM cards WHERE collection IS NULL")
        for uc in unassigned_cards:
            rand_col = random.choice(collections_list)
            await conn.execute("UPDATE cards SET collection = $1 WHERE card_id = $2", rand_col, uc['card_id'])

        thresh_row = await conn.fetchrow("SELECT value FROM settings WHERE key='default_threshold'")
        if thresh_row:
            global_default_threshold = int(thresh_row['value'])


async def init_db():
    """Initialize PostgreSQL with retries for temporary Railway/network resets."""
    global db_pool

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing. Set it in Railway Variables.")

    last_error = None
    for attempt in range(1, 9):
        try:
            await _init_db_once()
            print("✅ Database initialized successfully.")
            return
        except Exception as e:
            last_error = e
            print(
                f"⚠️ Database initialization attempt {attempt}/8 failed: "
                f"{type(e).__name__}: {e}"
            )

            # Discard a partially-created or unhealthy pool before retrying.
            if db_pool is not None:
                try:
                    await db_pool.close()
                except Exception:
                    pass
                db_pool = None

            if attempt < 8:
                delay = min(5 * attempt, 30)
                print(f"🔄 Retrying database connection in {delay} seconds...")
                await asyncio.sleep(delay)

    raise RuntimeError(
        f"Could not initialize PostgreSQL after 8 attempts: "
        f"{type(last_error).__name__}: {last_error}"
    ) from last_error


def get_rarity_emoji(r):
    if r == 'Beyond':
        return "♠"
    elif r == 'Raiden':
        return "📓"
    elif r == 'Kryora':
        return "🦠"
    elif r == 'Yugen':
        return "🎐"
    elif r == 'Shinsei':
        return "🧬"
    elif r == 'Kiwami':
        return "🪞"
    elif r == 'Kami':
        return "✨"
    elif r == 'Hoshi':
        return "⚜️"
    elif r == 'Birthday Special':
        return "🎀ʙɪʀᴛʜᴅᴀʏ sᴘᴇᴄɪᴀʟ🧸"
    else:
        return "💮"

def get_rarity_display(r):
    r_emoji = get_rarity_emoji(r)
    if r in ['Birthday Special', 'Beyond']:
        if r == 'Beyond':
            return f"{r_emoji} Beyond"
        return r_emoji
    return f"{r_emoji} {r}"

# Rarity probabilities used only by /pre and /kbox.
PRE_KBOX_RARITY_WEIGHTS = {
    "Ten": 45,
    "Hoshi": 25,
    "Kami": 14,
    "Kiwami": 8,
    "Shinsei": 5,
    "Yugen": 2,
    "Kryora": 1,
}
USER_CARD_MAX_PER_ID = 3


async def get_pre_kbox_weighted_card(conn, user_id):
    """Pick by rarity probability, then uniformly among eligible cards of that rarity."""
    rows = await conn.fetch("""
        SELECT c.card_id, c.name, c.type, c.file_id, c.rarity, c.collection, c.is_locked,
               COALESCE(uc.amount, 0) AS owned_amount
        FROM cards c
        LEFT JOIN user_cards uc
          ON uc.card_id = c.card_id AND uc.user_id = $1
        WHERE c.rarity = ANY($2::text[])
          AND COALESCE(uc.amount, 0) < $3
          AND c.is_locked = FALSE
    """, user_id, list(PRE_KBOX_RARITY_WEIGHTS.keys()), USER_CARD_MAX_PER_ID)

    # Preserve the existing fallback behavior when every eligible card is locked.
    if not rows:
        rows = await conn.fetch("""
            SELECT c.card_id, c.name, c.type, c.file_id, c.rarity, c.collection, c.is_locked,
                   COALESCE(uc.amount, 0) AS owned_amount
            FROM cards c
            LEFT JOIN user_cards uc
              ON uc.card_id = c.card_id AND uc.user_id = $1
            WHERE c.rarity = ANY($2::text[])
              AND COALESCE(uc.amount, 0) < $3
        """, user_id, list(PRE_KBOX_RARITY_WEIGHTS.keys()), USER_CARD_MAX_PER_ID)

    if not rows:
        return None

    available_by_rarity = {}
    for row in rows:
        available_by_rarity.setdefault(row["rarity"] or "Ten", []).append(row)

    rarities = list(available_by_rarity.keys())
    weights = [PRE_KBOX_RARITY_WEIGHTS.get(rarity, 1) for rarity in rarities]
    selected_rarity = random.choices(rarities, weights=weights, k=1)[0]
    return random.choice(available_by_rarity[selected_rarity])


async def add_user_card_with_limit(conn, user_id, card_id, amount=1):
    """Atomically add cards only if the user's resulting amount will not exceed 3."""
    if amount < 1 or amount > USER_CARD_MAX_PER_ID:
        return False
    result = await conn.fetchrow("""
        INSERT INTO user_cards (user_id, card_id, amount)
        VALUES ($1, $2, $3)
        ON CONFLICT (user_id, card_id) DO UPDATE
        SET amount = user_cards.amount + EXCLUDED.amount
        WHERE user_cards.amount + EXCLUDED.amount <= $4
        RETURNING amount
    """, user_id, card_id, amount, USER_CARD_MAX_PER_ID)
    return result is not None


async def check_and_auto_lock_card(conn, card_id):
    # Kryora cards lock after 30 distributed copies; Raiden cards lock after 20.
    card_info = await conn.fetchrow("SELECT rarity, is_locked FROM cards WHERE card_id = $1", card_id)
    if card_info and not card_info['is_locked']:
        rarity = card_info['rarity']
        lock_limit = 20 if rarity == 'Raiden' else (30 if rarity == 'Kryora' else None)
        if lock_limit is not None:
            total_copies = await conn.fetchval(
                "SELECT COALESCE(SUM(amount), 0) FROM user_cards WHERE card_id = $1", card_id
            )
            if total_copies >= lock_limit:
                await conn.execute("UPDATE cards SET is_locked = TRUE WHERE card_id = $1", card_id)

async def get_user_restriction_status(user_id: int):
    """Return active bot-command restriction (spam/ban/mute), if any."""
    if not db_pool:
        return None
    now = int(time.time())
    try:
        async with db_pool.acquire() as conn:
            spam_until = await conn.fetchval("SELECT until_ts FROM spam_restrictions WHERE user_id = $1", user_id)
            if spam_until and spam_until > now:
                return ("spam", int(spam_until))
            if spam_until:
                await conn.execute("DELETE FROM spam_restrictions WHERE user_id = $1", user_id)
            row = await conn.fetchrow("SELECT banned, muted_until FROM users WHERE user_id = $1", user_id)
            if row and row['banned']:
                return ("ban", 0)
            if row and row['muted_until'] and row['muted_until'] > now:
                return ("mute", int(row['muted_until']))
    except Exception as e:
        print(f"Restriction lookup failed for {user_id}: {type(e).__name__}: {e}")
    return None


async def spam_command_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Block every command during spam/ban/mute except /kprofile."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or user.is_bot or user.id == ADMIN_ID:
        return
    command = (msg.text or "").split()[0].split("@", 1)[0].lower()
    if command == "/kprofile":
        return
    restriction = await get_user_restriction_status(user.id)
    if not restriction:
        return
    kind, until_ts = restriction
    if kind == "spam":
        remaining = max(0, until_ts - int(time.time()))
        mins, secs = divmod(remaining, 60)
        time_text = f"{mins}m {secs}s" if mins else f"{secs}s"
        warning = f"🚫 Spam restriction active.\n⏳ Remaining: {time_text}\nOnly /kprofile can be used until it expires."
    elif kind == "ban":
        warning = "🚫 You are banned from using this bot. Only /kprofile is available."
    else:
        remaining = max(0, until_ts - int(time.time()))
        mins, secs = divmod(remaining, 60)
        time_text = f"{mins}m {secs}s" if mins else f"{secs}s"
        warning = f"🔇 You are muted.\n⏳ Remaining: {time_text}\nOnly /kprofile can be used until it expires."
    try:
        await _safe_reply_text(msg, warning)
    except Exception:
        pass
    raise ApplicationHandlerStop



def restricted_command(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        msg = update.effective_message
        if user and not user.is_bot and user.id != ADMIN_ID:
            try:
                async with db_pool.acquire() as conn:
                    user_row = await conn.fetchrow(
                        "SELECT banned, ban_reason, muted_until, mute_reason FROM users WHERE user_id = $1",
                        user.id
                    )
                    if user_row:
                        now = int(time.time())
                        if user_row['banned']:
                            reason = user_row['ban_reason'] or 'No reason provided'
                            if msg:
                                try:
                                    await _safe_reply_text(msg, 
                                        f"❌ You are banned from using this bot.\n📝 Reason: {reason}",
                                        parse_mode="HTML"
                                    )
                                except Exception:
                                    pass
                            return
                        if user_row['muted_until'] and user_row['muted_until'] > now:
                            remaining = user_row['muted_until'] - now
                            mins = remaining // 60
                            secs = remaining % 60
                            time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                            reason = user_row['mute_reason'] or 'No reason provided'
                            if msg:
                                try:
                                    await _safe_reply_text(msg, 
                                        f"🔇 You are muted from using this bot.\n⏳ Time remaining: {time_str}\n📝 Reason: {reason}",
                                        parse_mode="HTML"
                                    )
                                except Exception:
                                    pass
                            return
            except Exception:
                pass
        return await func(update, context)
    return wrapper

async def register_user_group(user, chat, context: ContextTypes.DEFAULT_TYPE = None):
    if not db_pool:
        return
    try:
        user_id = user.id if user and not user.is_bot else None
        chat_id = chat.id if chat and chat.type in ["group", "supergroup"] else None

        user_cached = user_id in seen_registered_users if user_id else True
        group_cached = chat_id in seen_registered_groups if chat_id else True

        if user_cached and group_cached:
            return

        async with db_pool.acquire() as conn:
            if user_id:
                await conn.execute("""
                    INSERT INTO users (user_id, name, coins) VALUES ($1, $2, 1000)
                    ON CONFLICT(user_id) DO UPDATE SET name = EXCLUDED.name
                """, user_id, user.first_name)
                seen_registered_users.add(user_id)

            if chat_id:
                added_id = user.id if user and not user.is_bot else None
                added_name = user.first_name if user and not user.is_bot else None
                
                is_new = await conn.fetchrow("SELECT chat_id, admin_warned FROM groups WHERE chat_id = $1", chat_id)
                
                await conn.execute("""
                    INSERT INTO groups (chat_id, title, added_by_id, added_by_name) 
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT(chat_id) DO UPDATE SET 
                        title = EXCLUDED.title,
                        added_by_id = COALESCE(groups.added_by_id, EXCLUDED.added_by_id),
                        added_by_name = COALESCE(groups.added_by_name, EXCLUDED.added_by_name)
                """, chat_id, chat.title or "Group", added_id, added_name)

                seen_registered_groups.add(chat_id)

                group_row = await conn.fetchrow("SELECT threshold, admin_warned FROM groups WHERE chat_id = $1", chat_id)
                if group_row and group_row['threshold'] is not None:
                    group_threshold[chat_id] = group_row['threshold']

                if group_row and not group_row['admin_warned']:
                    await conn.execute("UPDATE groups SET admin_warned = TRUE WHERE chat_id = $1", chat_id)
                    try:
                        await context.bot.send_message(
                            chat_id,
                            "Bot အသုံးပြုအတွက်ကျေးဇူးအထူးတင်ပါတယ် Bot ကို Admin ပေးပါက Media ကောင်းကောင်းအလုပ်လုပ်ပါမည်。"
                        )
                    except Exception:
                        pass

                if not is_new and context and added_id:
                    try:
                        member_count = await context.bot.get_chat_member_count(chat_id)
                    except Exception:
                        member_count = "N/A"

                    invite_link = "N/A"
                    try:
                        chat_obj = await context.bot.get_chat(chat_id)
                        if chat_obj.invite_link:
                            invite_link = chat_obj.invite_link
                        elif chat_obj.username:
                            invite_link = f"https://t.me/{chat_obj.username}"
                        else:
                            invite_link = await context.bot.export_chat_invite_link(chat_id)
                    except Exception:
                        pass

                    adder_text = get_mention(added_id, added_name or "User") if added_id else "—"
                    log_text = f"""🏰 New Group Added

🔹 Group Name: {chat.title or 'Group'}
👥 Members: {member_count}
🆔 Group ID: <code>{chat_id}</code>
🔗 Link: {invite_link}
👤 Added By: {adder_text}"""
                    try:
                        asyncio.create_task(context.bot.send_message(ADMIN_ID, log_text, parse_mode="HTML", disable_web_page_preview=True))
                    except Exception:
                        pass

                if user and not user.is_bot:
                    await conn.execute("""
                        INSERT INTO group_members (chat_id, user_id) VALUES ($1, $2)
                        ON CONFLICT DO NOTHING
                    """, chat_id, user.id)
    except Exception as e:
        print(f"Error in register_user_group: {e}")

async def add_coins(user_id, amount):
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                UPDATE users 
                SET coins = GREATEST(0, coins + $1) 
                WHERE user_id = $2
            """, amount, user_id)

def get_mention(user_id, name):
    clean_name = (name or "User").replace("<", "&lt;").replace(">", "&gt;")
    return f'<a href="tg://user?id={user_id}">{clean_name}</a>'

async def get_weighted_random_card(conn):
    # Regular drops include Raiden and all other normal rarities, but never Beyond or Birthday Special.
    all_cards = await conn.fetch("SELECT card_id, name, type, file_id, rarity, collection, is_locked FROM cards WHERE is_locked = FALSE AND rarity NOT IN ('Birthday Special', 'Beyond')")
    if not all_cards:
        all_cards = await conn.fetch("SELECT card_id, name, type, file_id, rarity, collection, is_locked FROM cards WHERE rarity NOT IN ('Birthday Special', 'Beyond') AND (rarity != 'Raiden' OR is_locked = FALSE)")
        if not all_cards:
            return None
    
    weights = []
    for c in all_cards:
        r = c.get('rarity', 'Ten')
        if r == 'Yugen':
            weights.append(1)
        elif r == 'Shinsei':
            weights.append(10)
        elif r == 'Kiwami':
            weights.append(30)
        elif r == 'Kami':
            weights.append(30)
        elif r == 'Hoshi':
            weights.append(30)
        else:
            weights.append(30)
            
    return random.choices(all_cards, weights=weights, k=1)[0]

async def check_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Require users to start the bot in private chat; admin is exempt."""
    user = update.effective_user
    if not user or user.is_bot or user.id == ADMIN_ID:
        return True
    chat = update.effective_chat
    if chat and chat.type == "private":
        return True
    async with db_pool.acquire() as conn:
        started = await conn.fetchval(
            "SELECT COALESCE(started_in_pm, FALSE) FROM users WHERE user_id = $1",
            user.id
        )
    if started:
        return True
    bot_info = await context.bot.get_me()
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Bot DM Start", url=f"https://t.me/{bot_info.username}?start=verify", style="success")
    ]])
    text = "💛Hello Bot dm မှာ start နှိပ်ပေးခြင်းဖြင့်ကူညီပြီး bot သုံးလို့ရပါတယ် 🖤"
    if update.effective_message:
        await _safe_reply_text(update.effective_message, text, reply_markup=keyboard)
    elif update.callback_query:
        await update.callback_query.answer("Bot DM မှာ Start နှိပ်ပေးပါ။", show_alert=True)
    return False

async def check_main_group_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    msg = update.message
    if not chat or not msg:
        return True
    
    if chat.type in ["group", "supergroup"]:
        is_main_group = False
        if chat.username and chat.username.lower() == "kachi_sgroup".lower():
            is_main_group = True
        
        if not is_main_group:
            restriction_text = (
                "This Command Will Work In This Main Group\n"
                "Heyy သူများGpမှာမဖောက်ပါနဲ့နော် သူများမျက်စိနှောက်လိမ့်မယ် "
                "အဲ့ဒီ command တွေကိုတော့ ဒီGroupမှာပဲ လာဖောက်ပေးပါနော် ✨"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Main Group 🏰", url=MAIN_GROUP_LINK, style="primary")]
            ])
            try:
                await _safe_reply_text(msg, restriction_text, reply_markup=keyboard)
            except Exception:
                pass
            return False
    return True

# /pre Command Implementation (Excluding Raiden, Kryora, Beyond, Birthday Special)
@restricted_command
async def pre_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    user = update.effective_user
    chat = update.effective_chat
    msg = update.message
    msg_id = msg.message_id
    await register_user_group(user, chat, context)

    now = int(time.time())
    cooldown = 86400

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            user_row = await conn.fetchrow(
                "SELECT pre_count, last_pre_reset, name FROM users WHERE user_id = $1 FOR UPDATE",
                user.id
            )
            pre_count = user_row['pre_count'] if user_row else 0
            last_pre_reset = user_row['last_pre_reset'] if user_row else 0
            display_name = user_row['name'] if user_row else user.first_name

            if now - last_pre_reset >= cooldown:
                pre_count = 0
                last_pre_reset = now

            if pre_count >= 3:
                remaining = cooldown - (now - last_pre_reset)
                hours = remaining // 3600
                mins = (remaining % 3600) // 60
                secs = remaining % 60
                time_str = f"{hours}h {mins}m {secs}s" if hours > 0 else (f"{mins}m {secs}s" if mins > 0 else f"{secs}s")
                return await _safe_reply_text(msg, 
                    f"⏳ မင်္ဂလာပါ {get_mention(user.id, display_name)}၊ /pre ကို တစ်နေ့လျှင် 3 ကြိမ်သာ ဖောက်ယူနိုင်ပါသည်။\n"
                    f"⏱ ကျန်ရှိသောအချိန်: {time_str}",
                    parse_mode="HTML"
                )

            card = await get_pre_kbox_weighted_card(conn, user.id)
            if not card:
                return await _safe_reply_text(msg, 
                    "❌ No eligible cards available. You may already own 3 copies of every available card."
                )

            added = await add_user_card_with_limit(conn, user.id, card['card_id'], 1)
            if not added:
                return await _safe_reply_text(msg, 
                    "❌ You already own the maximum 3 copies of this card. Please try again."
                )

            pre_count += 1
            await conn.execute(
                "UPDATE users SET pre_count = $1, last_pre_reset = $2 WHERE user_id = $3",
                pre_count, last_pre_reset, user.id
            )

    async with db_pool.acquire() as conn:
        global_drop_count = await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM user_cards WHERE card_id = $1", card['card_id']
        )

    r = card.get('rarity', 'Ten')
    r_display = get_rarity_display(r)
    col_name = card.get('collection') or "🎞 Ope Ope No Mi [ 🩺]"

    if r == 'Yugen':
        coin_value = "18000-28000"
    elif r == 'Shinsei':
        coin_value = "9000-15000"
    elif r == 'Kiwami':
        coin_value = "5000-7300"
    elif r == 'Kami':
        coin_value = "4500-5700"
    elif r == 'Hoshi':
        coin_value = "1800-2800"
    else:
        coin_value = "1000-2500"

    caption_text = f"""🎗 Congratulations , {get_mention(user.id, display_name)} You Got A New Character in Pre 

▰▰▱▱▰▰▱▱
🍀 Name - {card['name']}
    🍀  ID - <code>{card['card_id']}</code> 

🌟 Rarity - {r_display}
📚 Collection - {col_name}
🩸 Kachi Value ({coin_value})
   🐠 Global Drops - {global_drop_count} 

▰▰▱▱▰▰▱▱
❄Check /kc ❄"""

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Harem❄", switch_inline_query_current_chat=f"harem.{user.id}", style="success")]
    ])

    if card['type'] == 'photo':
        try:
            await _safe_reply_photo(msg, photo=card['file_id'], caption=caption_text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await _safe_reply_text(msg, caption_text, parse_mode="HTML", reply_markup=keyboard)
    else:
        try:
            await _safe_reply_video(msg, video=card['file_id'], caption=caption_text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await _safe_reply_text(msg, caption_text, parse_mode="HTML", reply_markup=keyboard)

@restricted_command

@restricted_command

@restricted_command
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    msg_id = update.message.message_id
    await register_user_group(user, chat, context)
    if chat and chat.type == "private":
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET started_in_pm = TRUE WHERE user_id = $1",
                user.id
            )

    if context.args:
        arg_val = context.args[0]
        if arg_val.startswith("ref_"):
            try:
                inviter_id = int(arg_val.replace("ref_", ""))
                if inviter_id != user.id:
                    async with db_pool.acquire() as conn:
                        async with conn.transaction():
                            u_row = await conn.fetchrow("SELECT referred_by FROM users WHERE user_id = $1 FOR UPDATE", user.id)
                            if u_row and not u_row['referred_by']:
                                await conn.execute("UPDATE users SET referred_by = $1 WHERE user_id = $2", inviter_id, user.id)
                                await conn.execute("UPDATE users SET referral_count = referral_count + 1, coins = coins + 200 WHERE user_id = $1", inviter_id)
                                try:
                                    asyncio.create_task(context.bot.send_message(
                                        inviter_id,
                                        f"🎉 New Referral!\n{get_mention(user.id, user.first_name)} joined using your referral link. You received +200 Kachi 🩸",
                                        parse_mode="HTML"
                                    ))
                                except Exception:
                                    pass
            except ValueError:
                pass
        elif arg_val.startswith("kachi_"):
            try:
                target_id = int(arg_val.replace("kachi_", ""))
                if target_id == user.id:
                    return await _safe_reply_text(update.effective_message, "❌ You cannot transfer Kachi to yourself.")
                
                pending_kachi_transfers[user.id] = target_id

                async with db_pool.acquire() as conn:
                    target_row = await conn.fetchrow("SELECT name FROM users WHERE user_id = $1", target_id)
                    target_name = target_row['name'] if target_row else "User"

                return await _safe_reply_text(update.effective_message, 
                    f"💸 Kachi Transfer\n\n"
                    f"You are sending Kachi to {target_name} (ID: <code>{target_id}</code>).\n"
                    f"Please enter the amount of Kachi you want to send:",
                    parse_mode="HTML"
                )
            except ValueError:
                pass

    # Tiger vs Dragon deep-link: tapping a channel button opens this DM directly.
    if context.args and context.args[0].startswith("tg_"):
        parts = context.args[0].split("_")
        if len(parts) == 3 and parts[2] in ("T", "D"):
            round_id, side = parts[1], parts[2]
            round_state = tg_rounds.get(round_id)
            if not round_state or not round_state.get("open"):
                return await _safe_reply_text(update.effective_message, "⏰ ဒီပွဲရဲ့ လောင်းကြေးပိတ်သွားပါပြီ။ နောက်ပွဲမှာ ပြန်လောင်းနိုင်ပါတယ်။")
            if user.id in round_state["bets"]:
                return await _safe_reply_text(update.effective_message, "ဒီပွဲမှာ လောင်းကြေးထည့်ပြီးပါပြီ။")
            if user.id in tg_pending_bets:
                return await _safe_reply_text(update.effective_message, "လက်ရှိလောင်းကြေးပမာဏကို အရင်ထည့်ပေးပါ။")
            tg_pending_bets[user.id] = {"round_id": round_id, "side": side}
            side_name = "🐅 ကျား (TIGER)" if side == "T" else "🐉 နဂါး (DRAGON)"
            return await _safe_reply_text(update.effective_message, 
                f"━━━━━━━━━━━━━━━━━━\n{side_name} ကို ရွေးထားပါတယ်။\n"
                "💰 လောင်းကြေးပမာဏကို ရိုက်ထည့်ပါ။\n"
                "အနည်းဆုံး 1 မှ အများဆုံး 100,000 Kachi\n"
                "ဥပမာ — 5000")

    bot_info = await context.bot.get_me()
    text = f"""👋 Hello {get_mention(user.id, user.first_name)}!

🌺 Welcome to Kachi Card GAME BOT!

📌 How to Use / အသုံးပြုနည်း

မြန်မာ
- Bot ကို Group ထဲသို့ ထည့်ပါ။
- Group ထဲတွင် စာများပေးပို့ပါ။ သတ်မှတ်ထားသော စာအရေအတွက်ပြည့်ပါက Character Card အလိုအလျောက် ကျလာပါမည်။
- ကတ်ကျလာပါက ကတ်ကို Reply ပြန်ပြီး <code>.k</code> ရိုက်ပါ။ Character Name ပေါ်လာပါမည်။ ထို့နောက် <code>/mm [name]</code> ဖြင့် ကတ်ကိုဖမ်းပါ။
- Leaderboard ကြည့်ရန် /ktop ကို အသုံးပြုပါ။

English
- Add the bot to your group.
- Send messages in the group. A Character Card will drop automatically when the message limit is reached.
- When a card drops, reply to that card and type <code>.k</code> to reveal the character name. Then catch it using <code>/mm [name]</code>.
- Use /ktop to view the leaderboard."""

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Help 📖", callback_data="menu_help", style="primary"),
            InlineKeyboardButton("Gift 🎁", callback_data="menu_kgift", style="primary")
        ],
        [
            InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{bot_info.username}?startgroup=true", style="success")
        ],
        [
            InlineKeyboardButton("Main Group 🏰", url=MAIN_GROUP_LINK, style="primary"),
            InlineKeyboardButton("Main Channel 📢", url="https://t.me/beyondpoe", style="primary")
        ]
    ])

    await _safe_reply_text(update.effective_message, text, parse_mode="HTML", reply_markup=keyboard)

@restricted_command
async def myre_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    user = update.effective_user
    chat = update.effective_chat
    msg_id = update.message.message_id
    await register_user_group(user, chat, context)

    async with db_pool.acquire() as conn:
        user_row = await conn.fetchrow("SELECT coins, name, referral_count FROM users WHERE user_id = $1", user.id)
        coins = user_row['coins'] if user_row else 1000
        name = user_row['name'] if user_row else user.first_name
        ref_count = user_row['referral_count'] if user_row else 0

    bot_info = await context.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user.id}"

    text = f"""Name : {name}
ID : <code>{user.id}</code>
Referral Count - {ref_count}
My Referral Link : <code>{ref_link}</code>

Kachi Balance : {coins}"""

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Share Link 🔗", url=f"https://t.me/share/url?url={ref_link}&text=Join%20Kachi%20Card%20Game%20Bot%20and%20collect%20cards!", style="primary")]
    ])

    await _safe_reply_text(update.effective_message, text, parse_mode="HTML", reply_markup=keyboard)

@restricted_command
async def kdaily_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_main_group_restriction(update, context):
        return
    if not await check_force_join(update, context):
        return
    user = update.effective_user
    chat = update.effective_chat
    msg = update.message
    msg_id = msg.message_id
    await register_user_group(user, chat, context)

    now = int(time.time())
    cooldown = 86400

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            user_row = await conn.fetchrow("SELECT last_kdaily, coins, name FROM users WHERE user_id = $1 FOR UPDATE", user.id)
            last_claim = user_row['last_kdaily'] if user_row else 0
            current_coins = user_row['coins'] if user_row else 1000
            display_name = user_row['name'] if user_row else user.first_name

            if now - last_claim < cooldown:
                remaining = cooldown - (now - last_claim)
                hours = remaining // 3600
                mins = (remaining % 3600) // 60
                secs = remaining % 60
                time_str = f"{hours}h {mins}m {secs}s" if hours > 0 else (f"{mins}m {secs}s" if mins > 0 else f"{secs}s")
                return await _safe_reply_text(msg, 
                    f"⏳ မင်္ဂလာပါ {get_mention(user.id, display_name)}၊ /kdaily ဆုလက်ဆောင်ကို 24 နာရီမှ တစ်ကြိမ်သာ ထုတ်ယူနိုင်ပါသည်။\n"
                    f"⏱ ကျန်ရှိသောအချိန်: {time_str}",
                    parse_mode="HTML"
                )

            new_coins = current_coins + 1000
            await conn.execute("UPDATE users SET coins = $1, last_kdaily = $2 WHERE user_id = $3", new_coins, now, user.id)

    await _safe_reply_text(msg, 
        f"🎁 Daily Kachi Claimed Successfully!\n\n"
        f"👤 User: {get_mention(user.id, display_name)}\n"
        f"💎 Reward: +1000 Kachi 🩸\n"
        f"💰 Total Balance: {new_coins} Kachi\n\n"
        f"<i>ကျေးဇူးတင်ပါတယ်! မနက်ဖြန်လည်း ထပ်မံလာရောက် ထုတ်ယူနိုင်ပါပြီ ✨</i>",
        parse_mode="HTML"
    )

@restricted_command
async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_id = update.message.message_id
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        return await _safe_reply_text(update.effective_message, "❌ Usage: `/addadmin [user_id]`", parse_mode="Markdown")

    try:
        target_id = int(context.args[0])
        async with db_pool.acquire() as conn:
            user_exists = await conn.fetchrow("SELECT name FROM users WHERE user_id = $1", target_id)
            if not user_exists:
                await conn.execute("INSERT INTO users (user_id, name, coins) VALUES ($1, 'User', 1000)", target_id)
            await conn.execute("UPDATE users SET is_card_admin = TRUE WHERE user_id = $1", target_id)

        await _safe_reply_text(update.effective_message, f"✅ User ID <code>{target_id}</code> is now a Card Admin.", parse_mode="HTML")
    except ValueError:
        await _safe_reply_text(update.effective_message, "❌ Invalid User ID.")

@restricted_command

@restricted_command
async def got_card_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_id = update.message.message_id
    user = update.effective_user

    if user.id != ADMIN_ID:
        return

    if not context.args:
        return await _safe_reply_text(update.effective_message, "❌ Usage: `/got [card_id]`", parse_mode="Markdown")

    target_card_id = context.args[0].strip()

    async with db_pool.acquire() as conn:
        card = await conn.fetchrow("SELECT card_id, name, type, file_id, rarity, collection FROM cards WHERE card_id = $1", target_card_id)
        if not card:
            return await _safe_reply_text(update.effective_message, f"❌ Card ID <code>{target_card_id}</code> not found.", parse_mode="HTML")

        async with conn.transaction():
            added = await add_user_card_with_limit(conn, ADMIN_ID, target_card_id, 1)
            if not added:
                return await _safe_reply_text(update.effective_message, 
                    "❌ Admin account already owns the maximum 3 copies of this card."
                )
            await check_and_auto_lock_card(conn, target_card_id)

    r = card.get('rarity', 'Ten')
    r_display = get_rarity_display(r)

    await _safe_reply_text(update.effective_message, 
        f"✅ Successfully added card {card['name']} (ID: <code>{card['card_id']}</code>, Rarity: {r_display}) to your inventory!",
        parse_mode="HTML"
    )

@restricted_command
async def unlock_card_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_id = update.message.message_id
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        return await _safe_reply_text(update.effective_message, "❌ Usage: `/unlock [card_id]`", parse_mode="Markdown")

    arg = context.args[0].strip().lower()
    
    async with db_pool.acquire() as conn:
        if arg == "all":
            await conn.execute("UPDATE cards SET is_locked = FALSE")
            return await _safe_reply_text(update.effective_message, "🔓 All cards have been successfully unlocked!", parse_mode="HTML")
        else:
            card = await conn.fetchrow("SELECT name, rarity, is_locked FROM cards WHERE card_id = $1", context.args[0].strip())
            if not card:
                return await _safe_reply_text(update.effective_message, f"❌ Card ID <code>{context.args[0].strip()}</code> not found.", parse_mode="HTML")

            if not card['is_locked']:
                return await _safe_reply_text(update.effective_message, f"i️ Card ID <code>{context.args[0].strip()}</code> ({card['name']}) is already unlocked.", parse_mode="HTML")

            await conn.execute("UPDATE cards SET is_locked = FALSE WHERE card_id = $1", context.args[0].strip())
            return await _safe_reply_text(update.effective_message, 
                f"✅ Card ID <code>{context.args[0].strip()}</code> ({card['name']}) has been successfully unlocked and can now drop in drops & Kboxes!",
                parse_mode="HTML"
            )

@restricted_command
async def lock_card_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_id = update.message.message_id
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        return await _safe_reply_text(update.effective_message, "❌ Usage: `/lock [card_id]`", parse_mode="Markdown")

    arg = context.args[0].strip().lower()

    async with db_pool.acquire() as conn:
        if arg == "all":
            await conn.execute("UPDATE cards SET is_locked = TRUE")
            return await _safe_reply_text(update.effective_message, "🔒 All cards have been successfully locked!", parse_mode="HTML")
        else:
            card = await conn.fetchrow("SELECT name, rarity, is_locked FROM cards WHERE card_id = $1", context.args[0].strip())
            if not card:
                return await _safe_reply_text(update.effective_message, f"❌ Card ID <code>{context.args[0].strip()}</code> not found.", parse_mode="HTML")

            if card['is_locked']:
                return await _safe_reply_text(update.effective_message, f"i️ Card ID <code>{context.args[0].strip()}</code> ({card['name']}) is already locked.", parse_mode="HTML")

            await conn.execute("UPDATE cards SET is_locked = TRUE WHERE card_id = $1", context.args[0].strip())
            return await _safe_reply_text(update.effective_message, 
                f"🔒 Card ID <code>{context.args[0].strip()}</code> ({card['name']}) has been successfully locked and will no longer drop in drops & Kboxes!",
                parse_mode="HTML"
            )

async def send_character_spawn(context, chat_id, card, reply_to_message_id=None, quiz_passed=False):
    high_rarities = {"Kryora", "Shinsei", "Raiden", "Yugen"}
    if card.get('rarity') in high_rarities and not quiz_passed:
        correct = random.randint(1, 4)
        buttons = [[
            InlineKeyboardButton(str(number), callback_data=f"highquiz_{number}",
                                 style="primary" if number == correct else "danger")
            for number in range(1, 5)
        ]]
        try:
            quiz_msg = await context.bot.send_message(
                chat_id, "Spawn High Rarity 🍁\nPlease Choose Correct Button 💠",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            quiz_data = {
                "chat_id": chat_id, "card": dict(card), "correct": correct,
                "used": False, "created_at": time.time(),
                "expires_at": time.time() + 50
            }
            active_high_rarity_quizzes[quiz_msg.message_id] = quiz_data

            async def expire_high_rarity_quiz(message_id, expected_quiz):
                await asyncio.sleep(50)
                current_quiz = active_high_rarity_quizzes.get(message_id)
                if current_quiz is not expected_quiz or current_quiz.get("used"):
                    return
                current_quiz["used"] = True
                active_high_rarity_quizzes.pop(message_id, None)
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text="⌛ အချိန် 50 စက္ကန့် ပြည့်သွားပါပြီ။ ကဒ်သက်တမ်းကုန်သွားပါပြီ။",
                        reply_markup=None
                    )
                except Exception as expire_error:
                    print(f"Could not expire high-rarity quiz {message_id}: {expire_error}")

            asyncio.create_task(expire_high_rarity_quiz(quiz_msg.message_id, quiz_data))
            return quiz_msg
        except Exception as e:
            print(f"Could not send high-rarity quiz: {e}")
            return None

    rarity_emoji = get_rarity_emoji(card.get('rarity') or 'Ten')
    caption = (f"{rarity_emoji} A Kachi Character Has Spawned in❄ The Chat\n\n"
               "📯 Fast to Catch 🐌 /mm name")
    try:
        if card['type'] == 'photo':
            msg = await context.bot.send_photo(chat_id, card['file_id'], caption=caption, parse_mode='HTML')
        else:
            msg = await context.bot.send_video(chat_id, card['file_id'], caption=caption, parse_mode='HTML')
    except Exception as e:
        print(f"Could not send character spawn: {e}")
        return None
    expires_at = time.time() + 180
    active_card_drops[msg.message_id] = {'chat_id': chat_id, 'card': card, 'winner_id': None,
        'winner_name': None, 'ended': False, 'name_button_used': False, 'expires_at': expires_at}
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM active_card_drops WHERE expires_at < $1",
                time.time() - 86400
            )
            await conn.execute(
                """INSERT INTO active_card_drops
                   (chat_id, message_id, card_id, card_name, card_type, file_id,
                    rarity, collection, expires_at, winner_id, winner_name, ended)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NULL, NULL, FALSE)
                   ON CONFLICT (chat_id, message_id) DO UPDATE SET
                     card_id = EXCLUDED.card_id, card_name = EXCLUDED.card_name,
                     card_type = EXCLUDED.card_type, file_id = EXCLUDED.file_id,
                     rarity = EXCLUDED.rarity, collection = EXCLUDED.collection,
                     expires_at = EXCLUDED.expires_at, winner_id = NULL,
                     winner_name = NULL, ended = FALSE""",
                chat_id, msg.message_id, str(card.get('card_id') or ''),
                str(card.get('name') or ''), card.get('type'), card.get('file_id'),
                card.get('rarity'), card.get('collection'), expires_at
            )
    except Exception as e:
        print(f"Could not persist active character spawn: {e}")
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=msg.message_id,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Use Reply With .k", callback_data="k_prompt_noop", style="primary")
            ]])
        )
    except Exception as e:
        print(f"Could not attach spawn name button: {e}")
    asyncio.create_task(run_character_drop_timer(context, msg.message_id))
    return msg


@restricted_command
async def spw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat = update.effective_message, update.effective_chat
    if update.effective_user.id != ADMIN_ID:
        return
    async with db_pool.acquire() as conn:
        if context.args:
            target_id = context.args[0].strip()
            card = await conn.fetchrow("SELECT card_id, name, type, file_id, rarity, collection, is_locked FROM cards WHERE card_id=$1", target_id)
            if not card:
                return await _safe_reply_text(msg, f"❌ Card ID <code>{target_id}</code> not found in database.", parse_mode='HTML')
            if card.get('rarity') in ('Beyond', 'Birthday Special'):
                return await _safe_reply_text(msg, "❌ Admin-only cards cannot be spawned as normal drops.")
            if card.get('rarity') == 'Raiden' and card.get('is_locked'):
                return await _safe_reply_text(msg, "🔒 This Raiden card is auto-locked and cannot drop again.")
        else:
            cards = await conn.fetch("SELECT card_id, name, type, file_id, rarity, collection FROM cards WHERE rarity NOT IN ('Birthday Special','Beyond') AND (rarity != 'Raiden' OR is_locked = FALSE)")
            if not cards:
                return await _safe_reply_text(msg, "❌ No cards available in database!")
            card = random.choice(cards)
    await send_character_spawn(context, chat.id, card, msg.message_id)


def normalize_character_answer(value: str) -> str:
    """Normalize character names to avoid false Wrong Name results from formatting differences."""
    value = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", str(value or "")))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    # Ignore invisible formatting characters, case, punctuation, and repeated spaces.
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Cf")
    value = value.casefold().strip()
    return " ".join(re.findall(r"[\w]+", value, flags=re.UNICODE))


@restricted_command
async def _mm_cmd_impl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not await check_force_join(update, context):
        return
    if not context.args:
        return await _safe_reply_text(msg, "Use: /mm character name")
    requested = normalize_character_answer(" ".join(context.args))
    if not requested:
        return await _safe_reply_text(msg, "Use: /mm character name")

    replied_id = (
        msg.reply_to_message.message_id
        if msg.reply_to_message and msg.reply_to_message.message_id else None
    )
    now = time.time()
    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                if replied_id is not None:
                    drop = await conn.fetchrow(
                        "SELECT * FROM active_card_drops WHERE chat_id=$1 AND message_id=$2 FOR UPDATE",
                        chat.id, replied_id
                    )
                else:
                    pending_id = pending_catch_targets.get((chat.id, user.id))
                    drop = None
                    if pending_id is not None:
                        drop = await conn.fetchrow(
                            """SELECT * FROM active_card_drops
                               WHERE chat_id=$1 AND message_id=$2 AND ended=FALSE
                                 AND winner_id IS NULL AND expires_at>$3
                               FOR UPDATE""",
                            chat.id, pending_id, now
                        )
                    if not drop:
                        rows = await conn.fetch(
                            """SELECT * FROM active_card_drops
                               WHERE chat_id=$1 AND ended=FALSE AND winner_id IS NULL AND expires_at>$2
                               ORDER BY message_id DESC FOR UPDATE""",
                            chat.id, now
                        )
                        # Match the typed name against every live spawn before falling back to newest.
                        drop = next(
                            (r for r in rows if normalize_character_answer(r['card_name']) == requested),
                            rows[0] if rows else None
                        )

                # Remember whether this is a database-backed drop before local fallback.
                drop_from_db = drop is not None

                # Backward-compatible local fallback for a spawn created during DB downtime.
                if not drop and replied_id is None:
                    local_items = [
                        (mid, d) for mid, d in active_card_drops.items()
                        if d.get('chat_id') == chat.id and not d.get('ended')
                        and d.get('winner_id') is None and d.get('expires_at', 0) > now
                    ]
                    if local_items:
                        mid, local = next(
                            ((mid, d) for mid, d in reversed(local_items)
                             if normalize_character_answer(d['card'].get('name', '')) == requested),
                            local_items[-1]
                        )
                        c = local['card']
                        drop = {
                            'chat_id': chat.id, 'message_id': mid,
                            'card_id': str(c.get('card_id') or ''), 'card_name': c.get('name') or '',
                            'card_type': c.get('type'), 'file_id': c.get('file_id'),
                            'rarity': c.get('rarity'), 'collection': c.get('collection'),
                            'expires_at': local.get('expires_at', 0), 'winner_id': local.get('winner_id'),
                            'winner_name': local.get('winner_name'), 'ended': local.get('ended', False)
                        }

                if not drop and replied_id is not None:
                    local = active_card_drops.get(replied_id)
                    if local and local.get('chat_id') == chat.id:
                        c = local['card']
                        drop = {
                            'chat_id': chat.id, 'message_id': replied_id,
                            'card_id': str(c.get('card_id') or ''), 'card_name': c.get('name') or '',
                            'card_type': c.get('type'), 'file_id': c.get('file_id'),
                            'rarity': c.get('rarity'), 'collection': c.get('collection'),
                            'expires_at': local.get('expires_at', 0), 'winner_id': local.get('winner_id'),
                            'winner_name': local.get('winner_name'), 'ended': local.get('ended', False)
                        }
                if not drop:
                    return await _safe_reply_text(msg, "❄ No active character spawn right now.")
                if drop['winner_id'] is not None:
                    winner = get_mention(drop['winner_id'], drop['winner_name'] or 'Winner')
                    return await _safe_reply_text(msg, f"Too Late 😭 {winner} got it first. Try next time ❄", parse_mode='HTML')
                if drop['ended'] or float(drop['expires_at']) <= now:
                    return

                expected = normalize_character_answer(drop['card_name'])
                first_name = expected.split()[0] if expected else ''
                if not (requested == expected or (' ' not in requested and requested == first_name)):
                    return await _safe_reply_text(msg, "❌ Wrong character name. Try again!")

                claim_date = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=6, minutes=30)).date()
                daily_claim = await conn.fetchrow(
                    """INSERT INTO daily_spawn_claims (user_id, claim_date, count) VALUES ($1, $2, 1)
                       ON CONFLICT (user_id, claim_date)
                       DO UPDATE SET count = daily_spawn_claims.count + 1
                       WHERE daily_spawn_claims.count < 35 RETURNING count""",
                    user.id, claim_date
                )
                if not daily_claim:
                    return await _safe_reply_text(msg, "⛔ You have reached today's 35 character catch limit. Please wait until tomorrow.")
                await conn.execute(
                    """INSERT INTO user_cards (user_id, card_id, amount) VALUES ($1, $2, 1)
                       ON CONFLICT (user_id, card_id)
                       DO UPDATE SET amount = user_cards.amount + 1""",
                    user.id, drop['card_id']
                )
                await check_and_auto_lock_card(conn, drop['card_id'])
                if drop_from_db:
                    claimed = await conn.fetchrow(
                        """UPDATE active_card_drops
                           SET winner_id=$1, winner_name=$2, ended=TRUE
                           WHERE chat_id=$3 AND message_id=$4
                             AND winner_id IS NULL AND ended=FALSE AND expires_at>$5
                           RETURNING message_id""",
                        user.id, user.first_name, chat.id, drop['message_id'], now
                    )
                    if not claimed:
                        # Roll back the daily counter and card award if another claim won.
                        raise RuntimeError("Character drop was already claimed or expired.")
        drop_id = drop['message_id']
        local_drop = active_card_drops.get(drop_id)
        if local_drop:
            local_drop.update({'winner_id': user.id, 'winner_name': user.first_name, 'ended': True})
        pending_catch_targets.pop((chat.id, user.id), None)
    except Exception as e:
        print(f"Error awarding spawned card: {e}")
        return await _safe_reply_text(msg, "❌ Could not award this card. Please try again.")

    animation_msg = None
    try:
        animation_msg = await context.bot.send_message(chat.id, '🪄')
        await asyncio.sleep(1.5)
        await context.bot.edit_message_text(chat_id=chat.id, message_id=animation_msg.message_id, text='⭐')
        await asyncio.sleep(1.5)
    except Exception as e:
        print(f"Could not run winner animation: {e}")
    finally:
        if animation_msg:
            try:
                await context.bot.delete_message(chat.id, animation_msg.message_id)
            except Exception:
                pass

    card = {
        'card_id': drop['card_id'], 'name': drop['card_name'], 'type': drop['card_type'],
        'file_id': drop['file_id'], 'rarity': drop['rarity'], 'collection': drop['collection']
    }
    rarity = get_rarity_display(card.get('rarity', 'Ten'))
    collection = card.get('collection') or '🎞 Ope Ope No Mi [ 🩺]'
    caption = (f"🐣 Congratulations , {get_mention(user.id, user.first_name)} You got A new Character\n\n"
               f"❀Cᴀʀᴅ - {_plain_text(html.escape(card['name']))}\n         ✿ {card['card_id']}\n"
               f"❀RARITY - {rarity}\n      ✿ {collection}")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton('Harem❄', switch_inline_query_current_chat=f'harem.{user.id}', style="success")]])
    try:
        if card['type'] == 'photo':
            await context.bot.send_photo(chat.id, card['file_id'], caption=caption, parse_mode='HTML', reply_markup=keyboard)
        else:
            await context.bot.send_video(chat.id, card['file_id'], caption=caption, parse_mode='HTML', reply_markup=keyboard)
    except Exception as e:
        print(f"Could not send claimed card: {e}")
        await _safe_reply_text(msg, caption, parse_mode='HTML', reply_markup=keyboard)


async def mm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Serialize claims in a chat so in-memory fallback drops have one winner too."""
    chat = update.effective_chat
    if chat is None:
        return await _mm_cmd_impl(update, context)
    lock = card_claim_locks.setdefault(chat.id, asyncio.Lock())
    async with lock:
        return await _mm_cmd_impl(update, context)


@restricted_command
async def kname_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    replied = msg.reply_to_message
    if not replied or replied.message_id not in active_card_drops:
        return await _safe_reply_text(msg, 'Reply to an active spawned card with /kname.')
    drop = active_card_drops[replied.message_id]
    if drop['ended'] or time.time() >= drop['expires_at']:
        return
    claim_command = f"/mm {drop['card']['name']}"
    display_command = f"/mm {_plain_text(html.escape(drop['card']['name']))}"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 Copy Claim Command", copy_text=CopyTextButton(text=claim_command), style="success")
    ]])
    return await _safe_reply_text(msg, 
        f"Use This - {display_command}",
        parse_mode='HTML', reply_markup=keyboard
    )


async def run_character_drop_timer(context: ContextTypes.DEFAULT_TYPE, msg_id: int):
    drop = active_card_drops.get(msg_id)
    if not drop:
        return
    await asyncio.sleep(max(0, drop['expires_at'] - time.time()))
    chat_id = drop['chat_id']
    expired = False
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE active_card_drops SET ended = TRUE
                   WHERE chat_id = $1 AND message_id = $2
                     AND ended = FALSE AND winner_id IS NULL AND expires_at <= $3
                   RETURNING chat_id""",
                chat_id, msg_id, time.time()
            )
            expired = row is not None
    except Exception as e:
        print(f"Could not finalize character spawn expiry in database: {e}")
        lock = card_drop_locks.setdefault(msg_id, asyncio.Lock())
        async with lock:
            current = active_card_drops.get(msg_id)
            if current and current.get('winner_id') is None and not current.get('ended') and time.time() >= current['expires_at']:
                current['ended'] = True
                expired = True
    if expired:
        try:
            await context.bot.send_message(chat_id, '⏰ အချိန်ပြည့်သွားပါပြီ။ ကဒ်ကို Lock ချလိုက်ပါပြီ။ နောက်ကဒ်ကျမှ ကောက်နိုင်ပါမယ်။')
        except Exception as e:
            print(f"Could not post expired notice: {e}")
    active_card_drops.pop(msg_id, None)
    card_drop_locks.pop(msg_id, None)


@restricted_command

# --- /kcode Command Implementation ---
@restricted_command

# --- User Command /rede ---
@restricted_command

# --- Admin /kclist Command Implementation ---
async def render_kclist_page(update_or_query, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    msg_id = update_or_query.message.message_id if isinstance(update_or_query, Update) and update_or_query.message else None
    async with db_pool.acquire() as conn:
        users = await conn.fetch("SELECT user_id, name, coins FROM users ORDER BY coins DESC, user_id ASC")

    if not users:
        text = "👥 User list is empty."
        if isinstance(update_or_query, Update):
            return await _safe_reply_text(update_or_query.message, text, parse_mode="HTML")
        else:
            return await update_or_query.edit_message_text(text, parse_mode="HTML")

    per_page = 10
    total_users = len(users)
    total_pages = (total_users + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))

    start_idx = page * per_page
    end_idx = start_idx + per_page
    current_users = users[start_idx:end_idx]

    user_text_list = []
    for idx, u in enumerate(current_users, start=start_idx + 1):
        mention = get_mention(u['user_id'], u['name'] or "User")
        user_text_list.append(f"{idx}. {mention} (ID: <code>{u['user_id']}</code>) — {u['coins']} Kachi 🩸")

    users_str = "\n".join(user_text_list)
    text = f"""💰 Kachi Users List (Page {page + 1}/{total_pages})

{users_str}"""

    buttons = []
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Back", callback_data=f"kclist_{page - 1}", style="primary"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"kclist_{page + 1}", style="primary"))
    if nav_row:
        buttons.append(nav_row)

    markup = InlineKeyboardMarkup(buttons) if buttons else None

    if isinstance(update_or_query, Update):
        await _safe_reply_text(update_or_query.message, text, parse_mode="HTML", reply_markup=markup)
    else:
        await update_or_query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)

@restricted_command
async def kclist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_id = update.message.message_id
    if update.effective_user.id != ADMIN_ID:
        return
    await render_kclist_page(update, context, page=0)

@restricted_command
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_id = update.message.message_id
    if update.effective_user.id != ADMIN_ID:
        return

    text = """⚡ Admin Commands

👑 List:
1. /kclist - View Kachi users list with pagination
2. /k [user_id] [amount] - Add/remove Kachi
3. /kcreset [amount] - Reset all users' Kachi balance to specified amount
4. /addadmin [user_id] - Add Card Admin
5. /got [card_id] - Get card directly to your inventory (Admin only)
6. /add - Add new card (reply to media) / /add [user_id] for Birthday Special / Beyond card
7. /unlock [card_id/all] - Unlock Yugen, locked card, or all cards
8. /lock [card_id/all] - Lock card or all cards
9. /spw [card_id] - Spawn random card or specific card
10. /del [card_id_or_name] - Delete card by ID or Name
11. /cardlist - View all cards
12. /seelock - View Yugen cards lock status and drops (Admin only)
13. /glist - View groups list
14. /stats - View stats
15. /broadcast - Broadcast message
16. /check [user_id] - Check user profile info
17. /mute [id] [time] [reason] - Mute user from bot usage
18. /ban [id] [reason] - Ban user from bot usage
19. /umute [id] - Unmute user from bot usage
20. /unban [id] - Unban user from bot usage"""

    await _safe_reply_text(update.effective_message, text, parse_mode="HTML")

@restricted_command
async def kcreset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_id = update.message.message_id
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        return await _safe_reply_text(update.effective_message, "❌ Usage: `/kcreset [amount]`", parse_mode="Markdown")

    try:
        amount = int(context.args[0])
    except ValueError:
        return await _safe_reply_text(update.effective_message, "❌ Invalid amount.", parse_mode="Markdown")

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET coins = $1", amount)

    await _safe_reply_text(update.effective_message, f"✅ Successfully reset all users' Kachi balance to {amount}!", parse_mode="HTML")

def parse_time(time_str: str) -> int:
    time_str = time_str.lower().strip()
    mult = 1
    if time_str.endswith('s'):
        mult = 1
        time_str = time_str[:-1]
    elif time_str.endswith('m'):
        mult = 60
        time_str = time_str[:-1]
    elif time_str.endswith('h'):
        mult = 3600
        time_str = time_str[:-1]
    elif time_str.endswith('d'):
        mult = 86400
        time_str = time_str[:-1]
    try:
        return int(time_str) * mult
    except ValueError:
        return 60

@restricted_command
async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    if user.id != ADMIN_ID:
        return

    if len(context.args) < 2:
        return await _safe_reply_text(msg, "❌ Usage: `/mute [user_id] [time] [reason]`", parse_mode="Markdown")

    try:
        target_id = int(context.args[0])
        time_str = context.args[1]
        reason = " ".join(context.args[2:]) if len(context.args) > 2 else "No reason provided"
    except ValueError:
        return await _safe_reply_text(msg, "❌ Invalid User ID.")

    seconds = parse_time(time_str)
    until_timestamp = int(time.time()) + seconds

    async with db_pool.acquire() as conn:
        user_exists = await conn.fetchrow("SELECT name FROM users WHERE user_id = $1", target_id)
        if not user_exists:
            await conn.execute("INSERT INTO users (user_id, name, coins, muted_until, mute_reason) VALUES ($1, 'User', 1000, $2, $3)", target_id, until_timestamp, reason)
        else:
            await conn.execute("UPDATE users SET muted_until = $1, mute_reason = $2 WHERE user_id = $3", until_timestamp, reason, target_id)

    await _safe_reply_text(msg, 
        f"🔇 Muted User from Bot\n\n👤 User ID: <code>{target_id}</code>\n⏳ Duration: {time_str}\n📝 Reason: {reason}",
        parse_mode="HTML"
    )

@restricted_command
async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    if user.id != ADMIN_ID:
        return

    if not context.args:
        return await _safe_reply_text(msg, "❌ Usage: `/ban [user_id] [reason]`", parse_mode="Markdown")

    try:
        target_id = int(context.args[0])
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason provided"
    except ValueError:
        return await _safe_reply_text(msg, "❌ Invalid User ID.")

    async with db_pool.acquire() as conn:
        user_exists = await conn.fetchrow("SELECT name FROM users WHERE user_id = $1", target_id)
        if not user_exists:
            await conn.execute("INSERT INTO users (user_id, name, coins, banned, ban_reason) VALUES ($1, 'User', 1000, TRUE, $2)", target_id, reason)
        else:
            await conn.execute("UPDATE users SET banned = TRUE, ban_reason = $1 WHERE user_id = $2", reason, target_id)

    await _safe_reply_text(msg, 
        f"🔨 Banned User from Bot\n\n👤 User ID: <code>{target_id}</code>\n📝 Reason: {reason}",
        parse_mode="HTML"
    )

@restricted_command
async def umute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    if user.id != ADMIN_ID:
        return

    if not context.args:
        return await _safe_reply_text(msg, "❌ Usage: `/umute [user_id]`", parse_mode="Markdown")

    try:
        target_id = int(context.args[0])
    except ValueError:
        return await _safe_reply_text(msg, "❌ Invalid User ID.")

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET muted_until = 0, mute_reason = NULL WHERE user_id = $1", target_id)

    await _safe_reply_text(msg, 
        f"🔊 Unmuted User from Bot\n\n👤 User ID: <code>{target_id}</code>",
        parse_mode="HTML"
    )

@restricted_command
async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    if user.id != ADMIN_ID:
        return

    if not context.args:
        return await _safe_reply_text(msg, "❌ Usage: `/unban [user_id]`", parse_mode="Markdown")

    try:
        target_id = int(context.args[0])
    except ValueError:
        return await _safe_reply_text(msg, "❌ Invalid User ID.")

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET banned = FALSE, ban_reason = NULL WHERE user_id = $1", target_id)

    await _safe_reply_text(msg, 
        f"🔓 Unbanned User from Bot\n\n👤 User ID: <code>{target_id}</code>",
        parse_mode="HTML"
    )

@restricted_command
async def kprofile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the caller's Kachi profile in the bot DM, matching the profile layout."""
    user = update.effective_user
    msg = update.effective_message
    chat = update.effective_chat
    if not user or not msg:
        return

    # Show the profile in the chat where /kprofile was invoked (private or group).
    try:
        await context.bot.send_chat_action(chat_id=chat.id, action="typing")
    except Exception:
        pass

    rarity_order = [
        ("Beyond", "♠", "BEYOND"),
        ("Birthday Special", "🎀", "BIRTHDAY"),
        ("Raiden", "📓", "RAIDEN"),
        ("Kryora", "🦠", "KRYORA"),
        ("Yugen", "🎐", "YUGEN"),
        ("Shinsei", "🧬", "SHINSEI"),
        ("Kiwami", "🪞", "KIWAMI"),
        ("Kami", "✨", "KAMI"),
        ("Hoshi", "⚜️", "HOSHI"),
        ("Ten", "💮", "TEN"),
    ]

    async with db_pool.acquire() as conn:
        user_row = await conn.fetchrow(
            "SELECT name, coins, selected_card_id, banned, muted_until FROM users WHERE user_id = $1",
            user.id
        )
        if not user_row:
            await conn.execute(
                "INSERT INTO users (user_id, name, coins) VALUES ($1, $2, 1000) ON CONFLICT (user_id) DO NOTHING",
                user.id, user.first_name or "User"
            )
            user_row = await conn.fetchrow(
                "SELECT name, coins, selected_card_id, banned, muted_until FROM users WHERE user_id = $1",
                user.id
            )

        rarity_rows = await conn.fetch("""
            SELECT c.rarity, COALESCE(SUM(uc.amount), 0) AS amount
            FROM user_cards uc
            JOIN cards c ON c.card_id = uc.card_id
            WHERE uc.user_id = $1
            GROUP BY c.rarity
        """, user.id)
        rarity_counts = {row['rarity'] or 'Ten': int(row['amount'] or 0) for row in rarity_rows}
        total_cards = sum(rarity_counts.values())

        global_rank = await conn.fetchval("""
            SELECT COUNT(*) + 1 FROM (
                SELECT u.user_id, COALESCE(SUM(uc.amount), 0) AS total_cards
                FROM users u LEFT JOIN user_cards uc ON u.user_id = uc.user_id
                GROUP BY u.user_id
            ) ranked
            WHERE ranked.total_cards > (
                SELECT COALESCE(SUM(amount), 0) FROM user_cards WHERE user_id = $1
            )
        """, user.id)

        group_rank = None
        if chat and chat.type in ("group", "supergroup"):
            group_rank = await conn.fetchval("""
                SELECT COUNT(*) + 1 FROM (
                    SELECT gm.user_id, COALESCE(SUM(uc.amount), 0) AS total_cards
                    FROM group_members gm
                    LEFT JOIN user_cards uc ON uc.user_id = gm.user_id
                    WHERE gm.chat_id = $1
                    GROUP BY gm.user_id
                ) ranked
                WHERE ranked.total_cards > COALESCE((
                    SELECT SUM(uc.amount)
                    FROM group_members gm2
                    LEFT JOIN user_cards uc ON uc.user_id = gm2.user_id
                    WHERE gm2.chat_id = $1 AND gm2.user_id = $2
                    GROUP BY gm2.user_id
                ), 0)
            """, chat.id, user.id)

        fav_card = None
        selected_id = user_row['selected_card_id'] if user_row else None
        if selected_id:
            fav_card = await conn.fetchrow(
                "SELECT name, card_id FROM cards WHERE card_id = $1", selected_id
            )
        if not fav_card:
            fav_card = await conn.fetchrow("""
                SELECT c.name, c.card_id
                FROM user_cards uc JOIN cards c ON c.card_id = uc.card_id
                WHERE uc.user_id = $1
                ORDER BY uc.amount DESC, c.card_id ASC LIMIT 1
            """, user.id)

    display_name = user.first_name or (user_row['name'] if user_row else None) or "User"
    name_line = get_mention(user.id, display_name)
    ban_status = "Yes" if user_row and user_row['banned'] else "No"
    muted_until = int(user_row['muted_until'] or 0) if user_row else 0
    mute_status = "Yes" if muted_until > int(time.time()) else "No"
    spam_status = await get_user_restriction_status(user.id)
    spam_line = "🚫 Spam Restriction - No"
    if spam_status and spam_status[0] == "spam":
        remaining = max(0, spam_status[1] - int(time.time()))
        mins, secs = divmod(remaining, 60)
        spam_line = f"🚫 Spam Restriction - Yes | ⏳ {mins}m {secs}s remaining"

    lines = [
        "❄️ Kachi USER profile",
        "━━━━━━━━━━━━━━━━━━",
        f"◉▷ Name - {name_line}",
        f"◉▷ ID - <code>{user.id}</code>",
        f"◉▷ Total Kachi Character - {total_cards}",
        "━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for rarity, emoji, label in rarity_order:
        lines.append(f"❂ <i>Rarity</i> {emoji} : {label} - {rarity_counts.get(rarity, 0)}")
    lines.extend([
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"🚫 Ban - {ban_status}",
        f"⭕ Mute - {mute_status}",
        spam_line,
        "",
        f"❀ Fav Card - {html.escape(fav_card['name'])} (ID: {fav_card['card_id']})" if fav_card else "❀ Fav Card - —",
        "",
        f"✪ Global Top ( ) #{global_rank or 1} ❄️",
        f"✪ Group Top ( ) #{group_rank if group_rank is not None else '—'} ❄️",
    ])
    profile_text = "<blockquote>" + "\n".join(lines) + "</blockquote>"

    try:
        profile_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton('Harem❄', switch_inline_query_current_chat=f'harem.{user.id}', style="success")]])
        photos = await context.bot.get_user_profile_photos(user.id, limit=1)
        if photos.total_count and photos.photos:
            await context.bot.send_photo(
                chat_id=chat.id, photo=photos.photos[0][-1].file_id, caption=profile_text,
                parse_mode="HTML", reply_markup=profile_keyboard
            )
        else:
            await context.bot.send_message(
                chat_id=chat.id, text=profile_text, parse_mode="HTML",
                reply_markup=profile_keyboard
            )
    except Exception as e:
        print(f"Could not send /kprofile in chat {chat.id} for {user.id}: {type(e).__name__}: {e}")
        try:
            await _safe_reply_text(msg, 
                "❌ I couldn't post your profile here. Please check that the bot has permission to send messages."
            )
        except Exception:
            pass


@restricted_command
async def check_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    if user.id != ADMIN_ID:
        return

    if not context.args:
        return await _safe_reply_text(msg, "❌ Usage: `/check [user_id]`", parse_mode="Markdown")

    try:
        target_id = int(context.args[0])
    except ValueError:
        return await _safe_reply_text(msg, "❌ Invalid User ID.")

    async with db_pool.acquire() as conn:
        user_row = await conn.fetchrow("SELECT coins, selected_card_id, name FROM users WHERE user_id = $1", target_id)
        if not user_row:
            return await _safe_reply_text(msg, f"❌ User ID <code>{target_id}</code> not found in database.", parse_mode="HTML")

        coins = user_row['coins'] or 1000
        selected_card_id = user_row['selected_card_id']
        db_name = user_row['name'] or "User"

        rank_row = await conn.fetchrow("""
            SELECT COUNT(*) + 1 AS rank FROM (
                SELECT u.user_id, COALESCE(SUM(uc.amount), 0) as tc
                FROM users u
                LEFT JOIN user_cards uc ON u.user_id = uc.user_id
                GROUP BY u.user_id
            ) sub WHERE tc > (
                SELECT COALESCE(SUM(amount), 0) FROM user_cards WHERE user_id = $1
            )
        """, target_id)
        rank = rank_row['rank'] if rank_row else 1

        user_card_rows = await conn.fetch("""
            SELECT c.card_id, c.name, c.type, c.file_id, c.rarity, c.collection, uc.amount 
            FROM user_cards uc 
            JOIN cards c ON uc.card_id = c.card_id 
            WHERE uc.user_id = $1 AND uc.amount > 0
            ORDER BY c.card_id ASC
        """, target_id)

    username_str = "None"
    try:
        chat_obj = await context.bot.get_chat(target_id)
        if chat_obj.username:
            username_str = f"@{chat_obj.username}"
        if chat_obj.first_name:
            db_name = chat_obj.first_name
    except Exception:
        pass

    text_header = f"""❀ Name - {get_mention(target_id, db_name)}
🌐 Global Rank - #{rank}
🏷 Username - {username_str}
⬤ ID - <code>{target_id}</code>
🩸 Kachi - {coins}"""

    cards_info_list = []
    for c in user_card_rows[:5]:
        count_str = f" ({c['amount']}x)" if c['amount'] > 1 else ""
        rarity_emoji = get_rarity_emoji(c.get('rarity'))
        cards_info_list.append(f"{rarity_emoji} {c['name']} ( <code>{c['card_id']}</code> ){count_str}")

    cards_info = "\n".join(cards_info_list) if cards_info_list else "<i>No cards yet.</i>"
    full_text = f"{text_header}\n\n❄ Owned Cards\n\n{cards_info}"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Harem❄", switch_inline_query_current_chat=f"harem.{target_id}", style="success")]
    ])

    active_card = None
    if selected_card_id:
        active_card = next((c for c in user_card_rows if c['card_id'] == selected_card_id), None)
    if not active_card and user_card_rows:
        active_card = user_card_rows[-1]

    try:
        if active_card and active_card['type'] == 'photo':
            await _safe_reply_photo(msg, photo=active_card['file_id'], caption=full_text, parse_mode="HTML", reply_markup=keyboard)
        elif active_card and active_card['type'] == 'video':
            await _safe_reply_video(msg, video=active_card['file_id'], caption=full_text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await _safe_reply_text(msg, full_text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await _safe_reply_text(msg, full_text, parse_mode="HTML", reply_markup=keyboard)

@restricted_command

@restricted_command
async def ktop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    msg_id = update.message.message_id
    async with db_pool.acquire() as conn:
        top_users = await conn.fetch("""
            SELECT u.user_id, u.name, COALESCE(SUM(uc.amount), 0) AS total_cards
            FROM users u
            LEFT JOIN user_cards uc ON u.user_id = uc.user_id
            GROUP BY u.user_id, u.name
            ORDER BY total_cards DESC, u.coins DESC
            LIMIT 10
        """)

    lines = [
        "KTOP GLOBAL🌍 ( Leaderboard ) 🎖",
        "▰▱▰▱▰▱▰▱▰▱▰",
        ""
    ]
    medals = ["🥇", "🥈", "🥉"]
    for i in range(10):
        prefix = medals[i] if i < 3 else "☛"
        if i < len(top_users):
            r = top_users[i]
            mention = get_mention(r['user_id'], r['name'])
            lines.append(f"{prefix} {mention} - {r['total_cards']} Cards")
        else:
            lines.append(f"{prefix}")
    lines.append("")
    lines.append("▰▱▰▱▰▱▰▱▰▱▰")
    text = "\n".join(lines)
    await _safe_reply_text(update.effective_message, text, parse_mode="HTML")

@restricted_command
async def kgtop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    chat = update.effective_chat
    msg_id = update.message.message_id
    if chat.type not in ["group", "supergroup"]:
        return await _safe_reply_text(update.effective_message, "❌ This command can only be used in groups.")

    async with db_pool.acquire() as conn:
        top_users = await conn.fetch("""
            SELECT u.user_id, u.name, COALESCE(SUM(uc.amount), 0) AS total_cards
            FROM users u
            JOIN group_members gm ON u.user_id = gm.user_id
            LEFT JOIN user_cards uc ON u.user_id = uc.user_id
            WHERE gm.chat_id = $1
            GROUP BY u.user_id, u.name
            ORDER BY total_cards DESC, u.coins DESC
            LIMIT 10
        """, chat.id)

    lines = [
        "KTOP IN GROUP🦊",
        "    ( Leaderboard ) 🎖",
        "▰▱▰▱▰▱▰▱▰▱▰▱",
        ""
    ]
    medals = ["🥇", "🥈", "🥉"]
    for i in range(10):
        prefix = medals[i] if i < 3 else "☛"
        if i < len(top_users):
            r = top_users[i]
            mention = get_mention(r['user_id'], r['name'])
            lines.append(f"{prefix} {mention} - {r['total_cards']} Cards")
        else:
            lines.append(f"{prefix}")
    lines.append("▰▱▰▱▰▱▰▱▰▱▰▱")
    text = "\n".join(lines)
    await _safe_reply_text(update.effective_message, text, parse_mode="HTML")

@restricted_command
async def kg_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    msg_id = update.message.message_id
    now = int(time.time())
    cooldown = 86400

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            setting_row = await conn.fetchrow("SELECT value FROM settings WHERE key = 'last_kg_reset' FOR UPDATE")
            last_kg_reset = int(setting_row['value']) if setting_row else 0

            if now - last_kg_reset >= cooldown:
                top_winners = await conn.fetch("""
                    SELECT user_id, daily_kg_win FROM users 
                    WHERE daily_kg_win > 0 
                    ORDER BY daily_kg_win DESC 
                    LIMIT 8
                """)

                prizes = [15000, 8000, 5000, 3000, 2000, 2000, 1500, 1500]
                for idx, winner in enumerate(top_winners):
                    prize = prizes[idx]
                    await conn.execute("UPDATE users SET coins = coins + $1 WHERE user_id = $2", prize, winner['user_id'])
                    try:
                        asyncio.create_task(context.bot.send_message(
                            winner['user_id'],
                            f"🏆 Daily KGAME Top Winner!\n\n"
                            f"Congratulations! You ranked #{idx+1} in Daily KGAME Top.\n"
                            f"🎁 Reward: +{prize} Kachi 🩸",
                            parse_mode="HTML"
                        ))
                    except Exception:
                        pass

                await conn.execute("UPDATE users SET daily_kg_win = 0")
                if setting_row:
                    await conn.execute("UPDATE settings SET value = $1 WHERE key = 'last_kg_reset'", str(now))
                else:
                    await conn.execute("INSERT INTO settings (key, value) VALUES ('last_kg_reset', $1)", str(now))

        top_users = await conn.fetch("""
            SELECT user_id, name, daily_kg_win FROM users
            ORDER BY daily_kg_win DESC, coins DESC
            LIMIT 15
        """)

    lines = [
        "DAILY 🌍TOP -15-  KGAME,  PLAYERS",
        "",
        "Players              Total Kachi Win",
        "▰▱▰▱▰▱▰▱▰▱▰"
    ]
    medals = ["🥇", "🥈", "🥉"]
    for i in range(15):
        prefix = medals[i] if i < 3 else "☞"
        if i < len(top_users):
            r = top_users[i]
            mention = get_mention(r['user_id'], r['name'])
            lines.append(f"{prefix} {mention} - {r['daily_kg_win']} Kachi")
        else:
            lines.append(f"{prefix}")
    lines.append("▰▱▰▱▰▱▰▱▰▱▰")
    text = "\n".join(lines)
    await _safe_reply_text(update.effective_message, text, parse_mode="HTML")

async def render_tdc_page(bot, chat_id, claim_date, page):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT u.user_id, COALESCE(u.name, 'Catcher') AS name,
                   SUM(d.count)::BIGINT AS total_catches
            FROM daily_spawn_claims d JOIN users u ON u.user_id = d.user_id
            WHERE d.claim_date = $1 AND d.count > 0
            GROUP BY u.user_id, u.name
            ORDER BY total_catches DESC, u.user_id ASC
        """, claim_date)
    page_size=15; pages=max(1,(len(rows)+page_size-1)//page_size); page=max(0,min(page,pages-1))
    lines=["🏆 DAILY CATCHER",f"📅 {claim_date.strftime('%d %b %Y')}","","Successful Drop Catchers","▰▱▰▱▰▱▰▱▰▱▰"]
    medals=["🥇","🥈","🥉"]
    chunk=rows[page*page_size:(page+1)*page_size]
    for i,row in enumerate(chunk):
        rank=page*page_size+i; prefix=medals[rank] if rank<3 else "☞"
        lines.append(f"{prefix} {get_mention(row['user_id'],row['name'])} — {row['total_catches']} catches")
    lines.extend(["▰▱▰▱▰▱▰▱▰▱▰",f"Page {page+1}/{pages}","Only successful automatic card drops are counted. /pre and /kbox are excluded."])
    nav=[]
    if page>0: nav.append(InlineKeyboardButton("◀️ Back",callback_data=f"tdcpage_{claim_date.isoformat()}_{page-1}"))
    if page+1<pages: nav.append(InlineKeyboardButton("Next ▶️",callback_data=f"tdcpage_{claim_date.isoformat()}_{page+1}"))
    return await bot.send_message(chat_id,"\n".join(lines),parse_mode="HTML",reply_markup=InlineKeyboardMarkup([nav]) if nav else None)

@restricted_command
async def tdc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context): return
    claim_date=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=6, minutes=30)).date()
    return await render_tdc_page(context.bot,update.effective_chat.id,claim_date,0)


@restricted_command
async def reveal_spawn_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    replied = msg.reply_to_message if msg else None
    if not replied or replied.message_id not in active_card_drops:
        return await _safe_reply_text(msg, "Reply to an active spawned card with .k or /k to reveal its name.")
    drop = active_card_drops[replied.message_id]
    if drop['ended'] or time.time() >= drop['expires_at']:
        return
    pending_catch_targets[(msg.chat_id, update.effective_user.id)] = replied.message_id
    claim_command = f"/mm {drop['card']['name']}"
    display_command = f"/mm {_plain_text(html.escape(drop['card']['name']))}"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📋 Copy Name", copy_text=CopyTextButton(text=claim_command), style="success")]])
    return await _safe_reply_text(msg, 
        f"Use This - {display_command}",
        parse_mode='HTML', reply_markup=keyboard
    )


async def admin_coin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_id = update.message.message_id
    if update.effective_user.id != ADMIN_ID:
        return

    try:
        target_id = int(context.args[0])
        amount = int(context.args[1])

        async with db_pool.acquire() as conn:
            async with conn.transaction():
                user_exists = await conn.fetchrow("SELECT name FROM users WHERE user_id = $1 FOR UPDATE", target_id)
                if not user_exists:
                    await conn.execute("INSERT INTO users (user_id, name, coins) VALUES ($1, $2, 1000)", target_id, "User")
                
                await conn.execute("""
                    UPDATE users 
                    SET coins = GREATEST(0, coins + $1) 
                    WHERE user_id = $2
                """, amount, target_id)
                
                new_row = await conn.fetchrow("SELECT coins, name FROM users WHERE user_id = $1", target_id)

        action_text = "added" if amount >= 0 else "deducted"
        await _safe_reply_text(update.effective_message, 
            f"✅ {abs(amount)} Kachi {action_text} to {get_mention(target_id, new_row['name'])}.\n"
            f"💰 Current Kachi: {new_row['coins']}",
            parse_mode="HTML"
        )
    except (IndexError, ValueError):
        await _safe_reply_text(update.effective_message, "❌ Usage: `/kac [user_id] [amount]`", parse_mode="Markdown")

@restricted_command
async def add_card_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_id = update.message.message_id
    user = update.effective_user

    async with db_pool.acquire() as conn:
        u_info = await conn.fetchrow("SELECT is_card_admin FROM users WHERE user_id = $1", user.id)
        is_card_admin = u_info['is_card_admin'] if u_info else False

    if user.id != ADMIN_ID and not is_card_admin:
        return

    reply = update.message.reply_to_message
    if not reply or (not reply.photo and not reply.video):
        return await _safe_reply_text(update.effective_message, "❌ Reply to a photo or video with `/add [name]` or `/add [user_id]` (for Birthday Special / Beyond)", parse_mode="Markdown")

    raw_args = update.message.text.partition(" ")[2].strip()
    if not raw_args:
        return await _safe_reply_text(update.effective_message, "❌ Usage: Reply to media with `/add [name]` or `/add [user_id]`", parse_mode="Markdown")

    is_birthday_add = False
    is_beyond_add = False
    target_user_id = None
    custom_card_id = None

    space_parts = raw_args.split()
    dot_parts = [p.strip() for p in raw_args.split(".")]

    if raw_args.lower() == "beyond":
        is_beyond_add = True
    elif raw_args.isdigit() and len(raw_args) >= 8:
        is_birthday_add = True
        target_user_id = int(raw_args)
    elif len(space_parts) == 2 and (space_parts[0].isdigit() and len(space_parts[0]) >= 8 or space_parts[1].isdigit() and len(space_parts[1]) >= 8):
        is_birthday_add = True
        p1, p2 = space_parts[0], space_parts[1]
        target_user_id = int(p1) if len(p1) >= 8 else int(p2)
        custom_card_id = p2 if len(p1) >= 8 else p1
    elif len(space_parts) == 2 and space_parts[0].lower() == "beyond":
        is_beyond_add = True
        custom_card_id = space_parts[1]
    elif len(dot_parts) == 2 and dot_parts[0].isdigit() and dot_parts[1].isdigit():
        p1, p2 = dot_parts[0], dot_parts[1]
        if len(p1) >= 8 or len(p2) >= 8:
            is_birthday_add = True
            target_user_id = int(p1) if len(p1) >= 8 else int(p2)
            custom_card_id = p2 if len(p1) >= 8 else p1

    if is_beyond_add:
        if user.id != ADMIN_ID:
            return await _safe_reply_text(update.effective_message, "❌ Only the Bot Admin can add ♠Beyond rarity cards!")
        
        card_name = f"♠Beyond"
        rarity = "Beyond"

        if custom_card_id:
            card_id = custom_card_id
            async with db_pool.acquire() as conn:
                existing_c = await conn.fetchrow("SELECT card_id FROM cards WHERE card_id = $1", card_id)
                if existing_c:
                    return await _safe_reply_text(update.effective_message, f"⚠️ Card ID (<code>{card_id}</code>) already exists.", parse_mode="HTML")
        else:
            async with db_pool.acquire() as conn:
                while True:
                    rand_id = str(random.randint(1000, 9999))
                    exists = await conn.fetchrow("SELECT card_id FROM cards WHERE card_id = $1", rand_id)
                    if not exists:
                        card_id = rand_id
                        break

        if reply.photo:
            file_id = reply.photo[-1].file_id
            c_type = "photo"
        else:
            file_id = reply.video.file_id
            c_type = "video"
        if c_type == "video":
            return await _safe_reply_text(update.effective_message, "❌ Video Card ကို Kryora မှာသာ Add လုပ်နိုင်ပါတယ်။ /add [name] နဲ့ ပုံမှန် Add လုပ်ပါ။")

        collections_list = [
            "🎞 Ope Ope No Mi [ 🩺]",
            "🎞 Hito Hito No Mi [🌕]",
            "🎞 Mera Mera No Mi [🔥]",
            "🎞 Hana Hana No Mi [🌸]",
            "🎞 Yomi Yomi No Mi [💀]",
            "🎞 Tori Tori No Mi [🦅]",
            "🎞 Yami Yami No Mi [🌑]",
            "🎞 Sora Sora No Mi [☁️]",
            "🎞 Kaze Kaze No Mi [🌪️]",
            "🎞 Mizu Mizu No Mi [💧]",
            "🎞 Kori Kori No Mi [❄️]",
            "🎞 Tsuchi Tsuchi No Mi [🪨]",
            "🎞 Kumo Kumo No Mi [🕸️]",
            "🎞 Hikari Hikari No Mi [✨]",
            "🎞 Kage Kage No Mi [🌘]",
            "🎞 Rai Rai No Mi [⚡]",
            "🎞 Niji Niji No Mi [🌈]",
            "🎞 Tsuki Tsuki No Mi [🌙]",
            "🎞 Taiyo Taiyo No Mi [☀️]",
            "🎞 Mori Mori No Mi [🌳]",
            "🎞 Bara Bara No Mi [🎈]",
            "🎞 Toge Toge No Mi [🌵]",
            "🎞 Hanaumi No Mi [🪷]",
            "🎞 Kiba Kiba No Mi [🦷]",
            "🎞 Hebi Hebi No Mi [🐍]",
            "🎞 Neko Neko No Mi [🐈]",
            "🎞 Inu Inu No Mi [🐕]",
            "🎞 Kuma Kuma No Mi [🐻]",
            "🎞 Kitsune Kitsune No Mi [🦊]",
            "🎞 Tatsu Tatsu No Mi [🐉]",
            "🎞 Kame Kame No Mi [🐢]",
            "🎞 Hoshi Hoshi No Mi [⭐]",
            "🎞 Umi Umi No Mi [🌊]",
            "🎞 Shio Shio No Mi [🧂]",
            "🎞 Ame Ame No Mi [🌧️]",
            "🎞 Yuki Yuki No Mi [🌨️]",
            "🎞 Kiri Kiri No Mi [🌫️]",
            "🎞 Goro Goro No Mi [🔔]",
            "🎞 Hibana Hibana No Mi [🧨]",
            "🎞 Kemuri Kemuri No Mi [💨]",
            "🎞 Mushi Mushi No Mi [🦋]",
            "🎞 Tama Tama No Mi [🥚]",
            "🎞 Kuri Kuri No Mi [🌰]",
            "🎞 Suna Suna No Mi [🏜️]",
            "🎞 Ishi Ishi No Mi [💎]",
            "🎞 Gami Gami No Mi [📜]",
            "🎞 Oni Oni No Mi [👹]",
            "🎞 Tenshi Tenshi No Mi [👼]",
            "🎞 Shishi Shishi No Mi [🦁]",
            "🎞 Ryu Ryu No Mi [🦖]",
            "🎞 Fukurou Fukurou No Mi [🦉]",
            "🎞 Chou Chou No Mi [🐝]",
            "🎞 Sakana Sakana No Mi [🐟]",
            "🎞 Tako Tako No Mi [🐙]",
            "🎞 Kani Kani No Mi [🦀]",
            "🎞 Shima Shima No Mi [🏝️]",
            "🎞 Yume Yume No Mi [💭]"
        ]
        selected_collection = random.choice(collections_list)

        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO cards (card_id, name, type, file_id, rarity, collection, is_locked)
                    VALUES ($1, $2, $3, $4, $5, $6, TRUE)
                """, card_id, card_name, c_type, file_id, rarity, selected_collection)

        r_display = get_rarity_display(rarity)
        log_text = f"""New ♠Beyond card added successfully

❀ Card Name : {card_name}
     ❝  ID : {card_id}
❀ Type : {c_type.capitalize()}
🌟 Rarity : {r_display}
📚 Collection : {selected_collection}
👤 Added By : {get_mention(user.id, user.first_name)}"""

        try:
            if c_type == "photo":
                asyncio.create_task(context.bot.send_photo(chat_id=CARD_LOG_CHANNEL_ID, photo=file_id, caption=log_text, parse_mode="HTML"))
            else:
                asyncio.create_task(context.bot.send_video(chat_id=CARD_LOG_CHANNEL_ID, video=file_id, caption=log_text, parse_mode="HTML"))
        except Exception:
            pass

        return await _safe_reply_text(update.effective_message, 
            f"✅ ♠Beyond rarity card added successfully (Locked by default, Admin sales only)!\n\n🆔 Card ID: <code>{card_id}</code>\n🎴 Name: {card_name}\n🌟 Rarity: {r_display}\n📚 Collection: {selected_collection}",
            parse_mode="HTML"
        )

    if is_birthday_add:
        if user.id != ADMIN_ID:
            return await _safe_reply_text(update.effective_message, "❌ Only the Bot Admin can add Birthday Special cards!")
        
        async with db_pool.acquire() as conn:
            u_row = await conn.fetchrow("SELECT name FROM users WHERE user_id = $1", target_user_id)
        user_name = u_row['name'] if u_row else "User"
        try:
            chat_obj = await context.bot.get_chat(target_user_id)
            if chat_obj.first_name:
                user_name = chat_obj.first_name
        except Exception:
            pass

        user_mention = get_mention(target_user_id, user_name)
        card_name = f"{user_mention} 🎀ʙɪʀᴛʜᴅᴀʏ sᴘᴇᴄɪᴀʟ🧸"
        rarity = "Birthday Special"

        if custom_card_id:
            card_id = custom_card_id
            async with db_pool.acquire() as conn:
                existing_c = await conn.fetchrow("SELECT card_id FROM cards WHERE card_id = $1", card_id)
                if existing_c:
                    return await _safe_reply_text(update.effective_message, f"⚠️ Card ID (<code>{card_id}</code>) already exists.", parse_mode="HTML")
        else:
            async with db_pool.acquire() as conn:
                while True:
                    rand_id = str(random.randint(1000, 9999))
                    exists = await conn.fetchrow("SELECT card_id FROM cards WHERE card_id = $1", rand_id)
                    if not exists:
                        card_id = rand_id
                        break

        if reply.photo:
            file_id = reply.photo[-1].file_id
            c_type = "photo"
        else:
            file_id = reply.video.file_id
            c_type = "video"
        if c_type == "video":
            return await _safe_reply_text(update.effective_message, "❌ Video Card ကို Kryora မှာသာ Add လုပ်နိုင်ပါတယ်။ /add [name] နဲ့ ပုံမှန် Add လုပ်ပါ။")

        collections_list = [
            "🎞 Ope Ope No Mi [ 🩺]",
            "🎞 Hito Hito No Mi [🌕]",
            "🎞 Mera Mera No Mi [🔥]",
            "🎞 Hana Hana No Mi [🌸]",
            "🎞 Yomi Yomi No Mi [💀]",
            "🎞 Tori Tori No Mi [🦅]",
            "🎞 Yami Yami No Mi [🌑]",
            "🎞 Sora Sora No Mi [☁️]",
            "🎞 Kaze Kaze No Mi [🌪️]",
            "🎞 Mizu Mizu No Mi [💧]",
            "🎞 Kori Kori No Mi [❄️]",
            "🎞 Tsuchi Tsuchi No Mi [🪨]",
            "🎞 Kumo Kumo No Mi [🕸️]",
            "🎞 Hikari Hikari No Mi [✨]",
            "🎞 Kage Kage No Mi [🌘]",
            "🎞 Rai Rai No Mi [⚡]",
            "🎞 Niji Niji No Mi [🌈]",
            "🎞 Tsuki Tsuki No Mi [🌙]",
            "🎞 Taiyo Taiyo No Mi [☀️]",
            "🎞 Mori Mori No Mi [🌳]",
            "🎞 Bara Bara No Mi [🎈]",
            "🎞 Toge Toge No Mi [🌵]",
            "🎞 Hanaumi No Mi [🪷]",
            "🎞 Kiba Kiba No Mi [🦷]",
            "🎞 Hebi Hebi No Mi [🐍]",
            "🎞 Neko Neko No Mi [🐈]",
            "🎞 Inu Inu No Mi [🐕]",
            "🎞 Kuma Kuma No Mi [🐻]",
            "🎞 Kitsune Kitsune No Mi [🦊]",
            "🎞 Tatsu Tatsu No Mi [🐉]",
            "🎞 Kame Kame No Mi [🐢]",
            "🎞 Hoshi Hoshi No Mi [⭐]",
            "🎞 Umi Umi No Mi [🌊]",
            "🎞 Shio Shio No Mi [🧂]",
            "🎞 Ame Ame No Mi [🌧️]",
            "🎞 Yuki Yuki No Mi [🌨️]",
            "🎞 Kiri Kiri No Mi [🌫️]",
            "🎞 Goro Goro No Mi [🔔]",
            "🎞 Hibana Hibana No Mi [🧨]",
            "🎞 Kemuri Kemuri No Mi [💨]",
            "🎞 Mushi Mushi No Mi [🦋]",
            "🎞 Tama Tama No Mi [🥚]",
            "🎞 Kuri Kuri No Mi [🌰]",
            "🎞 Suna Suna No Mi [🏜️]",
            "🎞 Ishi Ishi No Mi [💎]",
            "🎞 Gami Gami No Mi [📜]",
            "🎞 Oni Oni No Mi [👹]",
            "🎞 Tenshi Tenshi No Mi [👼]",
            "🎞 Shishi Shishi No Mi [🦁]",
            "🎞 Ryu Ryu No Mi [🦖]",
            "🎞 Fukurou Fukurou No Mi [🦉]",
            "🎞 Chou Chou No Mi [🐝]",
            "🎞 Sakana Sakana No Mi [🐟]",
            "🎞 Tako Tako No Mi [🐙]",
            "🎞 Kani Kani No Mi [🦀]",
            "🎞 Shima Shima No Mi [🏝️]",
            "🎞 Yume Yume No Mi [💭]"
        ]
        selected_collection = random.choice(collections_list)

        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO cards (card_id, name, type, file_id, rarity, collection, is_locked)
                    VALUES ($1, $2, $3, $4, $5, $6, FALSE)
                """, card_id, card_name, c_type, file_id, rarity, selected_collection)

        r_display = get_rarity_display(rarity)
        log_text = f"""New Birthday Special card added successfully

❀ Card Name : {card_name}
     ❝  ID : {card_id}
❀ Type : {c_type.capitalize()}
🌟 Rarity : {r_display}
📚 Collection : {selected_collection}
👤 Added By : {get_mention(user.id, user.first_name)}"""

        try:
            if c_type == "photo":
                asyncio.create_task(context.bot.send_photo(chat_id=CARD_LOG_CHANNEL_ID, photo=file_id, caption=log_text, parse_mode="HTML"))
            else:
                asyncio.create_task(context.bot.send_video(chat_id=CARD_LOG_CHANNEL_ID, video=file_id, caption=log_text, parse_mode="HTML"))
        except Exception:
            pass

        return await _safe_reply_text(update.effective_message, 
            f"✅ Birthday Special card added successfully!\n\n🆔 Card ID: <code>{card_id}</code>\n🎴 Name: {card_name}\n🌟 Rarity: {r_display}\n📚 Collection: {selected_collection}",
            parse_mode="HTML"
        )

    # Restriction: Other Card Admins can only add up to Kryora (Ten, Hoshi, Kami, Kiwami, Shinsei, Yugen, Kryora)
    # Raiden, Birthday Special, Beyond are restricted to Main Admin ID only.
    card_id = None
    card_name = None

    if "." in raw_args:
        part1, _, part2 = raw_args.partition(".")
        card_id = part1.strip()
        card_name = part2.strip()
        
        if any(c.isalpha() for c in card_id):
            return await _safe_reply_text(update.effective_message, 
                "❌ Card ID ထဲတွင် English စာလုံးများ (Letters) ပါဝင်၍ မရပါ။ နံပတ် (Numbers) သာ ဖြစ်ရပါမည် (ဥပမာ - 02873)。",
                parse_mode="HTML"
            )
    else:
        async with db_pool.acquire() as conn:
            while True:
                rand_id = str(random.randint(1000, 9999))
                exists = await conn.fetchrow("SELECT card_id FROM cards WHERE card_id = $1", rand_id)
                if not exists:
                    card_id = rand_id
                    break
        card_name = raw_args

    if reply.photo:
        file_id = reply.photo[-1].file_id
        c_type = "photo"
    else:
        file_id = reply.video.file_id
        c_type = "video"

    async with db_pool.acquire() as conn:
        existing_card = await conn.fetchrow("SELECT card_id FROM cards WHERE card_id = $1 OR file_id = $2", card_id, file_id)
        if existing_card:
            return await _safe_reply_text(update.effective_message, f"⚠️ Card ID (<code>{card_id}</code>) or media already exists.", parse_mode="HTML")

    pending_card_adds[user.id] = {
        "card_id": card_id,
        "card_name": card_name,
        "type": c_type,
        "file_id": file_id,
        "is_card_admin": is_card_admin
    }
    # Persist the pending add so the rarity buttons still work after Railway restarts.
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO card_add_sessions (user_id, session_type, payload, created_at)
               VALUES ($1, 'regular', $2, NOW())
               ON CONFLICT (user_id) DO UPDATE SET
                   session_type = EXCLUDED.session_type,
                   payload = EXCLUDED.payload,
                   created_at = NOW()""",
            user.id, json.dumps(pending_card_adds[user.id], ensure_ascii=False)
        )

    # Other Card Admins can only add up to Kryora
    keyboard_rows = [
        [InlineKeyboardButton("🎐 Yugen", callback_data=f"addrarity_{user.id}_Yugen", style="primary")],
        [
            InlineKeyboardButton("🧬 Shinsei", callback_data=f"addrarity_{user.id}_Shinsei", style="primary"),
            InlineKeyboardButton("🪞 Kiwami", callback_data=f"addrarity_{user.id}_Kiwami", style="primary")
        ],
        [
            InlineKeyboardButton("✨ Kami", callback_data=f"addrarity_{user.id}_Kami", style="primary"),
            InlineKeyboardButton("⚜️ Hoshi", callback_data=f"addrarity_{user.id}_Hoshi", style="primary")
        ],
        [
            InlineKeyboardButton("💮 Ten", callback_data=f"addrarity_{user.id}_Ten", style="primary")
        ]
    ]
    if c_type == "video":
        keyboard_rows.insert(0, [InlineKeyboardButton("🦠 Kryora (Video Only)", callback_data=f"addrarity_{user.id}_Kryora", style="primary")])
    if user.id == ADMIN_ID and c_type != "video":
        keyboard_rows.insert(0, [InlineKeyboardButton("♠ Beyond", callback_data=f"addrarity_{user.id}_Beyond", style="primary")])
        keyboard_rows.insert(1, [InlineKeyboardButton("📓 Raiden", callback_data=f"addrarity_{user.id}_Raiden", style="primary")])
        keyboard_rows.insert(2, [InlineKeyboardButton("🎀 Birthday Special", callback_data=f"addrarity_{user.id}_Birthday Special", style="primary")])

    await _safe_reply_text(update.effective_message, 
        f"🎴 Card Info Ready:\n"
        f"🆔 ID: <code>{card_id}</code>\n"
        f"🏷 Name: {card_name}\n\n"
        f"Please select rarity:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard_rows)
    )



@restricted_command
async def import_forwarded_card_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Import one forwarded card-log media message while preserving its original card ID."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or msg.chat.type != "private":
        return
    if user.id != ADMIN_ID:
        return

    # Accept only forwarded media messages; ordinary photos/videos are ignored.
    is_forwarded = bool(
        getattr(msg, "forward_date", None)
        or getattr(msg, "forward_from", None)
        or getattr(msg, "forward_from_chat", None)
        or getattr(msg, "forward_sender_name", None)
        or getattr(msg, "forward_origin", None)
    )
    if not is_forwarded:
        return

    caption = (msg.caption or "").strip()
    origin = getattr(msg, "forward_origin", None)
    origin_chat = getattr(origin, "chat", None) if origin else None
    legacy_chat = getattr(msg, "forward_from_chat", None)
    forwarded_username = (getattr(origin_chat, "username", None) or getattr(legacy_chat, "username", None) or "").lower()
    if forwarded_username == CHARACTER_CATCHER_LOGS_USERNAME:
        return
    if not caption or "Card Name" not in caption or "Rarity" not in caption:
        return await _safe_reply_text(msg, "❌ ဒီ Forward က Card Log ပုံစံမဟုတ်ပါ။ Card Log တစ်ခုလုံးကို ပုံ/ဗီဒီယိုနဲ့အတူ Forward လုပ်ပေးပါ။")

    def field(pattern):
        match = re.search(pattern, caption, flags=re.IGNORECASE | re.MULTILINE)
        return match.group(1).strip() if match else None

    card_id = field(r"^\s*(?:❝\s*)?ID\s*:\s*([0-9]+)\s*$")
    card_name = field(r"^\s*❀?\s*Card Name\s*:\s*(.+?)\s*$")
    collection = field(r"^\s*📚?\s*Collection\s*:\s*(.+?)\s*$")
    rarity_line = field(r"^\s*🌟?\s*Rarity\s*:\s*(.+?)\s*$")

    if not all((card_id, card_name, rarity_line)):
        return await _safe_reply_text(msg, "❌ Card Log ထဲက ID / Name / Rarity ကို မဖတ်နိုင်ပါ။ မူရင်း Log ကို Screenshot ပို့ပေးပါ။")

    # If the old log has no Collection field, assign one from the bot's existing collection pool.
    if not collection:
        collections_list = [
            "🎞 Ope Ope No Mi [ 🩺]",
            "🎞 Hito Hito No Mi [🌕]",
            "🎞 Mera Mera No Mi [🔥]",
            "🎞 Hana Hana No Mi [🌸]",
            "🎞 Yomi Yomi No Mi [💀]",
            "🎞 Tori Tori No Mi [🦅]",
            "🎞 Yami Yami No Mi [🌑]",
            "🎞 Sora Sora No Mi [☁️]",
            "🎞 Kaze Kaze No Mi [🌪️]",
            "🎞 Mizu Mizu No Mi [💧]",
            "🎞 Kori Kori No Mi [❄️]",
            "🎞 Tsuchi Tsuchi No Mi [🪨]",
            "🎞 Kumo Kumo No Mi [🕸️]",
            "🎞 Hikari Hikari No Mi [✨]",
            "🎞 Kage Kage No Mi [🌘]",
            "🎞 Rai Rai No Mi [⚡]",
            "🎞 Niji Niji No Mi [🌈]",
            "🎞 Tsuki Tsuki No Mi [🌙]",
            "🎞 Taiyo Taiyo No Mi [☀️]",
            "🎞 Mori Mori No Mi [🌳]",
            "🎞 Bara Bara No Mi [🎈]",
            "🎞 Toge Toge No Mi [🌵]",
            "🎞 Hanaumi No Mi [🪷]",
            "🎞 Kiba Kiba No Mi [🦷]",
            "🎞 Hebi Hebi No Mi [🐍]",
            "🎞 Neko Neko No Mi [🐈]",
            "🎞 Inu Inu No Mi [🐕]",
            "🎞 Kuma Kuma No Mi [🐻]",
            "🎞 Kitsune Kitsune No Mi [🦊]",
            "🎞 Tatsu Tatsu No Mi [🐉]",
            "🎞 Kame Kame No Mi [🐢]",
            "🎞 Hoshi Hoshi No Mi [⭐]",
            "🎞 Umi Umi No Mi [🌊]",
            "🎞 Shio Shio No Mi [🧂]",
            "🎞 Ame Ame No Mi [🌧️]",
            "🎞 Yuki Yuki No Mi [🌨️]",
            "🎞 Kiri Kiri No Mi [🌫️]",
            "🎞 Goro Goro No Mi [🔔]",
            "🎞 Hibana Hibana No Mi [🧨]",
            "🎞 Kemuri Kemuri No Mi [💨]",
            "🎞 Mushi Mushi No Mi [🦋]",
            "🎞 Tama Tama No Mi [🥚]",
            "🎞 Kuri Kuri No Mi [🌰]",
            "🎞 Suna Suna No Mi [🏜️]",
            "🎞 Ishi Ishi No Mi [💎]",
            "🎞 Gami Gami No Mi [📜]",
            "🎞 Oni Oni No Mi [👹]",
            "🎞 Tenshi Tenshi No Mi [👼]",
            "🎞 Shishi Shishi No Mi [🦁]",
            "🎞 Ryu Ryu No Mi [🦖]",
            "🎞 Fukurou Fukurou No Mi [🦉]",
            "🎞 Chou Chou No Mi [🐝]",
            "🎞 Sakana Sakana No Mi [🐟]",
            "🎞 Tako Tako No Mi [🐙]",
            "🎞 Kani Kani No Mi [🦀]",
            "🎞 Shima Shima No Mi [🏝️]",
            "🎞 Yume Yume No Mi [💭]"
        ]
        collection = random.choice(collections_list)

    rarity = None
    for candidate in ("Birthday Special", "Beyond", "Raiden", "Kryora", "Yugen", "Shinsei", "Kiwami", "Kami", "Hoshi", "Ten"):
        if candidate.lower() in rarity_line.lower():
            rarity = candidate
            break
    if rarity is None:
        if "🎀" in rarity_line:
            rarity = "Birthday Special"
        else:
            return await _safe_reply_text(msg, f"❌ မသိတဲ့ Rarity ဖြစ်ပါတယ်: {rarity_line}")

    if msg.photo:
        file_id, c_type = msg.photo[-1].file_id, "photo"
    elif msg.video:
        file_id, c_type = msg.video.file_id, "video"
    else:
        return await _safe_reply_text(msg, "❌ Forward လုပ်ထားတဲ့ Log မှာ Photo/Video မပါပါ။")

    if c_type == "video":
        rarity = "Kryora"
    elif rarity == "Kryora":
        return await _safe_reply_text(msg, "❌ Kryora သည် Video Card သီးသန့်ဖြစ်ပါတယ်။")
    is_locked = rarity == "Beyond"
    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    "SELECT card_id, name FROM cards WHERE card_id = $1", card_id
                )
                if existing:
                    return await _safe_reply_text(msg, 
                        f"⚠️ Duplicate Card ID: <code>{card_id}</code>\n"
                        f"ရှိပြီးသား Card: {existing['name']}",
                        parse_mode="HTML"
                    )
                await conn.execute("""
                    INSERT INTO cards (card_id, name, type, file_id, rarity, collection, is_locked)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                """, card_id, card_name, c_type, file_id, rarity, collection, is_locked)
    except Exception as exc:
        print(f"Forwarded card import failed for ID {card_id}: {type(exc).__name__}: {exc}")
        return await _safe_reply_text(msg, "❌ Database ထဲထည့်မရပါ။ Railway Deploy Logs ကို စစ်ပါ။")

    await _safe_reply_text(msg, 
        "✅ Card imported successfully!\n\n"
        f"🎴 Name: {card_name}\n"
        f"🆔 Original ID: <code>{card_id}</code>\n"
        f"🌟 Rarity: {get_rarity_display(rarity)}\n"
        f"📚 Collection: {collection}\n"
        f"🔒 Locked: {'Yes' if is_locked else 'No'}",
        parse_mode="HTML"
    )


@restricted_command
async def import_character_catcher_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prepare a forwarded Character Catcher Logs card for admin rarity selection."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or msg.chat.type != "private" or user.id != ADMIN_ID:
        return

    origin = getattr(msg, "forward_origin", None)
    origin_chat = getattr(origin, "chat", None) if origin else None
    legacy_chat = getattr(msg, "forward_from_chat", None)
    username = (getattr(origin_chat, "username", None) or getattr(legacy_chat, "username", None) or "").lower()
    if username != CHARACTER_CATCHER_LOGS_USERNAME:
        return

    if not (msg.photo or msg.video):
        return await _safe_reply_text(msg, "❌ Forward ထဲမှာ Card ပုံ/ဗီဒီယို မပါပါ။")

    caption = (msg.caption or "").strip()
    # Character Catcher Logs may describe an event/name/image update in one
    # sentence, or use a dedicated "Character Name:" / "New Name:" field.
    # Extract only the name value, never From/To/Rarity/Collection/admin text.
    caption_lines = [re.sub(r"<[^>]+>", "", line).strip() for line in caption.splitlines()]
    card_name = None

    # Prefer explicit name fields. "New Name" takes precedence over old names.
    explicit_patterns = [
        r"(?:^|\b)New\s+Name\s*:\s*(.+?)\s*$",
        r"(?:^|\b)Character\s+Name\s*:\s*(.+?)\s*$",
        r"(?:^|\b)Character\s*:\s*(.+?)\s*$",
    ]
    for pattern in explicit_patterns:
        for line in caption_lines:
            match = re.search(pattern, line, re.I)
            if match:
                card_name = match.group(1).strip()
                break
        if card_name:
            break

    # Logs such as "Amir... changed event for Character Isagi Yoichi"
    # and "Amir... updated image for Character Isagi Yoichi".
    if not card_name:
        action_patterns = [
            r"\b(?:changed\s+event\s+for|updated\s+image\s+for|added\s+new)\s+Character\s+(.+?)\s*$",
            r"\bCharacter\s+(.+?)\s*$",
        ]
        for pattern in action_patterns:
            for line in caption_lines:
                match = re.search(pattern, line, re.I)
                if match:
                    candidate = match.group(1).strip()
                    # Reject generic labels or audit fields accidentally matched.
                    if not re.match(r"^(?:Name|Rarity|Collection|Anime|From|To)\b", candidate, re.I):
                        card_name = candidate
                        break
            if card_name:
                break

    # Some logs put the value on the line immediately after the field label.
    if not card_name:
        for index, line in enumerate(caption_lines):
            if re.match(r"^(?:New\s+Name|Character\s+Name)\s*:?\s*$", line, re.I):
                if index + 1 < len(caption_lines):
                    card_name = caption_lines[index + 1].strip()
                    break

    if card_name:
        # Remove trailing audit text if it was included on the same line.
        card_name = re.split(
            r"\s+(?:From|To|RARITY|Rarity|Collection|Anime)\s*:",
            card_name, maxsplit=1, flags=re.I
        )[0].strip()
        card_name = re.sub(r"\s+", " ", card_name).strip(" .:-")
        # Title-case each space-separated word, preserving non-letter tags/emojis.
        card_name = " ".join(word[:1].upper() + word[1:].lower() for word in card_name.split())
    if not card_name:
        return await _safe_reply_text(msg, "❌ Forward caption ထဲက Character Name ကို မဖတ်နိုင်ပါ။")

    # Keep a trailing event tag such as [🏖], but remove punctuation noise.
    card_name = re.sub(r"\s+", " ", card_name).strip(" .:-")
    card_name = " ".join(word[:1].upper() + word[1:].lower() for word in card_name.split(" "))
    if not card_name:
        return await _safe_reply_text(msg, "❌ Character Name မတွေ့ပါ။")

    file_id = msg.photo[-1].file_id if msg.photo else msg.video.file_id
    c_type = "photo" if msg.photo else "video"
    async with db_pool.acquire() as conn:
        duplicate = await conn.fetchrow("SELECT card_id, name FROM cards WHERE file_id = $1", file_id)
    if duplicate:
        return await _safe_reply_text(msg, f"⚠️ ဒီ media က ရှိပြီးသား Card ဖြစ်ပါတယ်: {duplicate['name']} (ID: {duplicate['card_id']})")

    async with db_pool.acquire() as conn:
        while True:
            card_id = str(random.randint(1000, 9999))
            exists = await conn.fetchrow("SELECT card_id FROM cards WHERE card_id = $1", card_id)
            if not exists:
                break

    pending_cc_channel_adds[user.id] = {"card_id": card_id, "name": card_name, "type": c_type, "file_id": file_id}
    # Keep forwarded Character Catcher imports across process restarts too.
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO card_add_sessions (user_id, session_type, payload, created_at)
               VALUES ($1, 'character_catcher', $2, NOW())
               ON CONFLICT (user_id) DO UPDATE SET
                   session_type = EXCLUDED.session_type,
                   payload = EXCLUDED.payload,
                   created_at = NOW()""",
            user.id, json.dumps(pending_cc_channel_adds[user.id], ensure_ascii=False)
        )
    rarities = [
        [InlineKeyboardButton("♠ Beyond", callback_data=f"ccrarity_{user.id}_Beyond", style="primary")],
        [InlineKeyboardButton("📓 Raiden", callback_data=f"ccrarity_{user.id}_Raiden", style="primary")],
        [InlineKeyboardButton("🎀 Birthday Special", callback_data=f"ccrarity_{user.id}_Birthday Special", style="primary")],
        [InlineKeyboardButton("🎐 Yugen", callback_data=f"ccrarity_{user.id}_Yugen", style="primary")],
        [InlineKeyboardButton("🧬 Shinsei", callback_data=f"ccrarity_{user.id}_Shinsei", style="primary"), InlineKeyboardButton("🪞 Kiwami", callback_data=f"ccrarity_{user.id}_Kiwami", style="primary")],
        [InlineKeyboardButton("✨ Kami", callback_data=f"ccrarity_{user.id}_Kami", style="primary"), InlineKeyboardButton("⚜️ Hoshi", callback_data=f"ccrarity_{user.id}_Hoshi", style="primary")],
        [InlineKeyboardButton("💮 Ten", callback_data=f"ccrarity_{user.id}_Ten", style="primary")]
    ]
    if c_type == "video":
        rarities = [[InlineKeyboardButton("🦠 Kryora (Video Only)", callback_data=f"ccrarity_{user.id}_Kryora", style="primary")]] + rarities
    await _safe_reply_text(msg, 
        f"🎴 Forward Card Ready\n\n🆔 ID: <code>{card_id}</code>\n🏷 Name: {html.escape(card_name)}\n\nRarity ရွေးပါ:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rarities)
    )


@restricted_command
async def ttcard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message

    if not user or not msg:
        return

    # Reply instead of silently returning, so an ADMIN_ID mismatch is visible.
    if user.id != ADMIN_ID:
        return await _safe_reply_text(msg, "❌ This command is only available to the main bot admin.")

    status_msg = await _safe_reply_text(msg, "⏳ Checking all users' card collections...")

    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                affected = await conn.fetch("""
                    UPDATE user_cards
                    SET amount = 3
                    WHERE amount > 3
                    RETURNING user_id, card_id
                """)
                affected_users = len({row["user_id"] for row in affected})
                affected_rows = len(affected)

                locked_kryora = await conn.fetch("""
                    WITH eligible AS (
                        SELECT c.card_id
                        FROM cards c
                        JOIN user_cards uc ON uc.card_id = c.card_id
                        WHERE c.rarity = 'Kryora' AND c.is_locked = FALSE
                        GROUP BY c.card_id
                        HAVING SUM(uc.amount) >= 30
                    )
                    UPDATE cards
                    SET is_locked = TRUE
                    WHERE card_id IN (SELECT card_id FROM eligible)
                    RETURNING card_id
                """)
                locked_kryora_count = len(locked_kryora)

        await status_msg.edit_text(
            "✅ Card ownership audit completed.\n\n"
            f"👥 Users corrected: {affected_users}\n"
            f"🎴 Card records corrected: {affected_rows}\n"
            f"🔒 Kryora cards auto-locked: {locked_kryora_count}\n"
            "📌 No user can keep more than 3 copies of the same card.",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"/fixcard failed for admin {user.id}: {type(e).__name__}: {e}")
        try:
            await status_msg.edit_text(
                "❌ /fixcard failed while checking the database.\n"
                f"<code>{type(e).__name__}: {str(e)[:700]}</code>",
                parse_mode="HTML"
            )
        except Exception as reply_error:
            print(f"/ttcard error reply failed: {reply_error}")


@restricted_command
async def cad_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Card admins can claim up to five cards daily after adding at least seven cards that day."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    async with db_pool.acquire() as conn:
        admin_row = await conn.fetchrow("SELECT is_card_admin FROM users WHERE user_id = $1", user.id)
        if user.id != ADMIN_ID and not (admin_row and admin_row["is_card_admin"]):
            return await _safe_reply_text(msg, "❌ Card Admin များသာ အသုံးပြုနိုင်ပါတယ်။")
        if not context.args or len(context.args) > 2:
            return await _safe_reply_text(msg, "အသုံးပြုပုံ: /cad [card_id] [1x/2x]")
        card_id = context.args[0].strip()
        amount = 1
        if len(context.args) == 2:
            match = re.fullmatch(r"([12])x", context.args[1].lower())
            if not match:
                return await _safe_reply_text(msg, "❌ /cad id, /cad id 1x သို့မဟုတ် /cad id 2x သာ အသုံးပြုနိုင်ပါတယ်။")
            amount = int(match.group(1))
        async with conn.transaction():
            added_today = await conn.fetchval(
                "SELECT COUNT(*) FROM card_add_activity WHERE user_id = $1 AND added_on = CURRENT_DATE", user.id
            )
            if int(added_today or 0) < 7:
                return await _safe_reply_text(msg, f"❌ ဒီနေ့ အနည်းဆုံး 7 ကဒ် Add လုပ်ထားမှ ယူလို့ရပါတယ်။\nဒီနေ့ Add လုပ်ထားတာ: {added_today or 0}/7")
            await conn.execute(
                "INSERT INTO cad_daily_claims (user_id, claim_date, count) VALUES ($1, CURRENT_DATE, 0) "
                "ON CONFLICT (user_id, claim_date) DO NOTHING",
                user.id
            )
            claim = await conn.fetchrow(
                "SELECT count FROM cad_daily_claims WHERE user_id = $1 AND claim_date = CURRENT_DATE FOR UPDATE", user.id
            )
            used = int(claim["count"]) if claim else 0
            if used + amount > 5:
                return await _safe_reply_text(msg, f"❌ Daily limit ပြည့်ပါပြီ။\nဒီနေ့ ယူပြီး: {used}/5")
            card = await conn.fetchrow("SELECT card_id, name, rarity FROM cards WHERE card_id = $1", card_id)
            if not card:
                return await _safe_reply_text(msg, "❌ ဒီ Card ID ကို မတွေ့ပါ။")
            if not await add_user_card_with_limit(conn, user.id, card_id, amount):
                return await _safe_reply_text(msg, "❌ ဒီကဒ်ကို အများဆုံး 3x ပဲ ပိုင်ဆိုင်နိုင်ပါတယ်။")
            await conn.execute(
                "INSERT INTO cad_daily_claims (user_id, claim_date, count) VALUES ($1, CURRENT_DATE, $2) "
                "ON CONFLICT (user_id, claim_date) DO UPDATE SET count = cad_daily_claims.count + EXCLUDED.count",
                user.id, amount
            )
            await check_and_auto_lock_card(conn, card_id)
        await _safe_reply_text(msg, 
            f"✅ Card ရရှိပါပြီ။\n🎴 {html.escape(card['name'])}\n🆔 <code>{card_id}</code>\n"
            f"🌟 {get_rarity_display(card['rarity'])}\n📦 ယူပြီး: {used + amount}/5",
            parse_mode="HTML"
        )


@restricted_command
async def del_card_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_id = update.message.message_id
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        return await _safe_reply_text(update.effective_message, "❌ Usage: `/del [card_id or card_name]`", parse_mode="Markdown")

    target_query = " ".join(context.args).strip()

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            card = await conn.fetchrow("SELECT card_id, name FROM cards WHERE card_id = $1 OR name = $2", target_query, target_query)
            if not card:
                card = await conn.fetchrow("SELECT card_id, name FROM cards WHERE card_id = $1 OR name ILIKE $1", target_query)

            if not card:
                return await _safe_reply_text(update.effective_message, f"❌ Card (ID or Name) <code>{target_query}</code> not found.", parse_mode="HTML")

            target_card_id = card['card_id']
            card_name = card['name']

            await conn.execute("DELETE FROM user_cards WHERE card_id = $1", target_card_id)
            await conn.execute("DELETE FROM cards WHERE card_id = $1", target_card_id)
            await conn.execute("UPDATE users SET selected_card_id = NULL WHERE selected_card_id = $1", target_card_id)

    await _safe_reply_text(update.effective_message, f"✅ Card ID <code>{target_card_id}</code> ({card_name}) deleted successfully.", parse_mode="HTML")

@restricted_command
async def set_card_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    user = update.effective_user
    chat = update.effective_chat
    msg_id = update.message.message_id
    await register_user_group(user, chat, context)

    if not context.args:
        return await _safe_reply_text(update.effective_message, "❌ Usage: `/set [Card_ID]`", parse_mode="Markdown")

    target_card_id = context.args[0]

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            has_card = await conn.fetchrow("""
                SELECT c.name FROM user_cards uc
                JOIN cards c ON uc.card_id = c.card_id
                WHERE uc.user_id = $1 AND uc.card_id = $2
            """, user.id, target_card_id)

            if not has_card:
                return await _safe_reply_text(update.effective_message, f"❌ You do not own Card ID <code>{target_card_id}</code>.", parse_mode="HTML")

            await conn.execute("UPDATE users SET selected_card_id = $1 WHERE user_id = $2", target_card_id, user.id)

    await _safe_reply_text(update.effective_message, f"✅ Active profile card changed to {has_card['name']} for /kc.", parse_mode="HTML")

@restricted_command
async def khmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    user = update.effective_user
    chat = update.effective_chat
    msg_id = update.message.message_id
    await register_user_group(user, chat, context)

    text = "🎯 Select Rarity Mode for /kc\n\nကျေးဇူးပြု၍ စုပြလိုသော Rarity တစ်ခုကို ရွေးချယ်ပေးပါ -"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("♠ Beyond", callback_data=f"khset_{user.id}_Beyond", style="primary"),
            InlineKeyboardButton("🎀 Birthday Special", callback_data=f"khset_{user.id}_Birthday Special", style="primary")
        ],
        [
            InlineKeyboardButton("📓 Raiden", callback_data=f"khset_{user.id}_Raiden", style="primary"),
            InlineKeyboardButton("🦠 Kryora", callback_data=f"khset_{user.id}_Kryora", style="primary")
        ],
        [
            InlineKeyboardButton("🎐 Yugen", callback_data=f"khset_{user.id}_Yugen", style="primary"),
            InlineKeyboardButton("🧬 Shinsei", callback_data=f"khset_{user.id}_Shinsei", style="primary")
        ],
        [
            InlineKeyboardButton("🪞 Kiwami", callback_data=f"khset_{user.id}_Kiwami", style="primary"),
            InlineKeyboardButton("✨ Kami", callback_data=f"khset_{user.id}_Kami", style="primary")
        ],
        [
            InlineKeyboardButton("⚜️ Hoshi", callback_data=f"khset_{user.id}_Hoshi", style="primary"),
            InlineKeyboardButton("💮 Ten", callback_data=f"khset_{user.id}_Ten", style="primary")
        ]
    ])
    await _safe_reply_text(update.effective_message, text, parse_mode="HTML", reply_markup=keyboard)

@restricted_command
async def resethmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    user = update.effective_user
    chat = update.effective_chat
    msg_id = update.message.message_id
    await register_user_group(user, chat, context)

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET kc_rarity_mode = NULL WHERE user_id = $1", user.id)

    await _safe_reply_text(update.effective_message, "✅ Reset Successful! /kc တွင် ကတ်အားလုံးကို ပုံမှန်အတိုင်း ပြန်လည်ပြသမည် ဖြစ်ပါသည်။", parse_mode="HTML")

@restricted_command
async def kshop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    user = update.effective_user
    chat = update.effective_chat
    msg = update.message
    msg_id = msg.message_id
    await register_user_group(user, chat, context)

    text = f"""Hello {user.first_name}
You can buy Kiwami / Shinsei / Yugen / Kami / Birthday Special rarity cards here"""

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎀 Birthday Special", callback_data=f"kshop_rarity_{user.id}_Birthday Special", style="primary"),
            InlineKeyboardButton("🎐 Yugen", callback_data=f"kshop_rarity_{user.id}_Yugen", style="primary")
        ],
        [
            InlineKeyboardButton("🧬 Shinsei", callback_data=f"kshop_rarity_{user.id}_Shinsei", style="primary"),
            InlineKeyboardButton("🪞 Kiwami", callback_data=f"kshop_rarity_{user.id}_Kiwami", style="primary")
        ],
        [
            InlineKeyboardButton("✨ Kami", callback_data=f"kshop_rarity_{user.id}_Kami", style="primary")
        ]
    ])
    await _safe_reply_text(msg, text, parse_mode="HTML", reply_markup=keyboard)

async def render_kshop_card(query, context, user_id, rarity, index):
    async with db_pool.acquire() as conn:
        cards = await conn.fetch("SELECT card_id, name, type, file_id, rarity, collection FROM cards WHERE rarity = $1 ORDER BY card_id ASC", rarity)
    
    if not cards:
        return await query.answer("❌ No cards available in this rarity!", show_alert=True)
    
    index = max(0, min(index, len(cards) - 1))
    card = cards[index]
    
    price_key = (user_id, card['card_id'])
    if price_key not in kshop_prices:
        if rarity == 'Birthday Special':
            kshop_prices[price_key] = random.randint(80000, 140000)
        elif rarity == 'Yugen':
            kshop_prices[price_key] = random.randint(36000, 56000)
        elif rarity == 'Shinsei':
            kshop_prices[price_key] = random.randint(18000, 30000)
        elif rarity == 'Kiwami':
            kshop_prices[price_key] = random.randint(10000, 14600)
        elif rarity == 'Kami':
            kshop_prices[price_key] = random.randint(9000, 11400)
        else:
            kshop_prices[price_key] = random.randint(10000, 14600)
    price = kshop_prices[price_key]

    async with db_pool.acquire() as conn:
        global_drop_count = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM user_cards WHERE card_id = $1", card['card_id'])

    r_display = get_rarity_display(rarity)
    col_name = card.get('collection') or "🎞 Ope Ope No Mi [ 🩺]"

    caption_text = f"""🎗 Card Information ♡

▰▰▱▱▰▰▱▱
🍀 Name - {card['name']}
    🍀  ID - <code>{card['card_id']}</code>
🎬 Type - {card['type'].upper()}
🌟 Rarity - {r_display}
📚 Collection - {col_name}
🩸 Kachi Price - {price}
   🐠 Global Drops - {global_drop_count}

▰▰▱▱▰▰▱▱▰▰ ({index + 1}/{len(cards)})"""

    keyboard_rows = []
    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton("◀️ Back", callback_data=f"kshop_nav_{user_id}_{rarity}_{index - 1}", style="primary"))
    if index < len(cards) - 1:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"kshop_nav_{user_id}_{rarity}_{index + 1}", style="primary"))
    if nav_row:
        keyboard_rows.append(nav_row)

    keyboard_rows.append([
        InlineKeyboardButton("Buy This Card", callback_data=f"kshop_buy_{user_id}_{rarity}_{card['card_id']}_{price}", style="success")
    ])
    keyboard_rows.append([
        InlineKeyboardButton("◀️ Back Menu", callback_data=f"kshop_back_{user_id}", style="primary")
    ])
    markup = InlineKeyboardMarkup(keyboard_rows)

    media = InputMediaPhoto(media=card['file_id'], caption=caption_text, parse_mode="HTML") if card['type'] == 'photo' else InputMediaVideo(media=card['file_id'], caption=caption_text, parse_mode="HTML")

    try:
        await query.edit_message_media(media=media, reply_markup=markup)
    except Exception:
        try:
            await query.edit_message_caption(caption=caption_text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass

@restricted_command
async def kgift_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    sender = update.effective_user
    chat = update.effective_chat
    reply_msg = update.message.reply_to_message
    if not reply_msg or not reply_msg.from_user or reply_msg.from_user.is_bot:
        return await _safe_reply_text(update.effective_message, "❌ Reply to a user's message to send a gift.")
    receiver = reply_msg.from_user
    if sender.id == receiver.id:
        return await _safe_reply_text(update.effective_message, "❌ Cannot send gifts to yourself.")
    if not context.args:
        return await _safe_reply_text(update.effective_message,
            "❌ Usage: Reply to a user and use /kgift card_id, /kgift card_id2x, or /kgift id1 id2 id3.\nMaximum: 3 cards per gift.")

    requested = []
    for token in context.args:
        token = token.strip()
        m = re.fullmatch(r"(.+?)([1-3])x", token, re.IGNORECASE)
        if m:
            card_id, amount = m.group(1), int(m.group(2))
        else:
            card_id, amount = token, 1
        requested.append((card_id, amount))
    combined = {}
    for card_id, amount in requested:
        combined[card_id] = combined.get(card_id, 0) + amount
    requested = list(combined.items())
    if sum(amount for _, amount in requested) > 3:
        return await _safe_reply_text(update.effective_message, "❌ တစ်ကြိမ်လျှင် အများဆုံး ကတ် ၃ ကတ်သာ Gift ပေးနိုင်ပါတယ်။")

    await register_user_group(sender, chat, context)
    await register_user_group(receiver, chat, context)
    gift_cards = []
    async with db_pool.acquire() as conn:
        for card_id, amount in requested:
            card = await conn.fetchrow("SELECT card_id, name, type, file_id, collection FROM cards WHERE card_id = $1", card_id)
            if not card:
                return await _safe_reply_text(update.effective_message, f"❌ Card ID {html.escape(card_id)} not found.")
            owned = await conn.fetchrow("SELECT amount FROM user_cards WHERE user_id = $1 AND card_id = $2", sender.id, card_id)
            if not owned or owned["amount"] < amount:
                return await _safe_reply_text(update.effective_message,
                    f"❌ You do not own {amount} copy/copies of Card ID {html.escape(card_id)} ({html.escape(card['name'])}).")
            gift_cards.append({"card_id": str(card["card_id"]), "card_name": card["name"],
                "type": card["type"], "file_id": card["file_id"],
                "collection": card.get("collection") or "🎞 Ope Ope No Mi [ 🩺]", "amount": amount})

    lines = ["🎁 Kachi Card Gift Transfer", "",
        f"👤 Sender: {get_mention(sender.id, sender.first_name)}",
        f"👤 Receiver: {get_mention(receiver.id, receiver.first_name)}", "", "🎴 Card Info:"]
    for item in gift_cards:
        qty = f" ×{item['amount']}" if item["amount"] > 1 else ""
        lines.append(f"🏷️ {html.escape(item['card_name'])} — ID: <code>{html.escape(item['card_id'])}</code>{qty}")
    lines.extend(["", "Click button below to confirm."])
    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Cancel ❌", callback_data=f"gift_cancel_{sender.id}", style="danger"),
        InlineKeyboardButton("Confirm Gift 🎁", callback_data=f"gift_confirm_{sender.id}", style="success")
    ]])
    preview = gift_cards[0]
    if preview["type"] == "photo":
        sent_msg = await context.bot.send_photo(chat_id=chat.id, photo=preview["file_id"], caption=text, parse_mode="HTML", reply_markup=keyboard)
    else:
        sent_msg = await context.bot.send_video(chat_id=chat.id, video=preview["file_id"], caption=text, parse_mode="HTML", reply_markup=keyboard)
    pending_gifts[sent_msg.message_id] = {"type": "card", "sender_id": sender.id,
        "receiver_id": receiver.id, "receiver_name": receiver.first_name, "cards": gift_cards}

async def rngift_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot-owner-only card grant; does not deduct cards from the admin."""
    msg = update.message
    admin = update.effective_user
    if not admin or admin.id != ADMIN_ID:
        return await _safe_reply_text(msg, "❌ Only the Bot Admin can use /rngift.")

    if not context.args:
        return await _safe_reply_text(msg, 
            "❌ Usage:\n"
            "• Reply to a user's message: /rngift 123x2 92 828\n"
            "• By user ID: /rngift 123456789 123x2 92\n\n"
            "x2 means 2 copies."
        )

    reply = msg.reply_to_message
    target_id = None
    card_tokens = list(context.args)

    # Reply mode: recipient is the replied-to user.
    if reply and reply.from_user and not reply.from_user.is_bot:
        target_id = reply.from_user.id
    # Direct mode: first argument is a Telegram user ID (normally 8+ digits).
    elif card_tokens and card_tokens[0].isdigit() and len(card_tokens[0]) >= 8:
        target_id = int(card_tokens.pop(0))

    if not target_id:
        return await _safe_reply_text(msg, 
            "❌ Reply to the recipient's message, or provide their user ID first."
        )
    if target_id == admin.id:
        return await _safe_reply_text(msg, "❌ You cannot use /rngift to gift cards to yourself.")
    if not card_tokens:
        return await _safe_reply_text(msg, "❌ Please provide at least one card ID.")

    requested = []
    for token in card_tokens:
        match = re.fullmatch(r"([A-Za-z0-9_-]+?)(?:[xX](\d+))?", token)
        if not match:
            return await _safe_reply_text(msg, 
                f"❌ Invalid card format: {token}\nUse cardID or cardIDx2."
            )
        card_id = match.group(1)
        amount = int(match.group(2) or 1)
        if amount < 1 or amount > 10000:
            return await _safe_reply_text(msg, "❌ Each quantity must be between 1 and 10000.")
        requested.append((card_id, amount))

    # Combine repeated IDs so each card is updated once.
    combined = {}
    for card_id, amount in requested:
        combined[card_id] = combined.get(card_id, 0) + amount

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            cards = await conn.fetch(
                "SELECT card_id, name FROM cards WHERE card_id = ANY($1::text[])",
                list(combined.keys())
            )
            found = {row["card_id"]: row for row in cards}
            missing = [cid for cid in combined if cid not in found]
            if missing:
                return await _safe_reply_text(msg, 
                    "❌ Card ID not found: " + ", ".join(missing)
                )

            for card_id, amount in combined.items():
                await conn.execute("""
                    INSERT INTO user_cards (user_id, card_id, amount)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, card_id)
                    DO UPDATE SET amount = user_cards.amount + EXCLUDED.amount
                """, target_id, card_id, amount)

    details = "\n".join(
        f"• {found[cid]['name']} — <code>{cid}</code> ×{amount}"
        for cid, amount in combined.items()
    )
    await _safe_reply_text(msg, 
        f"✅ Admin Gift Successful\n\n"
        f"👤 User ID: <code>{target_id}</code>\n"
        f"🎴 Cards granted:\n{details}\n\n"
        f"<i>Admin inventory was not changed.</i>",
        parse_mode="HTML"
    )


async def render_kc_view(update_or_query, context: ContextTypes.DEFAULT_TYPE, page: int = 0, owner_id: int = None):
    user = update_or_query.effective_user if isinstance(update_or_query, Update) else update_or_query.from_user
    target_id = owner_id if owner_id else user.id
    msg_id = update_or_query.message.message_id if isinstance(update_or_query, Update) and update_or_query.message else None

    async with db_pool.acquire() as conn:
        user_row = await conn.fetchrow("SELECT selected_card_id, name, kc_rarity_mode FROM users WHERE user_id = $1", target_id)
        selected_card_id = user_row['selected_card_id'] if user_row else None
        display_name = user_row['name'] if user_row else user.first_name
        rarity_mode = user_row['kc_rarity_mode'] if user_row else None

        rank_row = await conn.fetchrow("""
            SELECT COUNT(*) + 1 AS rank FROM (
                SELECT u.user_id, COALESCE(SUM(uc.amount), 0) as tc
                FROM users u
                LEFT JOIN user_cards uc ON u.user_id = uc.user_id
                GROUP BY u.user_id
            ) sub WHERE tc > (
                SELECT COALESCE(SUM(amount), 0) FROM user_cards WHERE user_id = $1
            )
        """, target_id)
        global_rank = rank_row['rank'] if rank_row else 1

        if rarity_mode:
            user_card_rows = await conn.fetch("""
                SELECT c.card_id, c.name, c.type, c.file_id, c.rarity, c.collection, uc.amount 
                FROM user_cards uc 
                JOIN cards c ON uc.card_id = c.card_id 
                WHERE uc.user_id = $1 AND uc.amount > 0 AND c.rarity = $2
                ORDER BY c.card_id ASC
            """, target_id, rarity_mode)
        else:
            user_card_rows = await conn.fetch("""
                SELECT c.card_id, c.name, c.type, c.file_id, c.rarity, c.collection, uc.amount 
                FROM user_cards uc 
                JOIN cards c ON uc.card_id = c.card_id 
                WHERE uc.user_id = $1 AND uc.amount > 0
                ORDER BY c.card_id ASC
            """, target_id)

    total_owned_sum = sum(c['amount'] for c in user_card_rows)
    async with db_pool.acquire() as conn:
        all_owned_total = await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM user_cards WHERE user_id = $1",
            target_id
        )

    per_page = 8
    total_cards = len(user_card_rows)
    total_pages = (total_cards + per_page - 1) // per_page if total_cards > 0 else 1
    page = max(0, min(page, total_pages - 1))

    mode_title = f" [{get_rarity_display(rarity_mode)} Mode]" if rarity_mode else ""
    text_header = f"""❀ NAME - {get_mention(target_id, display_name)}
❀ Global No - #{global_rank}

HAREM PAGE{mode_title} ({page + 1}/{total_pages})
🎴 Total Cards: {all_owned_total}"""

    if not user_card_rows:
        full_text = f"{text_header}\n\nCollection\n<i>No cards found.</i>"
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"Harem❄ ({total_owned_sum})", switch_inline_query_current_chat=f"harem.{target_id}", style="success")
            ]
        ])
        if isinstance(update_or_query, Update):
            return await _safe_reply_text(update_or_query.message, full_text, parse_mode="HTML", reply_markup=keyboard)
        else:
            return await update_or_query.edit_message_caption(caption=full_text, parse_mode="HTML", reply_markup=keyboard)

    active_card = None
    if selected_card_id:
        active_card = next((c for c in user_card_rows if c['card_id'] == selected_card_id), None)
    if not active_card:
        active_card = user_card_rows[-1]

    start_idx = page * per_page
    end_idx = start_idx + per_page
    current_page_cards = user_card_rows[start_idx:end_idx]

    collections_dict = {}
    for c in current_page_cards:
        col = c.get('collection') or "🎞 Ope Ope No Mi [ 🩺]"
        if col not in collections_dict:
            collections_dict[col] = []
        collections_dict[col].append(c)

    content_blocks = ["Collection"]
    for col_name, cards_in_col in collections_dict.items():
        content_blocks.append("")
        content_blocks.append(f"🍀 {col_name}\n━━━━━━━━━━━━━━━━━━")
        for c in cards_in_col:
            r_emoji = get_rarity_emoji(c.get('rarity'))
            count_str = f" ({c['amount']}x)" if c['amount'] > 1 else " (1x)"
            content_blocks.append(f"{c['card_id']} | {r_emoji} | {c['name']}{count_str}")

    cards_info = "\n".join(content_blocks)
    full_text = f"{text_header}\n\n{cards_info}"

    keyboard_rows = []
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ Back", callback_data=f"kcpage_{page - 1}_{target_id}", style="primary"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"kcpage_{page + 1}_{target_id}", style="primary"))
        if nav_row:
            keyboard_rows.append(nav_row)

    keyboard_rows.append([
        InlineKeyboardButton(f"Harem❄ ({total_owned_sum})", switch_inline_query_current_chat=f"harem.{target_id}", style="success")
    ])
    markup = InlineKeyboardMarkup(keyboard_rows)

    if isinstance(update_or_query, Update):
        try:
            if active_card['type'] == 'photo':
                await _safe_reply_photo(update_or_query.message, photo=active_card['file_id'], caption=full_text, parse_mode="HTML", reply_markup=markup)
            else:
                await _safe_reply_video(update_or_query.message, video=active_card['file_id'], caption=full_text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            await _safe_reply_text(update_or_query.message, full_text, parse_mode="HTML", reply_markup=markup)
    else:
        query = update_or_query
        try:
            await query.edit_message_caption(caption=full_text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            try:
                await query.edit_message_text(text=full_text, parse_mode="HTML", reply_markup=markup)
            except Exception:
                pass

@restricted_command
async def check_kc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    user = update.effective_user
    chat = update.effective_chat
    await register_user_group(user, chat, context)
    await render_kc_view(update, context, page=0, owner_id=user.id)

@restricted_command
async def kachi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    user = update.effective_user
    chat = update.effective_chat
    msg_id = update.message.message_id
    await register_user_group(user, chat, context)

    target_id = user.id
    async with db_pool.acquire() as conn:
        user_row = await conn.fetchrow("SELECT coins, name FROM users WHERE user_id = $1", target_id)
        kachi_amount = user_row['coins'] if user_row else 1000
        display_name = user_row['name'] if user_row else user.first_name

    bot_info = await context.bot.get_me()
    text = f"Name : {display_name}\nKachi Balance : {kachi_amount}\nUser ID : <code>{target_id}</code>"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Send Kachi", url=f"https://t.me/{bot_info.username}?start=kachi_{target_id}", style="success")]
    ])
    
    await _safe_reply_text(update.effective_message, text, parse_mode="HTML", reply_markup=keyboard)

@restricted_command
async def kslot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    user = update.effective_user
    chat = update.effective_chat
    msg = update.message
    msg_id = msg.message_id
    await register_user_group(user, chat, context)

    if user.id in active_kslot_sessions:
        sess = active_kslot_sessions[user.id]
        chat_id_str = str(sess["chat_id"])
        if chat_id_str.startswith("-100"):
            msg_link = f"https://t.me/c/{chat_id_str[4:]}/{sess['message_id']}"
        else:
            msg_link = f"tg://openmessage?chat_id={sess['chat_id']}&message_id={sess['message_id']}"
        
        warning_text = (
            f"⚠️ အရင် kslot မပြီးသေးပါခင်ဗျာ!\n"
            f"ကျေးဇူးပြု၍ အောက်ပါလင့်ခ်သို့ သွားရောက်၍ အဆုံးသတ်ပေးပါရန် -\n"
            f"🔗 <a href=\"{msg_link}\">Go to Active Kslot Message</a>"
        )
        return await _safe_reply_text(msg, warning_text, parse_mode="HTML")

    now = time.time()
    last_kslot = kslot_cooldowns.get(user.id, 0)
    cooldown_duration = 15 if kslot_is_cancelled.get(user.id, False) else 35
    if now - last_kslot < cooldown_duration:
        remaining = int(cooldown_duration - (now - last_kslot))
        return await _safe_reply_text(msg, 
            f"⏳ မင်္ဂလာပါ {get_mention(user.id, user.first_name)}၊ /kslot ကို တစ်ပွဲပြီးလျှင် {cooldown_duration} စက္ကန့် စောင့်ဆိုင်းပေးပါ။\n"
            f"⏱ ကျန်ရှိသောအချိန်: {remaining}s",
            parse_mode="HTML"
        )

    amounts = [300, 500, 800, 1500, 2500, 4500, 7000]
    keyboard_rows = []
    row = []
    for amt in amounts:
        row.append(InlineKeyboardButton(f"{amt} Kachi 🩸", callback_data=f"kslot_bet_{user.id}_{amt}", style="primary"))
        if len(row) == 2:
            keyboard_rows.append(row)
            row = []
    if row:
        keyboard_rows.append(row)
    
    keyboard_rows.append([InlineKeyboardButton("🔥 All In 🩸", callback_data=f"kslot_bet_{user.id}_allin", style="success")])

    text = f"""🎰 Kachi Slot Machine Game

👤 Player: {get_mention(user.id, user.first_name)}
👇 ကျေးဇူးပြု၍ ဂိမ်းစတင်ရန် လောင်းကြေးပမာဏ (Betting Amount) ကို ရွေးချယ်ပါ -"""

    sent_msg = await _safe_reply_text(msg, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard_rows))
    active_kslot_sessions[user.id] = {
        "chat_id": chat.id,
        "message_id": sent_msg.message_id,
        "bet_amount": None
    }

def decode_slot_dice(value):
    idx = value - 1
    d1 = idx // 16
    d2 = (idx // 4) % 4
    d3 = idx % 4

    symbols = []
    total_score = 0

    for d in [d1, d2, d3]:
        if d == 3:
            s_name = "7"
            s_score = random.randint(11, 12)
        elif d == 2:
            s_name = "🍋"
            s_score = random.randint(9, 10)
        elif d == 1:
            s_name = "🍇"
            s_score = random.randint(7, 8)
        else:
            s_name = "BAR"
            s_score = random.randint(6, 7)
        symbols.append(s_name)
        total_score += s_score

    return symbols, total_score

async def run_kslot_task(bot, chat_id, query, user_id, display_name, bet_amount):
    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                user_row = await conn.fetchrow("SELECT coins FROM users WHERE user_id = $1 FOR UPDATE", user_id)
                current_coins = user_row['coins'] if user_row else 1000

                if current_coins < bet_amount:
                    try:
                        await query.answer(f"❌ Insufficient Kachi balance! Your current balance is {current_coins} Kachi.", show_alert=True)
                    except Exception:
                        pass
                    return

                new_balance_after_bet = current_coins - bet_amount
                await conn.execute("UPDATE users SET coins = $1 WHERE user_id = $2", new_balance_after_bet, user_id)

        try:
            await query.edit_message_text(
                f"🎰 Spinning...\n\n👤 Player: {get_mention(user_id, display_name)}\n🩸 Bet Amount: {bet_amount} Kachi\n\n<i>🎲 Rolling slot animation...</i>",
                parse_mode="HTML",
                reply_markup=None
            )
        except Exception:
            pass

        dice_msg = None
        try:
            reply_to_id = query.message.reply_to_message.message_id if query.message and query.message.reply_to_message else None
            dice_msg = await bot.send_dice(chat_id=chat_id, emoji="🎰")
        except Exception:
            try:
                dice_msg = await bot.send_dice(chat_id=chat_id, emoji="🎰")
            except Exception:
                dice_msg = None

        await asyncio.sleep(2.5)

        dice_val = dice_msg.dice.value if dice_msg and dice_msg.dice else random.randint(1, 64)
        symbols, score = decode_slot_dice(dice_val)

        if score >= 32:
            payout = int(bet_amount * 1.5)
            result_type = "Win 1.5x 🏆"
            net_gain = payout - bet_amount
        elif score >= 28:
            payout = int(bet_amount * 1.2)
            result_type = "Win 1.2x ✨"
            net_gain = payout - bet_amount
        elif score >= 26:
            payout = int(bet_amount * 0.8)
            result_type = "Loss -0.2x 📉"
            net_gain = payout - bet_amount
        else:
            payout = 0
            result_type = "Loss 0x 💔"
            net_gain = -bet_amount

        final_payout_total = payout
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE users SET coins = coins + $1, daily_kg_win = daily_kg_win + $3 WHERE user_id = $2", final_payout_total, user_id, max(0, net_gain))
                updated_user = await conn.fetchrow("SELECT coins FROM users WHERE user_id = $1", user_id)
                new_balance = updated_user['coins'] if updated_user else new_balance_after_bet + final_payout_total

        sym_str = " | ".join(symbols)
        result_text = f"""🎰 Kachi Slot Machine Result

👤 Player: {get_mention(user_id, display_name)}
🎲 Symbols: {sym_str}
📊 Score: {score}
🎯 Result: {result_type} ({net_gain} Kachi)
💰 Current Balance: {new_balance} Kachi ✨"""

        try:
            await query.edit_message_text(result_text, parse_mode="HTML", reply_markup=None)
        except Exception:
            pass
    finally:
        pass

@restricted_command
async def kbox_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    user = update.effective_user
    chat = update.effective_chat
    msg = update.message
    msg_id = msg.message_id
    await register_user_group(user, chat, context)

    now = time.time()
    last_kbox = kbox_cooldowns.get(user.id, 0)
    if now - last_kbox < 35:
        remaining = int(35 - (now - last_kbox))
        return await _safe_reply_text(msg, 
            f"⏳ မင်္ဂလာပါ {get_mention(user.id, user.first_name)}၊ /kbox ကို တစ်ပွဲပြီးလျှင် 35 စက္ကန့် စောင့်ဆိုင်းပေးပါ။\n"
            f"⏱ ကျန်ရှိသောအချိန်: {remaining}s",
            parse_mode="HTML"
        )
    kbox_cooldowns[user.id] = now

    if user.id in active_kbox_users:
        return await _safe_reply_text(msg, "❌ သင့်မှာ Kbox တစ်ခု ဖွင့်နေဆဲ ရှိပါသေးသည်။ အရင်ဟာ ပြီးဆုံးသည်အထိ စောင့်ဆိုင်းပေးပါ။")

    cooldown = 86400

    async with db_pool.acquire() as conn:
        user_row = await conn.fetchrow("SELECT kbox_count, last_kbox_reset, coins, name FROM users WHERE user_id = $1", user.id)
        kbox_count = user_row['kbox_count'] if user_row else 0
        last_kbox_reset = user_row['last_kbox_reset'] if user_row else 0
        coins = user_row['coins'] if user_row else 1000
        display_name = user_row['name'] if user_row else user.first_name

        if now - last_kbox_reset >= cooldown:
            kbox_count = 0
            last_kbox_reset = now
            await conn.execute("UPDATE users SET kbox_count = 0, last_kbox_reset = $1 WHERE user_id = $2", now, user.id)

        if kbox_count >= 7:
            remaining = cooldown - (now - last_kbox_reset)
            hours = remaining // 3600
            mins = (remaining % 3600) // 60
            secs = remaining % 60
            time_str = f"{hours}h {mins}m {secs}s" if hours > 0 else (f"{mins}m {secs}s" if mins > 0 else f"{secs}s")
            return await _safe_reply_text(msg, 
                f"⏳ မင်္ဂလာပါ {get_mention(user.id, display_name)}၊ /kbox ကို တစ်နေ့လျှင် 7 ကြိမ်သာ ဖွင့်လှစ်နိုင်ပါသည်။\n"
                f"⏱ ကျန်ရှိသောအချိန်: {time_str}",
                parse_mode="HTML"
            )

        if coins < 1000:
            return await _safe_reply_text(msg, f"❌ Insufficient Kachi! You need 1000 Kachi (Current: {coins}).")

    text = "Welcome To Kbox 💤\nLet's Open 🔫 (Cost: 1000 Kachi)"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("OPEN", callback_data=f"kbox_open_{user.id}", style="success")]
    ])
    await _safe_reply_text(msg, text, parse_mode="HTML", reply_markup=keyboard)

@restricted_command
async def ksearch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    user = update.effective_user
    chat = update.effective_chat
    msg_id = update.message.message_id
    await register_user_group(user, chat, context)

    text = "🔍 Global Card Search (Ksearch)\n\nClick the button below to browse all cards available in the bot database:"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Global Search 🔎", switch_inline_query_current_chat="ksearch", style="primary")]
    ])
    await _safe_reply_text(update.effective_message, text, parse_mode="HTML", reply_markup=keyboard)

async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query
    text = query.query.strip()
    user_id = query.from_user.id
    user_name = query.from_user.first_name

    offset = int(query.offset) if query.offset and query.offset.isdigit() else 0
    limit = 50
    results = []

    if text.startswith("ksearch") or text == "":
        search_query = text.replace("ksearch", "").strip()
        async with db_pool.acquire() as conn:
            if search_query:
                all_cards = await conn.fetch("""
                    SELECT card_id, name, type, file_id, rarity, collection 
                    FROM cards 
                    WHERE card_id ILIKE $1 OR name ILIKE $1 OR rarity ILIKE $1
                    ORDER BY card_id ASC
                    LIMIT $2 OFFSET $3
                """, f"%{search_query}%", limit, offset)
            else:
                all_cards = await conn.fetch("""
                    SELECT card_id, name, type, file_id, rarity, collection 
                    FROM cards 
                    ORDER BY card_id ASC
                    LIMIT $1 OFFSET $2
                """, limit, offset)

        for c in all_cards:
            r = c.get('rarity', 'Ten')
            r_display = get_rarity_display(r)
            col_name = c.get('collection') or "🎞 Ope Ope No Mi [ 🩺]"
            title = f"{c['name']} ({c['card_id']})"[:64]
            description = f"ID: {c['card_id']} | Rarity: {r}"

            async with db_pool.acquire() as conn:
                global_drop_count = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM user_cards WHERE card_id = $1", c['card_id'])

            caption = f"""❀ Global Card Search 🔎

✿ Card Name - {c['name']}
      ☘ ID - <code>{c['card_id']}</code>
✿ Rarity - {r_display}
✿ Collection - {col_name}

⌘ Global Drops - {global_drop_count}"""

            if c['type'] == 'photo':
                results.append(
                    InlineQueryResultCachedPhoto(
                        id=str(uuid.uuid4()),
                        photo_file_id=c['file_id'],
                        title=title,
                        description=description,
                        caption=caption,
                        parse_mode="HTML"
                    )
                )
            else:
                results.append(
                    InlineQueryResultCachedVideo(
                        id=str(uuid.uuid4()),
                        video_file_id=c['file_id'],
                        title=title,
                        description=description,
                        caption=caption,
                        parse_mode="HTML"
                    )
                )
    else:
        if text.startswith("harem."):
            try:
                target_id = int(text.split(".")[1])
            except Exception:
                target_id = user_id
        else:
            target_id = user_id

        async with db_pool.acquire() as conn:
            owner_row = await conn.fetchrow("SELECT name FROM users WHERE user_id = $1", target_id)
            owner_name = owner_row['name'] if owner_row and owner_row['name'] else user_name

            user_cards = await conn.fetch("""
                SELECT c.card_id, c.name, c.type, c.file_id, c.rarity, c.collection, uc.amount 
                FROM user_cards uc
                JOIN cards c ON uc.card_id = c.card_id 
                WHERE uc.user_id = $1 AND uc.amount > 0
                ORDER BY c.card_id ASC
                LIMIT $2 OFFSET $3
            """, target_id, limit, offset)

        for c in user_cards:
            r = c.get('rarity', 'Ten')
            r_display = get_rarity_display(r)
            col_name = c.get('collection') or "🎞 Ope Ope No Mi [ 🩺]"
            title = f"{c['name']} ({c['card_id']})"[:64]
            description = f"ID: {c['card_id']} | Rarity: {r}"
            amount_str = f" ({c['amount']}x)" if c['amount'] > 1 else ""

            async with db_pool.acquire() as conn:
                global_drop_count = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM user_cards WHERE card_id = $1", c['card_id'])

            owner_mention = get_mention(target_id, owner_name)
            caption = f"""❀ Harem Owner ❝ {owner_mention} ❞

✿ Card Name - {c['name']}{amount_str}
      ☘ ID - <code>{c['card_id']}</code>
✿ Rarity - {r_display}
✿ Collection - {col_name}

⌘ Global Drops - {global_drop_count}"""
            
            if c['type'] == 'photo':
                results.append(
                    InlineQueryResultCachedPhoto(
                        id=str(uuid.uuid4()),
                        photo_file_id=c['file_id'],
                        title=title,
                        description=description,
                        caption=caption,
                        parse_mode="HTML"
                    )
                )
            else:
                results.append(
                    InlineQueryResultCachedVideo(
                        id=str(uuid.uuid4()),
                        video_file_id=c['file_id'],
                        title=title,
                        description=description,
                        caption=caption,
                        parse_mode="HTML"
                    )
                )

    try:
        next_offset = str(offset + limit) if len(results) == limit else ""
        await query.answer(results, next_offset=next_offset, cache_time=1)
    except Exception:
        pass

@restricted_command
async def cardlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await render_cardlist_page(update, context, page=0, owner_id=update.effective_user.id)

async def render_see_rarity_menu(target, context, owner_id):
    rarities = await _fetch_distinct_rarities()
    if not rarities:
        return await target.edit_message_text("No rarities found.")
    rows = []
    row = []
    for rarity in rarities:
        row.append(InlineKeyboardButton(
            f"{get_rarity_display(rarity)}",
            callback_data=f"see_rarity_{owner_id}_{rarity}"
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    menu_text = "🎴 Select a rarity to view all cards:"
    markup = InlineKeyboardMarkup(rows)
    if isinstance(target, Message):
        await _safe_reply_text(target, menu_text, reply_markup=markup)
    else:
        await target.edit_message_text(menu_text, reply_markup=markup)


async def _fetch_distinct_rarities():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT rarity FROM cards WHERE rarity IS NOT NULL ORDER BY rarity"
        )
    return [r["rarity"] for r in rows]


async def render_see_rarity_page(query, context, owner_id, rarity, page):
    async with db_pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM cards WHERE rarity = $1", rarity
        )
        cards = await conn.fetch(
            """SELECT card_id, name, rarity FROM cards
               WHERE rarity = $1 ORDER BY card_id ASC
               LIMIT 20 OFFSET $2""",
            rarity, page * 20
        )
    total = int(total or 0)
    pages = max(1, (total + 19) // 20)
    lines = [f"🎴 {get_rarity_display(rarity)}", f"Total: {total} cards", f"Page {page + 1}/{pages}", ""]
    lines.extend(
        f"{page * 20 + i}. {c['name']} — <code>{c['card_id']}</code>"
        for i, c in enumerate(cards, 1)
    )
    buttons = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Back", callback_data=f"see_page_{owner_id}_{page-1}_{rarity}"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"see_page_{owner_id}_{page+1}_{rarity}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("↩️ Back Menu", callback_data=f"see_menu_{owner_id}")])
    await query.edit_message_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@restricted_command
async def see_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await render_see_rarity_menu(
        update.effective_message, context, update.effective_user.id
    )


@restricted_command
async def send_group_prompt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        return await _safe_reply_text(update.effective_message, "Usage: /send gplink [gplink ...] or /send all <minimum members>")

    if context.args[0].lower() == "all":
        if len(context.args) != 2 or not context.args[1].isdigit():
            return await _safe_reply_text(update.effective_message, "Usage: /send all <minimum members>")
        minimum_members = int(context.args[1])
        async with db_pool.acquire() as conn:
            known_groups = await conn.fetch("SELECT chat_id FROM groups")
        targets = []
        checked = 0
        for group in known_groups:
            try:
                count = await context.bot.get_chat_member_count(chat_id=group["chat_id"])
                checked += 1
                if count >= minimum_members:
                    targets.append(group["chat_id"])
            except Exception as e:
                print(f"/send all lookup failed for {group['chat_id']}: {type(e).__name__}: {e}")
        if not targets:
            return await _safe_reply_text(update.effective_message, f"❌ Member {minimum_members}+ ရှိတဲ့ group မတွေ့ပါ။ Checked: {checked}")
        pending_group_sends[update.effective_user.id] = {"targets": targets}
        return await _safe_reply_text(update.effective_message, f"📨 Group {len(targets)} ခု ရွေးထားပါတယ်။ Member {minimum_members}+\nပို့မယ့် message ကို ပို့ပေးပါ။")

    refs = []
    for raw in context.args:
        link = raw if raw.startswith(("https://t.me/", "http://t.me/")) else "https://t.me/" + raw.lstrip("@/")
        internal = re.search(r"t\.me/c/(\d+)", link)
        public = re.search(r"(?:t\.me/)([A-Za-z0-9_]+)", link)
        if internal:
            ref = int("-100" + internal.group(1))
        elif public:
            ref = "@" + public.group(1)
        elif re.fullmatch(r"-?\d+", raw):
            ref = int(raw)
        else:
            return await _safe_reply_text(update.effective_message, "❌ Invalid group link or chat ID: " + raw)
        refs.append(ref)
    pending_group_sends[update.effective_user.id] = {"targets": refs}
    await _safe_reply_text(update.effective_message, "Send the message text to broadcast to these groups.")

@restricted_command
async def seelock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await render_seelock_page(update, context, page=0, owner_id=update.effective_user.id)

async def render_seelock_page(update_or_query, context: ContextTypes.DEFAULT_TYPE, page: int = 0, owner_id: int = None):
    msg_id = update_or_query.message.message_id if isinstance(update_or_query, Update) and update_or_query.message else None
    async with db_pool.acquire() as conn:
        cards = await conn.fetch("SELECT card_id, name, type, rarity, collection, is_locked FROM cards WHERE rarity = 'Yugen' ORDER BY card_id ASC")

    if not cards:
        text = "🎴 Yugen card list is empty."
        if isinstance(update_or_query, Update):
            return await _safe_reply_text(update_or_query.message, text, parse_mode="HTML")
        else:
            return await update_or_query.edit_message_text(text, parse_mode="HTML")

    per_page = 15
    total_cards = len(cards)
    total_pages = (total_cards + per_page - 1) // per_page

    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    end_idx = start_idx + per_page
    current_page_cards = cards[start_idx:end_idx]

    card_text_list = []
    async with db_pool.acquire() as conn:
        for idx, c in enumerate(current_page_cards, start=start_idx + 1):
            r = c.get('rarity', 'Yugen')
            r_emoji = get_rarity_emoji(r)
            lock_str = "🔒 LOCKED" if c.get('is_locked') else "🔓 UNLOCKED"
            total_drops = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM user_cards WHERE card_id = $1", c['card_id'])
            card_text_list.append(f"{idx}. {r_emoji} {c['name']} (ID: <code>{c['card_id']}</code>) | {lock_str} | Drops: {total_drops}")

    cards_str = "\n".join(card_text_list)

    text = f"""🎐 Yugen Cards Lock Status (Page {page + 1}/{total_pages})

{cards_str}"""

    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("◀️ Back", callback_data=f"seelock_{page - 1}_{owner_id}", style="primary"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"seelock_{page + 1}_{owner_id}", style="primary"))

    markup = InlineKeyboardMarkup([buttons]) if buttons else None

    if isinstance(update_or_query, Update):
        await _safe_reply_text(update_or_query.message, text, parse_mode="HTML", reply_markup=markup)
    else:
        query = update_or_query
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass

async def render_cardlist_page(update_or_query, context: ContextTypes.DEFAULT_TYPE, page: int = 0, owner_id: int = None):
    msg_id = update_or_query.message.message_id if isinstance(update_or_query, Update) and update_or_query.message else None
    async with db_pool.acquire() as conn:
        cards = await conn.fetch("SELECT card_id, name, type, rarity, collection, is_locked FROM cards ORDER BY card_id ASC")

    if not cards:
        text = "🎴 Card list is empty."
        if isinstance(update_or_query, Update):
            return await _safe_reply_text(update_or_query.message, text, parse_mode="HTML")
        else:
            return await update_or_query.edit_message_text(text, parse_mode="HTML")

    per_page = 10
    total_cards = len(cards)
    total_pages = (total_cards + per_page - 1) // per_page

    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    end_idx = start_idx + per_page
    current_page_cards = cards[start_idx:end_idx]

    card_text_list = []
    for idx, c in enumerate(current_page_cards, start=start_idx + 1):
        type_emoji = "🖼" if c['type'] == 'photo' else "🎥"
        r = c.get('rarity', 'Ten')
        r_emoji = get_rarity_emoji(r)
        lock_status = " 🔒[LOCKED]" if c.get('is_locked') else ""
        col_name = c.get('collection') or "—"
        card_text_list.append(f"{idx}. {r_emoji} {c['name']} (ID: <code>{c['card_id']}</code>) [{type_emoji} {c['type'].upper()}]{lock_status}\n     └ {col_name}")

    cards_str = "\n".join(card_text_list)

    text = f"""📜 Kachi Card List (Page {page + 1}/{total_pages})
📌 <i>Use <code>/card [card_id]</code> for details.</i>

{cards_str}"""

    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("◀️ Back", callback_data=f"clist_{page - 1}_{owner_id}", style="primary"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("Next ▶", callback_data=f"clist_{page + 1}_{owner_id}", style="primary"))

    markup = InlineKeyboardMarkup([buttons]) if buttons else None

    if isinstance(update_or_query, Update):
        await _safe_reply_text(update_or_query.message, text, parse_mode="HTML", reply_markup=markup)
    else:
        query = update_or_query
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass

@restricted_command
async def card_detail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context):
        return
    user = update.effective_user
    chat = update.effective_chat
    msg_id = update.message.message_id
    await register_user_group(user, chat, context)

    if not context.args:
        return await _safe_reply_text(update.effective_message, "❌ Usage: `/card [card_id]`", parse_mode="Markdown")

    target_card_id = context.args[0].strip()

    async with db_pool.acquire() as conn:
        card = await conn.fetchrow("SELECT card_id, name, type, file_id, rarity, collection, is_locked FROM cards WHERE card_id = $1", target_card_id)

        if not card:
            return await _safe_reply_text(update.effective_message, f"❌ Card ID <code>{target_card_id}</code> not found.", parse_mode="HTML")

        global_drop_count = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM user_cards WHERE card_id = $1", target_card_id)

        top_owners = await conn.fetch("""
            SELECT u.user_id, u.name, uc.amount 
            FROM user_cards uc
            JOIN users u ON uc.user_id = u.user_id
            WHERE uc.card_id = $1 AND uc.amount > 0
            ORDER BY uc.amount DESC, u.user_id ASC
            LIMIT 10
        """, target_card_id)

    owners_text_list = []
    if top_owners:
        for idx, row in enumerate(top_owners, 1):
            owners_text_list.append(f"☛ {get_mention(row['user_id'], row['name'])} — {row['amount']}x")
        owners_str = "\n".join(owners_text_list)
    else:
        owners_str = "☛ <i>No owners yet.</i>"

    r = card.get('rarity', 'Ten')
    r_display = get_rarity_display(r)
    col_name = card.get('collection') or "🎞 Ope Ope No Mi [ 🩺]"
    lock_str = "\n🔒 Status: Locked" if card.get('is_locked') else ""

    if r == 'Beyond':
        coin_value = "280000-300000"
    elif r == 'Birthday Special':
        coin_value = "8000-10000"
    elif r == 'Raiden':
        coin_value = "170000-200000"
    elif r == 'Kryora':
        coin_value = "150000-180000"
    elif r == 'Yugen':
        coin_value = "100000-120000"
    elif r == 'Shinsei':
        coin_value = "70000-90000"
    elif r == 'Kiwami':
        coin_value = "50000-64000"
    elif r == 'Kami':
        coin_value = "10000-20000"
    elif r == 'Hoshi':
        coin_value = "5000-7000"
    elif r == 'Ten':
        coin_value = "3000-5000"
    else:
        coin_value = "3000-5000"

    caption_text = f"""🦋 Here We Go Character Information !

🐞 Collection Name
{col_name}
━━━━━━━━━━━━━━━━━━
{card['card_id']} - {card['name']}
[ {get_rarity_emoji(r)} · {r_display} ]

🌍 GLOBAL DROP - {global_drop_count}

⚡ TOP 10 OWNERS OF THIS CHARACTER !
{owners_str}

❀ User Sell Price - {coin_value}{lock_str}"""

    is_video = (card['type'] == 'video') or (str(card['file_id']).startswith("BAA"))

    if is_video:
        try:
            await context.bot.send_video(chat_id=chat.id, video=card['file_id'], caption=caption_text, parse_mode="HTML")
        except Exception:
            try:
                await context.bot.send_animation(chat_id=chat.id, animation=card['file_id'], caption=caption_text, parse_mode="HTML")
            except Exception:
                await _safe_reply_text(update.effective_message, caption_text, parse_mode="HTML")
    else:
        try:
            await context.bot.send_photo(chat_id=chat.id, photo=card['file_id'], caption=caption_text, parse_mode="HTML")
        except Exception:
            try:
                await context.bot.send_document(chat_id=chat.id, document=card['file_id'], caption=caption_text, parse_mode="HTML")
            except Exception:
                await _safe_reply_text(update.effective_message, caption_text, parse_mode="HTML")

@restricted_command
async def glist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await render_glist_page(update, context, page=0)

async def render_glist_page(update_or_query, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    msg_id = update_or_query.message.message_id if isinstance(update_or_query, Update) and update_or_query.message else None
    async with db_pool.acquire() as conn:
        groups = await conn.fetch("SELECT chat_id, title, added_by_id, added_by_name FROM groups")

    if not groups:
        text = "🏰 Group list is empty."
        if isinstance(update_or_query, Update):
            return await _safe_reply_text(update_or_query.message, text, parse_mode="HTML")
        else:
            return await update_or_query.edit_message_text(text, parse_mode="HTML")

    per_page = 5
    total_groups = len(groups)
    total_pages = (total_groups + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))

    start_idx = page * per_page
    end_idx = start_idx + per_page
    current_groups = groups[start_idx:end_idx]

    group_blocks = []
    for idx, g in enumerate(current_groups, start=start_idx + 1):
        chat_id = g['chat_id']
        member_count = "N/A"
        try:
            member_count = await context.bot.get_chat_member_count(chat_id)
        except Exception:
            pass

        invite_link = "N/A"
        try:
            chat_obj = await context.bot.get_chat(chat_id)
            if chat_obj.invite_link:
                invite_link = chat_obj.invite_link
            elif chat_obj.username:
                invite_link = f"https://t.me/{chat_obj.username}"
            else:
                invite_link = await context.bot.export_chat_invite_link(chat_id)
        except Exception:
            pass

        adder_text = "—"
        if g['added_by_id']:
            adder_text = get_mention(g['added_by_id'], g['added_by_name'] or "User")

        block = f"""{idx}. {g['title']}
🆔 Gp ID: <code>{chat_id}</code>
👥 Gp Member: {member_count}
🔗 Gp Link: {invite_link}
👤 Addသူ name: {adder_text}"""
        group_blocks.append(block)

    groups_str = "\n\n".join(group_blocks)
    text = f"""🏰 Group List (Page {page + 1}/{total_pages})

{groups_str}"""

    buttons = []
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Back", callback_data=f"glist_{page - 1}", style="primary"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"glist_{page + 1}", style="primary"))
    if nav_row:
        buttons.append(nav_row)

    markup = InlineKeyboardMarkup(buttons) if buttons else None

    if isinstance(update_or_query, Update):
        await _safe_reply_text(update_or_query.message, text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
    else:
        await update_or_query.edit_message_text(text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)

async def check_group_active(bot, chat_id):
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
        if member.status not in ['left', 'kicked']:
            return True
    except Exception:
        pass
    return False

@restricted_command
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_id = update.message.message_id
    if update.effective_user.id != ADMIN_ID:
        return

    async with db_pool.acquire() as conn:
        u_count = await conn.fetchval("SELECT COUNT(*) FROM users")
        all_groups = await conn.fetch("SELECT chat_id FROM groups")

    tasks = [check_group_active(context.bot, g['chat_id']) for g in all_groups]
    results = await asyncio.gather(*tasks)
    active_g_count = sum(1 for r in results if r)

    await _safe_reply_text(update.effective_message, 
        f"📊 Bot Stats:\n\n👥 Users: {u_count}\n🏰 Groups: {active_g_count}",
        parse_mode="HTML"
    )

async def run_async_broadcast(bot, chat_id, reply_msg_id, targets, status_msg_id):
    success = 0
    for tid in targets:
        try:
            await bot.forward_message(
                chat_id=tid,
                from_chat_id=chat_id,
                message_id=reply_msg_id
            )
            success += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass

    try:
        asyncio.create_task(bot.send_message(chat_id, f"✅ Broadcast completed! (Success: {success}/{len(targets)})"))
    except Exception:
        pass

@restricted_command
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_id = update.message.message_id
    if update.effective_user.id != ADMIN_ID:
        return
    reply = update.message.reply_to_message
    if not reply:
        return await _safe_reply_text(update.effective_message, "❌ Reply to the message you want to broadcast.")

    async with db_pool.acquire() as conn:
        users = [r['user_id'] for r in await conn.fetch("SELECT user_id FROM users")]
        groups = [r['chat_id'] for r in await conn.fetch("SELECT chat_id FROM groups")]

    targets = list(set(users + groups))
    status_msg = await _safe_reply_text(update.effective_message, f"🚀 Broadcasting started... (Targets: {len(targets)})")

    asyncio.create_task(run_async_broadcast(
        context.bot,
        update.effective_chat.id,
        reply.message_id,
        targets,
        status_msg.message_id
    ))

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg = update.message
    if not chat or not msg or not user or user.is_bot:
        return

    if user.id == ADMIN_ID and user.id in pending_group_sends and msg.text and not msg.text.startswith("/"):
        pending = pending_group_sends.pop(user.id)
        successes = 0
        failures = 0
        for target_ref in pending["targets"]:
            try:
                target_chat = await context.bot.get_chat(target_ref)
                key = str(uuid.uuid4())
                async with db_pool.acquire() as conn:
                    star_total = await conn.fetchval(
                        "SELECT COUNT(*) FROM send_message_reactions"
                    )
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("Like❤ 0", callback_data=f"sreact_{key}_like", style="primary"),
                        InlineKeyboardButton("Chee💩 0", callback_data=f"sreact_{key}_chee", style="danger"),
                        InlineKeyboardButton("HaHa😂 0", callback_data=f"sreact_{key}_haha"),
                    ],
                    [InlineKeyboardButton("Cm🖱", url=MAIN_GROUP_LINK)],
                    [InlineKeyboardButton(f"⭐ {star_total}", callback_data="sreact_total")],
                ])
                sent_message = await context.bot.send_message(
                    chat_id=target_chat.id,
                    text=f"🦋 Message \\n\\n{msg.text}",
                    reply_markup=keyboard
                )
                async with db_pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO send_message_likes(message_key,total_likes,chat_id,message_id) VALUES($1,0,$2,$3)",
                        key, target_chat.id, sent_message.message_id
                    )
                successes += 1
            except Exception as e:
                failures += 1
                print(f"/send delivery failed: {type(e).__name__}: {e}")
        return await _safe_reply_text(msg, f"✅ Sent: {successes} | Failed: {failures}")

    asyncio.create_task(register_user_group(user, chat, context))

    if msg.text and msg.text.strip().lower() == ".k" and msg.reply_to_message:
        if msg.reply_to_message.message_id in active_card_drops:
            return await reveal_spawn_name(update, context)

    if chat.type == "private":
        has_pending = user.id in pending_kachi_transfers
        if has_pending:
            target_id = pending_kachi_transfers[user.id]
            text_input = msg.text.strip() if msg.text else ""
            if not text_input.isdigit():
                return await _safe_reply_text(msg, "❌ Please enter a valid number for Kachi amount.")
            
            amount = int(text_input)
            if amount <= 0:
                return await _safe_reply_text(msg, "❌ Amount must be greater than 0.")

            async with db_pool.acquire() as conn:
                async with conn.transaction():
                    sender_row = await conn.fetchrow("SELECT coins, name FROM users WHERE user_id = $1 FOR UPDATE", user.id)
                    sender_coins = sender_row['coins'] if sender_row else 1000
                    sender_name = sender_row['name'] if sender_row else user.first_name

                    if sender_coins < amount:
                        if user.id in pending_kachi_transfers:
                            del pending_kachi_transfers[user.id]
                        return await _safe_reply_text(msg, f"❌ Insufficient Kachi balance. (Current: {sender_coins} Kachi)")

                    target_row = await conn.fetchrow("SELECT coins, name FROM users WHERE user_id = $1 FOR UPDATE", target_id)
                    target_coins = target_row['coins'] if target_row else 1000
                    target_name = target_row['name'] if target_row else "User"

                    new_sender_coins = max(0, sender_coins - amount)
                    new_target_coins = target_coins + amount

                    await conn.execute("UPDATE users SET coins = $1 WHERE user_id = $2", new_sender_coins, user.id)
                    await conn.execute("UPDATE users SET coins = $1 WHERE user_id = $2", new_target_coins, target_id)

            if user.id in pending_kachi_transfers:
                del pending_kachi_transfers[user.id]

            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            receipt_sender = f"""✦ KACHI TRANSFER RECEIPT ✦

📤 Sender: {get_mention(user.id, sender_name)}
📥 Recipient: {get_mention(target_id, target_name)}
💎 Amount Transferred: {amount} Kachi
💳 Remaining Balance: {new_sender_coins} Kachi

Status: Successful ✅
🕒 Time: {time_str} UTC"""

            receipt_target = f"""✦ KACHI TRANSFER RECEIPT ✦

📤 Sender: {get_mention(user.id, sender_name)}
📥 Recipient: {get_mention(target_id, target_name)}
💎 Amount Transferred: {amount} Kachi
💳 Current Balance: {new_target_coins} Kachi

Status: Successful ✅
🕒 Time: {time_str} UTC"""

            await _safe_reply_text(msg, receipt_sender, parse_mode="HTML")
            try:
                asyncio.create_task(context.bot.send_message(target_id, receipt_target, parse_mode="HTML"))
            except Exception:
                pass
            return

    if chat.type in ["group", "supergroup"]:
        chat_id = chat.id
        now = time.time()
        key = (chat_id, user.id)

        # Persisted spam/ban/mute restrictions also block reply-based card catches.
        if user.id != ADMIN_ID:
            restriction = await get_user_restriction_status(user.id)
            if restriction:
                return

        if key in spam_ignores and spam_ignores[key] > now:
            return

        if key not in user_message_timestamps:
            user_message_timestamps[key] = []
        
        user_message_timestamps[key] = [ts for ts in user_message_timestamps[key] if now - ts <= 5]
        user_message_timestamps[key].append(now)

        is_spamming = len(user_message_timestamps[key]) >= 4
        if is_spamming:
            spam_ignores[key] = now + 600
            user_message_timestamps[key] = []
            try:
                async with db_pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO spam_restrictions (user_id, until_ts, reason) VALUES ($1, $2, 'Spam') "
                        "ON CONFLICT (user_id) DO UPDATE SET until_ts = EXCLUDED.until_ts, reason = EXCLUDED.reason",
                        user.id, int(now + 600)
                    )
            except Exception as e:
                print(f"Could not persist spam restriction for {user.id}: {type(e).__name__}: {e}")

        if is_spamming:
            warning_text = f"{get_mention(user.id, user.first_name)} 10minutes ignore please don't spam like that again ⛩"
            try:
                await _safe_reply_text(msg, warning_text, parse_mode="HTML")
            except Exception:
                pass

            invite_link = "N/A"
            try:
                chat_obj = await context.bot.get_chat(chat_id)
                if chat_obj.invite_link:
                    invite_link = chat_obj.invite_link
                elif chat_obj.username:
                    invite_link = f"https://t.me/{chat_obj.username}"
                else:
                    invite_link = await context.bot.export_chat_invite_link(chat_id)
            except Exception:
                pass

            username_str = f"@{user.username}" if user.username else "None"
            log_text = f"""🚨 Spam Detected!

👤 Spam Name: {get_mention(user.id, user.first_name)}
🏷 Username: {username_str}
🆔 User ID: <code>{user.id}</code>
🏰 Group Name: {chat.title or 'Group'}
🔗 Group Link: {invite_link}"""

            try:
                asyncio.create_task(context.bot.send_message(ADMIN_ID, log_text, parse_mode="HTML", disable_web_page_preview=True))
            except Exception:
                pass

            return

        try:
            async with db_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("""
                        INSERT INTO groups (chat_id, title, msg_count) VALUES ($1, $2, 0)
                        ON CONFLICT (chat_id) DO UPDATE SET title = COALESCE($2, groups.title)
                    """, chat_id, chat.title or "Group")
                    
                    threshold = 95

                    row = await conn.fetchrow("""
                        UPDATE groups 
                        SET threshold = 95,
                            msg_count = CASE WHEN msg_count + 1 >= $2 THEN 0 ELSE msg_count + 1 END
                        WHERE chat_id = $1
                        RETURNING msg_count, (msg_count = 0) AS should_drop
                    """, chat_id, threshold)

                    if row and row['should_drop']:
                        asyncio.create_task(trigger_card_drop(context, chat_id))
        except Exception as e:
            print(f"Error in group message handler: {e}")

async def trigger_card_drop(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    try:
        anim = await context.bot.send_message(chat_id, '🐌')
    except Exception:
        anim = None
    await asyncio.sleep(4)
    if anim:
        try: await context.bot.delete_message(chat_id, anim.message_id)
        except Exception: pass
    async with db_pool.acquire() as conn:
        card = await get_weighted_random_card(conn)
    if card:
        await send_character_spawn(context, chat_id, card)


async def run_kbox_task(bot, chat_id, query, user_id, display_name):
    try:
        loading_steps = [
            "Card Opening Wait\nLoading... [ 25% ]",
            "Card Opening Wait\nLoading... [ 50% ]",
            "Card Opening Wait\nLoading... [ 75% ]",
            "Card Opening Wait\nLoading... [ 100% ]"
        ]

        try:
            await query.edit_message_text(loading_steps[0], parse_mode="HTML")
        except Exception:
            pass

        for step in loading_steps[1:]:
            await asyncio.sleep(0.6)
            try:
                await query.edit_message_text(step, parse_mode="HTML")
            except Exception:
                pass

        await asyncio.sleep(0.6)

        async with db_pool.acquire() as conn:
            async with conn.transaction():
                card = await get_pre_kbox_weighted_card(conn, user_id)
                if not card:
                    try:
                        await query.edit_message_text(
                            "❌ No eligible cards available. You may already own 3 copies of every available card.",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                    # Return the Kbox fee if no eligible card can be awarded.
                    await conn.execute("UPDATE users SET coins = coins + 1000, kbox_count = GREATEST(0, kbox_count - 1) WHERE user_id = $1", user_id)
                    return

                added = await add_user_card_with_limit(conn, user_id, card['card_id'], 1)
                if not added:
                    await conn.execute("UPDATE users SET coins = coins + 1000, kbox_count = GREATEST(0, kbox_count - 1) WHERE user_id = $1", user_id)
                    try:
                        await query.edit_message_text("❌ You already own 3 copies of that card. Your Kbox fee was refunded.", parse_mode="HTML")
                    except Exception:
                        pass
                    return
                await check_and_auto_lock_card(conn, card['card_id'])

        r = card.get('rarity', 'Ten')
        r_display = get_rarity_display(r)
        col_name = card.get('collection') or "🎞 Ope Ope No Mi [ 🩺]"

        result_caption = f"""🎉 Congratulations! {get_mention(user_id, display_name)} You got a card!
Card Name : {card['name']}
Id : <code>{card['card_id']}</code>
Rarity : {r_display}
Collection : {col_name}

🔍 Check: /kc
📖 Details: /card {card['card_id']}"""

        # Kbox rewards must always be displayed with their original photo/video media.
        # Never replace a failed media delivery with a text-only card result.
        try:
            if card['type'] == 'photo':
                await bot.send_photo(
                    chat_id=chat_id, photo=card['file_id'],
                    caption=result_caption, parse_mode="HTML"
                )
            else:
                await bot.send_video(
                    chat_id=chat_id, video=card['file_id'],
                    caption=result_caption, parse_mode="HTML"
                )
        except Exception as send_error:
            print(f"Could not send Kbox card media: {send_error}")
            try:
                await query.edit_message_text(
                    "❌ Your Kbox card was added, but its photo/video could not be displayed. Please contact an admin.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        else:
            try:
                await query.message.delete()
            except Exception:
                pass
    except Exception as e:
        print(f"Error in run_kbox_task: {e}")
        try:
            await query.edit_message_text("❌ An error occurred while opening Kbox. Please try again later.", parse_mode="HTML")
        except Exception:
            pass
    finally:
        active_kbox_users.discard(user_id)


# ---------------- Tiger vs Dragon Kachi betting game ----------------
TG_BET_SECONDS = 60
TG_NEXT_ROUND_SECONDS = 10
TG_MAX_BET = 100000
TG_CARD_SUITS = ("♠️", "♥️", "♦️", "♣️")
TG_CARD_RANKS = (("A", 1), ("2", 2), ("3", 3), ("4", 4), ("5", 5),
                 ("6", 6), ("7", 7), ("8", 8), ("9", 9), ("10", 10),
                 ("J", 11), ("Q", 12), ("K", 13))


def tg_game_keyboard(round_id, bot_username):
    # Deep links open the bot DM directly; start payload selects the side.
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🐅  ကျား (T) လောင်းမယ်", url=f"https://t.me/{bot_username}?start=tg_{round_id}_T", style="success"),
        InlineKeyboardButton("🐉  နဂါး (D) လောင်းမယ်", url=f"https://t.me/{bot_username}?start=tg_{round_id}_D", style="success")
    ]])


def tg_game_text(round_state, status="🟢 လောင်းကြေးဖွင့်ထားသည်"):
    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🐅 ကျားနဂါးတိုက်ပွဲ 🐉\n"
        "TIGER  ⚔️  DRAGON\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🐅 ကျား TIGER\n"
        f"💰 {round_state['tiger_total']:,} Kachi\n\n"
        "🐉 နဂါး DRAGON\n"
        f"💰 {round_state['dragon_total']:,} Kachi\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⏳ {status}\n"
        "💎 အများဆုံးလောင်းကြေး — 100,000 Kachi\n\n"
        "👇 အောက်က ခလုတ်နဲ့ ဘက်ရွေးပြီး Bot DM မှာ လောင်းကြေးထည့်ပါ"
    )


def tg_result_text(round_state, tiger_label, dragon_label, result_label, winners):
    text = (
        "━━━━━━━━━━━━━━━━━━\n"
        "🏁 TIGER  VS  DRAGON — RESULT\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🐅 Tiger: {tiger_label}\n"
        f"🐉 Dragon: {dragon_label}\n\n"
        f"{result_label}\n\n"
        f"🐅 Tiger Bet: {round_state['tiger_total']:,} Kachi\n"
        f"🐉 Dragon Bet: {round_state['dragon_total']:,} Kachi\n\n"
    )
    if winners:
        text += "🏆 WINNERS\n" + " · ".join(winners)
    elif result_label.startswith("🤝"):
        text += "🤝 သရေဖြစ်သည် — လောင်းကြေး 2x ပြန်ရရှိပါသည်။"
    else:
        text += "🏆 ဒီပွဲမှာ အနိုင်ရသူ မရှိပါ။"
    return text


@restricted_command
async def tg_start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global tg_game_task, tg_stop_requested
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or user.id != ADMIN_ID:
        return
    if not chat or chat.type != "private":
        return await _safe_reply_text(msg, "ဒီ Command ကို Bot DM မှာပဲ အသုံးပြုပါ။")
    if tg_game_task and not tg_game_task.done():
        return await _safe_reply_text(msg, "🐅🐉 Tiger vs Dragon Game လည်ပတ်နေပြီးသားပါ။")
    tg_stop_requested = False
    tg_game_task = asyncio.create_task(run_tg_game(context))
    await _safe_reply_text(msg, "✅ Tiger vs Dragon Game ကို Main Channel မှာ စတင်လိုက်ပါပြီ။")


@restricted_command
async def tg_stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global tg_stop_requested
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or user.id != ADMIN_ID:
        return
    if not chat or chat.type != "private":
        return await _safe_reply_text(msg, "ဒီ Command ကို Bot DM မှာပဲ အသုံးပြုပါ။")
    if not tg_game_task or tg_game_task.done():
        return await _safe_reply_text(msg, "လက်ရှိ Tiger vs Dragon Game မဖွင့်ထားပါ။")
    tg_stop_requested = True
    await _safe_reply_text(msg, "🛑 /stoptg လက်ခံပြီးပါပြီ။ လက်ရှိပွဲပြီးဆုံးတာနဲ့ Game ကို ရပ်ပါမယ်။")


async def run_tg_game(context: ContextTypes.DEFAULT_TYPE):
    global tg_stop_requested, tg_current_round, tg_game_task
    try:
        bot_info = await context.bot.get_me()
        while not tg_stop_requested:
            round_id = str(int(time.time() * 1000))
            round_state = {
                "round_id": round_id,
                "open": True,
                "settled": False,
                "bets": {},
                "tiger_total": 0,
                "dragon_total": 0,
                "message_id": None
            }
            tg_rounds[round_id] = round_state
            tg_current_round = round_state
            try:
                game_msg = await context.bot.send_message(
                    chat_id=TIGER_DRAGON_CHANNEL_ID,
                    text=tg_game_text(round_state),
                    parse_mode="HTML",
                    reply_markup=tg_game_keyboard(round_id, bot_info.username)
                )
                round_state["message_id"] = game_msg.message_id
            except Exception as e:
                print(f"Tiger vs Dragon could not post to channel: {e}")
                tg_rounds.pop(round_id, None)
                break

            await asyncio.sleep(TG_BET_SECONDS)
            round_state["open"] = False
            try:
                await context.bot.edit_message_text(
                    chat_id=TIGER_DRAGON_CHANNEL_ID,
                    message_id=round_state["message_id"],
                    text=tg_game_text(round_state, "🔒 လောင်းကြေးပိတ်ပါပြီ။ ဖဲဖွင့်နေပါသည်..."),
                    parse_mode="HTML"
                )
            except Exception as e:
                print(f"Tiger vs Dragon could not close bet post: {e}")

            deck = [(rank, value, suit) for rank, value in TG_CARD_RANKS for suit in TG_CARD_SUITS]
            tiger_card, dragon_card = random.sample(deck, 2)
            if tiger_card[1] > dragon_card[1]:
                result = "T"
                result_label = "🐅 TIGER အနိုင်ရပါတယ်!"
            elif dragon_card[1] > tiger_card[1]:
                result = "D"
                result_label = "🐉 DRAGON အနိုင်ရပါတယ်!"
            else:
                result = "DRAW"
                result_label = "🤝 သရေကျပါတယ်!"

            # Settle each stake exactly once. Stakes were deducted when accepted.
            winner_mentions = []
            for bettor_id, bet in list(round_state["bets"].items()):
                amount = bet["amount"]
                if result == "DRAW":
                    payout = amount * 2
                    outcome = "သရေ 2x"
                elif bet["side"] == result:
                    payout = amount * 3 // 2
                    outcome = "အနိုင် 1.5x"
                else:
                    payout = 0
                    outcome = "အရှုံး"
                if payout > 0:
                    async with db_pool.acquire() as conn:
                        async with conn.transaction():
                            await conn.execute(
                                "UPDATE users SET coins = coins + $1 WHERE user_id = $2",
                                payout, bettor_id
                            )
                if result != "DRAW" and bet["side"] == result:
                    winner_mentions.append(get_mention(bettor_id, bet["name"]))

            round_state["settled"] = True
            tiger_label = f"{tiger_card[0]}{tiger_card[2]} ({tiger_card[1]})"
            dragon_label = f"{dragon_card[0]}{dragon_card[2]} ({dragon_card[1]})"
            result_text = tg_result_text(
                round_state, tiger_label, dragon_label, result_label, winner_mentions
            )
            try:
                # Reuse the round post for the result, then remove it before next round.
                await context.bot.edit_message_text(
                    chat_id=TIGER_DRAGON_CHANNEL_ID,
                    message_id=round_state["message_id"],
                    text=result_text,
                    parse_mode="HTML"
                )
            except Exception as e:
                print(f"Tiger vs Dragon result update failed: {e}")

            await asyncio.sleep(TG_NEXT_ROUND_SECONDS)
            try:
                await context.bot.delete_message(
                    chat_id=TIGER_DRAGON_CHANNEL_ID,
                    message_id=round_state["message_id"]
                )
            except Exception as e:
                print(f"Tiger vs Dragon old post delete failed: {e}")

            tg_rounds.pop(round_id, None)
            if tg_current_round is round_state:
                tg_current_round = None
            # Remove pending DM prompts associated with this finished round.
            for pending_uid, pending in list(tg_pending_bets.items()):
                if pending.get("round_id") == round_id:
                    tg_pending_bets.pop(pending_uid, None)

            if tg_stop_requested:
                break

    except Exception as e:
        print(f"Tiger vs Dragon loop error: {type(e).__name__}: {e}")
    finally:
        tg_stop_requested = False
        tg_current_round = None
        tg_game_task = None
        try:
            await context.bot.send_message(
                chat_id=TIGER_DRAGON_CHANNEL_ID,
                text="🛑 Tiger vs Dragon Game ရပ်နားလိုက်ပါပြီ။",
                parse_mode="HTML"
            )
        except Exception:
            pass


async def handle_tg_bet_callback(query, context):
    user = query.from_user
    parts = query.data.split("_")
    if len(parts) != 3:
        return await query.answer("Game data မမှန်ကန်ပါ။", show_alert=True)
    round_id, side = parts[1], parts[2]
    round_state = tg_rounds.get(round_id)
    if not round_state or not round_state["open"]:
        return await query.answer("⏰ ဒီပွဲရဲ့ လောင်းကြေးပိတ်သွားပါပြီ။", show_alert=True)
    if user.id in round_state["bets"]:
        return await query.answer("ဒီပွဲမှာ လောင်းကြေးထည့်ပြီးပါပြီ။", show_alert=True)
    if user.id in tg_pending_bets:
        return await query.answer("Bot DM မှာ လောင်းကြေးပမာဏ အရင်ထည့်ပေးပါ။", show_alert=True)
    tg_pending_bets[user.id] = {"round_id": round_id, "side": side}
    side_name = "🐅 ကျား (TIGER)" if side == "T" else "🐉 နဂါး (DRAGON)"
    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=(
                f"{side_name} ကို ရွေးထားပါတယ်။\n"
                "လောင်းကြေး Kachi ပမာဏကို အခုရိုက်ထည့်ပါ။\n"
                "အနည်းဆုံး 1၊ အများဆုံး 100,000 Kachi ဖြစ်ပါတယ်။\n"
                "ဥပမာ - 5000"
            )
        )
    except Exception:
        tg_pending_bets.pop(user.id, None)
        return await query.answer("Bot DM ကို အရင် /start လုပ်ပြီးမှ လောင်းနိုင်ပါတယ်။", show_alert=True)
    await query.answer("Bot DM ကို စစ်ပြီး လောင်းကြေးထည့်ပါ။")


async def tg_bet_amount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg or user.id not in tg_pending_bets:
        return
    pending = tg_pending_bets.get(user.id)
    round_state = tg_rounds.get(pending["round_id"])
    if not round_state or not round_state["open"]:
        tg_pending_bets.pop(user.id, None)
        return await _safe_reply_text(msg, "⏰ ဒီပွဲရဲ့ လောင်းကြေးပိတ်သွားပါပြီ။ နောက်ပွဲမှာ ပြန်လောင်းနိုင်ပါတယ်။")
    raw_amount = (msg.text or "").strip().replace(",", "")
    if not raw_amount.isdigit():
        return await _safe_reply_text(msg, "❌ Kachi ပမာဏကို ဂဏန်းနဲ့ပဲ ရိုက်ထည့်ပါ။ ဥပမာ 5000")
    amount = int(raw_amount)
    if amount < 1 or amount > TG_MAX_BET:
        return await _safe_reply_text(msg, "❌ တစ်ကြိမ်လောင်းကြေးသည် 1 မှ 100,000 Kachi အတွင်း ဖြစ်ရပါမယ်။")

    async with tg_game_lock:
        # Re-check after lock to prevent double submissions / late bets.
        round_state = tg_rounds.get(pending["round_id"])
        if not round_state or not round_state["open"]:
            tg_pending_bets.pop(user.id, None)
            return await _safe_reply_text(msg, "⏰ လောင်းကြေးပိတ်သွားပါပြီ။")
        if user.id in round_state["bets"]:
            tg_pending_bets.pop(user.id, None)
            return await _safe_reply_text(msg, "ဒီပွဲမှာ လောင်းကြေးထည့်ပြီးပါပြီ။")
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                updated = await conn.fetchrow(
                    """UPDATE users SET coins = coins - $1
                       WHERE user_id = $2 AND coins >= $1
                       RETURNING coins""",
                    amount, user.id
                )
                if not updated:
                    current = await conn.fetchval(
                        "SELECT coins FROM users WHERE user_id = $1", user.id
                    )
                    return await _safe_reply_text(msg, 
                        f"❌ Kachi မလုံလောက်ပါ။ လက်ကျန် {current or 0:,} Kachi ရှိပါတယ်။"
                    )
        side = pending["side"]
        round_state["bets"][user.id] = {
            "side": side, "amount": amount, "name": user.first_name
        }
        if side == "T":
            round_state["tiger_total"] += amount
            side_name = "🐅 Tiger"
        else:
            round_state["dragon_total"] += amount
            side_name = "🐉 Dragon"
        tg_pending_bets.pop(user.id, None)

    await _safe_reply_text(msg, 
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✅ BET CONFIRMED\n"
        f"{side_name} ဘက် — {amount:,} Kachi\n"
        f"💰 လက်ကျန် — {updated['coins']:,} Kachi\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        "ပွဲရလဒ်ကို Channel မှာ ကြည့်နိုင်ပါတယ်။",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📣 Channel ကို ပြန်သွားမယ်", url="https://t.me/beyondpoe", style="primary")]])
    )
    try:
        bot_info = await context.bot.get_me()
        await context.bot.edit_message_text(
            chat_id=TIGER_DRAGON_CHANNEL_ID,
            message_id=round_state["message_id"],
            text=tg_game_text(round_state),
            parse_mode="HTML",
            reply_markup=tg_game_keyboard(round_state["round_id"], bot_info.username)
        )
    except Exception as e:
        print(f"Tiger vs Dragon total bet update failed: {e}")



@restricted_command
async def kdic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not msg or not user or not chat:
        return
    key = (chat.id, user.id)
    if key in active_kdic_games:
        previous = active_kdic_games[key]
        previous_chat_id = str(previous.get("chat_id", chat.id))
        previous_message_id = previous.get("message_id")
        if previous_message_id:
            if previous_chat_id.startswith("-100"):
                previous_link = f"https://t.me/c/{previous_chat_id[4:]}/{previous_message_id}"
            else:
                previous_link = f"tg://openmessage?chat_id={previous_chat_id}&message_id={previous_message_id}"
            previous_text = f"အရင်ပွဲကို <a href=\"{previous_link}\">ဒီနေရာမှာ</a> အဆုံးသတ်ပေးပါ။"
        else:
            previous_text = "အရင်ပွဲကို အရင်ဆုံး အဆုံးသတ်ပေးပါ။"
        return await _safe_reply_text(msg, 
            f"⚠️ Dice Game အရင်ပွဲ မပြီးသေးပါဘူး။\n{previous_text}",
            parse_mode="HTML"
        )
    now = time.time()
    last_game = kdic_cooldowns.get(user.id, 0)
    if now - last_game < 20:
        remaining = max(1, int(20 - (now - last_game)))
        return await _safe_reply_text(msg, 
            f"⏳ နောက်တစ်ပွဲကစားရန် {remaining} စက္ကန့် စောင့်ပေးပါ။",
            parse_mode="HTML"
        )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Small (1–3)", callback_data=f"kdic_side_{user.id}_small", style="primary"),
        InlineKeyboardButton("Big (4–6)", callback_data=f"kdic_side_{user.id}_big", style="primary"),
    ]])
    sent = await _safe_reply_text(msg, "🎲 Kachi Dice Game\n\nSmall — 1 / 2 / 3\nBig — 4 / 5 / 6\n\nဘယ်ဘက်ကို ရွေးမလဲ?", parse_mode="HTML", reply_markup=keyboard)
    active_kdic_games[key] = {
        "owner_id": user.id, "chat_id": chat.id, "message_id": sent.message_id,
        "stage": "side", "side": None, "bet": 0
    }


@restricted_command
async def kbet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not msg or not user or not chat:
        return
    key = (chat.id, user.id)
    game = active_kdic_games.get(key)
    if not game or game.get("stage") != "bet":
        return await _safe_reply_text(msg, "❌ အရင်ဆုံး /kdic နဲ့ဂိမ်းစပြီး Small / Big ရွေးပါ။")
    args = context.args or []
    if len(args) == 2 and args[0].lower() == "kachi":
        amount_text = args[1]
    elif len(args) == 1:
        amount_text = args[0]
    else:
        return await _safe_reply_text(msg, "အသုံးပြုပုံ — /kbet Kachi 1000")
    try:
        amount = int(amount_text.replace(",", ""))
    except ValueError:
        return await _safe_reply_text(msg, "❌ Kachi ပမာဏကို ဂဏန်းနဲ့ရေးပါ။ ဥပမာ /kbet Kachi 1000")
    if amount <= 0:
        return await _safe_reply_text(msg, "❌ လောင်းကြေးသည် 1 Kachi ထက် မနည်းရပါ။")
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT coins FROM users WHERE user_id = $1 FOR UPDATE", user.id)
            if not row:
                return await _safe_reply_text(msg, "❌ User အကောင့်ကို မတွေ့ပါ။ အရင်ဆုံး /start နှိပ်ပါ။")
            balance = int(row["coins"] or 0)
            if balance < amount:
                return await _safe_reply_text(msg, f"❌ Kachi မလုံလောက်ပါ။ လက်ကျန် {balance:,} Kachi ရှိပါတယ်။")
            await conn.execute("UPDATE users SET coins = coins - $1 WHERE user_id = $2", amount, user.id)
    game["bet"] = amount
    game["stage"] = "spin"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎲 Spin", callback_data=f"kdic_spin_{user.id}", style="success"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"kdic_cancel_{user.id}", style="danger"),
    ]])
    sent = await _safe_reply_text(msg, f"🎲 Dice Game Ready\n\nရွေးထားသောဘက် — {game['side'].title()}\nလောင်းကြေး — {amount:,} Kachi\n\nSpin နှိပ်ပြီး ကစားပါ။", parse_mode="HTML", reply_markup=keyboard)
    game["message_id"] = sent.message_id


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query and query.data and query.data.startswith("sreact_"):
        parts = query.data.split("_")
        if query.data == "sreact_total":
            return await query.answer("⭐ Total reactions across all /send messages.")
        if len(parts) != 3 or parts[2] not in ("like", "chee", "haha"):
            return await query.answer("Invalid reaction.", show_alert=True)
        key, reaction = parts[1], parts[2]
        uid = query.from_user.id
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                exists = await conn.fetchval(
                    "SELECT reaction FROM send_message_reactions WHERE message_key=$1 AND user_id=$2",
                    key, uid
                )
                if exists:
                    return await query.answer(
                        "ဒီ message ကို reaction ပေးပြီးပါပြီ။ တစ်ခုသာပေးလို့ရပါတယ်။"
                    )
                inserted = await conn.fetchval(
                    """INSERT INTO send_message_reactions(message_key,user_id,reaction)
                       VALUES($1,$2,$3) ON CONFLICT DO NOTHING RETURNING reaction""",
                    key, uid, reaction
                )
                if not inserted:
                    return await query.answer("ဒီ message ကို reaction ပေးပြီးပါပြီ။")
                star_total = await conn.fetchval(
                    "SELECT COUNT(*) FROM send_message_reactions"
                )
                per_message_counts = await conn.fetch(
                    """SELECT message_key,
                              COUNT(*) FILTER (WHERE reaction='like') AS likes,
                              COUNT(*) FILTER (WHERE reaction='chee') AS chees,
                              COUNT(*) FILTER (WHERE reaction='haha') AS hahas
                       FROM send_message_reactions
                       GROUP BY message_key"""
                )
                sent_messages = await conn.fetch(
                    """SELECT message_key,chat_id,message_id
                       FROM send_message_likes
                       WHERE chat_id IS NOT NULL AND message_id IS NOT NULL"""
                )
        count_map = {r["message_key"]: r for r in per_message_counts}
        # Reaction buttons show counts for that individual message.
        # The star row shows the all-time total across every /send message.
        for sent_row in sent_messages:
            local = count_map.get(sent_row["message_key"])
            likes = local["likes"] if local else 0
            chees = local["chees"] if local else 0
            hahas = local["hahas"] if local else 0
            markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        f"Like❤ {likes}",
                        callback_data=f"sreact_{sent_row['message_key']}_like",
                        style="primary"
                    ),
                    InlineKeyboardButton(
                        f"Chee💩 {chees}",
                        callback_data=f"sreact_{sent_row['message_key']}_chee",
                        style="danger"
                    ),
                    InlineKeyboardButton(
                        f"HaHa😂 {hahas}",
                        callback_data=f"sreact_{sent_row['message_key']}_haha"
                    ),
                ],
                [InlineKeyboardButton("Cm🖱", url=MAIN_GROUP_LINK)],
                [InlineKeyboardButton(f"⭐ {star_total}", callback_data="sreact_total")],
            ])
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=sent_row["chat_id"],
                    message_id=sent_row["message_id"],
                    reply_markup=markup
                )
            except Exception as e:
                print(
                    f"Reaction counter update failed for "
                    f"{sent_row['chat_id']}/{sent_row['message_id']}: {e}"
                )
        label = {"like": "Like❤", "chee": "Chee💩", "haha": "HaHa😂"}[reaction]
        return await query.answer(label)
    if query and query.data and query.data.startswith("tdcpage_"):
        parts=query.data[len("tdcpage_"):].split("_"); date=datetime.date.fromisoformat(parts[0]); page=int(parts[1])
        await query.answer()
        # Update the existing page in place.
        async with db_pool.acquire() as conn:
            rows=await conn.fetch("""SELECT u.user_id,COALESCE(u.name,'Catcher') AS name,SUM(d.count)::BIGINT AS total_catches
                FROM daily_spawn_claims d JOIN users u ON u.user_id=d.user_id WHERE d.claim_date=$1 AND d.count>0
                GROUP BY u.user_id,u.name ORDER BY total_catches DESC,u.user_id ASC""",date)
        page_size=15; pages=max(1,(len(rows)+page_size-1)//page_size); page=max(0,min(page,pages-1)); chunk=rows[page*page_size:(page+1)*page_size]
        lines=["🏆 DAILY CATCHER",f"📅 {date.strftime('%d %b %Y')}","","Successful Drop Catchers","▰▱▰▱▰▱▰▱▰▱▰"]
        medals=["🥇","🥈","🥉"]
        for i,row in enumerate(chunk):
            rank=page*15+i; prefix=medals[rank] if rank<3 else "☞"; lines.append(f"{prefix} {get_mention(row['user_id'],row['name'])} — {row['total_catches']} catches")
        lines.extend(["▰▱▰▱▰▱▰▱▰▱▰",f"Page {page+1}/{pages}","Only successful automatic card drops are counted. /pre and /kbox are excluded."])
        nav=[]
        if page>0: nav.append(InlineKeyboardButton("◀️ Back",callback_data=f"tdcpage_{date.isoformat()}_{page-1}"))
        if page+1<pages: nav.append(InlineKeyboardButton("Next ▶️",callback_data=f"tdcpage_{date.isoformat()}_{page+1}"))
        return await query.edit_message_text("\n".join(lines),parse_mode="HTML",reply_markup=InlineKeyboardMarkup([nav]) if nav else None)
    if query and query.data and query.data.startswith("highquiz_"):
        quiz = active_high_rarity_quizzes.get(query.message.message_id if query.message else None)
        if not quiz or quiz.get("used"):
            return await query.answer("ဒီရွေးချယ်မှုကို အသုံးပြုပြီးပါပြီ။ နောက်ကတ်ကို စောင့်ပါ။", show_alert=True)
        if time.time() >= quiz.get("expires_at", quiz.get("created_at", 0) + 50):
            quiz["used"] = True
            active_high_rarity_quizzes.pop(query.message.message_id, None)
            try:
                await query.edit_message_text(
                    "⌛ အချိန် 50 စက္ကန့် ပြည့်သွားပါပြီ။ ကဒ်သက်တမ်းကုန်သွားပါပြီ။",
                    reply_markup=None
                )
            except Exception:
                pass
            return await query.answer("ဒီမေးခွန်း သက်တမ်းကုန်သွားပါပြီ။ နောက်ကတ်ကို စောင့်ပါ။", show_alert=True)
        quiz["used"] = True
        selected = int(query.data.rsplit("_", 1)[1])
        active_high_rarity_quizzes.pop(query.message.message_id, None)
        if selected != quiz["correct"]:
            mention = get_mention(query.from_user.id, query.from_user.first_name)
            try:
                await query.edit_message_text(
                    f"❌ {mention} သည် အမှားကို နှိပ်လိုက်ပါပြီ။\nနောက်ကတ်ကျမှ ထပ်ကောက်ပါ။",
                    parse_mode="HTML", reply_markup=None
                )
            except Exception:
                pass
            return await query.answer("Wrong button!", show_alert=True)
        try:
            await query.edit_message_text("✅ မှန်ကန်ပါတယ်။ High Rarity ကတ် ပေါ်လာပါပြီ။", reply_markup=None)
        except Exception:
            pass
        await query.answer("Correct! High Rarity card unlocked.")
        await send_character_spawn(context, quiz["chat_id"], quiz["card"], quiz_passed=True)
        return
    if query and query.data and query.data.startswith("kdic_"):
        data = query.data
        user = query.from_user
        chat_id = query.message.chat_id if query.message else None
        if not chat_id:
            return await query.answer()
        parts = data.split("_")
        try:
            owner_id = int(parts[2])
        except (ValueError, IndexError):
            return await query.answer("ဒီဂိမ်းကို အသုံးမပြုနိုင်တော့ပါ။", show_alert=True)
        if user.id != owner_id:
            return await query.answer("❌ ဒီဂိမ်းကို စတင်ထားသူတစ်ယောက်တည်းသာ နှိပ်နိုင်ပါတယ်။", show_alert=True)
        key = (chat_id, owner_id)
        game = active_kdic_games.get(key)
        if not game:
            return await query.answer("ဒီဂိမ်း သက်တမ်းကုန်သွားပါပြီ။", show_alert=True)
        action = parts[1]
        if action == "side":
            if game.get("stage") != "side":
                return await query.answer("ဘက်ရွေးချယ်မှု ပြီးသွားပါပြီ။", show_alert=True)
            side = parts[3] if len(parts) > 3 else ""
            if side not in ("small", "big"):
                return await query.answer("ရွေးချယ်မှု မမှန်ပါ။", show_alert=True)
            game["side"] = side
            game["stage"] = "bet"
            await query.answer()
            await query.edit_message_text(f"🎲 {side.title()} ကို ရွေးပြီးပါပြီ။\n\nလောင်းကြေးတင်ရန် — /kbet Kachi 1000\nဥပမာ — /kbet Kachi 5000", parse_mode="HTML", reply_markup=None)
            return
        if action == "cancel":
            if game.get("stage") != "spin":
                return await query.answer("Cancel လုပ်လို့မရတော့ပါ။", show_alert=True)
            amount = int(game.get("bet", 0))
            await add_coins(owner_id, amount)
            active_kdic_games.pop(key, None)
            kdic_cooldowns[owner_id] = time.time()
            await query.answer("လောင်းကြေး ပြန်အမ်းပြီးပါပြီ။")
            return await query.edit_message_text(f"❌ Game Cancelled\n💰 {amount:,} Kachi ပြန်အမ်းပြီးပါပြီ။\n\n⏳ နောက်တစ်ပွဲကစားရန် 20 စက္ကန့် စောင့်ပေးပါ။", parse_mode="HTML", reply_markup=None)
        if action == "spin":
            if game.get("stage") != "spin":
                return await query.answer("Spin လုပ်ရန် အဆင်သင့်မဖြစ်သေးပါ။", show_alert=True)
            game["stage"] = "rolling"
            await query.answer("Rolling...")
            try:
                dice_message = await context.bot.send_dice(chat_id=chat_id, emoji="🎲")
                value = dice_message.dice.value
                # Telegram dice animations take a few seconds to finish client-side.
                await asyncio.sleep(4)
                won = (game["side"] == "small" and value in (1, 2, 3)) or (game["side"] == "big" and value in (4, 5, 6))
                amount = int(game.get("bet", 0))
                payout = amount * 2 if won else 0
                if payout:
                    await add_coins(owner_id, payout)
                async with db_pool.acquire() as conn:
                    row = await conn.fetchrow("SELECT coins FROM users WHERE user_id = $1", owner_id)
                balance = int(row["coins"] or 0) if row else 0
                result = "🎉 YOU WIN!" if won else "😢 YOU LOSE!"
                result += f"\n\n🎲 ရလဒ် — {value}\nရွေးထားသောဘက် — {game['side'].title()}\nလောင်းကြေး — {amount:,} Kachi\nဆုငွေ — {payout:,} Kachi\n💰 လက်ကျန် — {balance:,} Kachi"
                active_kdic_games.pop(key, None)
                kdic_cooldowns[owner_id] = time.time()
                return await query.edit_message_text(
                    result + "\n\n⏳ နောက်တစ်ပွဲကစားရန် 20 စက္ကန့် စောင့်ပေးပါ။",
                    parse_mode="HTML", reply_markup=None
                )
            except Exception as e:
                print(f"/kdic spin failed: {e}")
                # If the dice animation failed, restore the reserved stake.
                await add_coins(owner_id, int(game.get("bet", 0)))
                active_kdic_games.pop(key, None)
                kdic_cooldowns[owner_id] = time.time()
                return await query.edit_message_text("❌ ဂိမ်းလုပ်ဆောင်ရာတွင် အမှားဖြစ်သွားသောကြောင့် လောင်းကြေးကို ပြန်အမ်းပေးလိုက်ပါတယ်။\n\n⏳ နောက်တစ်ပွဲကစားရန် 20 စက္ကန့် စောင့်ပေးပါ။", reply_markup=None)
        return await query.answer()
    if query and query.data and query.data.startswith("tg_"):
        return await handle_tg_bet_callback(query, context)

    user = query.from_user
    chat_id = query.message.chat_id
    msg_id = query.message.message_id
    data = query.data

    asyncio.create_task(register_user_group(user, query.message.chat, context))

    if data == "k_prompt_noop":
        return await query.answer()

    if data.startswith("kdropname_"):
        try:
            drop_id = int(data.split("_", 1)[1])
        except (ValueError, IndexError):
            return await query.answer("This card is no longer available.", show_alert=True)

        drop = active_card_drops.get(drop_id)
        if not drop or drop["chat_id"] != chat_id:
            return await query.answer()

        lock = card_drop_locks.setdefault(drop_id, asyncio.Lock())
        async with lock:
            if drop["winner_id"] is not None:
                winner = get_mention(drop["winner_id"], drop["winner_name"] or "Winner")
                return await query.answer(
                    f"Too Late 😭 {drop['winner_name'] or 'Winner'} got it first. Try next time.",
                    show_alert=True
                )
            if drop["ended"] or time.time() >= drop["expires_at"]:
                return await query.answer()
            if drop.get("name_button_used"):
                return await query.answer("The character name has already been requested.", show_alert=True)
            drop["name_button_used"] = True

            claim_command = f"/mm {drop['card']['name']}"
            display_command = f"/mm {_plain_text(html.escape(drop['card']['name']))}"
            copy_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "📋 Copy Name",
                    copy_text=CopyTextButton(text=claim_command)
                , style="primary")
            ]])
            await query.answer("The character name has been sent.")
            try:
                await context.bot.send_message(
                    chat_id,
                    f"Use This - {display_command}",
                    parse_mode="HTML",
                    reply_markup=copy_markup
                )
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception as e:
                print(f"Could not send spawned card name: {e}")
        return

    if data.startswith("kslot_bet_"):
        parts = data.split("_")
        allowed_uid = int(parts[2])
        bet_token = parts[3]

        if user.id != allowed_uid:
            return await query.answer("❌ This is not your game! Please use /kslot to start your own game.", show_alert=True)

        async with db_pool.acquire() as conn:
            user_row = await conn.fetchrow("SELECT coins, name FROM users WHERE user_id = $1", user.id)
            current_coins = user_row['coins'] if user_row else 1000
            display_name = user_row['name'] if user_row else user.first_name

            if bet_token == "allin":
                bet_amount = current_coins
            else:
                bet_amount = int(bet_token)

            if current_coins < bet_amount or bet_amount <= 0:
                return await query.answer(f"❌ Insufficient Kachi balance! Your current balance is {current_coins} Kachi.", show_alert=True)

        active_kslot_sessions[user.id] = {
            "chat_id": chat_id,
            "message_id": msg_id,
            "bet_amount": bet_amount
        }

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Spin 🎰", callback_data=f"kslot_spin_{user.id}_{bet_amount}", style="success"),
                InlineKeyboardButton("Cancel ❌", callback_data=f"kslot_cancel_{user.id}", style="danger")
            ]
        ])

        try:
            await query.edit_message_text(
                f"🎰 Kachi Slot Machine Game\n\n👤 Player: {get_mention(user.id, display_name)}\n🩸 Bet Amount: {bet_amount} Kachi\n\n<i>👇 ကျေးဇူးပြု၍ Spin သို့မဟုတ် Cancel ကို နှိပ်ပါ -</i>",
                parse_mode="HTML",
                reply_markup=keyboard
            )
        except Exception:
            pass
        return await query.answer("🎯 Bet selected! Choose Spin or Cancel.")

    if data.startswith("kslot_cancel_"):
        parts = data.split("_")
        allowed_uid = int(parts[2])

        if user.id != allowed_uid:
            return await query.answer("❌ This is not your game!", show_alert=True)

        if user.id in active_kslot_sessions:
            del active_kslot_sessions[user.id]
        
        kslot_is_cancelled[user.id] = True
        kslot_cooldowns[user.id] = time.time()

        try:
            await query.edit_message_text(
                "❌ Kachi Slot Game Cancelled.\n⏱ နောက်တစ်ကြိမ် ကစားရန် 15 စက္ကန့် စောင့်ဆိုင်းပေးပါ။",
                parse_mode="HTML",
                reply_markup=None
            )
        except Exception:
            pass
        return await query.answer("❌ Game cancelled. 15s cooldown applied.")

    if data.startswith("kslot_spin_"):
        parts = data.split("_")
        allowed_uid = int(parts[2])
        bet_amount = int(parts[3])

        if user.id != allowed_uid:
            return await query.answer("❌ This is not your game!", show_alert=True)

        if user.id in active_kslot_sessions:
            del active_kslot_sessions[user.id]

        kslot_is_cancelled[user.id] = False
        kslot_cooldowns[user.id] = time.time()

        async with db_pool.acquire() as conn:
            user_row = await conn.fetchrow("SELECT name FROM users WHERE user_id = $1", user.id)
            display_name = user_row['name'] if user_row else user.first_name

        await query.answer("🎯 Spin started!")
        asyncio.create_task(run_kslot_task(context.bot, chat_id, query, user.id, display_name, bet_amount))
        return

    if data.startswith("buy_confirm_"):
        parts = data.split("_")
        allowed_buyer_id = int(parts[2])
        seller_id = int(parts[3])

        if user.id != allowed_buyer_id and user.id != ADMIN_ID:
            return await query.answer("❌ Only the authorized buyer can click this.", show_alert=True)

        buy_info = pending_card_buys.get(msg_id)
        
        if not buy_info:
            return await query.answer("❌ Purchase session expired or already completed.", show_alert=True)

        async with db_pool.acquire() as conn:
            async with conn.transaction():
                missing_cards = []
                for item in buy_info["items"]:
                    c_id = item['card_id']
                    req_amount = item['amount']
                    uc_row = await conn.fetchrow("""
                        SELECT amount FROM user_cards WHERE user_id = $1 AND (
                            card_id = $2 OR ltrim(card_id, '0') = ltrim($2, '0')
                        ) FOR UPDATE
                    """, seller_id, c_id)
                    owned = uc_row['amount'] if uc_row else 0
                    if owned < req_amount:
                        missing_cards.append(c_id)

                if missing_cards:
                    if msg_id in pending_card_buys:
                        del pending_card_buys[msg_id]
                    return await query.answer("❌ Seller no longer owns the required cards/amounts!", show_alert=True)

                for item in buy_info["items"]:
                    c_id = item['card_id']
                    req_amount = item['amount']
                    uc_row = await conn.fetchrow("""
                        SELECT card_id, amount FROM user_cards WHERE user_id = $1 AND (
                            card_id = $2 OR ltrim(card_id, '0') = ltrim($2, '0')
                        ) FOR UPDATE
                    """, seller_id, c_id)
                    if not uc_row:
                        continue
                    actual_c_id = uc_row['card_id']
                    current_amt = uc_row['amount']
                    new_amt = current_amt - req_amount
                    if new_amt <= 0:
                        await conn.execute("DELETE FROM user_cards WHERE user_id = $1 AND card_id = $2", seller_id, actual_c_id)
                    else:
                        await conn.execute("UPDATE user_cards SET amount = $1 WHERE user_id = $2 AND card_id = $3", new_amt, seller_id, actual_c_id)

        if msg_id in pending_card_buys:
            del pending_card_buys[msg_id]

        success_text = f"""✅ Purchase Successful & Cards Removed!

?? Buyer: {get_mention(buy_info['buyer_id'], user.first_name)}
👤 Seller: {get_mention(seller_id, buy_info['seller_name'])}

🎴 Cards successfully removed from seller's inventory."""

        try:
            await query.edit_message_text(text=success_text, parse_mode="HTML", reply_markup=None)
        except Exception:
            pass
        return await query.answer("✅ Cards purchased and removed successfully!")

    if data.startswith("kbox_open_"):
        allowed_uid = int(data.split("_")[2])
        if user.id != allowed_uid:
            return await query.answer("❌ This is not your Kbox button!", show_alert=True)

        if user.id in active_kbox_users:
            return await query.answer("❌ သင့်၏ Kbox တစ်ခုမှာ ယခုလက်ရှိ ဖွင့်ဆိုဆဲဖြစ်ပါသည်၊ ပြီးဆုံးသည်အထိ စောင့်ပါရန်။", show_alert=True)
        active_kbox_users.add(user.id)

        now = int(time.time())
        cooldown = 86400

        async with db_pool.acquire() as conn:
            async with conn.transaction():
                user_row = await conn.fetchrow("SELECT coins, name, kbox_count, last_kbox_reset FROM users WHERE user_id = $1 FOR UPDATE", user.id)
                current_coins = user_row['coins'] if user_row else 1000
                display_name = user_row['name'] if user_row else user.first_name
                kbox_count = user_row['kbox_count'] if user_row else 0
                last_kbox_reset = user_row['last_kbox_reset'] if user_row else 0

                if now - last_kbox_reset >= cooldown:
                    kbox_count = 0
                    last_kbox_reset = now

                if kbox_count >= 7:
                    active_kbox_users.discard(user.id)
                    return await query.answer("❌ /kbox ကို တစ်နေ့လျှင် 7 ကြိမ်သာ ဖွင့်လှစ်နိုင်ပါတော့သည်။", show_alert=True)

                if current_coins < 1000:
                    active_kbox_users.discard(user.id)
                    return await query.answer(f"❌ Insufficient Kachi! You need 1000 Kachi (Current: {current_coins}).", show_alert=True)

                new_balance = current_coins - 1000
                kbox_count += 1
                await conn.execute("UPDATE users SET coins = $1, kbox_count = $2, last_kbox_reset = $3 WHERE user_id = $4", new_balance, kbox_count, last_kbox_reset, user.id)

        asyncio.create_task(run_kbox_task(context.bot, chat_id, query, user.id, display_name))
        await query.answer("🎉 Opening Kbox...")
        return

    if data.startswith("khset_"):
        parts = data.split("_")
        allowed_uid = int(parts[1])
        rarity = " ".join(parts[2:])

        if user.id != allowed_uid:
            return await query.answer("❌ This is not for you!", show_alert=True)

        confirm_text = f"Are You Sure To Set This Rarity On Kc\n\n📌 Selected Rarity: {get_rarity_display(rarity)}"
        confirm_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Cancel ❌", callback_data=f"khcancel_{user.id}", style="danger"),
                InlineKeyboardButton("Confirm ✅", callback_data=f"khconfirm_{user.id}_{rarity}", style="success")
            ],
            [
                InlineKeyboardButton("◀️ Back Menu", callback_data=f"khback_{user.id}", style="primary")
            ]
        ])
        try:
            await query.edit_message_text(confirm_text, parse_mode="HTML", reply_markup=confirm_keyboard)
        except Exception:
            pass
        await query.answer()
        return

    if data.startswith("khcancel_"):
        allowed_uid = int(data.split("_")[1])
        if user.id != allowed_uid:
            return await query.answer("❌ This is not for you!", show_alert=True)
        try:
            asyncio.create_task(query.message.delete())
        except Exception:
            pass
        await query.answer("Cancelled.")
        return

    if data.startswith("khback_"):
        allowed_uid = int(data.split("_")[1])
        if user.id != allowed_uid:
            return await query.answer("❌ This is not for you!", show_alert=True)
        
        text = "🎯 Select Rarity Mode for /kc\n\nကျေးဇူးပြု၍ စုပြလိုသော Rarity တစ်ခုကို ရွေးချယ်ပေးပါ -"
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("♠ Beyond", callback_data=f"khset_{user.id}_Beyond", style="primary"),
                InlineKeyboardButton("🎀 Birthday Special", callback_data=f"khset_{user.id}_Birthday Special", style="primary")
            ],
            [
                InlineKeyboardButton("📓 Raiden", callback_data=f"khset_{user.id}_Raiden", style="primary"),
                InlineKeyboardButton("🦠 Kryora", callback_data=f"khset_{user.id}_Kryora", style="primary")
            ],
            [
                InlineKeyboardButton("🎐 Yugen", callback_data=f"khset_{user.id}_Yugen", style="primary"),
                InlineKeyboardButton("🧬 Shinsei", callback_data=f"khset_{user.id}_Shinsei", style="primary")
            ],
            [
                InlineKeyboardButton("🪞 Kiwami", callback_data=f"khset_{user.id}_Kiwami", style="primary"),
                InlineKeyboardButton("✨ Kami", callback_data=f"khset_{user.id}_Kami", style="primary")
            ],
            [
                InlineKeyboardButton("⚜️ Hoshi", callback_data=f"khset_{user.id}_Hoshi", style="primary"),
                InlineKeyboardButton("💮 Ten", callback_data=f"khset_{user.id}_Ten", style="primary")
            ]
        ])
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            pass
        await query.answer()
        return

    if data.startswith("khconfirm_"):
        parts = data.split("_")
        allowed_uid = int(parts[1])
        rarity = " ".join(parts[2:])

        if user.id != allowed_uid:
            return await query.answer("❌ This is not for you!", show_alert=True)

        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET kc_rarity_mode = $1 WHERE user_id = $2", rarity, user.id)

        try:
            await query.edit_message_text(
                f"✅ Successfully set rarity mode to {get_rarity_display(rarity)}!\n\nယခုမှစ၍ `/kc` တွင် ဤ Rarity ကတ်များကိုသာ သီးသန့်ပြသပေးမည် ဖြစ်ပါသည်။",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await query.answer("✅ Rarity mode updated successfully!")
        return

    if data.startswith("see_menu_"):
        owner_id = int(data.split("_")[2])
        if user.id != owner_id or user.id != ADMIN_ID:
            return await query.answer("❌ Admin only.", show_alert=True)
        await render_see_rarity_menu(query, context, owner_id)
        return await query.answer()

    if data.startswith("see_rarity_"):
        parts = data.split("_", 3)
        owner_id = int(parts[2])
        rarity = parts[3]
        if user.id != owner_id or user.id != ADMIN_ID:
            return await query.answer("❌ Admin only.", show_alert=True)
        await render_see_rarity_page(query, context, owner_id, rarity, 0)
        return await query.answer()

    if data.startswith("see_page_"):
        parts = data.split("_", 4)
        owner_id = int(parts[2])
        page = int(parts[3])
        rarity = parts[4]
        if user.id != owner_id or user.id != ADMIN_ID:
            return await query.answer("❌ Admin only.", show_alert=True)
        await render_see_rarity_page(query, context, owner_id, rarity, page)
        return await query.answer()

    if data.startswith("kshop_rarity_"):
        parts = data.split("_")
        allowed_uid = int(parts[2])
        rarity = " ".join(parts[3:])

        if user.id != allowed_uid:
            return await query.answer("❌ This is not for you!", show_alert=True)

        await render_kshop_card(query, context, user.id, rarity, 0)
        await query.answer()
        return

    if data.startswith("kshop_nav_"):
        parts = data.split("_")
        allowed_uid = int(parts[2])
        target_idx = int(parts[-1])
        rarity = " ".join(parts[3:-1])

        if user.id != allowed_uid:
            return await query.answer("❌ This is not for you!", show_alert=True)

        await render_kshop_card(query, context, user.id, rarity, target_idx)
        await query.answer()
        return

    if data.startswith("kshop_buy_"):
        parts = data.split("_")
        allowed_uid = int(parts[2])
        price = int(parts[-1])
        card_id = parts[-2]
        rarity = " ".join(parts[3:-2])

        if user.id != allowed_uid:
            return await query.answer("❌ This is not for you!", show_alert=True)

        async with db_pool.acquire() as conn:
            async with conn.transaction():
                user_row = await conn.fetchrow("SELECT coins FROM users WHERE user_id = $1 FOR UPDATE", user.id)
                coins = user_row['coins'] if user_row else 1000

                if coins < price:
                    return await query.answer(f"❌ Insufficient Kachi! You need {price} Kachi (Current: {coins}).", show_alert=True)

                owned_row = await conn.fetchrow(
                    "SELECT amount FROM user_cards WHERE user_id = $1 AND card_id = $2 FOR UPDATE",
                    user.id, card_id
                )
                owned_amount = owned_row["amount"] if owned_row else 0
                if owned_amount >= USER_CARD_MAX_PER_ID:
                    return await query.answer(
                        "❌ You can own a maximum of 3 copies of the same card.",
                        show_alert=True
                    )

                new_coins = coins - price
                await conn.execute("UPDATE users SET coins = $1 WHERE user_id = $2", new_coins, user.id)

                added = await add_user_card_with_limit(conn, user.id, card_id, 1)
                if not added:
                    return await query.answer(
                        "❌ You can own a maximum of 3 copies of the same card.",
                        show_alert=True
                    )
                await check_and_auto_lock_card(conn, card_id)

                card_info = await conn.fetchrow("SELECT name FROM cards WHERE card_id = $1", card_id)
                card_name = card_info['name'] if card_info else card_id

        return await query.answer(f"✅ Successfully bought {card_name} for {price} Kachi!", show_alert=True)

    if data.startswith("kshop_back_"):
        allowed_uid = int(data.split("_")[2])
        if user.id != allowed_uid:
            return await query.answer("❌ This is not for you!", show_alert=True)

        text = f"""Hello {user.first_name}
You can buy Kiwami / Shinsei / Yugen / Kami / Birthday Special rarity cards here"""
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎀 Birthday Special", callback_data=f"kshop_rarity_{user.id}_Birthday Special", style="primary"),
                InlineKeyboardButton("🎐 Yugen", callback_data=f"kshop_rarity_{user.id}_Yugen", style="primary")
            ],
            [
                InlineKeyboardButton("🧬 Shinsei", callback_data=f"kshop_rarity_{user.id}_Shinsei", style="primary"),
                InlineKeyboardButton("🪞 Kiwami", callback_data=f"kshop_rarity_{user.id}_Kiwami", style="primary")
            ],
            [
                InlineKeyboardButton("✨ Kami", callback_data=f"kshop_rarity_{user.id}_Kami", style="primary")
            ]
        ])
        try:
            await query.edit_message_caption(caption=text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            try:
                await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=keyboard)
            except Exception:
                pass
        await query.answer()
        return




    if data == "menu_help":
        help_text = """📖 Help & Guidelines

- Add the bot to your group and send messages. Character Cards will drop automatically when the message limit is reached.
- /ktop - View the Global Leaderboard.
- /kgtop - View the Group Leaderboard.
- /card [card_id] - View card details and owners.
- /set [card_id] - Set your profile card for /kc."""
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu_back", style="primary")]])
        try:
            await query.edit_message_text(help_text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass
        await query.answer()
        return

    if data == "menu_kgift":
        kgift_text = """🎁 Gift Guidelines

Reply to a user's message and use:
- /kgift [card_id] - Send the specified card (Example: /kgift 1201)."""
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu_back", style="primary")]])
        try:
            await query.edit_message_text(kgift_text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass
        await query.answer()
        return

    if data == "menu_back":
        bot_info = await context.bot.get_me()
        text = f"""👋 Hello {get_mention(user.id, user.first_name)}!

🌺 Welcome to Kachi Card GAME BOT!

📌 How to Use
- Add the bot to your group.
- Send messages in the group. Character Cards will drop automatically when the message limit is reached.
- When a card drops, tap နာမည်ဖြေရန် and enter the character name.
- Use /ktop to view the leaderboard."""
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Help 📖", callback_data="menu_help", style="primary"),
                InlineKeyboardButton("Gift 🎁", callback_data="menu_kgift", style="primary")
            ],
            [
                InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{bot_info.username}?startgroup=true", style="success")
            ]
        ])
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            pass
        await query.answer()
        return

    if data.startswith("ccrarity_"):
        _, allowed_uid_text, rarity = data.split("_", 2)
        if user.id != int(allowed_uid_text):
            return await query.answer("❌ Not for you.", show_alert=True)
        card_info = None
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT session_type, payload FROM card_add_sessions WHERE user_id = $1",
                user.id
            )
            if row and row["session_type"] == "character_catcher":
                try:
                    card_info = json.loads(row["payload"])
                except (TypeError, json.JSONDecodeError):
                    card_info = None
                if card_info:
                    await conn.execute("DELETE FROM card_add_sessions WHERE user_id = $1", user.id)
        pending_cc_channel_adds.pop(user.id, None)
        if not card_info:
            return await query.answer("❌ Card addition session expired. Please forward the card again.", show_alert=True)
        selected_collection = "🎞 Ope Ope No Mi [ 🩺]"
        if (card_info["type"] == "video" and rarity != "Kryora") or (card_info["type"] != "video" and rarity == "Kryora"):
            return await query.answer("❌ Kryora သည် Video Card သီးသန့်ဖြစ်ပါတယ်။", show_alert=True)
        is_locked = rarity == "Beyond"
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                exists = await conn.fetchrow("SELECT card_id FROM cards WHERE card_id = $1 OR file_id = $2", card_info["card_id"], card_info["file_id"])
                if exists:
                    return await query.answer("⚠️ ဒီ Card ရှိပြီးသားပါ။", show_alert=True)
                await conn.execute(
                    "INSERT INTO cards (card_id, name, type, file_id, rarity, collection, is_locked) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                    card_info["card_id"], card_info["name"], card_info["type"], card_info["file_id"], rarity, selected_collection, is_locked
                )
                if user.id != ADMIN_ID:
                    await conn.execute("INSERT INTO card_add_activity (user_id, card_id) VALUES ($1, $2)", user.id, card_info["card_id"])
        display_rarity = get_rarity_display(rarity)
        log_caption = (
            f"New card added successfully\n\n❀ Card Name : {html.escape(card_info['name'])}\n"
            f"     ❝  ID : {card_info['card_id']}\n❀ Type : {card_info['type'].capitalize()}\n"
            f"🌟 Rarity : {display_rarity}\n📚 Collection : {selected_collection}\n"
            f"👤 Added By : {get_mention(user.id, user.first_name)}"
        )
        try:
            if card_info["type"] == "photo":
                await context.bot.send_photo(chat_id=CHARACTER_CATCHER_IMPORT_LOG_CHANNEL, photo=card_info["file_id"], caption=log_caption, parse_mode="HTML")
            else:
                await context.bot.send_video(chat_id=CHARACTER_CATCHER_IMPORT_LOG_CHANNEL, video=card_info["file_id"], caption=log_caption, parse_mode="HTML")
        except Exception as exc:
            print(f"Character Catcher card log send failed: {type(exc).__name__}: {exc}")
        try:
            await query.message.edit_text(
                f"✅ Card added successfully!\n\n🆔 Card ID: <code>{card_info['card_id']}</code>\n"
                f"🎴 Name: {html.escape(card_info['name'])}\n🌟 Rarity: {display_rarity}\n📚 Collection: {selected_collection}",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await query.answer("✅ Card added successfully!")
        return


    if data.startswith("addrarity_"):
        _, allowed_uid_text, rarity = data.split("_", 2)
        allowed_uid = int(allowed_uid_text)

        if user.id != allowed_uid:
            return await query.answer("❌ Not for you.", show_alert=True)
        if rarity in ("Beyond", "Birthday Special") and user.id != ADMIN_ID:
            return await query.answer("❌ Only the Bot Admin can add this rarity.", show_alert=True)

        card_info = None
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT session_type, payload FROM card_add_sessions WHERE user_id = $1",
                user.id
            )
            if row and row["session_type"] == "regular":
                try:
                    card_info = json.loads(row["payload"])
                except (TypeError, json.JSONDecodeError):
                    card_info = None
                if card_info:
                    await conn.execute("DELETE FROM card_add_sessions WHERE user_id = $1", user.id)
        pending_card_adds.pop(user.id, None)

        if not card_info:
            return await query.answer("❌ Card addition session expired. Please run /add again.", show_alert=True)

        if (card_info["type"] == "video" and rarity != "Kryora") or (card_info["type"] != "video" and rarity == "Kryora"):
            return await query.answer("❌ Kryora သည် Video Card သီးသန့်ဖြစ်ပါတယ်။", show_alert=True)
        card_info["rarity"] = rarity

        collections_list = [
            "🎞 Ope Ope No Mi [ 🩺]",
            "🎞 Hito Hito No Mi [🌕]",
            "🎞 Mera Mera No Mi [🔥]",
            "🎞 Hana Hana No Mi [🌸]",
            "🎞 Yomi Yomi No Mi [💀]",
            "🎞 Tori Tori No Mi [🦅]",
            "🎞 Yami Yami No Mi [🌑]",
            "🎞 Sora Sora No Mi [☁️]",
            "🎞 Kaze Kaze No Mi [🌪️]",
            "🎞 Mizu Mizu No Mi [💧]",
            "🎞 Kori Kori No Mi [❄️]",
            "🎞 Tsuchi Tsuchi No Mi [🪨]",
            "🎞 Kumo Kumo No Mi [🕸️]",
            "🎞 Hikari Hikari No Mi [✨]",
            "🎞 Kage Kage No Mi [🌘]",
            "🎞 Rai Rai No Mi [⚡]",
            "🎞 Niji Niji No Mi [🌈]",
            "🎞 Tsuki Tsuki No Mi [🌙]",
            "🎞 Taiyo Taiyo No Mi [☀️]",
            "🎞 Mori Mori No Mi [🌳]",
            "🎞 Bara Bara No Mi [🎈]",
            "🎞 Toge Toge No Mi [🌵]",
            "🎞 Hanaumi No Mi [🪷]",
            "🎞 Kiba Kiba No Mi [🦷]",
            "🎞 Hebi Hebi No Mi [🐍]",
            "🎞 Neko Neko No Mi [🐈]",
            "🎞 Inu Inu No Mi [🐕]",
            "🎞 Kuma Kuma No Mi [🐻]",
            "🎞 Kitsune Kitsune No Mi [🦊]",
            "🎞 Tatsu Tatsu No Mi [🐉]",
            "🎞 Kame Kame No Mi [🐢]",
            "🎞 Hoshi Hoshi No Mi [⭐]",
            "🎞 Umi Umi No Mi [🌊]",
            "🎞 Shio Shio No Mi [🧂]",
            "🎞 Ame Ame No Mi [🌧️]",
            "🎞 Yuki Yuki No Mi [🌨️]",
            "🎞 Kiri Kiri No Mi [🌫️]",
            "🎞 Goro Goro No Mi [🔔]",
            "🎞 Hibana Hibana No Mi [🧨]",
            "🎞 Kemuri Kemuri No Mi [💨]",
            "🎞 Mushi Mushi No Mi [🦋]",
            "🎞 Tama Tama No Mi [🥚]",
            "🎞 Kuri Kuri No Mi [🌰]",
            "🎞 Suna Suna No Mi [🏜️]",
            "🎞 Ishi Ishi No Mi [💎]",
            "🎞 Gami Gami No Mi [📜]",
            "🎞 Oni Oni No Mi [👹]",
            "🎞 Tenshi Tenshi No Mi [👼]",
            "🎞 Shishi Shishi No Mi [🦁]",
            "🎞 Ryu Ryu No Mi [🦖]",
            "🎞 Fukurou Fukurou No Mi [🦉]",
            "🎞 Chou Chou No Mi [🐝]",
            "🎞 Sakana Sakana No Mi [🐟]",
            "🎞 Tako Tako No Mi [🐙]",
            "🎞 Kani Kani No Mi [🦀]",
            "🎞 Shima Shima No Mi [🏝️]",
            "🎞 Yume Yume No Mi [💭]"
        ]
        selected_collection = random.choice(collections_list)

        card_id = card_info["card_id"]
        card_name = card_info["card_name"]
        c_type = card_info["type"]
        file_id = card_info["file_id"]
        is_card_admin = card_info["is_card_admin"]
        
        # Beyond cards are admin-sale-only and must not appear in ordinary drops.
        is_locked = (rarity == "Beyond")

        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO cards (card_id, name, type, file_id, rarity, collection, is_locked)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                """, card_id, card_name, c_type, file_id, rarity, selected_collection, is_locked)
                if is_card_admin and user.id != ADMIN_ID:
                    await conn.execute("INSERT INTO card_add_activity (user_id, card_id) VALUES ($1, $2)", user.id, card_id)

                added_reward_coins = 50 if rarity in ["Hoshi", "Ten"] else (80 if rarity in ["Kiwami", "Kami"] else (120 if rarity in ['Shinsei', 'Yugen', 'Kryora'] else 150))

                if is_card_admin and user.id != ADMIN_ID:
                    await conn.execute("""
                        UPDATE users 
                        SET coins = GREATEST(0, coins + $1) 
                        WHERE user_id = $2
                    """, added_reward_coins, user.id)

        r_display = get_rarity_display(rarity)
        lock_msg_note = ""
        log_text = f"""New card added successfully

❀ Card Name : {card_name}
     ❝  ID : {card_id}
❀ Type : {c_type.capitalize()}
🌟 Rarity : {r_display}
📚 Collection : {selected_collection}{lock_msg_note}
👤 Added By : {get_mention(user.id, user.first_name)}"""

        try:
            if c_type == "photo":
                asyncio.create_task(context.bot.send_photo(chat_id=CARD_LOG_CHANNEL_ID, photo=file_id, caption=log_text, parse_mode="HTML"))
            else:
                asyncio.create_task(context.bot.send_video(chat_id=CARD_LOG_CHANNEL_ID, video=file_id, caption=log_text, parse_mode="HTML"))
        except Exception:
            pass

        reward_notice = f"\n🎉 Received +{added_reward_coins} Kachi for adding card!" if (is_card_admin and user.id != ADMIN_ID) else ""
        try:
            await query.message.edit_text(
                f"✅ Card added successfully!\n\n🆔 Card ID: <code>{card_id}</code>\n🎴 Name: {card_name}\n🌟 Rarity: {r_display}\n📚 Collection: {selected_collection}{lock_msg_note}{reward_notice}",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await query.answer("✅ Card added successfully!")
        return

    if data.startswith("check_join_"):
        return await query.answer("ဒီစနစ်ကို ပြောင်းလဲထားပါတယ်။ Bot DM မှာ Start နှိပ်ပေးပါ။", show_alert=True)

    if data == "join_card_drop":
        return await query.answer("Use /mm character name to catch this card.", show_alert=True)

    if data.startswith("gift_"):
        parts = data.split("_")
        action = parts[1]
        allowed_sender_id = int(parts[2])

        if user.id != allowed_sender_id:
            return await query.answer("❌ Only the sender can click this.", show_alert=True)

        gift_info = pending_gifts.get(msg_id)
        if gift_info:
            del pending_gifts[msg_id]

        if not gift_info:
            return await query.answer("❌ Gift transfer expired.", show_alert=True)

        if action == "cancel":
            cancel_text = "❌ Gift transfer cancelled."
            try:
                if query.message.caption:
                    await query.edit_message_caption(caption=cancel_text, parse_mode="HTML")
                else:
                    await query.edit_message_text(text=cancel_text, parse_mode="HTML")
            except Exception:
                pass
            return await query.answer("Cancelled.")

        elif action == "confirm":
            sender_id = gift_info["sender_id"]
            receiver_id = gift_info["receiver_id"]
            receiver_name = gift_info["receiver_name"]

            try:
                async with db_pool.acquire() as conn:
                    async with conn.transaction():
                        cards_to_send = gift_info.get("cards") or [{
                            "card_id": gift_info["card_id"], "card_name": gift_info["card_name"], "amount": 1
                        }]
                        # Validate all requested cards before making any ownership changes.
                        for item in cards_to_send:
                            card_id = str(item["card_id"])
                            amount = int(item.get("amount", 1))
                            sender_card = await conn.fetchrow(
                                "SELECT amount FROM user_cards WHERE user_id=$1 AND card_id=$2 FOR UPDATE",
                                sender_id, card_id
                            )
                            if not sender_card or sender_card["amount"] < amount:
                                raise ValueError(f"You no longer own enough copies of Card ID {card_id}.")
                            receiver_card = await conn.fetchrow(
                                "SELECT amount FROM user_cards WHERE user_id=$1 AND card_id=$2 FOR UPDATE",
                                receiver_id, card_id
                            )
                            receiver_amount = receiver_card["amount"] if receiver_card else 0
                            if receiver_amount + amount > USER_CARD_MAX_PER_ID:
                                raise ValueError(f"Receiver cannot own more than 3 copies of Card ID {card_id}.")
                        success_lines = []
                        for item in cards_to_send:
                            card_id = str(item["card_id"])
                            amount = int(item.get("amount", 1))
                            card_name = item.get("card_name", card_id)
                            await conn.execute(
                                "UPDATE user_cards SET amount=amount-$1 WHERE user_id=$2 AND card_id=$3",
                                amount, sender_id, card_id
                            )
                            await conn.execute(
                                "DELETE FROM user_cards WHERE user_id=$1 AND card_id=$2 AND amount<=0",
                                sender_id, card_id
                            )
                            added = await add_user_card_with_limit(conn, receiver_id, card_id, amount)
                            if not added:
                                raise ValueError(f"Could not transfer Card ID {card_id}.")
                            await check_and_auto_lock_card(conn, card_id)
                            qty = f" ×{amount}" if amount > 1 else ""
                            success_lines.append(f"{html.escape(card_name)} (ID: <code>{html.escape(card_id)}</code>){qty}")
                        success_text = (
                            f"✅ {get_mention(sender_id, user.first_name)} sent the following card(s) to "
                            f"{get_mention(receiver_id, receiver_name)}!\n\n" + "\n".join(success_lines)
                        )
            except ValueError as e:
                pending_gifts[msg_id] = gift_info
                return await query.answer(str(e), show_alert=True)

            try:
                if query.message.caption:
                    await query.edit_message_caption(caption=success_text, parse_mode="HTML")
                else:
                    await query.edit_message_text(text=success_text, parse_mode="HTML")
            except Exception:
                pass
            return await query.answer("Gift sent successfully!")

    if data.startswith("kcpage_"):
        parts = data.split("_")
        page = int(parts[1])
        target_id = int(parts[2])

        if user.id != target_id:
            return await query.answer("❌ Not your /kc view.", show_alert=True)

        await render_kc_view(query, context, page=page, owner_id=target_id)
        await query.answer()
        return

    if data.startswith("glist_"):
        if user.id != ADMIN_ID:
            return await query.answer("❌ Admin only.", show_alert=True)
        page = int(data.replace("glist_", ""))
        await render_glist_page(query, context, page=page)
        await query.answer()
        return

    if data.startswith("kclist_"):
        if user.id != ADMIN_ID:
            return await query.answer("❌ Admin only.", show_alert=True)
        page = int(data.replace("kclist_", ""))
        await render_kclist_page(query, context, page=page)
        await query.answer()
        return

    if data.startswith("clist_"):
        parts = data.split("_")
        page = int(parts[1])
        owner_id = int(parts[2]) if len(parts) > 2 else None

        if owner_id and user.id != owner_id:
            return await query.answer("❌ Not your card list.", show_alert=True)

        await render_cardlist_page(query, context, page=page, owner_id=owner_id)
        await query.answer()
        return

    if data.startswith("seelock_"):
        parts = data.split("_")
        page = int(parts[1])
        owner_id = int(parts[2]) if len(parts) > 2 else None

        if owner_id and user.id != owner_id:
            return await query.answer("❌ Not your seelock view.", show_alert=True)
        if user.id != ADMIN_ID:
            return await query.answer("❌ Admin only.", show_alert=True)

        await render_seelock_page(query, context, page=page, owner_id=owner_id)
        await query.answer()
        return

async def reset3x_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: reduce any ownership above 3 copies of the same card to 3."""
    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        return await _safe_reply_text(update.effective_message, "❌ Admin only.")

    if db_pool is None:
        return await _safe_reply_text(update.effective_message, "❌ Database is not ready. Please try again.")

    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                affected = await conn.fetch("""
                    UPDATE user_cards
                    SET amount = 3
                    WHERE amount > 3
                    RETURNING user_id, card_id
                """)

        affected_users = len({row["user_id"] for row in affected})
        affected_rows = len(affected)
        await _safe_reply_text(update.effective_message, 
            "✅ /reset3x completed\n\n"
            f"🎴 Card records corrected: {affected_rows}\n"
            f"👤 Users affected: {affected_users}\n"
            "📌 Each user now has at most 3 copies of each card.",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"/reset3x failed for admin {user.id}: {type(e).__name__}: {e}")
        await _safe_reply_text(update.effective_message, "❌ Reset failed. Check the bot logs and try again.")


async def post_init(app: Application):
    # Initialize the database only. Card ownership limits are enforced during
    # purchases/gifts; existing over-limit amounts are changed only by /reset3x.
    await init_db()


async def application_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log unexpected handler errors without stopping the polling process."""
    error = context.error
    update_id = getattr(update, "update_id", "unknown")
    print(
        f"⚠️ Unhandled update error (update_id={update_id}): "
        f"{type(error).__name__}: {error}"
    )


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in Railway Variables.")

    update_processor = UserSerialUpdateProcessor(max_concurrent_updates=24)
    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .concurrent_updates(update_processor)
        .build()
    )
    app.add_error_handler(application_error_handler)
    app.add_handler(MessageHandler(filters.COMMAND, spam_command_gate), group=-2)

    # Public User Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("pre", pre_cmd))
    app.add_handler(CommandHandler("kc", check_kc_cmd))
    app.add_handler(CommandHandler("kprofile", kprofile_cmd))
    app.add_handler(CommandHandler("khmode", khmode_cmd))
    app.add_handler(CommandHandler("resethmode", resethmode_cmd))
    app.add_handler(CommandHandler("mm", mm_cmd))
    app.add_handler(CommandHandler("kname", kname_cmd))
    app.add_handler(CommandHandler("kachi", kachi_cmd))
    app.add_handler(CommandHandler("myre", myre_cmd))
    app.add_handler(CommandHandler("kslot", kslot_cmd))  
    app.add_handler(CommandHandler("kdic", kdic_cmd))
    app.add_handler(CommandHandler("kbet", kbet_cmd))
    app.add_handler(CommandHandler("kbox", kbox_cmd))
    app.add_handler(CommandHandler("kdaily", kdaily_cmd))
    app.add_handler(CommandHandler("set", set_card_cmd))
    app.add_handler(CommandHandler("kgift", kgift_cmd))
    app.add_handler(CommandHandler("rngift", rngift_cmd))
    app.add_handler(CommandHandler("card", card_detail_cmd))
    app.add_handler(CommandHandler("ktop", ktop_cmd))
    app.add_handler(CommandHandler("tdc", tdc_cmd))
    app.add_handler(CommandHandler("kgtop", kgtop_cmd))
    app.add_handler(CommandHandler("ksearch", ksearch_cmd))

    # Admin Commands
    app.add_handler(CommandHandler("kclist", kclist_cmd))
    app.add_handler(CommandHandler("cardlist", cardlist_cmd))
    app.add_handler(CommandHandler("seelock", seelock_cmd))
    app.add_handler(CommandHandler("see", see_cmd))
    app.add_handler(CommandHandler("send", send_group_prompt_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("kcreset", kcreset_cmd))
    app.add_handler(CommandHandler("glist", glist_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("kac", admin_coin_cmd))
    app.add_handler(CommandHandler("k", reveal_spawn_name))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("got", got_card_cmd))
    app.add_handler(CommandHandler("add", add_card_cmd))
    app.add_handler(CommandHandler("cad", cad_cmd))
    app.add_handler(CommandHandler("unlock", unlock_card_cmd))
    app.add_handler(CommandHandler("lock", lock_card_cmd))
    app.add_handler(CommandHandler("spw", spw_cmd))
    app.add_handler(CommandHandler("tg", tg_start_cmd))
    app.add_handler(CommandHandler("stoptg", tg_stop_cmd))
    app.add_handler(CommandHandler("del", del_card_cmd))
    app.add_handler(CommandHandler("check", check_user_cmd))
    app.add_handler(CommandHandler("reset3x", reset3x_cmd))
    app.add_handler(CommandHandler("fixcard", ttcard_cmd))
    app.add_handler(CommandHandler("ttcard", ttcard_cmd))  # Backward-compatible alias

    # Bot-level Moderation Commands
    app.add_handler(CommandHandler("mute", mute_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("umute", umute_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(InlineQueryHandler(inline_query_handler))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.PHOTO | filters.VIDEO), import_character_catcher_forward), group=1)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.PHOTO | filters.VIDEO), import_forwarded_card_log), group=1)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & (~filters.COMMAND), tg_bet_amount_handler), group=-1)
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), message_handler))

    print("🤖 Python Game Bot Running...")
    app.run_polling()

if __name__ == "__main__":
    main()
