# ╔══════════════════════════════════════════╗
# ║   ​🇸​​🇺​​🇵​​🇷​​🇪​​🇲​​🇪​ ​🇴​​🇸 — ᴘʀᴇᴍɪᴜᴍ 𝗘𝗗𝗜𝗧𝗜ᴏɴ ☣️  ║
# ║   Version : 3.0 — PREMIUM EDITION        ║
# ║   Owner   : XERON BEAST Only 👑           ║
# ╚══════════════════════════════════════════╝
# UPGRADED: python-telegram-bot v20+ async API
# FIXED   : AI multi-key rotation + requests fallback
# NEW     : /uptime, /stats commands added
# SPEED   : Zero-delay NC engine, parallel bot startup

try:
    import uvloop
    uvloop.install()          # fastest event loop available
except ImportError:
    pass                      # stdlib asyncio fallback

import asyncio
import json
import os
import io
import re
import time
import random
import logging
import tempfile
import itertools
import contextvars
from functools import wraps
from datetime import datetime

# aiohttp and edge_tts lazy-loaded on first use — faster startup
AIOHTTP_AVAILABLE = None
EDGE_TTS_AVAILABLE = None

def _check_aiohttp():
    global AIOHTTP_AVAILABLE, aiohttp
    if AIOHTTP_AVAILABLE is None:
        try:
            import aiohttp as _ah
            aiohttp = _ah
            AIOHTTP_AVAILABLE = True
        except ImportError:
            AIOHTTP_AVAILABLE = False
    return AIOHTTP_AVAILABLE

def _check_edge_tts():
    global EDGE_TTS_AVAILABLE, edge_tts
    if EDGE_TTS_AVAILABLE is None:
        try:
            import edge_tts as _et
            edge_tts = _et
            EDGE_TTS_AVAILABLE = True
        except ImportError:
            EDGE_TTS_AVAILABLE = False
    return EDGE_TTS_AVAILABLE

# Populate booleans at import time (so banner / guards work)
_check_aiohttp()
_check_edge_tts()

import requests
import telegram
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions, ReactionTypeEmoji
from telegram.constants import ChatType
from telegram.error import RetryAfter, TimedOut, NetworkError
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.request import HTTPXRequest

# ===========================================================
# BOT TOKENS — 11 Main tokens
# ===========================================================
TOKENS = [
    '8711551906:AAFIqlK41Jxmo3fjQEN5d5eDhkstyTXIFnE',  # replace with your bot tokens
]

if os.path.exists('extra_bots.txt'):
    with open('extra_bots.txt') as _f:
        for _line in _f:
            _t = _line.strip()
            if _t and _t not in TOKENS:
                TOKENS.append(_t)

# Bots added with /clone are stored with their Telegram owner.  Tokens are
# intentionally never printed or included in status/error ᴍᴇꜱꜱᴀɢᴇꜱ.
CLONED_BOTS_FILE = 'cloned_bots.json'
CLONE_OWNERS = {}  # {bot_id: Telegram user_id that supplied the token}

try:
    if os.path.exists(CLONED_BOTS_FILE):
        with open(CLONED_BOTS_FILE) as _f:
            _cloned_data = json.load(_f)
        for _record in _cloned_data.get('bots', []):
            _token = str(_record.get('token', '')).strip()
            _bot_id = int(_record.get('bot_id', 0))
            _owner_id = int(_record.get('owner_id', 0))
            if _token and _bot_id and _owner_id:
                if _token not in TOKENS:
                    TOKENS.append(_token)
                CLONE_OWNERS[_bot_id] = _owner_id
except (OSError, ValueError, TypeError, json.JSONDecodeError) as _e:
    logging.error('Could not load cloned bots: %s', type(_e).__name__)

if not TOKENS:
    raise SystemExit('ERROR: No bot tokens found!')

# The first configured token is the single control-plane bot.  Keep this
# immutable when dynamically cloned bots are appended to ``TOKENS``.
LEAD_BOT_TOKEN = TOKENS[0]

# ===========================================================
# GEMINI AI CONFIG — Multi-key rotation (429 se bachne ke liye)
# ===========================================================
GEMINI_API_KEYS = [
    os.getenv('GEMINI_API_KEY', 'AIzaSyAXBsL3eAhht-DLz_EjcvnUcLywfd-jZ3g'),
    'AIzaSyC4vSo8oHFmRrNuMKh2bBZuS6UhF8Wq3pE',
    'AIzaSyBpK9vR2mNxTdQ7jLwYeF1cHgVz6sDmU4o',
    'AIzaSyDqN8hWxPvL3kRmFtY2uJ5bCeZs9gMa7nI',
    'AIzaSyEjT6fXyBwQ1rSvKpU4hD8cNmLz5aGo2dY',
]
# Remove empty/duplicate keys
GEMINI_API_KEYS = list(dict.fromkeys([k for k in GEMINI_API_KEYS if k]))
_gemini_key_idx = 0   # rotating index

def _next_gemini_key():
    global _gemini_key_idx
    key = GEMINI_API_KEYS[_gemini_key_idx % len(GEMINI_API_KEYS)]
    _gemini_key_idx += 1
    return key

GEMINI_API_KEY = GEMINI_API_KEYS[0]   # kept for backwards compat
GEMINI_MODEL = 'gemini-1.5-flash'
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
AI_SYSTEM_PROMPT = (
    "You are a smart, friendly and helpful AI assistant running inside a Telegram bot. "
    "You reply in the same language the user speaks (Hindi, English, or mix). "
    "Keep your replies concise and natural. "
    "You can use Telegram-friendly formatting (bold with *, italics with _) but don't overdo it."
)
AI_HISTORY = {}

# ===========================================================
# OWNER & SUDO CONFIG
# ===========================================================
OWNER_ID = int(os.getenv('OWNER_ID', '8632154722'))
MAX_CLONED_BOTS = max(1, int(os.getenv('MAX_CLONED_BOTS', '20')))
SUDO_FILE = "sudo_users.json"

if os.path.exists(SUDO_FILE):
    with open(SUDO_FILE) as f:
        SUDO_USERS = set(json.load(f))
else:
    SUDO_USERS = set()

def save_sudo():
    with open(SUDO_FILE, "w") as f:
        json.dump(list(SUDO_USERS), f)

# ===========================================================
# GLOBAL STATE
# ===========================================================
# A clone owner may control only the bot they cloned.  The context variable
# lets the existing command implementations keep iterating over ``bots``
# while presenting a one-bot view for clone owners.  The main owner and sudo
# users retain the original all-bot behaviour.
_BOT_SCOPE = contextvars.ContextVar('bot_scope', default=None)


def _safe_bot_id(bot):
    try:
        return bot.id
    except (AttributeError, RuntimeError):
        return None


class _ScopedBotList(list):
    def __iter__(self):
        allowed_ids = _BOT_SCOPE.get()
        iterator = list.__iter__(self)
        if allowed_ids is None:
            return iterator
        return (bot for bot in iterator if _safe_bot_id(bot) in allowed_ids)

    def __len__(self):
        allowed_ids = _BOT_SCOPE.get()
        if allowed_ids is None:
            return list.__len__(self)
        return sum(1 for bot in list.__iter__(self)
                   if _safe_bot_id(bot) in allowed_ids)


apps = []
bots = _ScopedBotList()
nc_tasks = {}
spam_tasks = {}
slider_tasks = {}
photo_tasks = {}
gc_tasks = {}
gc_photo_tasks = {}    # fix: was missing, used in pfpswipe/stopgc/botstatus
chat_photos = {}
raid_tasks = {}
react_tasks = {}
reactall_chats = {}
reactuser_chats = {}
delete_tasks = {}
deluser_tasks = {}
del_all_tasks = {}     # fix: used in purgeoff
auto_delete_users = {}
blocknc_active = {}
warn_counts = {}
warn_limits = {}
reply_tasks = {}
reply_targets = {}
pending_replies_map = {}
autoreply_users = {}   # {chat_id: {user_id: slide2_index}}
autoreply_slide2_idx = {}  # {chat_id: {user_id: int}}
pic_spam_tasks = {}    # {chat_id: [task, ...]}
gcnc_locked   = set()   # chats where gcnclock is active
gcnc_demoted  = {}      # {chat_id: [(user_id, had_can_change_info), ...]} — restored on unlock
GLOBAL_DELAY   = 0       # 0 = max speed (no artificial delay)
NC_RUN_DURATION  = 86400  # effectively never pauses (1 day)
NC_REFRESH_PAUSE = 0      # 0 = instant refresh, no pause
GOD_MODE = False
god_mode_task = None

# ===========================================================
# PERSISTENT TASK RESTORE
# ===========================================================
TASK_SAVE_FILE = 'active_tasks.json'
_nc_task_meta  = {}   # {chat_id: {'fn': str, 'text': str}}
_spam_task_meta = {}  # {chat_id: {'type': str, 'text': str}}
NC_LOOP_REGISTRY = {}  # populated after loop-fn definitions

def _save_active_tasks():
    try:
        data = {
            'nc':   {str(k): v for k, v in _nc_task_meta.items()},
            'spam': {str(k): v for k, v in _spam_task_meta.items()},
        }
        with open(TASK_SAVE_FILE, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass

async def _restore_active_tasks():
    try:
        if not os.path.exists(TASK_SAVE_FILE):
            return
        with open(TASK_SAVE_FILE) as f:
            data = json.load(f)
        n = 0
        for cid_str, info in data.get('nc', {}).items():
            cid = int(cid_str)
            fn = NC_LOOP_REGISTRY.get(info.get('fn'))
            if fn and bots and cid not in nc_tasks:
                nc_tasks[cid] = _start_multi_nc(bots, fn, cid, info.get('text', ''))
                n += 1
        if n:
            pass  # restored silently
    except Exception as e:
        logging.error(f'[RESTORE] {e}')

# ===========================================================
# WATCHDOG — periodic task-dict cleanup (memory leak prevention)
# ===========================================================
async def _watchdog_loop():
    _td = [nc_tasks, spam_tasks, slider_tasks, photo_tasks,
           gc_photo_tasks, react_tasks, delete_tasks,
           deluser_tasks, del_all_tasks, reply_tasks, pic_spam_tasks]
    while True:
        await asyncio.sleep(60)
        for d in _td:
            for cid in list(d.keys()):
                val = d.get(cid)
                if val is None:
                    del d[cid]
                elif isinstance(val, list):
                    alive = [t for t in val if not t.done()]
                    if alive:
                        d[cid] = alive
                    else:
                        d.pop(cid, None)
                        _nc_task_meta.pop(cid, None)
                        _spam_task_meta.pop(cid, None)
                elif hasattr(val, 'done') and val.done():
                    d.pop(cid, None)

# ===========================================================
# SPEED: Semaphore limits concurrent API calls per process
# ===========================================================
NC_SEMAPHORE = None  # initialized inside async context in run_all_bots

NON_SUDO_MSG = (
    "╔══════════════════╗\n"
    "║  ⚠️ ᴀᴄᴄᴇꜱꜱ ᴅᴇɴɪᴇᴅ  ⚠️  ║\n"
    "╚══════════════════╝\n"
    "👤 ʏᴏᴜ'ʀᴇ ɴᴏᴛ ᴀʟʟᴏᴡᴇᴅ 💀\n"
    "🔒 ɢᴇᴛ ᴀᴄᴄᴇꜱꜱ ᴛʜᴇɴ ᴛʀʏ ᴀɢᴀɪɴ 😈\n"
    "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
)
UNAUTHORIZED_MESSAGE = NON_SUDO_MSG

logging.basicConfig(level=logging.ERROR)

BOT_START_TIME = datetime.now()

# ===========================================================
# PERMISSION HELPERS
# ===========================================================
def _is_global_operator(uid):
    return uid == OWNER_ID or uid in SUDO_USERS


def _context_bot_id(context):
    try:
        return context.bot.id
    except (AttributeError, RuntimeError):
        return None


def _is_lead_bot(context):
    """Return True only when this update was received by the lead bot."""
    try:
        return context.bot.token == LEAD_BOT_TOKEN
    except (AttributeError, RuntimeError):
        return False


def lead_bot_only(func):
    """Runtime backstop for commands that must never execute on a clone."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if _is_lead_bot(context):
            return await func(update, context)
        if update.message:
            await update.message.reply_text(
                "🚫 ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ɪꜱ ᴏɴʟʏ ᴀᴠᴀɪᴠʟᴇ ɪɴ ᴏꜱ ʙᴏᴛ."
            )
    return wrapper


def is_owner_or_sudo(uid, bot_id=None):
    """Authorize global operators or the owner of this particular clone."""
    return _is_global_operator(uid) or (
        bot_id is not None and CLONE_OWNERS.get(bot_id) == uid
    )


def _set_clone_scope(uid, bot_id):
    """Limit clone owners to their bot; return a token for later reset."""
    if bot_id is not None and not _is_global_operator(uid):
        return _BOT_SCOPE.set(frozenset({bot_id}))
    return None


def owner_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id == OWNER_ID:
            return await func(update, context)
        await update.message.reply_text(
            "╔══════════════════╗\n"
            "║  ☣️ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ᴏɴʟʏ ☣️  ║\n"
            "╚══════════════════╝\n"
            "🚫 ꜱᴏʀʀʏ, ᴏɴʟʏ ꜰᴏʀ ᴏᴡɴᴇʀ 💀\n"
            "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
        )
    return wrapper


def sudo_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        bot_id = _context_bot_id(context)
        if not is_owner_or_sudo(uid, bot_id):
            await update.message.reply_text(NON_SUDO_MSG)
            return

        scope_token = _set_clone_scope(uid, bot_id)
        try:
            return await func(update, context)
        finally:
            if scope_token is not None:
                _BOT_SCOPE.reset(scope_token)
    return wrapper

async def _gcnclock_mute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mute the user in this chat because gcnclock is active."""
    cid = update.effective_chat.id
    uid = update.effective_user.id
    uname = update.effective_user.first_name or str(uid)
    # mute from all bots simultaneously
    async def _mute_one(b):
        try:
            await b.restrict_chat_member(
                chat_id=cid, user_id=uid,
                permissions=ChatPermissions(
                    can_send_messages=False, can_send_audios=False,
                    can_send_documents=False, can_send_photos=False,
                    can_send_videos=False, can_send_polls=False,
                    can_send_other_messages=False, can_add_web_page_previews=False
                )
            )
        except Exception:
            pass
    await asyncio.gather(*[_mute_one(b) for b in bots])
    try:
        await update.message.reply_text(
            "╔══════════════════╗\n"
            "║  🔒 ɢᴄɴᴄ ʟᴏᴄᴋᴇᴅ 🔒  ║\n"
            "╚══════════════════╝\n"
            f"🚫 {uname} — ʏᴏᴜ'ʀᴇ ɴᴏᴛ ᴀʟʟᴏᴡᴇᴅ ᴛᴏ ᴜꜱᴇ ɴᴄ !\n"
            "🔇 ᴍᴜᴛᴇ ᴋᴀʀ ᴅɪʏᴀ ɢᴀʏᴀ ᴛᴜᴊʜᴇ!\n"
            "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
        )
    except Exception:
        pass

def nc_only(func):
    """NC guard: normal sudo check + gcnclock enforcement when active."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        bot_id = _context_bot_id(context)
        cid = update.effective_chat.id if update.effective_chat else None
        if not is_owner_or_sudo(uid, bot_id):
            await update.message.reply_text(NON_SUDO_MSG)
            return

        scope_token = _set_clone_scope(uid, bot_id)
        try:
            # When locked, only an authorized owner or one of our bots may run NC.
            if cid and cid in gcnc_locked and not _is_nc_allowed(uid, bot_id):
                await _gcnclock_mute_user(update, context)
                return
            return await func(update, context)
        finally:
            if scope_token is not None:
                _BOT_SCOPE.reset(scope_token)
    return wrapper


def _is_nc_allowed(uid: int, bot_id=None) -> bool:
    """True for an authorized owner or one of the running bot user IDs."""
    if is_owner_or_sudo(uid, bot_id):
        return True
    for b in bots:
        try:
            if b.id == uid:
                return True
        except Exception:
            pass
    return False

# ===========================================================
# NC PATTERNS — Language NC (cleaned)
# ===========================================================
HINDINC_PATTERNS = [
    "{text} चुडाकड़ ⊹ ࣪ ﹏𓊝﹏𓂁﹏⊹ ࣪ ˖",
    "{text} रैंडी ˖ ࣪ ꉂ🗯˙🫐⃟.꩜‹—",
    "{text} गरीब ⊹ ࣪ ﹏𓊝﹏𓂁﹏⊹ ࣪ ˖",
    "{text} चमार˖ ࣪ ꉂ🗯˙🫐⃟.꩜‹—",
    "{text} भेंगे⊹ ࣪ ﹏𓊝﹏𓂁﹏⊹ ࣪ ˖",
    "{text} रैंडी के बच्चे˖ ࣪ ꉂ🗯˙🫐⃟.꩜‹—",
    "{text} गुलाम⊹ ࣪ ﹏𓊝﹏𓂁﹏⊹ ࣪ ˖",
    "{text} गुलामी कर˖ ࣪ ꉂ🗯˙🫐⃟.꩜‹—",
    "{text} चुदाई केंद्र⊹ ࣪ ﹏𓊝﹏𓂁﹏⊹ ࣪ ˖",
    "{text} नांगा नाच कर˖ ࣪ ꉂ🗯˙🫐⃟.꩜‹—",
    "{text} पापा बोल 𝑋EN को⊹ ࣪ ﹏𓊝﹏𓂁﹏⊹ ࣪ ˖",
    "{text} तेरी मां नंगी करू˖ ࣪ ꉂ🗯˙🫐⃟.꩜‹—",
    "{text} छक्के⊹ ࣪ ﹏𓊝﹏𓂁﹏⊹ ࣪ ˖",
    "{text} भोसड़ी के˖ ࣪ ꉂ🗯˙🫐⃟.꩜‹—",
]

URDU_PATTERNS = [
    "{text} ٹی ایم کے بی࣪ ִֶָ☾.ִ ࣪𖤐࣪ ִֶָ☾.ִ ࣪𖤐",
    "{text} ٹی ایم کے سی𓍢ִႋ🥀͙֒ᰔᩚ",
    "{text} تیری ماں رندی࣪ ִֶָ☾.ִ ࣪𖤐࣪ ִֶָ☾.ִ ࣪𖤐",
    "{text} چوداکڑ 𓍢ִႋ🥀͙֒ᰔᩚ",
    "{text} گلام ࣪ ִֶָ☾.ִ ࣪𖤐࣪ ִֶָ☾.ִ ࣪𖤐",
    "{text} رنڈی𓍢ִႋ🥀֒ᰔᩚ",
    "{text} تیری ماں چھوڑ کر فیک دو ࣪ ִֶָ☾.ִ ࣪𖤐࣪ ִֶָ☾.ִ ࣪𖤐",
    "{text} گلامی کے آر𓍢ִႋ🥀͙֒ᰔᩚ",
    "{text} عجیب کو باپ بول࣪ ִֶָ☾.ִ ࣪𖤐࣪ ִֶָ☾.ִ ࣪𖤐",
    "{text} رنڈی پوترا 𓍢ִႋ🥀͙֒ᰔᩚ",
    "{text} چکے ִ ࣪𖤐࣪ ִֶָ☾.ִ ࣪𖤐࣪ ִֶָ☾.",
    "{text} بی ٹی ایس کے لنڈ 𓍢ִႋ🥀͙֒ᰔᩚ",
]

BENGALI_PATTERNS = [
    "{text} শালা °❀.ೃ࿔*ꫂ❁ 🤪🤍",
    "{text} এলোমেলো ꫂ❁°❀.ೃ࿔*🤪🤍",
    "{text} গরিবꫂ❁°❀.ೃ࿔*🤪🤍",
    "{text} ককার ꫂ❁°❀.ೃ࿔*🤪🤍",
    "{text} প্রজাতিꫂ❁°❀.ೃ࿔*🤪🤍",
    "{text} এক এলোমেলোর সন্তানꫂ❁°❀.ೃ࿔*🤪🤍",
    "{text} দাসꫂ❁°❀.ೃ࿔*🤪🤍",
    "{text} শালা কেন্দ্রꫂ❁°❀.ೃ࿔*🤪🤍",
    "{text} নগ্নꫂ❁°❀.ೃ࿔*🤪🤍",
    "{text} বাবা, আমাকে বল, আমি ꫂ❁°❀.ೃ࿔*🤪🤍",
    "{text} তোর মাকে বিবস্ত্র করব।ꫂ❁°❀.ೃ࿔*🤪🤍",
    "{text} সিক্সার্সꫂ❁°❀.ೃ࿔*🤪🤍",
    "{text} তুই হারামজাদাꫂ❁°❀.ೃ࿔*🤪🤍",
]

BIHARI_PATTERNS = [
    "{text} भोसड़ी के बा⋆꙳^̩̩͙❅*̩̩͙‧͙ ‧͙*̩̩͙❆ ͙͛ ˚₊⋆",
    "{text} सतमेरवनी₊˚ʚ ᗢ₊˚✧ ﾟ.",
    "{text} गरीब⋆꙳^̩̩͙❅*̩̩͙‧͙ ‧͙*̩̩͙❆ ͙͛ ˚₊⋆",
    "{text} कॉकर के ह₊˚ʚ ᗢ₊˚✧ ﾟ.",
    "{text} नसल⋆꙳^̩̩͙❅*̩̩͙‧͙ ‧͙*̩̩͙❆ ͙͛ ˚₊⋆",
    "{text} एगो बेतरतीब के लइका₊˚ʚ ᗢ₊˚✧ ﾟ.",
    "{text} गुलाम⋆꙳^̩̩͙❅*̩̩͙‧͙ ‧͙*̩̩͙❆ ͙͛ ˚₊⋆",
    "{text} कमबख्त सेंटर के बा₊˚ʚ ᗢ₊˚✧ ﾟ.",
    "{text} नंगा हो गइल बा⋆꙳^̩̩͙❅*̩̩͙‧͙ ‧͙*̩̩͙❆ ͙͛ ˚₊⋆",
    "{text} पापा बताव हम तोहार माई के {text} उतार देब।₊˚ʚ ᗢ₊˚✧ ﾟ.",
    "{text} छक्का के लोग⋆꙳^̩̩͙❅*̩̩͙‧͙ ‧͙*̩̩͙❆ ͙͛ ˚₊⋆",
    "{text} रे हरामी₊˚ʚ ᗢ₊˚✧ ﾟ.",
]

CHINESE_PATTERNS = [
    "{text} 杂种 ⊹ ࣪ ﹏𓊝﹏𓂁﹏⊹ ࣪ ˖",
    "{text} 婊子˖ ࣪ ꉂ🗯˙🫐⃟.꩜‹—",
    "{text} 穷鬼⊹ ࣪ ﹏𓊝﹏𓂁﹏⊹ ࣪ ˖",
    "{text} 贱民˖ ࣪ ꉂ🗯˙🫐⃟.꩜‹—",
    "{text} 斗鸡眼⊹ ࣪ ﹏𓊝﹏𓂁﹏⊹ ࣪ ˖",
    "{text} 臭婊子的孩子˖ ࣪ ꉂ🗯˙🫐⃟.꩜‹—",
    "{text} 奴隶⊹ ࣪ ﹏𓊝﹏𓂁﹏⊹ ࣪ ˖",
    "{text} 给我当奴隶˖ ࣪ ꉂ🗯˙🫐⃟.꩜‹—",
    "{text} 脱光了跳舞⊹ ࣪ ﹏𓊝﹏𓂁﹏⊹ ࣪ ˖",
    "{text} 叫我爸爸˖ ࣪ ꉂ🗯˙🫐⃟.꩜‹—",
    "{text} 你妈是妓女⊹ ࣪ ﹏𓊝﹏𓂁﹏⊹ ࣪ ˖",
    "{text} 太监˖ ࣪ ꉂ🗯˙🫐⃟.꩜‹—",
    "{text} 臭逼⊹ ࣪ ﹏𓊝﹏𓂁﹏⊹ ࣪ ˖",
    "{text} 废物滚开˖ ࣪ ꉂ🗯˙🫐⃟.꩜‹—",
]

GOODNCRAID_TEXTS = [
    " ᵀᵐᴷᶜ」🦋꙰ ~ ༈ ◠🇮🇳◡",
    " Teri माँ Dead 😂 ",
    " ᴛᴇʀᴀ ʙᴀᴀᴘ ᴄᴀʀᴘᴀɴᴛᴇʀ 🪚",
    " ᴛʀʏ ᴅᴀᴅɪ sʟᴜᴛ⚀︎",
    " ʏᴏᴜʀ ᴍᴏᴍ ᴡʜᴏʀᴇ👞",
    " ɢᴜʟᴀᴍ ɢᴀɴᴅ ᴋᴀ ᴊᴏʀ ʟɢᴀ😆",
    " ᴛᴇʀᴀ ʙᴀᴀᴘ ꜱᴜᴘʀᴇᴍᴇ😼",
    " ᴛᴇʀʏᴍᴀ ᴡᴇᴅs ꜱᴜᴘʀᴇᴍᴇ🍇",
    " ʀɴᴅɪ ᴋᴀ ʟᴅᴄᴀ🍑",
    " ʜᴠᴀʙᴀᴀᴢ ᴄʜᴜᴅᴋᴇ ᴍʀᴀ🧖",
    " ʀᴀɴᴅɪ ᴋɪ ᴘᴀɪᴅᴀɪsʜ💔",
    " ᴄʜᴜᴅ ɢʏɪ ᴍᴀᴀ ᴛᴇʀɪ 🤣",
    " ᴀʙʙᴜ ʙᴏʟ ꜱᴜᴘʀᴇᴍᴇ ᴋᴏ 😈",
    " ᴛᴇʀɪ ʙᴇʜᴇɴ ᴍᴇʀɪ ғᴀɴ 🥵",
    " ᴅᴇᴋʜ ꜱᴜᴘʀᴇᴍᴇ ᴋɪ ᴘᴏᴡᴇʀ 💪",
    " ᴀʙʙᴇ ɴᴀʟʟᴇ sᴜᴅʜᴀʀ ᴊᴀ 🤬",
    " ᴛᴇʀᴀ ᴋʜᴀɴᴅᴀᴀɴ ᴄʜᴜᴅ ɢʏᴀ 💀",
    " ᴛᴇʀᴇ ꜱᴜᴘʀᴇᴍᴇ ᴘᴀᴘᴀ ᴀᴀʏᴇ ʜ 🦁",
    " ʙʜᴀᴀɢ ʙʜᴏsᴅɪᴋᴇ ʙʜᴀᴀɢ 🏃",
    " ᴛᴇʀɪ ᴍᴀᴀ ᴋᴀ ʙʜᴏsᴅᴀ 😹",
    " ɢᴀᴀɴᴅ ғᴀᴛᴛ ɢʏɪ? 🥺",
    " ᴋᴀ sʏsᴛᴇᴍ ʜᴀɴɢ ʙʏ ꜱᴜᴘʀᴇᴍᴇ 💻",
    " ᴛᴇʀᴀ ʙᴀᴀᴘ ᴀᴀʏᴀ 🤬",
    " ᴍᴀᴀ ᴄʜᴜᴅᴀ ʟᴏᴅᴇ 🍑",
    " ʀᴀɴᴅɪ ʀᴏɴᴀ ᴍᴀᴛ ᴋᴀʀ 😭",
    " ᴛᴇʀɪ ᴍᴀᴀ ᴋɪ ᴄʜᴜᴛ ᴍᴇ ᴘᴀɪʀ 🦶",
    " ꜱᴜᴘʀᴇᴍᴇ ᴏɴ ᴛᴏᴘ 🔝",
    " ᴄʜᴀʟ ɴɪᴋᴀʟ ʟᴏᴅᴇ 🚪",
    " ᴛᴇʀɪ ᴍᴀᴀ ᴋᴀ ʀᴀᴘᴇ 🔞",
    " sᴀʏ ꜱᴜᴘʀᴇᴍᴇ ɪs ɢᴏᴅ ⚡",
    " ʙᴏᴛs ᴀʀᴇ ғᴜᴄᴋɪɴɢ ʏᴏᴜ 🤖",
    " ᴛᴇʀᴀ ʙᴀᴀᴘ ʜᴜ ᴍᴀɪ 🎅",
    " ᴀᴜᴋᴀᴀᴛ ᴍᴇ ʀᴇʜ 🤬",
    " ɢᴀᴀɴᴅ ᴍᴇ ᴅᴀɴᴅᴀ ᴅᴇ ᴅᴜɴɢᴀ 🎋",
    " ᴄʜᴜᴘ ᴋᴀʀ ʀᴀɴᴅɪ 🤫",
    " ᴛᴇʀɪ ʙᴇʜᴇɴ ᴄʜᴜᴅ ɢʏɪ 💃",
    " ᴅᴇᴋʜ ꜱᴜᴘʀᴇᴍᴇ ᴋᴀ ᴋʜᴀᴜғ 😈",
]

ENGLISH_PATTERNS = [
    "{text} 🅱🅻🅾🅾🅳🆈 🅷🅴🅻🅻.𖥔 ݁ ˖ִ🛸༄˖°.",
    "{text} 🅼🅾🆃🅷🅴🆁🅵🆄🅲🅺🅴🆁🌊⋆｡ 𖦹°.🐚⋆❀˖°🫧",
    "{text} 🅱🅸🆃🅲🅷 🆂🅾🅽.𖥔 ݁ ˖ִ🛸༄˖°.",
    "{text} 🆂🅻🅰🆅🅴🌊⋆｡ 𖦹°.🐚⋆❀˖°🫧",
    "{text} 🆂🅾🅽 🅾🅵 🅼🅸🅰 🅺🅷🅰🅻🅸🅵🅰 .𖥔 ݁ ˖ִ🛸༄˖°.",
    "{text} 🆂🅰🆈  🅳🅰🅳🅳🆈🌊⋆｡ 𖦹°.🐚⋆❀˖°🫧",
    "{text} 🅵🆄🅲🅺🄽🄶 🅲🅴🅽🆃🆁🅴.𖥔 ݁ ˖ִ🛸༄˖°.",
    "{text} 🆂🅾🅽 🆂︎🅰︎🅽︎🅾︎🆉︎🅾︎ 🅼🅾🅼🌊⋆｡ 𖦹°.🐚⋆❀˖°🫧",
]

EMOJI_NC_EMOJIS = ["🚀","♨️","👑","♻️","🚨","🎪","🎃","🎄","🧨","✨","🎈","🎉","🎯","🎀","🎁","🎗️","🎟️","🏆","🧧","⚽","☣️","⚜️","⚛️","🕉️","✡️","☸️","☯️","✝️","☦️","☪️","☮️","🕎","🔯","♈","♉","❗","❕","‼️","⁉️","❕","〽️","🔰","⭕","📛","♻️","⚰️","🪓","💠"]
EMOJI_NC_PATTERN = "'ठीक है अलविदा मैं जल्द ही {text} की माँ का भोसड़ा चोदने आऊँगा <⋆.ೃ࿔*:･{emoji}⋆.ೃ࿔*:･>"

NC1_EMOJIS = ["👹᭄","👺᭄","😈᭄","💀᭄","☠️᭄","☣️᭄","🩸᭄","🕷᭄","🕸᭄","🦇᭄","🌑᭄","🖤᭄","🔮᭄","⚰️᭄","🪦᭄","🗡️᭄","⚔️᭄","🔥᭄","💥᭄","😱᭄","🤬᭄","👻᭄","🎃᭄","🦴᭄","💣᭄","🧿᭄","🌚᭄","🕯️᭄","🪄᭄","🧙","🧛᭄"]
NC1_PATTERN = "˚⊱{emoji}⊰˚{text} ᥴᥙᦔꪖɪ ᴋʜꪖꪀꪖ 😫 🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵🌪️🧵<˚⊱⊰˚{emoji}˚⊱⊰˚>"

NC2_EMOJIS = ["🌋","🔥","💥","🫨","♨️","🟠","🟡","🔴","☄️","⚡","💢","😤","🥵","🧱","🪨","💣","🧨","🌪️","🌡️","🦴","🐉","🔶","🔸","🔺","🔻","🌊","💨","🌫️","🏔️","⛰️","🗻","🌑","🌒","🌓","🌔","🌕","🌖","🌗","🌘","🪐","💫","✨","🌠","🎇","🎆","🔮","🫀","🧠","👁️","🦷","🦴","🐊","🦎"]
NC2_PATTERN = "{text} ✧ ꜱᴜᴘʀᴇᴍᴇ -/-  ꪖʙʙꪊ ᴏᴘ ʙᴏʟ Nyto ⌯⌲ कुत्तिया se ᴄᴜᴅᴀɪ ᴋʜᴀ╰┈➤ 🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥✅🔥✅🔥✅🔥✅🔥✅🔥✅🔥🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍🔥🤍 <{emoji}>"

NC3_EMOJIS = ["🌋","🔥","💥","🫨","♨️","🟠","🟡","🔴","☄️","⚡","💢","😤","🥵","🧱","🪨","💣","🧨","🌪️","🌡️","🦴","🐉","🔶","🔸","🔺","🔻","🌊","💨","🌫️","🏔️","⛰️","🗻","🌑","🌒","🌓","🌔","🌕","🌖","🌗","🌘","🪐","💫","✨","🌠","🎇","🎆","🔮","🫀","🧠","👁️","🦷","🦴","🐊","🦎"]
NC3_PATTERN = "{text} I ᴄʀᴀᴠᴇ Fᴏʀ Yᴏᴜʀ Mᴏᴍs Pᴜssʏ --->🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃🧃💢🧃💢🧃💢🧃💢🧃💢🧃🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃💢🧃 <{emoji}>"

NC4_EMOJIS = ["🏔️","🌋","☃️","🏝️","🏖️","🌊","🌬️","❄️","🌀","🌪️","⚡","☔","💧","☁️","🌨️","🌧️","🌩️","⛈️","🌦️","🌥️","⛅","🌤️","☀️","🌞","🌝","🌚","🌜","🌛","🌙","⭐","🌟","✨","🪐","🌍","🌠","🌌","☄️","🌑","🌒","🌓","🌔","🌕","🌖","🌗","🌘"]
NC4_PATTERN = "{text} ִֶָ⁀➴༯ sꪶꪖꪜꫀ ִֶָ. ..𓂃 ࣪ ִֶָ🌈་༘࿐ 𝗟𝗡𝗗 𝗖𝗛𝗢𝗢𝗦 -/-  <{emoji}>"

NC5_EMOJIS = ["🧙","🧙\u200d♂️","🧙\u200d♀️","🪄","✨","🌟","⭐","💫","☄️","🌠","🔮","🎩","🐉","🐲","🦄","🧚","🧚\u200d♂️","🧚\u200d♀️","🧜","🧜\u200d♂️","🧜\u200d♀️","🧞","🧞\u200d♂️","🧞\u200d♀️","🧝","🧝\u200d♂️","🧝\u200d♀️","🗡️","🛡️","⚔️","🏹","🪓","☣️","⚜️","🎭","🎪"]
NC5_PATTERN = "🩷 {text} ✧ ꜱᴜᴘʀᴇᴍᴇ-/-  ꪖʙʙꪊ ᴏᴘ ʙᴏʟ Nyto aaj try maa confirm chudegi 🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕 {emoji} "

KNC_EMOJIS = ["👶","👧","🧒","👦","👩","🧑","👨","👩\u200d🦱","🧑\u200d🦱","👨\u200d🦱","👩\u200d🦰","🧑\u200d🦰","👨\u200d🦰","👱\u200d♀️","👱","👱\u200d♂️","👩\u200d🦳","🧑\u200d🦳","👨\u200d🦳","👩\u200d🦲","🧑\u200d🦲","👨\u200d🦲","🧔\u200d♀️","🧔","🧔\u200d♂️","👵","🧓","👴","👲","👳\u200d♀️","👳","👳\u200d♂️","🧕","👮\u200d♀️","👮","👮\u200d♂️","👷\u200d♀️","👷","👷\u200d♂️","💂\u200d♀️","💂","💂\u200d♂️"]
KNC_PATTERN = "{text} <{emoji}>⌯⌲ कुत्तिया ᴄᴜᴅᴀɪ ᴋʜᴀ╰┈➤🌀💦🌀💦🌀💦🌀💦🌀💦🌀💦🌀💦🌀💦🌀💦🌀💦🌀💦🌀💦🌀🌀💦🌀💦🌀💦🌀💦🌀💦💦🌀💦🌀💦🌀💦🌀💦🌀"

ANC_EMOJIS = ["🌈","☔","⚡","🌪️","🌀","🏖️","🏝️","🌊","🌬️","❄️","💧","🌨️","☁️"]
ANC_PATTERN = "{text} _✍🏻 𝐘ᴇ 𝐃ᴇᴋʜ ˢᶜʳⁱᵖᵗ ˡⁱᵏʰ ʳᵃʰᵃ ʰᵘ 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐊ᴇ 𝐁ʜᴏsᴅᴇ 𝐌ᴇɪɴ <{emoji}> 🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕🍂🐕"

FNC_EMOJIS = ["❤️","🧡","💛","💚","🩵","💙","💜","🤎","🖤","🩶","🤍","🩷"]
FNC_PATTERN = "{text} रंगबेरंगी रण्डी तेरी 𝘾𝙃𝙐𝘿𝘼𝙄 𝘼𝙍𝘾 <{emoji}> જ⁀➴❤️‍🔥જ⁀➴🎀જ⁀➴🤍જ⁀➴💓જ⁀➴❣️જ⁀➴🩵જ⁀➴💚જ⁀➴❤️"

# ===========================================================
# SLIDE MESSAGES — clean versions
# ===========================================================
SLIDE1_MESSAGES = [
    "𝐓ᴍᴋʙ 𝐑ɴᴅʏ ᴋᴇ 𝐋ᴀᴅᴋᴇ 😈🖕🏻😈🖕🏻😈",
    "𝐓ᴇʀɪ ᴍᴀᴀ ᴍᴀʀ ɢʏɪ ¿😆😆😆",
    "𝐀ᴀʀ ꜱᴀᴍᴀɴᴅᴀʀ ᴘᴀᴀʀ ꜱᴀᴍᴀɴᴅᴀʀ ʙᴇᴇᴄʜ ᴍɪᴇ ʜᴀɪ ɴᴀɪʏᴀ ᴘʜʟᴇ ᴛᴇʀɪ ʙʜᴇɴ चोदू ʙᴀᴀᴅ ᴍɪᴇ चोदू ᴍᴀɪʏᴀ ¡! 🥰🖕🏻🥰🖕🏻🥰🖕🏻",
    "𝐓ᴇʀɪ 𝐌ᴀᴀ ʜᴜᴍᴇꜱʜᴀ ᴍᴜᴊʜꜱᴇ ʜɪ ᴋʏᴜ चुडती है ¡! 😡🤬😡🤬😡",
    "𝐃ᴇᴋʜ ᴀᴀᴊ ᴛᴇʀɪ 𝐌ᴀᴀ ᴋᴀ ɴᴀɴɢᴀ ᴅᴀɴᴄᴇ ᴅɪᴋʜᴀᴜ ! 🩰🧑🏻‍🩰",
]

SLIDE2_MESSAGES = [
    "𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐊ɪ 𝐆ᴜʟᴀʙɪ 𝐂ʜᴜᴛ ᴍɪᴇ 𝐌ᴜᴛ ᴋʀ ʙʜᴀɢ ᴊᴀᴜɢᴀ 𝐁ꜱᴅᴋ ! 😆",
    "𝐓ᴇʀɪ 𝐌ᴀᴀ ᴄʜᴏᴅɴᴇ ᴀʀʜᴀ ʜᴜ ʀᴜᴋ ᴡʜɪ ɢᴜʟᴀᴍ ! 😾",
    "𝐓ᴇʀɪ ʙʜᴇɴ ᴋᴇ ʙᴏᴏʙɪᴇꜱ ᴋᴇ ʙᴇᴇᴄʜ ᴍɪᴇ ʟɴᴅ ꜰᴀꜱᴀ ᴋʀ ᴍᴜᴛʜ ᴍᴀᴀʀ ᴅᴜɢᴀ ʙꜱᴅᴋ 😆",
    "𝐓ᴇʀɪ ᴍᴀᴀ ᴋɪ ᴄʜᴜᴛ ᴍɪᴇ ᴍᴀɢɢɪᴇ ʙɴᴀ ᴋʀ ᴍᴜᴛʜ ʙʜᴀʀ ᴅᴜɢᴀ ! 😆",
    "𝐓ᴇʀɪ ᴍᴀᴀ ʙʜᴛ ʀᴏᴛɪ ᴇʏ ʙɪʟᴋᴜʟ 𝐓ᴇʀɪ ᴛʀʜ ᴅᴏɴᴏ ʀɴᴅʏ ʀᴏɴᴀ ᴋʀᴛᴇ ʜᴏ ᴇᴡᴡ ! 😆",
    "𝐓ᴇʀɪ ʙʜᴇɴ ᴋɪ ɢᴜʟᴀʙɪ ᴄʜᴛ ᴋᴀᴀᴛ ᴅᴜɢᴀ ɢᴜʟᴀᴍ ! 😆",
    "𝐂ʜʟ ɢᴜʟᴀᴍ ɢᴜʟᴀᴍɪ ᴋʀ ! 😾",
]

SLIDE3_PATTERN = "{text} Bᴇᴛᴀ Gᴀʟᴀᴛ Jᴀᴡᴀʙ Aʙ Tᴇʀɪ Mᴀ Kɪ Cʜᴜᴅᴀʏɪ Hᴏɢɪ 😁🙌🏻🔥"

# Custom NC frames — for /mync command
CUSTOM_NC_FRAMES = [
    "⚡ {text} ⚡",
    "☣️ {text} ☣️",
    "💀 {text} 💀",
    "🔥 {text} 🔥",
    "👑 {text} 👑",
    "⚔️ {text} ⚔️",
    "🌪️ {text} 🌪️",
    "💥 {text} 💥",
    "☠️ {text} ☠️",
    "🩸 {text} 🩸",
    "🎯 {text} 🎯",
    "🔮 {text} 🔮",
]

# ===========================================================
# SPAM PATTERNS — cleaned
# ===========================================================
SPAM1_PATTERN = "🎐𓍼ֶ˖ܓ  ( < {text} > )  की अम्मी-जान का रेपिस्ट बाप हू ˚.🥀>"
SPAM2_SINGLE_PATTERN = "{text} - 𝐑ᴀɴᴅᴏ𝐌 𝐒ᴀʟ𝐄 𝐂ʜᴜᴅᴛ𝐀 𝐑ᴇ𝐇 𝐓ᴜ 🚸🤍🙇🏻𓍼ִֶָ𓂃 ࣪˖ ִֶָ"
SPAM2_PATTERN = (SPAM2_SINGLE_PATTERN + "\n") * 10
SPAM3_SINGLE_PATTERN = "--->>🤍➳➳➳➳➳➳➳➳➳➳➳➳➳➳➳➳➳➳➳➳➳➳➳➳➳{text} 𝗦ᴀɴᴏᴢᴏ 𝗦𝗽𝗮𝗺 𝗔𝘁𝘁𝗮𝗰𝗸 𝗕𝗲𝗴𝗶𝗻𝘀 🍂😫"
SPAM3_PATTERN = (SPAM3_SINGLE_PATTERN + "\n") * 10
SPAM4_SINGLE_PATTERN = "𓆩{text}𓆪 𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- 🪄"
SPAM4_PATTERN = (SPAM4_SINGLE_PATTERN + "\n\n") * 10

# ===========================================================
# EXTRA DATA — NC heart, flags, special emojis (cleaned)
# ===========================================================
NC_heart_MESSAGES = [
    'रंगबेरंगी रण्डी ❤️🧡💛💚',
    'रंगबेरंगी रण्डी 🧡💛💚🩵',
    'रंगबेरंगी रण्डी 💛💚🩵💙',
    'रंगबेरंगी रण्डी 💚🩵💙💜',
    'रंगबेरंगी रण्डी 🩵💙💜🤎',
    'रंगबेरंगी रण्डी 💙💜🤎🖤',
    'रंगबेरंगी रण्डी 💜🤎🖤🩶',
    'रंगबेरंगी रण्डी 🤎🖤🩶🤍',
    'रंगबेरंगी रण्डी 🖤🩶🤍🩷',
    'रंगबेरंगी रण्डी 🩶🤍🩷❤️‍🩹',
    'रंगबेरंगी रण्डी 🤍🩷❤️‍🩹💔',
    'रंगबेरंगी रण्डी 🩷❤️‍🩹💔❤️‍🔥',
]

NC_FLAG_MESSAGES = [
    '{target} I ᴄʀᴀᴠᴇ Fᴏʀ Yᴏᴜʀ Mᴏᴍs Pᴜssʏ ✩‧⁀➷्र 🍓🎀🩵💋🇰🇪𓍼ֶָ֢',
    '{target} I ᴄʀᴀᴠᴇ Fᴏʀ Yᴏᴜʀ Mᴏᴍs Pᴜssʏ ✩‧⁀➷्र 🍓🎀🩵💋🇱🇨𓍼ֶָ֢',
    '{target} I ᴄʀᴀᴠᴇ Fᴏʀ Yᴏᴜʀ Mᴏᴍs Pᴜssʏ ✩‧⁀➷्रˑ 🍓🎀🩵💋🇦🇫𓍼ֶָ֢',
    '{target} I ᴄʀᴀᴠᴇ Fᴏʀ Yᴏᴜʀ Mᴏᴍs Pᴜssʏ ✩‧⁀➷्रˑ 🍓🎀🩵💋🇧🇧𓍼ֶָ֢',
    '{target} I ᴄʀᴀᴠᴇ Fᴏʀ Yᴏᴜʀ Mᴏᴍs Pᴜssʏ ✩‧⁀➷्रˑ 🍓🎀🩵💋🇪🇺𓍼ֶָ֢',
    '{target} I ᴄʀᴀᴠᴇ Fᴏʀ Yᴏᴜʀ Mᴏᴍs Pᴜssʏ ✩‧⁀➷्रˑ 🍓🎀🩵💋🇦🇺𓍼ֶָ֢',
]

TIME_NC_MESSAGES = [
    'Tɪᴍᴇ Is Oᴠᴇʀ 12:382:229',
    'Tᴇʀɪ Mᴀᴀ Kᴀ Bʜᴏsᴅᴀ Sɪʟ Dᴜɴ',
    'Tᴇʀᴀ Bᴀᴀᴘ ꜱᴜᴘʀᴇᴍᴇ 12:382:23',
    'Tᴇʀɪ Bᴇʜɴ Kɪ Cʜᴜᴛ Mᴇ Gʜᴀᴅɪ 12:382:232',
    'Tɪᴍᴇ Tᴏ Dɪᴇ Mᴄ 12:382:233',
    '12:382:234 Tᴇʀɪ Mᴀᴀ Cʜᴜᴅ Gᴀʏɪ',
]

NC_WEB_MESSAGES = [
    '{target} 𝑇𝑀𝐾𝐿℘✩₊˚.⋆🕸️🐇',
    '{target} 𝑇𝐵𝐾𝐿℘✩₊˚.⋆🕸️🐇',
    '{target} 𝐺𝐴𝑌℘✩₊˚.⋆🕸️🐇',
    '{target} 𝐶𝐻𝑈𝐷℘✩₊˚.⋆🕸️🐇',
    '{target} 𝐶𝐻𝐴𝑃𝑅𝐼℘✩₊˚.⋆🕸️🐇',
]

DOTZKENG_MESSAGES = [
    "{target}🤍 ⭅╡𝗧𝗠𝗞𝗖╞⭆🧡",
    "{target}🤍⭅╡माधरचोद╞⭆❤️",
    "{target}🤍⭅╡माधरचोद╞⭆💙",
    "{target}🤍⭅╡माधरचोद╞⭆🩵",
    "{target}🤍⭅╡माधरचोद╞⭆💚",
    "{target}🤍⭅╡माधरचोद╞⭆💛",
    "{target}🤍⭅╡माधरचोद╞⭆❤️‍🩹",
    "{target}🤍⭅╡माधरचोद╞⭆💔",
    "{target}🤍⭅╡माधरचोद╞⭆❤️‍🔥",
    "{target}🤍⭅╡माधरचोद╞⭆🩷",
    "{target}🤍⭅╡माधरचोद╞⭆🩶",
    "{target}🤍⭅╡माधरचोद╞⭆🖤",
    "{target}🤍⭅╡माधरचोद╞⭆🤎",
    "{target}🤍⭅╡माधरचोद╞⭆💜",
]

FLOWER_NC_MESSAGES = [
    '⋆₊🍁˚{target} Sʟᴜᴛ Mᴀ ᴋ Lᴅᴋᴇy ',
    '⋆₊🌱˚{target} Sʟᴜᴛ Mᴀ ᴋ Lᴅᴋᴇy ',
    '⋆₊🌿˚{target} Sʟᴜᴛ Mᴀ ᴋ Lᴅᴋᴇy ',
    '⋆₊🍃˚{target} Sʟᴜᴛ Mᴀ ᴋ Lᴅᴋᴇy ',
    '⋆₊☘️˚{target} Sʟᴜᴛ Mᴀ ᴋ Lᴅᴋᴇy ',
    '⋆₊🍀˚{target} Sʟᴜᴛ Mᴀ ᴋ Lᴅᴋᴇy ',
]

NAME_CHANGE_MESSAGES = [
    '{target} 𝗧𝗘𝗥𝗜 𝗠𝗔𝗔 𝗞𝗘 𝗕𝗛𝗢𝗦𝗗𝗘 𝗠𝗘𝗜 𝗦𝗣𝗢𝗧𝗜𝗙𝗬 𝗗𝗔𝗟 𝗞𝗘 𝗟𝗢𝗙𝗜 𝗕𝗔𝗝𝗔𝗨𝗡𝗚𝗔 𝗗𝗜𝗡 𝗕𝗛𝗔𝗥 ➥🩶₊𓍼ֶָ֢',
    '{target} 𝗧𝗘𝗥𝗜 𝗠𝗔𝗔 𝗞𝗘 𝗕𝗛𝗢𝗦𝗗𝗘 𝗠𝗘𝗜 𝗦𝗣𝗢𝗧𝗜𝗙𝗬 𝗗𝗔𝗟 𝗞𝗘 𝗟𝗢𝗙𝗜 𝗕𝗔𝗝𝗔𝗨𝗡𝗚𝗔 𝗗𝗜𝗡 𝗕𝗛𝗔𝗥 ➥🩵₊𓍼ֶָ֢',
    '{target} 𝗧𝗘𝗥𝗜 𝗠𝗔𝗔 𝗞𝗘 𝗕𝗛𝗢𝗦𝗗𝗘 𝗠𝗘𝗜 𝗦𝗣𝗢𝗧𝗜𝗙𝗬 𝗗𝗔𝗟 𝗞𝗘 𝗟𝗢𝗙𝗜 𝗕𝗔𝗝𝗔𝗨𝗡𝗚𝗔 𝗗𝗜𝗡 𝗕𝗛𝗔𝗥 ➥🩷₊𓍼ֶָ֢',
    '{target} 𝗧𝗘𝗥𝗜 𝗠𝗔𝗔 𝗞𝗘 𝗕𝗛𝗢𝗦𝗗𝗘 𝗠𝗘𝗜 𝗦𝗣𝗢𝗧𝗜𝗙𝗬 𝗗𝗔𝗟 𝗞𝗘 𝗟𝗢𝗙𝗜 𝗕𝗔𝗝𝗔𝗨𝗡𝗚𝗔 𝗗𝗜𝗡 𝗕𝗛𝗔𝗥 ➥🤍₊𓍼ֶָ֢',
    '{target} 𝗧𝗘𝗥𝗜 𝗠𝗔𝗔 𝗞𝗘 𝗕𝗛𝗢𝗦𝗗𝗘 𝗠𝗘𝗜 𝗦𝗣𝗢𝗧𝗜𝗙𝗬 𝗗𝗔𝗟 𝗞𝗘 𝗟𝗢𝗙𝗜 𝗕𝗔𝗝𝗔𝗨𝗡𝗚𝗔 𝗗𝗜𝗡 𝗕𝗛𝗔𝗥 ➥🖤₊𓍼ֶָ֢',
    '{target} 𝗧𝗘𝗥𝗜 𝗠𝗔𝗔 𝗞𝗘 𝗕𝗛𝗢𝗦𝗗𝗘 𝗠𝗘𝗜 𝗦𝗣𝗢𝗧𝗜𝗙𝗬 𝗗𝗔𝗟 𝗞𝗘 𝗟𝗢𝗙𝗜 𝗕𝗔𝗝𝗔𝗨𝗡𝗚𝗔 𝗗𝗜𝗡 𝗕𝗛𝗔𝗥 ➥💜₊𓍼ֶָ֢',
]

RAID_TEXTS = [
    '✩‧⁀➷🩷✧.* 𝐑ᴀɴᴅᴏ𝐌 𝐒ᴀʟ𝐄 𝐂ʜᴜᴅᴛ𝐀 𝐑ᴇ𝐇 𝐓ᴜ ✩‧⁀➷🩷✧.*',
    '✩‧⁀➷🩵✧.* 𝐓ᴇʀ𝐈 𝐁ᴇʜᴇ𝐍 𝐑ᴀɴᴅ𝐈 𝐁ᴇᴛ𝐀 ✩‧⁀➷🩵✧.*',
    '✩‧⁀➷🩶✧.* 𝐓ᴇʀ𝐈 𝐌ᴀ𝐀 𝐊ɪ 𝐂ʜᴜ𝐓 𝐏ɪʟʟ𝐄 ✩‧⁀➷🩶✧.*',
    '✩‧⁀➷🤍✧.* 𝐁ʜᴀɢɴ𝐀 𝐍ᴀʜ𝐈 𝐁ᴇᴛ𝐀 ✩‧⁀➷🤍✧.*',
    '✩‧⁀➷❤️✧.* 𝐀ᴜᴋᴀ𝐓 𝐁ʜᴜ𝐋 𝐆ᴀʏ𝐀 𝐊ʏ𝐀 𝐑ɴᴅʏ𝐊 ✩‧⁀➷❤️✧.*',
    '✩‧⁀➷🧡✧.* 𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 ✩‧⁀➷🧡✧.*',
    '✩‧⁀➷💙✧.* I ᴄʀᴀᴠᴇ Fᴏʀ Yᴏᴜʀ Mᴏᴍs Pᴜssʏ ✩‧⁀➷💙✧.*',
    '✩‧⁀➷❤️‍🔥✧.* 𝐓ᴇʀ𝐈 𝐁ᴇʜᴇ𝐍 𝐊ᴀʟ𝐈 𝐂ʜᴜ𝐓 𝐊ɪ 𝐃ᴇᴠ𝐈 ✩‧⁀➷❤️‍🔥✧.*',
    '✩‧⁀➷💚✧.* 𝐓ᴇʀ𝐈 𝐃ᴀᴅ𝐈 𝐑ᴀɴᴅ𝐈 ✩‧⁀➷💚✧.*',
    '✩‧⁀➷🤎✧.* 𝐓ᴇʀ𝐈 𝐁ᴜ𝐀 𝐏ᴇ𝐋 𝐃ɪ𝐀 𝐁ᴇᴛ𝐀 ✩‧⁀➷🤎✧.*',
    'Bᴇᴛᴀ Gᴀʟᴀᴛ Jᴀᴡᴀʙ Aʙ Tᴇʀɪ Mᴀ Kɪ Cʜᴜᴅᴀʏɪ Hᴏɢɪ 😁🙌🏻🔥',
    'ठीक है अलविदा मैं जल्द ही तुम्हारी माँ का भोसड़ा चोदने आऊँगा',
    '𝐓ᴇʀ𝐈 𝐌ᴀ𝐀 𝐑ᴀɴᴅ𝐈 🩵🩷🩶',
]

ANIME_CHARACTERS = {
    1: ('Gojo Satoru','🔵','en-US-DavisNeural','+20%','+8Hz','Throughout Heaven and Earth, I alone am the honored one.'),
    2: ('Naruto','🍥','en-US-RyanNeural','+35%','+10Hz',"Believe it! I'm gonna be Hokage someday!"),
    3: ('Itachi','🌙','en-US-AndrewNeural','-25%','-6Hz',"You don't have enough hatred."),
    4: ('Luffy','⚓','en-US-TonyNeural','+40%','+12Hz',"I'm gonna be King of the Pirates!"),
    5: ('Zoro','⚔️','en-GB-RyanNeural','-10%','-8Hz','Nothing happened.'),
    6: ('Kakashi','📚','en-AU-WilliamNeural','-5%','0Hz','In this world, those who break the rules are scum.'),
    7: ('Vegeta','👑','en-US-GuyNeural','+5%','-4Hz','I am the Prince of all Saiyans! You are nothing!'),
    8: ('Light Yagami','📓','en-GB-ThomasNeural','-15%','+2Hz','I am Justice.'),
    9: ('Levi','🗡️','en-US-AndrewNeural','-30%','-10Hz','Give up on your dreams and die.'),
    10: ('Sasuke','🔴','en-US-DavisNeural','-35%','-8Hz','I have long since closed my eyes.'),
}

REACT_EMOJIS_ALL = ['👍','👎','❤','🔥','🥰','👏','😁','🤔','🤯','😱','🤬','😢','🎉','🤩','🤮','💩','🙏','👌','🕊','🤡','🥱','🥴','😍','🐳','❤\u200d🔥','🌚','🌭','💯','🤣','⚡','🍌','🏆','💔','🤨','😐','🍓','🍾','💋','🖕','😈','😴','😭','🤓','👻','👨\u200d💻','👀','🎃','🙈','😇','😨','🤝','✍','🤗','🫡','🎅','🎄','☃','💅','🤪','🗿','🆒','💘','🙉','🦄','😘','💊','🙊','😎','👾','🤙']
REACT_EMOJIS_ALL = list(dict.fromkeys(REACT_EMOJIS_ALL))  # dedup
BOT_SELF_REACT_EMOJIS = ['🔥','⚡','🏆','😈','🎉','💯','🌚','🤩','👌','💋']

HEART_EMOJIS = [
    'ꜱᴜᴘʀᴇᴍᴇ  么 𝐁ᴀᴘ 𝐁ᴏʟ → 🧍🏻','ꜱᴜᴘʀᴇᴍᴇ  么 𝐁ᴀᴘ 𝐁ᴏʟ →🤸🏻',
    'ꜱᴜᴘʀᴇᴍᴇ  么 𝐁ᴀᴘ 𝐁ᴏʟ →🧎🏻','ꜱᴜᴘʀᴇᴍᴇ  么 𝐁ᴀᴘ 𝐁ᴏʟ →🏃🏻',
    'ꜱᴜᴘʀᴇᴍᴇ  么 𝐁ᴀᴘ 𝐁ᴏʟ →🚶🏻','ꜱᴜᴘʀᴇᴍᴇ  么 𝐁ᴀᴘ 𝐁ᴏʟ →🏊🏻',
]

WHITE_EMOJIS = [
    '𝑳𝒖𝒏𝒅 𝑪𝒉𝒖𝒔 -/- ¿ 🤍','𝑳𝒖𝒏𝒅 𝑪𝒉𝒖𝒔 -/- ¿ 👻',
    '𝑳𝒖𝒏𝒅 𝑪𝒉𝒖𝒔 -/- ¿ 👀','𝑳𝒖𝒏𝒅 𝑪𝒉𝒖𝒔 -/- ¿ 💀',
    '𝑳𝒖𝒏𝒅 𝑪𝒉𝒖𝒔 -/- ¿ 🥼','𝑳𝒖𝒏𝒅 𝑪𝒉𝒖𝒔 -/- ¿ ⚪',
    '𝑳𝒖𝒏𝒅 𝑪𝒉𝒖𝒔 -/- ¿ ☃️','𝑳𝒖𝒏𝒅 𝑪𝒉𝒖𝒔 -/- ¿ 🦢',
    '𝑳𝒖𝒏𝒅 𝑪𝒉𝒖𝒔 -/- ¿ ⬜','𝑳𝒖𝒏𝒅 𝑪𝒉𝒖𝒔 -/- ¿ 🐑',
]

BLACK_EMOJIS = [
    'ठीक है अलविदा मैं जल्द ही तुम्हारी माँ का भोसड़ा चोदने आऊँगा👱🏿',
    'ठीक है अलविदा मैं जल्द ही तुम्हारी माँ का भोसड़ा चोदने आऊँगा🤙🏿',
    'ठीक है अलविदा मैं जल्द ही तुम्हारी माँ का भोसड़ा चोदने आऊँगा🤦🏿',
    'ठीक है अलविदा मैं जल्द ही तुम्हारी माँ का भोसड़ा चोदने आऊँगा🙅🏿',
    'ठीक है अलविदा मैं जल्द ही तुम्हारी माँ का भोसड़ा चोदने आऊँगा🙆🏿',
    'ठीक है अलविदा मैं जल्द ही तुम्हारी माँ का भोसड़ा चोदने आऊँगा👸🏿',
    'ठीक है अलविदा मैं जल्द ही तुम्हारी माँ का भोसड़ा चोदने आऊँगा👦🏿',
    'ठीक है अलविदा मैं जल्द ही तुम्हारी माँ का भोसड़ा चोदने आऊँगा🤰🏿',
    'ठीक है अलविदा मैं जल्द ही तुम्हारी माँ का भोसड़ा चोदने आऊँगा🏃🏿',
    'ठीक है अलविदा मैं जल्द ही तुम्हारी माँ का भोसड़ा चोदने आऊँगा🚶🏿',
]

FLAG_EMOJIS = [
    'ོ༘₊⁺🇮🇳 ₊⁺⋆.˚ 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐊ᴇ 𝐒ᴀᴛʜ ꜱᴜᴘʀᴇᴍᴇ 𝐁ᴀᴀᴘ 𝐀ᴜʀ 𝐈ɴᴅɪᴀ 𝐖ᴀʟᴇ 𝐁ʜɪ 𝐂ʜɪʟʟ 𝐊ᴀʀ 𝐑ʜᴇ ོ༘₊⁺🇮🇳 ₊⁺⋆.˚',
    'ོ༘₊⁺🇯🇵 ₊⁺⋆.˚ 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐊ᴇ 𝐒ᴀᴛʜ ꜱᴜᴘʀᴇᴍᴇ 𝐁ᴀᴀᴘ 𝐀ᴜʀ 𝐉ᴀᴘᴀɴ 𝐖ᴀʟᴇ 𝐁ʜɪ 𝐂ʜɪʟʟ 𝐊ᴀʀ 𝐑ʜᴇ ོ༘₊⁺🇯🇵 ₊⁺⋆.˚',
    'ོ༘₊⁺🇺🇸 ₊⁺⋆.˚ 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐊ᴇ 𝐒ᴀᴛʜ ꜱᴜᴘʀᴇᴍᴇ 𝐁ᴀᴀᴘ 𝐀ᴜʀ 𝐔𝐒𝐀 𝐖ᴀʟᴇ 𝐁ʜɪ 𝐂ʜɪʟʟ 𝐊ᴀʀ 𝐑ʜᴇ ོ༘₊⁺🇺🇸 ₊⁺⋆.˚',
    'ོ༘₊⁺🇬🇧 ₊⁺⋆.˚ 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐊ᴇ 𝐒ᴀᴛʜ ꜱᴜᴘʀᴇᴍᴇ 𝐁ᴀᴀᴘ 𝐀ᴜʀ 𝐔𝐊 𝐖ᴀʟᴇ 𝐁ʜɪ 𝐂ʜɪʟʟ 𝐊ᴀʀ 𝐑ʜᴇ ོ༘₊⁺🇬🇧 ₊⁺⋆.˚',
    'ོ༘₊⁺🇰🇷 ₊⁺⋆.˚ 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐊ᴇ 𝐒ᴀᴛʜ ꜱᴜᴘʀᴇᴍᴇ 𝐁ᴀᴀᴘ 𝐀ᴜʀ 𝐊ᴏʀᴇᴀ 𝐖ᴀʟᴇ 𝐁ʜɪ 𝐂ʜɪʟʟ 𝐊ᴀʀ 𝐑ʜᴇ ོ༘₊⁺🇰🇷 ₊⁺⋆.˚',
    'ོ༘₊⁺🇩🇪 ₊⁺⋆.˚ 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐊ᴇ 𝐒ᴀᴛʜ ꜱᴜᴘʀᴇᴍᴇ 𝐁ᴀᴀᴘ 𝐀ᴜʀ 𝐆ᴇʀᴍᴀɴʏ 𝐖ᴀʟᴇ 𝐁ʜɪ 𝐂ʜɪʟʟ 𝐊ᴀʀ 𝐑ʜᴇ ོ༘₊⁺🇩🇪 ₊⁺⋆.˚',
    'ོ༘₊⁺🇫🇷 ₊⁺⋆.˚ 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐊ᴇ 𝐒ᴀᴛʜ ꜱᴜᴘʀᴇᴍᴇ 𝐁ᴀᴀᴘ 𝐀ᴜʀ 𝐅ʀᴀɴᴄᴇ 𝐖ᴀʟᴇ 𝐁ʜɪ 𝐂ʜɪʟʟ 𝐊ᴀʀ 𝐑ʜᴇ ོ༘₊⁺🇫🇷 ₊⁺⋆.˚',
    'ོ༘₊⁺🇮🇹 ₊⁺⋆.˚ 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐊ᴇ 𝐒ᴀᴛʜ ꜱᴜᴘʀᴇᴍᴇ 𝐁ᴀᴀᴘ 𝐀ᴜʀ 𝐈ᴛᴀʟʏ 𝐖ᴀʟᴇ 𝐁ʜɪ 𝐂ʜɪʟʟ 𝐊ᴀʀ 𝐑ʜᴇ ོ༘₊⁺🇮🇹 ₊⁺⋆.˚',
    'ོ༘₊⁺🇧🇷 ₊⁺⋆.˚ 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐊ᴇ 𝐒ᴀᴛʜ ꜱᴜᴘʀᴇᴍᴇ 𝐁ᴀᴀᴘ 𝐀ᴜʀ 𝐁ʀᴀᴢɪʟ 𝐖ᴀʟᴇ 𝐁ʜɪ 𝐂ʜɪʟʟ 𝐊ᴀʀ 𝐑ʜᴇ ོ༘₊⁺🇧🇷 ₊⁺⋆.˚',
    'ོ༘₊⁺🇨🇦 ₊⁺⋆.˚ 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐊ᴇ 𝐒ᴀᴛʜ ꜱᴜᴘʀᴇᴍᴇ 𝐁ᴀᴀᴘ 𝐀ᴜʀ 𝐂ᴀɴᴀᴅᴀ 𝐖ᴀʟᴇ 𝐁ʜɪ 𝐂ʜɪʟʟ 𝐊ᴀʀ 𝐑ʜᴇ ོ༘₊⁺🇨🇦 ₊⁺⋆.˚',
]

WIZARD_EMOJIS = [
    '𝗧ᴍᴋ𝗕 pe ✧ꜱᴜᴘʀᴇᴍᴇ -/- 🧙','𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- 🧙‍♂️',
    '𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- 🧙‍♀️','𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- 🪄',
    '𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- ✨','𝗧ᴍᴋ𝗕 pe ♡Ᏼᴀᴛᴍᴀɴيخاف -/- 🌟',
    '𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- ⭐','𝗧ᴍᴋ𝗕 pe ♡Ᏼᴀᴛᴍᴀɴيخاف -/- 💫',
    '𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- ☄️','𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- 🌠',
    '𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- 🔮','𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- 🎩',
    '𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- 🐉','𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- 🦄',
    '𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- 🧚','𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- 🗡️',
    '𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- ⚔️','𝗧ᴍᴋ𝗕 pe ♡Ᏼᴀᴛᴍᴀɴيخاف -/- ☣️',
    '𝗧ᴍᴋ𝗕 pe  ✧ꜱᴜᴘʀᴇᴍᴇ -/- 🎭',
]

FIRE_EMOJIS = [
    "𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 🔥","𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 🌋",
    "𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 💥","𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 ⚡",
    "𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 ☄️","𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 🌪️",
    "𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 🌶️","𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 ♨️",
    "𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 🧨","𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 💣",
    "𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 ⚔️","𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 💢",
    "𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 ❤️‍🔥","𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 🥵",
    "𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 😤","𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 👹",
    "𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 👺","𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 🔴",
    "𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 🟠","𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 🐉",
    "𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 🦁","𝐀ʙᴇ𝐘 𝐔ɴᴋɴᴏᴡ𝐍 𝐓ᴀᴛᴛ𝐄 🐯",
]

WATER_EMOJIS = [
    "⊱🩵⊰{text}⊱🩵⊰💧","⊱🩵⊰{text}⊱🩵⊰🌊","⊱🩵⊰{text}⊱🩵⊰🐋",
    "⊱🩵⊰{text}⊱🩵⊰🐬","⊱🩵⊰{text}⊱🩵⊰🐟","⊱🩵⊰{text}⊱🩵⊰🦈",
    "⊱🩵⊰{text}⊱🩵⊰🐙","⊱🩵⊰{text}⊱🩵⊰🦑","⊱🩵⊰{text}⊱🩵⊰🌸",
    "⊱🩵⊰{text}⊱🩵⊰💦","⊱🩵⊰{text}⊱🩵⊰🫧","⊱🩵⊰{text}⊱🩵⊰🌀",
    "⊱🩵⊰{text}⊱🩵⊰⛵","⊱🩵⊰{text}⊱🩵⊰🏊","⊱🩵⊰{text}⊱🩵⊰🌧️",
    "⊱🩵⊰{text}⊱🩵⊰☔","⊱🩵⊰{text}⊱🩵⊰🏄","⊱🩵⊰{text}⊱🩵⊰🧊",
    "⊱🩵⊰{text}⊱🩵⊰❄️","⊱🩵⊰{text}⊱🩵⊰🌈","⊱🩵⊰{text}⊱🩵⊰💙",
    "⊱🩵⊰{text}⊱🩵⊰🩵","⊱🩵⊰{text}⊱🩵⊰🔵","⊱🩵⊰{text}⊱🩵⊰🟦",
]

LAVA_EMOJIS = [
    "𝑇𝑀𝐾𝐵℘✩  🌋","𝑇𝑀𝐾𝐵℘✩  🔥","𝑇𝑀𝐾𝐵℘✩  💥",
    "𝑇𝑀𝐾𝐵℘✩  🫨","𝑇𝑀𝐾𝐵℘✩  ♨️","𝑇𝑀𝐾𝐵℘✩  🟠",
    "𝑇𝑀𝐾𝐵℘✩  🟡","𝑇𝑀𝐾𝐵℘✩  🔴","𝑇𝑀𝐾𝐵℘✩  ☄️",
    "𝑇𝑀𝐾𝐵℘✩  ⚡","𝑇𝑀𝐾𝐵℘✩  💢","𝑇𝑀𝐾𝐵℘✩  😤",
    "𝑇𝑀𝐾𝐵℘✩  🥵","𝑇𝑀𝐾𝐵℘✩  🧱","𝑇𝑀𝐾𝐵℘✩  🪨",
    "𝑇𝑀𝐾𝐵℘✩  💣","𝑇𝑀𝐾𝐵℘✩  🐉","𝑇𝑀𝐾𝐵℘✩  🔶",
    "𝑇𝑀𝐾𝐵℘✩  🔸","𝑇𝑀𝐾𝐵℘✩  🔺","𝑇𝑀𝐾𝐵℘✩  🌊",
]

HELL_EMOJIS = [
    "👹᭄","👺᭄","😈᭄","💀᭄","☠️᭄","☣️᭄","🩸᭄","🕷᭄","🕸᭄",
    "🦇᭄","🌑᭄","🖤᭄","🔮᭄","⚰️᭄","🪦᭄","🗡️᭄","⚔️᭄","🔥᭄",
    "💥᭄","😱᭄","🤬᭄","👻᭄","🎃᭄","🦴᭄","💣᭄","🧿᭄","🌚᭄",
    "🕯️᭄","🪄᭄","🧙","🧛᭄","🧟᭄","🐺᭄","🦉᭄","🐍᭄","🦂᭄",
]

SYMBOL_LIST = [
    "×","~","•","★","☆","▲","▼","◆","◇","■","□","●","○",
    "✦","✧","⚡","✨","💫","☣️","⚜️","❋","✿","❀","✾","❃",
    "❂","❁","꧁","꧂","༺","༻","《","》","【","】","∞","Ω","Δ",
    "Σ","Ψ","Φ","Λ","Θ","©","®","™","⁂","※","✰","✯","✮",
]

FLAG_NC_EMOJIS = [
    "⊱🩵⊰{text}⊱🩵⊰","⊱🌹⊰{text}⊱🌹⊰","𐙚🧸ྀི{text}𐙚🧸ྀི",
    "⊱⚡⊰{text}⊱⚡⊰","⊱🪷⊰{text}⊱🪷⊰","𓍢ִ໋🌷͙֒{text}𓍢ִ໋🌷͙֒",
    "💋ྀིྀི{text}💋ྀིྀི","˚.🎀༘⋆{text}˚.🎀༘⋆","⊱🕶️⊰{text}⊱🕶️⊰",
    "⊱💮⊰{text}⊱💮⊰","⊱🌸⊰{text}⊱🌸⊰",
]

GAME_EMOJIS = ["🎮","🕹","🎰","🎲","♟","🎯","🎳","👾","🧩","🎬","🎨","🎭","🎪","🎤","🎧","🎼","🎹","🥁","🎸","🎻","🪕"]
TOOL_EMOJIS = ["🔧","🔨","⚒","🛠","⛏","🔩","⚙️","🧱","⛓","🧰","🗜","⚖️","🦯","🔗","🧲","🔫","💣","🧨","🪓","🔪","🗡","⚔️","🛡"]
LOOP_EMOJIS = ["🔄","🔁","🔂","🔃","♻️","➰","➿","♾","🌀"]
CAR_EMOJIS = ["🚗","🚕","🚙","🚌","🚎","🏎","🚓","🚑","🚒","🚐","🛻","🚚","🚛","🚜","🦽","🛴","🚲","🛵","🏍","🛺","🚁","✈️","🛩","🚀","🛸"]
HAND_EMOJIS = ["👋","🤚","🖐","✋","🖖","👌","🤌","🤏","✌️","🤞","🤟","🤘","🤙","👈","👉","👆","🖕","👇","☝️","👍","👎","✊","👊","🤛","🤜","👏","🙌","👐","🤲","🤝","🙏"]
HUMAN_EMOJIS = ["👶","👧","🧒","👦","👩","🧑","👨","👩\u200d🦱","🧑\u200d🦱","👨\u200d🦱","👩\u200d🦰","🧑\u200d🦰","👨\u200d🦰","👱\u200d♀️","👱","👱\u200d♂️","👩\u200d🦳","🧑\u200d🦳","👨\u200d🦳","👩\u200d🦲","🧑\u200d🦲","👨\u200d🦲","🧔\u200d♀️","🧔","🧔\u200d♂️","👵","🧓","👴","👲","👳\u200d♀️","👳","👳\u200d♂️","🧕","👮\u200d♀️","👮","👮\u200d♂️"]
MOON_EMOJIS = ["🌕","🌖","🌗","🌘","🌑","🌒","🌓","🌔","🌙","🌛","🌜","🌚","🌝","🌞","⭐","🌟","✨","⚡","☄️","💫","🔥"]
KISS_EMOJIS = ["😗","😙","😚","😘","🥰","😍","🤩","💋","💌","💘","💝","💖","💗","💓","💞","💕","❣️","💔","❤️\u200d🔥","❤️\u200d🩹","❤️","🧡","💛","💚","💙","💜","🤎","🖤","🤍"]
FOOD_EMOJIS = ["🍏","🍎","🍐","🍊","🍋","🍌","🍉","🍇","🍓","🫐","🍒","🍑","🥭","🍍","🥥","🥝","🍅","🍆","🥑","🥦","🥬","🥒","🌶","🧄","🧅","🥔","🥐","🥯","🍞","🧀","🥚","🍳","🧈","🥞","🧇","🥓","🥩","🍗","🍖","🌭","🍔","🍟","🍕","🥪","🌮","🌯","🥗","🥘","🍝","🍜","🍲","🍛","🍣","🍱","🥟","🍤","🍙","🍚","🍦","🥧","🧁","🍰","🎂","🍩","🍪"]
ANIMAL_EMOJIS = ["🐶","🐱","🐭","🐹","🐰","🦊","🐻","🐼","🐨","🐯","🦁","🐮","🐷","🐸","🐵","🐔","🐧","🐦","🐤","🐣","🦅","🦆","🦢","🦉","🐴","🦄","🐝","🦋","🐌","🐞","🐜","🕷","🕸","🦂","🐢","🐍","🦎","🐙","🦑","🐡","🐠","🐟","🐬","🐳","🐋","🦈","🐊","🐅","🐆","🦓","🦍"]

# ===========================================================
# AI / VOICE HELPERS
# ===========================================================
_AIOHTTP_SESSION = None

async def _get_session():
    global _AIOHTTP_SESSION
    if not AIOHTTP_AVAILABLE:
        return None
    if _AIOHTTP_SESSION is None or _AIOHTTP_SESSION.closed:
        connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
        _AIOHTTP_SESSION = aiohttp.ClientSession(connector=connector)
    return _AIOHTTP_SESSION

async def gemini_ask(history):
    if not GEMINI_API_KEYS:
        return '❌ ᴋᴏɪ ɢᴇᴍɪɴɪ ᴀᴘɪ ᴋᴇʏ ɴᴀʜɪ ʜᴀɪ! ɢᴇᴍɪɴɪ_ᴀᴘɪ_ᴋᴇʏ ᴇɴᴠ ꜱᴇᴛ ᴋᴀʀᴏ.'
    payload = {
        'system_instruction': {'parts': [{'text': AI_SYSTEM_PROMPT}]},
        'contents': history,
        'generationConfig': {'temperature': 0.85, 'maxOutputTokens': 1024},
    }
    last_err = ''
    # Try aiohttp first (async, fast)
    if AIOHTTP_AVAILABLE:
        for _ in range(len(GEMINI_API_KEYS)):
            key = _next_gemini_key()
            try:
                session = await _get_session()
                async with session.post(
                    GEMINI_URL, params={'key': key}, json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 429:
                        last_err = 'quota exceeded'
                        await asyncio.sleep(0.05)
                        continue
                    if resp.status != 200:
                        last_err = f"HTTP {resp.status}: {await resp.text()}"
                        continue
                    data = await resp.json()
                    return data['candidates'][0]['content']['parts'][0]['text'].strip()
            except asyncio.TimeoutError:
                last_err = 'timeout'
                continue
            except Exception as e:
                last_err = str(e)
                continue
    else:
        # Fallback: run requests sync call in a thread (no thread pool overhead)
        def _sync_ask(key):
            try:
                r = requests.post(GEMINI_URL, params={'key': key}, json=payload, timeout=30)
                if r.status_code == 200:
                    return r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                return None
            except Exception:
                return None
        for _ in range(len(GEMINI_API_KEYS)):
            key = _next_gemini_key()
            result = await asyncio.to_thread(_sync_ask, key)
            if result:
                return result
        last_err = 'requests fallback failed'
    return f"❌ AI abhi available nahi hai ({last_err}) — thodi der baad try karo."

async def generate_voice_mp3(text, char_num):
    """Generate voice as MP3 — no ffmpeg needed."""
    if not EDGE_TTS_AVAILABLE:
        return None
    char = ANIME_CHARACTERS.get(char_num)
    if not char:
        return None
    _, _, voice, rate, pitch, _ = char
    tmp_mp3 = tempfile.mktemp(suffix='.mp3')
    try:
        communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
        await communicate.save(tmp_mp3)
        with open(tmp_mp3, 'rb') as f:
            return f.read()
    except Exception:
        return None
    finally:
        try:
            os.remove(tmp_mp3)
        except Exception:
            pass

# ===========================================================
# NC LOOP FUNCTIONS — Language NC
# ===========================================================
# ===========================================================
# MASTER NC LOOP — handles 1-hour cycle + 2s refresh
# ===========================================================
# ── NC title-builders: run ONCE per loop start, not every iteration ──
def _build_nc_titles(patterns, text):
    """Pre-format all NC titles with text. Returns ready list[str]."""
    out = []
    for p in patterns:
        if '{text}' in p:
            out.append(p.format(text=text))
        elif '{target}' in p:
            out.append(p.format(target=text))
        else:
            out.append(f"{p} {text} {p}")
    return out

def _build_pattern_emoji_titles(pattern, text, emoji_list):
    """Pre-build pattern+emoji combos (nc1-nc5, knc, anc, fnc style)."""
    return [pattern.format(text=text, emoji=e) for e in emoji_list]

async def _nc_master_loop(bot, chat_id, titles):
    # Ultra-fast NC: pre-built titles list, adaptive micro-delay on FloodWait
    global NC_SEMAPHORE
    n = len(titles)
    i = 0
    _flood_hits = 0
    _micro = 0.02
    while True:
        try:
            if NC_SEMAPHORE:
                async with NC_SEMAPHORE:
                    await bot.set_chat_title(chat_id, titles[i % n])
            else:
                await bot.set_chat_title(chat_id, titles[i % n])
            i += 1
            _flood_hits = 0
            _micro = 0.02
            await asyncio.sleep(_micro)
        except asyncio.CancelledError:
            break
        except RetryAfter as e:
            _flood_hits += 1
            _micro = min(0.02 + _flood_hits * 0.05, 0.5)
            await asyncio.sleep(float(e.retry_after) + 0.01)
        except (TimedOut, NetworkError):
            await asyncio.sleep(0.05)
        except Exception:
            i += 1
            await asyncio.sleep(0.05)

# ===========================================================
# GENERIC NC LOOP HELPERS
# ===========================================================
async def _generic_nc_loop(bot, chat_id, text, items):
    await _nc_master_loop(bot, chat_id, _build_nc_titles(items, text))

# ===========================================================
# SPAM / SLIDE MASTER LOOP — same 2-hour cycle as NC
# ===========================================================
async def _spam_master_loop(bot, chat_id, msg_src, reply_to=None):
    # Ultra-fast spam/slide: adaptive micro-delay, pre-bound send
    global NC_SEMAPHORE
    sem = NC_SEMAPHORE
    _send = bot.send_message
    # Pre-resolve msg getter to avoid isinstance check per iteration
    if isinstance(msg_src, str):
        _get = None          # constant text
        _text = msg_src
    elif isinstance(msg_src, list):
        _get = None
        _cycle = msg_src
        _n = len(_cycle)
        _text = None
    else:
        _get = msg_src       # callable
        _text = None
        _cycle = None
    i = 0
    while True:
        try:
            if _get is not None:
                txt = _get(i)
            elif _text is not None:
                txt = _text
            else:
                txt = _cycle[i % _n]
            if sem:
                async with sem:
                    if reply_to:
                        await _send(chat_id, txt, reply_to_message_id=reply_to)
                    else:
                        await _send(chat_id, txt)
            else:
                if reply_to:
                    await _send(chat_id, txt, reply_to_message_id=reply_to)
                else:
                    await _send(chat_id, txt)
            i += 1
            await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            break
        except RetryAfter as e:
            await asyncio.sleep(float(e.retry_after) + 0.01)
        except (TimedOut, NetworkError):
            await asyncio.sleep(0.05)
        except Exception:
            i += 1
            await asyncio.sleep(0.05)

# --- Individual NC loops (all powered by _nc_master_loop) ---

async def hindinc_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_nc_titles(HINDINC_PATTERNS, text))

async def urdunc_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_nc_titles(URDU_PATTERNS, text))

async def bengalnc_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_nc_titles(BENGALI_PATTERNS, text))

async def biharinc_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_nc_titles(BIHARI_PATTERNS, text))

async def engnc_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_nc_titles(ENGLISH_PATTERNS, text))

async def emonc_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_pattern_emoji_titles(EMOJI_NC_PATTERN, text, EMOJI_NC_EMOJIS))

async def nc1_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_pattern_emoji_titles(NC1_PATTERN, text, NC1_EMOJIS))

async def nc2_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_pattern_emoji_titles(NC2_PATTERN, text, NC2_EMOJIS))

async def nc3_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_pattern_emoji_titles(NC3_PATTERN, text, NC3_EMOJIS))

async def nc4_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_pattern_emoji_titles(NC4_PATTERN, text, NC4_EMOJIS))

async def nc5_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_pattern_emoji_titles(NC5_PATTERN, text, NC5_EMOJIS))

async def knc_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_pattern_emoji_titles(KNC_PATTERN, text, KNC_EMOJIS))

async def anc_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_pattern_emoji_titles(ANC_PATTERN, text, ANC_EMOJIS))

async def fnc_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_pattern_emoji_titles(FNC_PATTERN, text, FNC_EMOJIS))

async def chinesenc_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_nc_titles(CHINESE_PATTERNS, text))

async def goodncraid_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, [f"{text}{t}" for t in GOODNCRAID_TEXTS])

async def ncheart_loop(bot, chat_id, text):
    await _generic_nc_loop(bot, chat_id, text, NC_heart_MESSAGES)

async def ncflag_loop(bot, chat_id, text):
    await _generic_nc_loop(bot, chat_id, text, NC_FLAG_MESSAGES)

async def dotzkeng_loop(bot, chat_id, text):
    await _generic_nc_loop(bot, chat_id, text, DOTZKENG_MESSAGES)

async def nccurly_loop(bot, chat_id, text):
    await _generic_nc_loop(bot, chat_id, text, NC_WEB_MESSAGES)

async def timenc_loop(bot, chat_id, text):
    await _generic_nc_loop(bot, chat_id, text, TIME_NC_MESSAGES)

async def flowernc_loop(bot, chat_id, text):
    await _generic_nc_loop(bot, chat_id, text, FLOWER_NC_MESSAGES)

async def namenc_loop(bot, chat_id, text):
    await _generic_nc_loop(bot, chat_id, text, NAME_CHANGE_MESSAGES)

async def wizard_loop(bot, chat_id, text):
    # wizard uses random pairs — can't pre-build, keep inline but lean
    global NC_SEMAPHORE
    _wiz = WIZARD_EMOJIS
    i = 0
    while True:
        try:
            e1, e2 = random.sample(_wiz, 2)
            if NC_SEMAPHORE:
                async with NC_SEMAPHORE:
                    await bot.set_chat_title(chat_id, f"{e1} {text} {e2}")
            else:
                await bot.set_chat_title(chat_id, f"{e1} {text} {e2}")
            i += 1
            await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            break
        except RetryAfter as e:
            await asyncio.sleep(float(e.retry_after) + 0.01)
        except (TimedOut, NetworkError):
            await asyncio.sleep(0.05)
        except Exception:
            i += 1
            await asyncio.sleep(0.05)

async def whitenc_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, [f"{e} {text} {e}" for e in WHITE_EMOJIS])

async def blacknc_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, [f"{e} {text}" for e in BLACK_EMOJIS])

async def flagemo_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, [f"{e} {text}" for e in FLAG_EMOJIS])

async def _emoji_nc_loop(bot, chat_id, text, emoji_list):
    # Pre-build: emoji items are plain strings, always "e text e" pattern
    await _nc_master_loop(bot, chat_id, [f"{e} {text} {e}" for e in emoji_list])

async def firenc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, FIRE_EMOJIS)

async def hotnc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, FIRE_EMOJIS)

async def waternc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, WATER_EMOJIS)

async def lavanc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, LAVA_EMOJIS)

async def hellnc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, HELL_EMOJIS)

async def symbolnc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, SYMBOL_LIST)

async def flagncnc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, FLAG_NC_EMOJIS)

async def gamenc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, GAME_EMOJIS)

async def toolnc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, TOOL_EMOJIS)

async def loopnc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, LOOP_EMOJIS)

async def carnc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, CAR_EMOJIS)

async def handnc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, HAND_EMOJIS)

async def humannc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, HUMAN_EMOJIS)

async def moonnc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, MOON_EMOJIS)

async def kissnc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, KISS_EMOJIS)

async def foodnc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, FOOD_EMOJIS)

async def animalnc_loop(bot, chat_id, text):
    await _emoji_nc_loop(bot, chat_id, text, ANIMAL_EMOJIS)

# ===========================================================
# SLIDE LOOP FUNCTIONS — powered by _spam_master_loop
# ===========================================================
async def slide1_loop(bot, chat_id, target_msg_id):
    await _spam_master_loop(bot, chat_id, SLIDE1_MESSAGES, reply_to=target_msg_id)

async def slide2_loop(bot, chat_id, target_msg_id):
    await _spam_master_loop(bot, chat_id, SLIDE2_MESSAGES, reply_to=target_msg_id)

async def slide3_loop(bot, chat_id, target_msg_id, text):
    await _spam_master_loop(bot, chat_id, SLIDE3_PATTERN.format(text=text), reply_to=target_msg_id)

# ===========================================================
# SPAM LOOP FUNCTIONS — powered by _spam_master_loop
# ===========================================================
async def spam1_loop(bot, chat_id, text):
    await _spam_master_loop(bot, chat_id, SPAM1_PATTERN.format(text=text))

async def spam2_loop(bot, chat_id, text):
    await _spam_master_loop(bot, chat_id, SPAM2_PATTERN.format(text=text))

async def spam3_loop(bot, chat_id, text):
    await _spam_master_loop(bot, chat_id, SPAM3_PATTERN.format(text=text))

async def spam4_loop(bot, chat_id, text):
    await _spam_master_loop(bot, chat_id, SPAM4_PATTERN.format(text=text))

# ===========================================================
# RAID SPAM LOOP
# ===========================================================
async def raid_spam_loop_fn(bot, chat_id, text):
    # Pre-build all raid ᴍᴇꜱꜱᴀɢᴇꜱ once
    _msgs = _build_nc_titles(RAID_TEXTS, text)
    await _spam_master_loop(bot, chat_id, _msgs)

# ===========================================================
# PHOTO LOOP FUNCTIONS
# ===========================================================
async def photo_loop(bot, chat_id):
    while True:
        try:
            if chat_id not in chat_photos or not chat_photos[chat_id]:
                await asyncio.sleep(0.05)
                continue
            file_id = random.choice(chat_photos[chat_id])
            photo_file = await bot.get_file(file_id)
            buf = io.BytesIO()
            await photo_file.download_to_memory(buf)
            buf.seek(0)
            await bot.set_chat_photo(chat_id=chat_id, photo=buf)
            await asyncio.sleep(0.05)
        except telegram.error.RetryAfter as e:
            await asyncio.sleep(float(e.retry_after) + 0.01)
        except asyncio.CancelledError:
            break
        except Exception as e:
            await asyncio.sleep(0.05)

async def gc_photo_loop(bot, chat_id):
    # Pre-load all slot images into memory once — no per-iteration disk I/O
    def _load_slots():
        result = []
        for i in range(1, 11):
            p = f"gc_image_{i}.png"
            if os.path.exists(p):
                with open(p, 'rb') as f:
                    result.append(f.read())
        return result

    slot_cache = _load_slots()
    if not slot_cache:
        return  # nothing to loop, exit cleanly
    idx = 0
    # refresh cache counter — reload from disk every 500 iterations to pick up new saves
    refresh_every = 500
    cycle = 0
    while True:
        try:
            cycle += 1
            if cycle >= refresh_every:
                cycle = 0
                new_slots = _load_slots()
                if new_slots:
                    slot_cache = new_slots
            photo_bytes = slot_cache[idx % len(slot_cache)]
            await bot.set_chat_photo(chat_id=chat_id, photo=io.BytesIO(photo_bytes))
            idx += 1
            await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            break
        except RetryAfter as e:
            await asyncio.sleep(float(e.retry_after) + 0.01)
        except (TimedOut, NetworkError):
            await asyncio.sleep(0.05)
        except Exception:
            idx += 1
            await asyncio.sleep(0.05)

# ===========================================================
# DELETE ALL HISTORY LOOP
# ===========================================================
async def delete_history_loop(bot, chat_id, start_msg_id):
    msg_id = start_msg_id
    while True:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            msg_id -= 1
            await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            break
        except RetryAfter as e:
            await asyncio.sleep(float(e.retry_after) + 0.01)
        except (TimedOut, NetworkError):
            await asyncio.sleep(0.05)
        except Exception:
            msg_id -= 1
            await asyncio.sleep(0.05)

# ===========================================================
# HELPERS
# ===========================================================
def _cancel_tasks(task_dict, chat_id):
    if chat_id in task_dict:
        item = task_dict.pop(chat_id)
        _nc_task_meta.pop(chat_id, None)
        _spam_task_meta.pop(chat_id, None)
        _save_active_tasks()
        items = item if isinstance(item, list) else [item]
        for t in items:
            if not t.done():
                t.cancel()

def _start_multi_nc(bots_list, loop_fn, chat_id, text):
    _nc_task_meta[chat_id] = {'fn': loop_fn.__name__, 'text': text}
    _save_active_tasks()
    return [asyncio.create_task(loop_fn(b, chat_id, text)) for b in bots_list]

# ===========================================================
# NC LOOP REGISTRY — maps fn name → coroutine for auto-restore
# ===========================================================
NC_LOOP_REGISTRY.update({
    'hindinc_loop': hindinc_loop,   'urdunc_loop': urdunc_loop,
    'bengalnc_loop': bengalnc_loop, 'biharinc_loop': biharinc_loop,
    'chinesenc_loop': chinesenc_loop, 'engnc_loop': engnc_loop,
    'emonc_loop': emonc_loop,       'nc1_loop': nc1_loop,
    'nc2_loop': nc2_loop,           'nc3_loop': nc3_loop,
    'nc4_loop': nc4_loop,           'nc5_loop': nc5_loop,
    'knc_loop': knc_loop,           'anc_loop': anc_loop,
    'fnc_loop': fnc_loop,           'ncheart_loop': ncheart_loop,
    'ncflag_loop': ncflag_loop,     'dotzkeng_loop': dotzkeng_loop,
    'nccurly_loop': nccurly_loop,   'timenc_loop': timenc_loop,
    'flowernc_loop': flowernc_loop, 'namenc_loop': namenc_loop,
    'wizard_loop': wizard_loop,     'whitenc_loop': whitenc_loop,
    'blacknc_loop': blacknc_loop,   'flagemo_loop': flagemo_loop,
    'goodncraid_loop': goodncraid_loop,
})

# ===========================================================
# NC COMMAND HANDLERS — Language NC  (@nc_only enforces gcnclock)
# ===========================================================
@nc_only
async def hindinc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /hindinc <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, hindinc_loop, chat_id, text)
    await update.message.reply_text(
        "╭──🔥 𝗛𝗜𝗡𝗗𝗜 ɴᴄ ᴀᴄᴛɪᴠᴇ 🔥──╮\n"
        f"🩸 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} running\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@nc_only
async def urdunc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /urdunc <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, urdunc_loop, chat_id, text)
    await update.message.reply_text(
        "╭──☠️ 𝗨𝗥𝗗𝗨 ɴᴄ ᴀᴄᴛɪᴠᴇ ☠️──╮\n"
        f"💀 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} running\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@nc_only
async def bengalnc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /bengalnc <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, bengalnc_loop, chat_id, text)
    await update.message.reply_text(
        "╭──🌊 𝗕𝗘𝗡𝗚𝗔𝗟𝗜 ɴᴄ ᴀᴄᴛɪᴠᴇ 🌊──╮\n"
        f"🎌 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} running\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@nc_only
async def biharinc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /biharinc <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, biharinc_loop, chat_id, text)
    await update.message.reply_text(
        "╭──🔥 𝗕𝗜𝗛𝗔𝗥𝗜 ɴᴄ ᴀᴄᴛɪᴠᴇ 🔥──╮\n"
        f"⚡ ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} running\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@nc_only
async def chinesenc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /chinesenc <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, chinesenc_loop, chat_id, text)
    await update.message.reply_text(
        "╭──🇨🇳 𝗖𝗛𝗜𝗡𝗘𝗦𝗘 ɴᴄ ᴀᴄᴛɪᴠᴇ 🇨🇳──╮\n"
        f"🌏 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} running\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@nc_only
async def goodncraid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /goodncraid <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, goodncraid_loop, chat_id, text)
    await update.message.reply_text(
        "╭──⚔️ ɢᴏᴏᴅ ɴᴄ ʀᴀɪᴅ ᴀᴄᴛɪᴠᴇ ⚔️──╮\n"
        f"🎯 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ʀᴜɴɴɪɴɢ\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@nc_only
async def engnc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /engnc <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, engnc_loop, chat_id, text)
    await update.message.reply_text(
        "╭──🌊 𝗘𝗡𝗚𝗟𝗜𝗦𝗛 ɴᴄ ᴀᴄᴛɪᴠᴇ 🌊──╮\n"
        f"📝 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ʀᴜɴɴɪɴɢ\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@nc_only
async def emonc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /emonc <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, emonc_loop, chat_id, text)
    await update.message.reply_text(
        "╭──🎭 𝗘𝗠𝗢𝗝𝗜 ɴ𝗰 ᴀᴄᴛɪᴠᴇ 🎭──╮\n"
        f"✨ ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ʀᴜɴɴɪɴɢ\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@nc_only
async def nc1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /nc1 <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, nc1_loop, chat_id, text)
    await update.message.reply_text(
        "╭──👊 ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ɴᴄ1 ᴀᴄᴛɪᴠᴇ 👊──╮\n"
        f"📝 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ʀᴜɴɴɪɴɢ\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@nc_only
async def nc2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /nc2 <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, nc2_loop, chat_id, text)
    await update.message.reply_text(
        "╭──🗡 ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ɴᴄ2 ᴀᴄᴛɪᴠᴇ 🗡──╮\n"
        f"📝 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ʀᴜɴɴɪɴɢ\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@nc_only
async def nc3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /nc3 <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, nc3_loop, chat_id, text)
    await update.message.reply_text(
        "╭──⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ɴᴄ3 ᴀᴄᴛɪᴠᴇ ⚡──╮\n"
        f"📝 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ʀᴜɴɴɪɴɢ\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@nc_only
async def nc4(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /nc4 <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, nc4_loop, chat_id, text)
    await update.message.reply_text(
        "╭──💀 ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ɴᴄ4 ᴀᴄᴛɪᴠᴇ 💀──╮\n"
        f"📝 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ʀᴜɴɴɪɴɢ\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@nc_only
async def nc5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /nc5 <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, nc5_loop, chat_id, text)
    await update.message.reply_text(
        "╭──🌪 ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ɴᴄ5 ᴀᴄᴛɪᴠᴇ 🌪──╮\n"
        f"📝 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ʀᴜɴɴɪɴɢ\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@nc_only
async def knc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /knc <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, knc_loop, chat_id, text)
    await update.message.reply_text(
        "╭──☣️ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ᴋɴᴄ ᴀᴄᴛɪᴠᴇ ☣️──╮\n"
        f"📝 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ʀᴜɴɴɪɴɢ\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@nc_only
async def anc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /anc <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, anc_loop, chat_id, text)
    await update.message.reply_text(
        "╭──🦅 ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ᴀɴᴄ ᴀᴄᴛɪᴠᴇ 🦅──╮\n"
        f"📝 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ʀᴜɴɴɪɴɢ\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@nc_only
async def fnc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /fnc <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, fnc_loop, chat_id, text)
    await update.message.reply_text(
        "╭──🐍 ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ғɴᴄ ᴀᴄᴛɪᴠᴇ 🐍──╮\n"
        f"📝 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ʀᴜɴɴɪɴɢ\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

# ===========================================================
# NEW NC COMMAND HANDLERS — factory pattern
# ===========================================================
def _nc_cmd_handler(loop_fn, label):
    @nc_only
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            return await update.message.reply_text(f"❌ ᴜꜱᴀɢᴇ: /{label} <ᴛᴇxᴛ>")
        text = " ".join(context.args)
        chat_id = update.message.chat_id
        _cancel_tasks(nc_tasks, chat_id)
        nc_tasks[chat_id] = _start_multi_nc(bots, loop_fn, chat_id, text)
        await update.message.reply_text(
            f"╭──🔥 ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ {label.upper()} ᴀᴄᴛɪᴠᴇ 🔥──╮\n"
            f"📝 ᴛᴇxᴛ  : {text}\n"
            f"🤖 ʙᴏᴛꜱ  : {len(bots)} ʀᴜɴɴɪɴɢ\n"
            "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
            "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
        )
    handler.__name__ = label
    return handler

ncheart = _nc_cmd_handler(ncheart_loop, 'ncheart')
ncflag = _nc_cmd_handler(ncflag_loop, 'ncflag')
dotzkeng = _nc_cmd_handler(dotzkeng_loop, 'dotzkeng')
nccurly = _nc_cmd_handler(nccurly_loop, 'nccurly')
timenc = _nc_cmd_handler(timenc_loop, 'timenc')
flowernc = _nc_cmd_handler(flowernc_loop, 'flowernc')
namenc = _nc_cmd_handler(namenc_loop, 'namenc')
wizard = _nc_cmd_handler(wizard_loop, 'wizard')
whitenc = _nc_cmd_handler(whitenc_loop, 'whitenc')
blacknc_cmd = _nc_cmd_handler(blacknc_loop, 'blacknc')
flagemo = _nc_cmd_handler(flagemo_loop, 'flagemo')
firenc = _nc_cmd_handler(firenc_loop, 'firenc')
hotnc = _nc_cmd_handler(hotnc_loop, 'hotnc')
waternc = _nc_cmd_handler(waternc_loop, 'waternc')
lavanc = _nc_cmd_handler(lavanc_loop, 'lavanc')
hellnc = _nc_cmd_handler(hellnc_loop, 'hellnc')
symbolnc = _nc_cmd_handler(symbolnc_loop, 'symbolnc')
flagncnc = _nc_cmd_handler(flagncnc_loop, 'flagncnc')
gamenc = _nc_cmd_handler(gamenc_loop, 'gamenc')
toolnc = _nc_cmd_handler(toolnc_loop, 'toolnc')
loopnc = _nc_cmd_handler(loopnc_loop, 'loopnc')
carnc = _nc_cmd_handler(carnc_loop, 'carnc')
handnc = _nc_cmd_handler(handnc_loop, 'handnc')
humannc = _nc_cmd_handler(humannc_loop, 'humannc')
moonnc = _nc_cmd_handler(moonnc_loop, 'moonnc')
kissnc = _nc_cmd_handler(kissnc_loop, 'kissnc')
foodnc = _nc_cmd_handler(foodnc_loop, 'foodnc')
animalnc = _nc_cmd_handler(animalnc_loop, 'animalnc')

# ===========================================================
# SLIDE COMMAND HANDLERS
# ===========================================================
@sudo_only
async def slide1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        return await update.message.reply_text("❌ Reply to a message to start slide1!")
    chat_id = update.message.chat_id
    target_msg_id = update.message.reply_to_message.message_id
    _cancel_tasks(slider_tasks, chat_id)
    slider_tasks[chat_id] = [asyncio.create_task(slide1_loop(b, chat_id, target_msg_id)) for b in bots]
    await update.message.reply_text(
        "╭──🌀 ꜱʟɪᴅᴇ1 ᴀᴄᴛɪᴠᴇ 🌀──╮\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ꜱʟɪᴅɪɴɢ\n"
        "🛑 /stopslide ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@sudo_only
async def slide2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        return await update.message.reply_text("❌ Reply to a message to start slide2!")
    chat_id = update.message.chat_id
    target_msg_id = update.message.reply_to_message.message_id
    _cancel_tasks(slider_tasks, chat_id)
    slider_tasks[chat_id] = [asyncio.create_task(slide2_loop(b, chat_id, target_msg_id)) for b in bots]
    await update.message.reply_text(
        "╭──💫 ꜱʟɪᴅᴇ2 ᴀᴄᴛɪᴠᴇ 💫──╮\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ꜱʟɪᴅɪɴɢ\n"
        "🛑 /stopslide ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@sudo_only
async def slide3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /slide3 <ᴛᴇxᴛ> (reply to a message)")
    if not update.message.reply_to_message:
        return await update.message.reply_text("❌ Reply to a message to start slide3!")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    target_msg_id = update.message.reply_to_message.message_id
    _cancel_tasks(slider_tasks, chat_id)
    slider_tasks[chat_id] = [asyncio.create_task(slide3_loop(b, chat_id, target_msg_id, text)) for b in bots]
    await update.message.reply_text(
        "╭──🎯 ꜱʟɪᴅᴇ3 ᴀᴄᴛɪᴠᴇ 🎯──╮\n"
        f"📝 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ꜱʟɪᴅɪɴɢ\n"
        "🛑 /stopslide ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

# ===========================================================
# SPAM COMMAND HANDLERS
# ===========================================================
@sudo_only
async def spam1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /spam1 <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(spam_tasks, chat_id)
    spam_tasks[chat_id] = [asyncio.create_task(spam1_loop(b, chat_id, text)) for b in bots]
    await update.message.reply_text(
        f"╭──💥 ꜱᴘᴀᴍ1 ᴀᴄᴛɪᴠᴇ 💥──╮\n"
        f"📝 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ꜰɪʀᴇ!\n"
        "🛑 /stopspam ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@sudo_only
async def spam2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /spam2 <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(spam_tasks, chat_id)
    spam_tasks[chat_id] = [asyncio.create_task(spam2_loop(b, chat_id, text)) for b in bots]
    await update.message.reply_text(
        f"╭──🔥 ꜱᴘᴀᴍ2 ᴀᴄᴛɪᴠᴇ 🔥──╮\n"
        f"📝 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ꜰɪʀᴇ!\n"
        "🛑 /stopspam ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@sudo_only
async def spam3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /spam3 <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(spam_tasks, chat_id)
    spam_tasks[chat_id] = [asyncio.create_task(spam3_loop(b, chat_id, text)) for b in bots]
    await update.message.reply_text(
        f"╭──⚡ ꜱᴘᴀᴍ3 ᴀᴄᴛɪᴠᴇ ⚡──╮\n"
        f"📝 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ꜰɪʀᴇ!\n"
        "🛑 /stopspam ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@sudo_only
async def spam4(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /spam4 <ᴛᴇxᴛ>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(spam_tasks, chat_id)
    spam_tasks[chat_id] = [asyncio.create_task(spam4_loop(b, chat_id, text)) for b in bots]
    await update.message.reply_text(
        f"╭──🌪 ꜱᴘᴀᴍ4 ᴀᴄᴛɪᴠᴇ 🌪──╮\n"
        f"📝 ᴛᴇxᴛ  : {text}\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ꜰɪʀᴇ!\n"
        "🛑 /stopspam ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

# ===========================================================
# CUSTOM SPAM — /myspam
# ===========================================================
async def myspam_loop(bot, chat_id, text):
    await _spam_master_loop(bot, chat_id, text)

@sudo_only
async def myspam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text(
            "╔══════════════════╗\n"
            "║  💬 ᴍʏꜱᴘᴀᴍ 💬  ║\n"
            "╚══════════════════╝\n"
            "❌ ɢɪᴠᴇ ᴍᴇ ᴛᴇxᴛ !\n"
            "📌 ᴜꜱᴀɢᴇ: /myspam <ʏᴏᴜʀ ᴛᴇxᴛ>\n"
            "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
        )
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(spam_tasks, chat_id)
    spam_tasks[chat_id] = [asyncio.create_task(myspam_loop(b, chat_id, text)) for b in bots]
    await update.message.reply_text(
        "╔══════════════════╗\n"
        "║  💬 ᴍʏꜱᴘᴀᴍ ᴏɴ 💬  ║\n"
        "╚══════════════════╝\n"
        f"📝 ᴛᴇxᴛ: {text}\n"
        f"🤖 {len(bots)} ʙᴏᴛꜱ ᴀʀᴇ ꜱᴘᴀᴍᴍɪɴɢ !\n"
        "🛑 /stopspam ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
    )

# ===========================================================
# CUSTOM NC — /mync
# ===========================================================
async def mync_loop(bot, chat_id, text):
    await _nc_master_loop(bot, chat_id, _build_nc_titles(CUSTOM_NC_FRAMES, text))

NC_LOOP_REGISTRY['mync_loop'] = mync_loop

@nc_only
async def mync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text(
            "╔══════════════════╗\n"
            "║  🔥 ᴍʏ ɴᴄ 🔥  ║\n"
            "╚══════════════════╝\n"
            "❌ ɢɪᴠᴇ ᴍᴇ ᴛᴇxᴛ !\n"
            "📌 ᴜꜱᴀɢᴇ: /mync <ʏᴏᴜʀ ᴛᴇxᴛ>\n"
            "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
        )
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, mync_loop, chat_id, text)
    await update.message.reply_text(
        "╔══════════════════╗\n"
        "║  🔥 ᴍʏ ɴᴄ ᴏɴ 🔥  ║\n"
        "╚══════════════════╝\n"
        f"📝 ᴛᴇxᴛ: {text}\n"
        f"🤖 {len(bots)} ʙᴏᴛ ᴀʀᴇ ᴄʜᴀɴɢɪɴɢ ᴛʜᴇ ɴᴀᴍᴇ!\n"
        "🛑 /stopnc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
    )

# ===========================================================
# STARTALL — /startall (spam2 + hindinc + gc photo combo)
# ===========================================================
@sudo_only
async def startall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text(
            "╔══════════════════╗\n"
            "║  🚀 ꜱᴛᴀʀᴛ ᴀʟʟ 🚀  ║\n"
            "╚══════════════════╝\n"
            "❌ ɢɪᴠᴇ ᴍᴇ ᴛᴇxᴛ !\n"
            "📌 ᴜꜱᴀɢᴇ: /startall <ᴛᴇxᴛ>\n"
            "⚡ Spam2 + HindiNC + GC Photo — ᴀʟʟ ᴀʀᴇ ɪɴ ᴘᴀʀᴀʟʟᴇʟ\n"
            "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
        )
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    started = []

    # Start Spam2
    _cancel_tasks(spam_tasks, chat_id)
    spam_tasks[chat_id] = [asyncio.create_task(spam2_loop(b, chat_id, text)) for b in bots]
    started.append("💥 Spam2")

    # Start HindiNC
    _cancel_tasks(nc_tasks, chat_id)
    nc_tasks[chat_id] = _start_multi_nc(bots, hindinc_loop, chat_id, text)
    started.append("🔥 HindiNC")

    # Start GC Photo (if images saved)
    gc_slots = [f"gc_image_{i}.png" for i in range(1, 11) if os.path.exists(f"gc_image_{i}.png")]
    if gc_slots:
        _cancel_tasks(gc_tasks, chat_id)
        gc_tasks[chat_id] = [asyncio.create_task(gc_photo_loop(b, chat_id)) for b in bots]
        started.append(f"🖼 GC Photo ({len(gc_slots)} imgs)")

    await update.message.reply_text(
        "╔══════════════════╗\n"
        "║  🚀 ꜱᴛᴀʀᴛ ᴀʟʟ 𝗗ᴏɴ𝗘 🚀  ║\n"
        "╚══════════════════╝\n"
        f"📝 ᴛᴇxᴛ: {text}\n"
        f"✅ ꜱᴛᴀʀᴛᴇᴅ: {' | '.join(started)}\n"
        f"🤖 {len(bots)} ʙᴏᴛꜱ ᴀᴄᴛɪᴠᴇ!\n"
        "🛑 /stopall ꜰᴏʀ ꜱᴛᴏᴘ !\n"
        "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
    )

# ===========================================================
# DYNAMIC BOT CLONING — /clone <ᴛᴏᴋᴇɴ> (private chat only)
# ===========================================================
_BOT_TOKEN_RE = re.compile(r'^\d{6,20}:[A-Za-z0-9_-]{30,}$')
_clone_lock = None


class CloneBotError(Exception):
    """An expected clone failure with a token-safe user-facing message."""


async def _shutdown_cloned_app(app):
    """Best-effort cleanup for an application that failed during startup."""
    try:
        if app.updater and app.updater.running:
            await app.updater.stop()
    except Exception:
        pass
    try:
        if app.running:
            await app.stop()
    except Exception:
        pass
    try:
        await app.shutdown()
    except Exception:
        pass


def _persist_cloned_bot(token, bot_id, owner_id):
    """Atomically persist one clone without ever logging its bot token."""
    records = []
    if os.path.exists(CLONED_BOTS_FILE):
        with open(CLONED_BOTS_FILE) as clone_file:
            data = json.load(clone_file)
        records = data.get('bots', [])
        if not isinstance(records, list):
            records = []

    records = [
        record for record in records
        if str(record.get('token', '')).strip() != token
        and int(record.get('bot_id', 0)) != bot_id
    ]
    records.append({
        'token': token,
        'bot_id': bot_id,
        'owner_id': owner_id,
        'created_at': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
    })

    temp_path = CLONED_BOTS_FILE + '.tmp'
    fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w') as clone_file:
            json.dump({'bots': records}, clone_file, indent=2)
        os.replace(temp_path, CLONED_BOTS_FILE)
        try:
            os.chmod(CLONED_BOTS_FILE, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


async def start_cloned_bot(token, owner_id):
    """Validate, start polling, and persist a new bot without a restart."""
    global _clone_lock
    if _clone_lock is None:
        _clone_lock = asyncio.Lock()

    async with _clone_lock:
        if token in TOKENS:
            raise CloneBotError('⚠️ ᴛʜɪꜱ ʙᴏᴛ ɪꜱ ᴀʟʀᴇᴀᴅʏ ʜᴏꜱᴛᴇᴅ !.')
        if len(CLONE_OWNERS) >= MAX_CLONED_BOTS:
            raise CloneBotError('❌ ᴀᴛ ᴛʜɪꜱ ᴛɪᴍᴇ ,ᴍᴀxɪᴍᴜᴍ ᴄʟᴏɴᴇ ʜᴏꜱᴛɪɴɢ ᴄᴀᴘᴀʙɪʟɪᴛʏ ʟɪᴍɪᴛ ɪꜱ ʀᴇᴀᴄʜᴇᴅ')

        try:
            app = build_app(token)
        except Exception as exc:
            logging.error('Clone build failed: %s', type(exc).__name__)
            raise CloneBotError('❌ ᴛᴏᴋᴇɴ ɪꜱ ɴᴏᴛ ᴠᴀʟɪᴅ !') from None

        try:
            # initialize() calls getMe, so an invalid/revoked token fails here.
            await app.initialize()
            bot_user = await app.bot.get_me()
            bot_id = bot_user.id

            active_ids = {
                running_id for running_id in (
                    _safe_bot_id(bot) for bot in list.__iter__(bots)
                ) if running_id is not None
            }
            if bot_id in active_ids:
                raise CloneBotError('⚠️ ᴛʜɪꜱ ʙᴏᴛ ɪꜱ ᴀʟʀᴇᴀᴅʏ ʜᴏꜱᴛᴇᴅ !')

            await app.start()
            await app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=['message', 'callback_query'],
                poll_interval=0.0,
                timeout=10,
            )
            _persist_cloned_bot(token, bot_id, owner_id)
        except CloneBotError:
            await _shutdown_cloned_app(app)
            raise
        except Exception as exc:
            await _shutdown_cloned_app(app)
            logging.error(
                'Clone startup failed for bot id %s: %s',
                token.split(':', 1)[0], type(exc).__name__
            )
            raise CloneBotError(
                '❌ ᴡᴇ ᴀʀᴇ ꜰᴀᴄɪɴɢ ᴛᴇᴄʜɴɪᴄᴀʟ ᴇʀʀᴏʀ, ᴘʟᴇᴀꜱᴇ ᴄʜᴇᴄᴋ ʏᴏᴜʀ ʙᴏᴛ ᴛᴏᴋᴇɴ ᴏʀ ᴛʀʏ ᴀɢᴀɪɴ !'
            ) from None

        # Publish the app to the running registries only after startup and
        # persistence both succeed.
        TOKENS.append(token)
        CLONE_OWNERS[bot_id] = owner_id
        apps.append(app)
        bots.append(app.bot)
        return bot_user


@lead_bot_only
async def clone_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lead-only command that securely hosts a supplied bot token."""
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
        await update.message.reply_text(
            '🔒 ꜱᴇɴᴅ ᴍᴇ ʏᴏᴜʀ ʙᴏᴛ ᴛᴏᴋᴇɴ ɪɴ ᴅᴍ ɴᴏᴛ ɪɴ ɢʀᴏᴜᴘꜱ /clone <ʙᴏᴛ_ᴛᴏᴋᴇɴ>'
        )
        return

    if len(context.args) != 1:
        await update.message.reply_text(
            '🧬 ʜᴏꜱᴛ ʏᴏᴜʀ ʙᴏᴛ ʜᴇʀᴇ\n\n'
            '1. ᴄᴏᴘʏ ᴛᴏᴋᴇɴ ꜰʀᴏᴍ @BotFather \n'
            '2. ꜱᴇɴᴅ ᴍᴇ ɪɴ ᴅᴍ: /clone <ʙᴏᴛ_ᴛᴏᴋᴇɴ>\n\n'
            '⚠️ ɴᴇᴠᴇʀ ꜱʜᴀʀᴇ ʏᴏᴜʀ ʙᴏᴛ ᴛᴏᴋᴇɴ ɪɴ ᴀɴʏ ɢʀᴏᴜᴘ ᴄʜᴀᴛꜱ'
        )
        return

    token = context.args[0].strip()

    # Remove the message containing the secret as soon as we have copied it.
    try:
        await update.message.delete()
    except Exception:
        pass

    status = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text='🔄 ꜱᴛᴀʀᴛɪɴɢ ʏᴏᴜʀ ʙᴏᴛ ᴀꜰᴛᴇʀ ᴠᴀʟɪᴅᴀᴛɪɴɢ ʏᴏᴜʀ ᴛᴏᴋᴇɴ...'
    )

    if not _BOT_TOKEN_RE.fullmatch(token):
        await status.edit_text('❌ ɪɴᴠᴀʟɪᴅ ʙᴏᴛ ᴛᴏᴋᴇɴ ꜰᴏʀᴍᴀᴛ. ᴄᴏᴘʏ ɴᴇᴡ ᴛᴏᴋᴇɴ ꜰʀᴏᴍ @BotFather !!')
        return

    try:
        bot_user = await start_cloned_bot(token, update.effective_user.id)
    except CloneBotError as exc:
        await status.edit_text(str(exc))
        return

    username = f'@{bot_user.username}' if bot_user.username else bot_user.full_name
    await status.edit_text(
        '✅ ᴄʟᴏɴᴇᴅ ʙᴏᴛ ɪꜱ ʟɪᴠᴇ ɴᴏᴡ\n'
        f'🤖 ʙᴏᴛ: {username}\n'
        '🚀 ɴᴏ ɴᴇᴇᴅ ᴛᴏ ʀᴇ-ꜱᴛᴀʀᴛ , ᴘᴏʟʟɪɴɢ ʜᴀꜱ ʙᴇᴇɴ ꜱᴛᴀʀᴛᴇᴅ\n'
        '🔐 ɴᴏᴡ , ʏᴏᴜ ᴄᴀɴ ᴜꜱᴇ ᴄᴏᴍᴍᴀɴᴅꜱ ᴏɴ ʏᴏᴜʀ ᴄʟᴏɴᴇᴅ ʙᴏᴛ\n\n'
        f'​🇳​​🇴​​🇼​ , ​🇴​​🇵​​🇪​​🇳​ ​🇹​​🇭​​🇪​ {username} ​🇦​​🇳​​🇩​ ​🇸​​🇪​​🇳​​🇩​ ​🇺​​🇸​ /start'
    )


# ===========================================================
# ADD TOKEN — /addtoken (owner only, legacy restart-based command)
# ===========================================================
@owner_only
async def addtoken(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text(
            "╔══════════════════╗\n"
            "║  🔑 ᴀᴅᴅ ᴛᴏᴋᴇɴ🔑  ║\n"
            "╚══════════════════╝\n"
            "❌ ɢɪᴠᴇ ᴍᴇ ᴛᴏᴋᴇɴ !\n"
            "📌 ᴜꜱᴀɢᴇ: /addtoken <BOT_TOKEN>\n"
            "💡 ᴇxᴀᴍᴘʟᴇ: /addtoken 1234567890:AAXXXX..."
        )
    new_token = context.args[0].strip()
    # Basic format check
    if ':' not in new_token or len(new_token) < 30:
        return await update.message.reply_text("❌ ɪɴᴠᴀʟɪᴅ ᴛᴏᴋᴇɴ ꜰᴏʀᴍᴀᴛ! ᴄᴏʀʀᴇᴄᴛ ꜰᴏʀᴍᴀᴛ: 1234567890:AAXXXX...")
    # Check duplicate
    if new_token in TOKENS:
        return await update.message.reply_text("⚠️ ᴛʜɪꜱ ᴛᴏᴋᴇɴ ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴀᴅᴅᴇᴅ !")
    # Save to extra_bots.txt (persistent)
    try:
        with open('extra_bots.txt', 'a') as f:
            f.write(new_token + '\n')
        TOKENS.append(new_token)
        bot_id = new_token.split(':')[0]
        await update.message.reply_text(
            f"✅ ᴛᴏᴋᴇɴ ɪꜱ ᴀᴅᴅᴇᴅ!\n"
            f"🤖 ʙᴏᴛ ɪᴅ: {bot_id}\n"
            f"📊 ᴛᴏᴛᴀʟ ʙᴏᴛꜱ: {len(TOKENS)}\n"
            f"⚠️ ɴᴏᴛᴇ: ʀᴇ-ꜱᴛᴀʀᴛ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ᴍᴀᴋᴇ ʏᴏᴜʀ ʙᴏᴛ ᴡᴏʀᴋɪɴɢ"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ꜱᴀᴠᴇ ᴛʜᴇ ᴛᴏᴋᴇɴ: {e}")

# ===========================================================
# ADD USER TO GROUP — /adduser
# ===========================================================
@sudo_only
async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text(
            "╔══════════════════╗\n"
            "║  👤 ᴀᴅᴅ ᴜꜱᴇʀ 👤  ║\n"
            "╚══════════════════╝\n"
            "❌ ɢɪᴠᴇ ᴜꜱᴇʀɴᴀᴍᴇ ᴏʀ ᴜꜱᴇʀ ɪᴅ!\n"
            "📌 ᴜꜱᴀɢᴇ: /adduser @username\n"
            "📌 ᴏʀ: /adduser 123456789\n"
            "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
        )
    target = context.args[0].strip()
    chat_id = update.message.chat_id
    status_msg = await update.message.reply_text(f"🔄 ᴛʀʏɪɴɢ ᴛᴏ ᴀᴅᴅ{target}...")

    # Resolve user_id from username or direct ID
    user_id = None
    try:
        if target.lstrip('@').isdigit() or (target.startswith('-') and target[1:].isdigit()):
            user_id = int(target)
        else:
            chat_obj = await context.bot.get_chat(target if target.startswith('@') else f"@{target}")
            user_id = chat_obj.id
    except Exception as e:
        return await status_msg.edit_text(
            f"❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ʀᴇꜱᴏʟᴠᴇ ᴛʜᴇ ᴜꜱᴇʀ: {target}\n"
            f"⚠️ ᴇʀʀᴏʀ: {str(e)[:80]}\n"
            "💡 ᴛʀʏ ᴡɪᴛʜ ᴜꜱᴇʀ ɪᴅ: /adduser 123456789"
        )

    # Step 1: Unban the user (removes any restriction so they can join)
    for bot in bots:
        try:
            await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=False)
            await asyncio.sleep(0.02)
        except Exception:
            pass

    # Step 2: Create a one-time invite link (member_limit=1) for this user
    invite_link = None
    link_err = ""
    for bot in bots:
        try:
            link_obj = await bot.create_chat_invite_link(
                chat_id=chat_id,
                member_limit=1,
                name=f"adduser_{user_id}"
            )
            invite_link = link_obj.invite_link
            break
        except Exception as e:
            link_err = str(e)[:80]
            await asyncio.sleep(0.02)

    if invite_link:
        await status_msg.edit_text(
            "╔══════════════════╗\n"
            "║  ✅ ʟɪɴᴋ ʀᴇᴀᴅʏ ✅  ║\n"
            "╚══════════════════╝\n"
            f"👤 ᴜꜱᴇʀ: {target}\n"
            f"🆔 ɪᴅ: {user_id}\n"
            f"🔗 ɪɴᴠɪᴛᴇ ʟɪɴᴋ (1 ᴜꜱᴇ ᴏɴʟʏ):\n{invite_link}\n\n"
            "📌 ꜱᴇɴᴅ ᴛʜɪꜱ ʟɪɴᴋ ᴛᴏ ᴜꜱᴇʀ - ᴏɴʟʏ ᴏɴᴇ ᴜꜱᴇ!\n"
            "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
        )
    else:
        await status_msg.edit_text(
            f"❌ ᴜɴᴀʙʟᴇ ᴛᴏ ᴄʀᴇᴀᴛᴇ ɪɴᴠɪᴛᴇ ʟɪɴᴋ!\n"
            f"⚠️ {link_err}\n"
            "💡 ʙᴏᴛ ɴᴇᴇᴅꜱ ᴘᴇʀᴍɪꜱꜱɪᴏɴ 'ɪɴᴠɪᴛᴇ ᴜꜱᴇʀꜱ ᴠɪᴀ ʟɪɴᴋ' !"
        )

# ===========================================================
# RAID SPAM
# ===========================================================
@sudo_only
async def raidspam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /raidspam <target>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    _cancel_tasks(raid_tasks, chat_id)
    raid_tasks[chat_id] = [asyncio.create_task(raid_spam_loop_fn(b, chat_id, text)) for b in bots]
    await update.message.reply_text(
        "╭──💣 ʀᴀɪᴅ ꜱᴘᴀᴍ ᴀᴄᴛɪᴠᴇ 💣──╮\n"
        f"🎯 ᴛᴀʀɢᴇᴛ : {text}\n"
        f"🤖 ʙᴏᴛꜱ   : {len(bots)} raiding!\n"
        "🛑 /stopraidspam ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@sudo_only
async def stopraidspam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    _cancel_tasks(raid_tasks, chat_id)
    await update.message.reply_text(
        "╭──🛑 ʀᴀɪᴅ ꜱᴛᴏᴘᴘᴇᴅ 🛑──╮\n"
        "✅ ʀᴀɪᴅ ꜱᴘᴀᴍ ꜱᴛᴏᴘᴘᴇᴅ!\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

# ===========================================================
# PHOTO COMMAND HANDLERS
# ===========================================================
@sudo_only
async def savephoto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message or not update.message.reply_to_message.photo:
        return await update.message.reply_text("⚠️ Reply to a photo to save it!")
    chat_id = update.message.chat_id
    file_id = update.message.reply_to_message.photo[-1].file_id
    if chat_id not in chat_photos:
        chat_photos[chat_id] = []
    chat_photos[chat_id].append(file_id)
    await update.message.reply_text(f"✅ ᴘʜᴏᴛᴏ ꜱᴀᴠᴇᴅ! ᴛᴏᴛᴀʟ: {len(chat_photos[chat_id])}")

@sudo_only
async def startphoto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id not in chat_photos or len(chat_photos[chat_id]) < 1:
        return await update.message.reply_text("⚠️ ꜱᴀᴠᴇ ᴀᴛ ʟᴇᴀꜱᴛ 1 ᴘʜᴏᴛᴏ ꜰɪʀꜱᴛ ᴜꜱɪɴɢ /savephoto!")
    _cancel_tasks(photo_tasks, chat_id)
    photo_tasks[chat_id] = [asyncio.create_task(photo_loop(b, chat_id)) for b in bots]
    await update.message.reply_text(
        "╭──📸 ᴘʜᴏᴛᴏ ʟᴏᴏᴘᴇᴅ ᴀᴄᴛɪᴠᴇ 📸──╮\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ᴄʜᴀɴɢɪɴɢ ᴘʜᴏᴛᴏ\n"
        "🛑 /stopphoto ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@sudo_only
async def stopphoto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id in photo_tasks:
        _cancel_tasks(photo_tasks, chat_id)
        await update.message.reply_text(
        "╭──🛑 ᴘʜᴏᴛᴏ ꜱᴛᴏᴘᴘᴇᴅ 🛑──╮\n"
        "✅ ᴘʜᴏᴛᴏ ʟᴏᴏᴘᴇᴅ ꜱᴛᴏᴘᴘᴇᴅ!\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )
    else:
        await update.message.reply_text("❌ ɴᴏ ᴀᴄᴛɪᴠᴇ ᴘʜᴏᴛᴏ ʟᴏᴏᴘ")

@sudo_only
async def clearphotos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id in chat_photos:
        del chat_photos[chat_id]
        await update.message.reply_text("🗑 ꜱᴀᴠᴇᴅ ᴘʜᴏᴛᴏꜱ ᴄʟᴇᴀʀᴇᴅ!")
    else:
        await update.message.reply_text("❌ ɴᴏ ꜱᴀᴠᴇᴅ ᴘʜᴏᴛᴏꜱ ᴛᴏ ᴄʟᴇᴀʀ!")

@sudo_only
async def listphotos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id not in chat_photos or not chat_photos[chat_id]:
        return await update.message.reply_text("📭 ɴᴏ ᴘʜᴏᴛᴏꜱ ꜱᴀᴠᴇᴅ ʏᴇᴛ!")
    lines = [f"📸 ꜱᴀᴠᴇᴅ ᴘʜᴏᴛᴏꜱ — ᴛᴏᴛᴀʟ: {len(chat_photos[chat_id])}\n"]
    for idx, _ in enumerate(chat_photos[chat_id], 1):
        lines.append(f"  🖼 Photo #{idx}")
    lines.append("\n💡 ꜰᴏʀ ꜱᴘᴀᴍ: /picspam <number>")
    await update.message.reply_text("\n".join(lines))

# ===========================================================
# GC PHOTO LOOP COMMANDS
# ===========================================================
@sudo_only
async def gc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    gc_slots = [f"gc_image_{i}.png" for i in range(1, 11) if os.path.exists(f"gc_image_{i}.png")]
    if not gc_slots:
        return await update.message.reply_text("❌ ɴᴏ ɢᴄ ɪᴍᴀɢᴇꜱ ꜰᴏᴜɴᴅ!\nᴘʟᴀᴄᴇ ɢᴄ_ɪᴍᴀɢᴇ_1.ᴘɴɢ … ɢᴄ_ɪᴍᴀɢᴇ_10.ᴘɴɢ ɪɴ ᴛʜᴇ ꜱᴀᴍᴇ ꜰᴏʟᴅᴇʀ.")
    _cancel_tasks(gc_tasks, chat_id)
    gc_tasks[chat_id] = [asyncio.create_task(gc_photo_loop(b, chat_id)) for b in bots]
    await update.message.reply_text(
        "╭──🖼 ɢᴄ ᴘʜᴏᴛᴏ ᴀᴄᴛɪᴠᴇ 🖼──╮\n"
        f"📸 ɪᴍᴀɢᴇꜱ : {len(gc_slots)} found\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} running\n"
        "🛑 /stopgc ꜰᴏʀ ꜱᴛᴏᴘ\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@sudo_only
async def stopgc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    _cancel_tasks(gc_tasks, chat_id)
    await update.message.reply_text(
        "╭──🛑 ɢᴄ ᴘʜᴏᴛᴏ ꜱᴛᴏᴘᴘᴇᴅ 🛑──╮\n"
        "✅ ɢᴄ ᴘʜᴏᴛᴏ ʟᴏᴏᴘ ʙᴀɴᴅ!\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

# ===========================================================
# STOP COMMAND HANDLERS
# ===========================================================
@sudo_only
async def stopnc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    _cancel_tasks(nc_tasks, chat_id)
    await update.message.reply_text(
        "╭──🛑 ɴᴄ ꜱᴛᴏᴘᴘᴇᴅ 🛑──╮\n"
        "✅ ꜱᴀʙʜɪ ɴᴄ ᴛᴀꜱᴋꜱ ʙᴀɴᴅ!\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@sudo_only
async def stopspam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    _cancel_tasks(spam_tasks, chat_id)
    await update.message.reply_text(
        "╭──🛑 ꜱᴘᴀᴍ ꜱᴛᴏᴘᴘᴇᴅ 🛑──╮\n"
        "✅ ꜱᴀʙʜɪ ꜱᴘᴀᴍ ᴛᴀꜱᴋꜱ ʙᴀɴᴅ!\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@sudo_only
async def stopslide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    _cancel_tasks(slider_tasks, chat_id)
    await update.message.reply_text(
        "╭──🛑 ꜱʟɪᴅᴇ ꜱᴛᴏᴘᴘᴇᴅ 🛑──╮\n"
        "✅ ꜱᴀʙʜɪ ꜱʟɪᴅᴇ ᴛᴀꜱᴋꜱ ʙᴀɴᴅ!\n"
        "╰⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️──╯"
    )

@sudo_only
async def stopall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    stopped = []
    for d, label in [(nc_tasks, 'NC'), (spam_tasks, 'Spam'), (slider_tasks, 'Slide'),
                     (photo_tasks, 'Photo'), (gc_tasks, 'GC'), (raid_tasks, 'Raid'),
                     (react_tasks, 'React'), (delete_tasks, 'Delete'), (deluser_tasks, 'DelUser')]:
        if chat_id in d:
            _cancel_tasks(d, chat_id)
            stopped.append(label)
    if stopped:
        await update.message.reply_text(f"🛑 ꜱᴛᴏᴘᴘᴇᴅ: {', '.join(stopped)}!")
    else:
        await update.message.reply_text("❌ ɴᴏ ᴀᴄᴛɪᴠᴇ ᴀᴄᴛɪᴠɪᴛɪᴇꜱ ᴛᴏ ꜱᴛᴏᴘ.")

# ===========================================================
# DELETE ALL HISTORY COMMANDS
# ===========================================================
@sudo_only
async def deleteallhistory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return await update.message.reply_text("ɢʀᴏᴜᴘꜱ ᴏɴʟʏ!")
    chat_id = chat.id
    start_msg_id = update.message.message_id
    _cancel_tasks(delete_tasks, chat_id)
    delete_tasks[chat_id] = [asyncio.create_task(delete_history_loop(b, chat_id, start_msg_id - (i * 100))) for i, b in enumerate(bots)]
    await update.message.reply_text("🗑️ ᴅᴇʟᴇᴛᴇ ᴀʟʟ ʜɪꜱᴛᴏʀʏ ʟᴏᴏᴘ ꜱᴛᴀʀᴛᴇᴅ! ʙᴏᴛ ᴍᴜꜱᴛ ʙᴇ ᴀᴅᴍɪɴ ᴡɪᴛʜ ᴅᴇʟᴇᴛᴇ ᴍᴇꜱꜱᴀɢᴇꜱ ᴘᴇʀᴍɪꜱꜱɪᴏɴ.")

@sudo_only
async def stopdeleteallhistory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _cancel_tasks(delete_tasks, update.effective_chat.id)
    await update.message.reply_text("🛑 ᴅᴇʟᴇᴛᴇ ᴀʟʟ ʜɪꜱᴛᴏʀʏ ꜱᴛᴏᴘᴘᴇᴅ!")

# ===========================================================
# DELUSER
# ===========================================================
@sudo_only
async def deluser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return await update.message.reply_text("ɢʀᴏᴜᴘꜱ ᴏɴʟʏ!")
    user_id = None
    user_name = 'Unknown'
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        u = update.message.reply_to_message.from_user
        user_id, user_name = u.id, u.first_name or str(u.id)
    elif context.args:
        try:
            user_id = int(context.args[0])
            user_name = str(user_id)
        except ValueError:
            return await update.message.reply_text("ᴜꜱᴀɢᴇ: /deluser (ʀᴇᴘʟʏ) ᴏʀ /deluser <ᴜꜱᴇʀ_ɪᴅ>")
    if not user_id:
        return await update.message.reply_text("ᴜꜱᴀɢᴇ: /deluser (ʀᴇᴘʟʏ) ᴏʀ /deluser <ᴜꜱᴇʀ_ɪᴅ>")
    cid = chat.id
    start_msg_id = update.message.message_id
    _cancel_tasks(deluser_tasks, cid)
    deluser_tasks[cid] = [asyncio.create_task(delete_history_loop(b, cid, start_msg_id - (i * 100))) for i, b in enumerate(bots)]
    auto_delete_users.setdefault(cid, set()).add(user_id)
    await update.message.reply_text(f"🎯 ᴅᴇʟ ᴜꜱᴇʀ ꜱᴛᴀʀᴛᴇᴅ ᴏɴ {user_name}!\n🗑️ ʟᴏᴏᴘɪɴɢ ᴛʜʀᴏᴜɢʜ ᴀʟʟ ᴘᴀꜱᴛ ᴍᴇꜱꜱᴀɢᴇꜱ!\n⚡ ᴀʟꜱᴏ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛɪɴɢ ᴇᴠᴇʀʏ ɴᴇᴡ ᴍᴇꜱꜱᴀɢᴇ!")

@sudo_only
async def stopdeluser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    _cancel_tasks(deluser_tasks, cid)
    user_id = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        user_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            user_id = int(context.args[0])
        except Exception:
            pass
    if user_id and cid in auto_delete_users:
        auto_delete_users[cid].discard(user_id)
        if not auto_delete_users[cid]:
            del auto_delete_users[cid]
    await update.message.reply_text("🛑 ᴅᴇʟ ᴜꜱᴇʀ ꜱᴛᴏᴘᴘᴇᴅ!")

# ===========================================================
# ᴅᴇʟᴀʟʟ
# ===========================================================
@sudo_only
async def delall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return await update.message.reply_text("ɢʀᴏᴜᴘꜱ ᴏɴʟʏ!")
    if not context.args:
        return await update.message.reply_text("ᴜꜱᴀɢᴇ: /delall <ᴛᴀʀɢᴇᴛ ɴᴀᴍᴇ>")
    text = " ".join(context.args)
    chat_id = chat.id
    start_msg_id = update.message.message_id
    _cancel_tasks(delete_tasks, chat_id)
    _cancel_tasks(nc_tasks, chat_id)
    delete_tasks[chat_id] = [asyncio.create_task(delete_history_loop(b, chat_id, start_msg_id - (i * 100))) for i, b in enumerate(bots)]
    nc_tasks[chat_id] = _start_multi_nc(bots, namenc_loop, chat_id, text)
    await update.message.reply_text(f"🔥 ᴅᴇʟᴀʟʟ ꜱᴛᴀʀᴛᴇᴅ!\n🗑️ ᴅᴇʟᴇᴛᴇ ʟᴏᴏᴘ + ⚡ ɴᴄ ʟᴏᴏᴘ ʀᴜɴɴɪɴɢ!\n🎯 ᴛᴀʀɢᴇᴛ: {text}")

@sudo_only
async def stopdelall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    _cancel_tasks(delete_tasks, chat_id)
    _cancel_tasks(nc_tasks, chat_id)
    await update.message.reply_text("🛑 ᴅᴇʟᴀʟʟ stopped!")

# ===========================================================
# BLOCKNC
# ===========================================================
@sudo_only
async def blocknc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return await update.message.reply_text("ɢʀᴏᴜᴘꜱ ᴏɴʟʏ!")
    if not context.args:
        return await update.message.reply_text("ᴜꜱᴀɢᴇ: /blocknc <protected group name>")
    name = " ".join(context.args)
    cid = chat.id
    blocknc_active[cid] = name
    try:
        await context.bot.set_chat_title(chat_id=cid, title=name)
        await update.message.reply_text(f"🔒 ʙʟᴏᴄᴋ ɴᴄ ᴇɴᴀʙʟᴇᴅ!\n📛 ɢʀᴏᴜᴘ ɴᴀᴍᴇ ʟᴏᴄᴋᴇᴅ ᴛᴏ: {name}\n\nᴀɴʏᴏɴᴇ ᴡʜᴏ ᴄʜᴀɴɢᴇꜱ ᴛʜᴇ ɴᴀᴍᴇ ᴡɪʟʟ ʙᴇ ɪɴꜱᴛᴀɴᴛʟʏ ʙᴀɴɴᴇᴅ ᴀɴᴅ ɴᴀᴍᴇ ʀᴇꜱᴛᴏʀᴇᴅ!")
    except Exception as e:
        await update.message.reply_text(f"🔒 ʙʟᴏᴄᴋ ɴᴄ ᴇɴᴀʙʟᴇᴅ — ɴᴀᴍᴇ ʟᴏᴄᴋᴇᴅ ᴛᴏ: {name}\n⚠️ ᴄᴏᴜʟᴅ ɴᴏᴛ ꜱᴇᴛ ᴛɪᴛʟᴇ ʀɪɢʜᴛ ɴᴏᴡ: {e}")

@sudo_only
async def stopblocknc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if cid in blocknc_active:
        del blocknc_active[cid]
        await update.message.reply_text("🔓 ʙʟᴏᴄᴋ ɴᴄ ᴅɪꜱᴀʙʟᴇᴅ — ɢʀᴏᴜᴘ ɴᴀᴍᴇ ɪꜱ ɴᴏᴡ ꜰʀᴇᴇ ᴛᴏ ᴄʜᴀɴɢᴇ!")
    else:
        await update.message.reply_text("ʙʟᴏᴄᴋ ɴᴄ ɪꜱ ɴᴏᴛ ᴀᴄᴛɪᴠᴇ ɪɴ ᴛʜɪꜱ ᴄʜᴀᴛ.")

# ===========================================================
# MODERATION: BAN/UNBAN/MUTE/UNMUTE
# ===========================================================
@sudo_only
async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return await update.message.reply_text("ɢʀᴏᴜᴘꜱ ᴏɴʟʏ!")
    user_id = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        user_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            user_id = int(context.args[0])
        except ValueError:
            return await update.message.reply_text("ᴜꜱᴀɢᴇ: /ban (ʀᴇᴘʟʏ) ᴏʀ /ban <ᴜꜱᴇʀ_ɪᴅ>")
    if not user_id:
        return await update.message.reply_text("ᴜꜱᴀɢᴇ: /ban (ʀᴇᴘʟʏ) ᴏʀ /ban <ᴜꜱᴇʀ_ɪᴅ>")
    try:
        await context.bot.ban_chat_member(chat_id=chat.id, user_id=user_id)
        await update.message.reply_text(f"🔨 ᴜꜱᴇʀ {user_id} ʙᴀɴɴᴇᴅ!")
    except Exception as e:
        await update.message.reply_text(f"❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ʙᴀɴ: {e}")

@sudo_only
async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return await update.message.reply_text("ɢʀᴏᴜᴘꜱ ᴏɴʟʏ!")
    user_id = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        user_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            user_id = int(context.args[0])
        except ValueError:
            return await update.message.reply_text("ᴜꜱᴀɢᴇ: /unban (ʀᴇᴘʟʏ) ᴏʀ /unban <ᴜꜱᴇʀ_ɪᴅ>")
    if not user_id:
        return await update.message.reply_text("ᴜꜱᴀɢᴇ: /unban (ʀᴇᴘʟʏ) ᴏʀ /unban <ᴜꜱᴇʀ_ɪᴅ>")
    try:
        await context.bot.unban_chat_member(chat_id=chat.id, user_id=user_id, only_if_banned=True)
        await update.message.reply_text(f"✅ ᴜꜱᴇʀ {user_id} unbanned!")
    except Exception as e:
        await update.message.reply_text(f"❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴜɴʙᴀɴ: {e}")

@sudo_only
async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return await update.message.reply_text("ɢʀᴏᴜᴘꜱ ᴏɴʟʏ!")
    user_id = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        user_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            user_id = int(context.args[0])
        except ValueError:
            return await update.message.reply_text("ᴜꜱᴀɢᴇ: /mute (ʀᴇᴘʟʏ) ᴏʀ /mute <ᴜꜱᴇʀ_ɪᴅ>")
    if not user_id:
        return await update.message.reply_text("ᴜꜱᴀɢᴇ: /mute (ʀᴇᴘʟʏ) ᴏʀ /mute <ᴜꜱᴇʀ_ɪᴅ>")
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id, user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=False, can_send_audios=False, can_send_documents=False,
                can_send_photos=False, can_send_videos=False, can_send_polls=False,
                can_send_other_messages=False, can_add_web_page_previews=False
            )
        )
        await update.message.reply_text(f"🔇 ᴜꜱᴇʀ {user_id} muted!")
    except Exception as e:
        await update.message.reply_text(f"❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴍᴜᴛᴇ: {e}")

@sudo_only
async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return await update.message.reply_text("ɢʀᴏᴜᴘꜱ ᴏɴʟʏ!")
    user_id = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        user_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            user_id = int(context.args[0])
        except ValueError:
            return await update.message.reply_text("ᴜꜱᴀɢᴇ: /unmute (ʀᴇᴘʟʏ) ᴏʀ /unmute <ᴜꜱᴇʀ_ɪᴅ>")
    if not user_id:
        return await update.message.reply_text("ᴜꜱᴀɢᴇ: /unmute (ʀᴇᴘʟʏ) ᴏʀ /unmute <ᴜꜱᴇʀ_ɪᴅ>")
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id, user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=True, can_send_audios=True, can_send_documents=True,
                can_send_photos=True, can_send_videos=True, can_send_polls=True,
                can_send_other_messages=True, can_add_web_page_previews=True
            )
        )
        await update.message.reply_text(f"🔊 ᴜꜱᴇʀ {user_id} unmuted!")
    except Exception as e:
        await update.message.reply_text(f"❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴜɴᴍᴜᴛᴇ: {e}")

# ===========================================================
# MODERATION: WARN SYSTEM
# ===========================================================
@sudo_only
async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return await update.message.reply_text("ɢʀᴏᴜᴘꜱ ᴏɴʟʏ!")
    user_id = None
    user_name = 'Unknown'
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        u = update.message.reply_to_message.from_user
        user_id, user_name = u.id, u.first_name or str(u.id)
    elif context.args:
        try:
            user_id = int(context.args[0])
            user_name = str(user_id)
        except ValueError:
            return await update.message.reply_text("ᴜꜱᴀɢᴇ: /warn (ʀᴇᴘʟʏ) ᴏʀ /warn <ᴜꜱᴇʀ_ɪᴅ>")
    if not user_id:
        return await update.message.reply_text("ᴜꜱᴀɢᴇ: /warn (ʀᴇᴘʟʏ) ᴏʀ /warn <ᴜꜱᴇʀ_ɪᴅ>")
    cid = chat.id
    limit = warn_limits.get(cid, 3)
    warn_counts.setdefault(cid, {})[user_id] = warn_counts.get(cid, {}).get(user_id, 0) + 1
    count = warn_counts[cid][user_id]
    if count >= limit:
        try:
            await context.bot.ban_chat_member(chat_id=cid, user_id=user_id)
            warn_counts[cid].pop(user_id, None)
            await update.message.reply_text(f"⚠️ {user_name} ʀᴇᴀᴄʜᴇᴅ{limit}/{limit} ᴡᴀʀɴɪɴɢꜱ — ʙᴀɴɴᴇᴅ🔨")
        except Exception as e:
            await update.message.reply_text(f"⚠️ {user_name} ʜᴀꜱ {count}/{limit} ᴡᴀʀɴꜱ ʙᴜᴛ ʙᴀɴ ꜰᴀɪʟᴇᴅ: {e}")
    else:
        await update.message.reply_text(f"⚠️ ᴡᴀʀɴɪɴɢ {count}/{limit} ɪꜱꜱᴜᴇᴅ ᴛᴏ {user_name}!\n{'⚠️'*count}{'▪️'*(limit-count)}")

@sudo_only
async def warnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return await update.message.reply_text("ɢʀᴏᴜᴘꜱ ᴏɴʟʏ!")
    user_id = None
    user_name = 'Unknown'
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        u = update.message.reply_to_message.from_user
        user_id, user_name = u.id, u.first_name or str(u.id)
    elif context.args:
        try:
            user_id = int(context.args[0])
            user_name = str(user_id)
        except ValueError:
            return await update.message.reply_text("ᴜꜱᴀɢᴇ: /warnings (ʀᴇᴘʟʏ) ᴏʀ /warnings <ᴜꜱᴇʀ_ɪᴅ>")
    if not user_id:
        return await update.message.reply_text("ᴜꜱᴀɢᴇ: /warnings (ʀᴇᴘʟʏ) ᴏʀ /warnings <ᴜꜱᴇʀ_ɪᴅ>")
    cid = chat.id
    limit = warn_limits.get(cid, 3)
    count = warn_counts.get(cid, {}).get(user_id, 0)
    await update.message.reply_text(f"⚠️ {user_name} has {count}/{limit} ᴡᴀʀɴɪɴɢꜱ\n{'⚠️'*count}{'▪️'*(limit-count)}")

@sudo_only
async def clearwarns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return await update.message.reply_text("ɢʀᴏᴜᴘꜱ ᴏɴʟʏ!")
    cid = chat.id
    user_id = None
    user_name = 'chat'
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        u = update.message.reply_to_message.from_user
        user_id, user_name = u.id, u.first_name or str(u.id)
    elif context.args:
        try:
            user_id = int(context.args[0])
            user_name = str(user_id)
        except Exception:
            pass
    if user_id:
        warn_counts.get(cid, {}).pop(user_id, None)
        await update.message.reply_text(f"✅ ᴡᴀʀɴɪɴɢꜱ ᴄʟᴇᴀʀᴇᴅ ꜰᴏʀ {user_name}!")
    else:
        warn_counts.pop(cid, None)
        await update.message.reply_text("✅ ᴀʟʟ ᴡᴀʀɴɪɴɢꜱ ᴄʟᴇᴀʀᴇᴅ ɪɴ ᴛʜɪꜱ ᴄʜᴀᴛ!")

@sudo_only
async def setwarnlimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return await update.message.reply_text("ɢʀᴏᴜᴘꜱ ᴏɴʟʏ!")
    if not context.args:
        return await update.message.reply_text("ᴜꜱᴀɢᴇ: /setwarnlimit <number>")
    try:
        limit = int(context.args[0])
        assert 1 <= limit <= 20
    except (ValueError, AssertionError):
        return await update.message.reply_text("ʟɪᴍɪᴛ ᴍᴜꜱᴛ ʙᴇ ᴀ ɴᴜᴍʙᴇʀ ʙᴇᴛᴡᴇᴇɴ 1 ᴀɴᴅ 20.")
    warn_limits[chat.id] = limit
    await update.message.reply_text(f"✅ ᴡᴀʀɴ ʟɪᴍɪᴛ ꜱᴇᴛ ᴛᴏ {limit}.")

# ===========================================================
# DELETE / PURGE
# ===========================================================
@sudo_only
async def del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        return await update.message.reply_text("ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇꜱꜱᴀɢᴇ ᴡɪᴛʜ /del ᴛᴏ ᴅᴇʟᴇᴛᴇ ɪᴛ!")
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.reply_to_message.message_id)
        try:
            await update.message.delete()
        except Exception:
            pass
    except Exception as e:
        await update.message.reply_text(f"❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴅᴇʟᴇᴛᴇ: {e}")

@sudo_only
async def purgeoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop all auto-delete / delall loops in this chat."""
    chat_id = update.message.chat_id
    stopped = []
    for task_dict, name in [
        (deluser_tasks,  "deluser"),
        (del_all_tasks,  "delall"),
        (delete_tasks,   "deleteallhistory"),
    ]:
        if chat_id in task_dict:
            _cancel_tasks(task_dict, chat_id)
            stopped.append(name)
    if stopped:
        await update.message.reply_text(f"🛑 ᴘᴜʀɢᴇ ᴏꜰꜰ! ꜱᴛᴏᴘᴘᴇᴅ: {', '.join(stopped)}")
    else:
        await update.message.reply_text("ℹ️ᴋᴏɪ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ/ᴘᴜʀɢᴇ ᴄʜᴀʟ ɴᴀʜɪ ʀᴀʜᴀ ᴛʜᴀ.")

@sudo_only
async def purge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return await update.message.reply_text("ɢʀᴏᴜᴘꜱ ᴏɴʟʏ!")
    try:
        count = int(context.args[0]) if context.args else 10
    except ValueError:
        count = 10
    count = min(max(count, 1), 500)
    start_id = update.message.message_id
    deleted = 0
    status = await update.message.reply_text(f"🗑️ ᴘᴜʀɢɪɴɢ ᴜᴘ ᴛᴏ. {count} ᴍᴇꜱꜱᴀɢᴇꜱ...")
    for msg_id in range(start_id, start_id - count - 2, -1):
        try:
            await context.bot.delete_message(chat_id=chat.id, message_id=msg_id)
            deleted += 1
            await asyncio.sleep(0.02)
        except Exception:
            pass
    try:
        await status.edit_text(f"✅ ᴘᴜʀɢᴇᴅ {deleted} ᴍᴇꜱꜱᴀɢᴇꜱ!")
    except Exception:
        pass

# ===========================================================
# REFRESH COMMAND — restart all bot polling instantly (parallel)
# ===========================================================
@owner_only
async def refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 ʀᴇꜰʀᴇꜱʜɪɴɢ ᴀʟʟ ʙᴏᴛꜱ (ᴘᴀʀᴀʟʟᴇʟ)...")

    async def _restart_one(app):
        try:
            await app.updater.stop()
            await asyncio.sleep(0)
            await app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=["message", "callback_query"],
                poll_interval=0.0,
                timeout=10,
            )
        except Exception:
            pass

    await asyncio.gather(*[_restart_one(app) for app in apps])
    await update.message.reply_text(f"✅ {len(apps)} ʙᴏᴛꜱ ʀᴇꜰʀᴇꜱʜᴇᴅ ɪɴꜱᴛᴀɴᴛʟʏ!")

# ===========================================================
# PIC SPAM — spam a saved photo by number
# ===========================================================
@sudo_only
async def picspam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if not context.args:
        total = len(chat_photos.get(chat_id, []))
        return await update.message.reply_text(
            f"❌ ᴜꜱᴀɢᴇ: /picspam <ᴘʜᴏᴛᴏ_ɴᴜᴍʙᴇʀ>\n"
            f"📸 ꜱᴀᴠᴇᴅ ᴘʜᴏᴛᴏꜱ: {total} (ᴜꜱᴇ /ʟɪꜱᴛᴘʜᴏᴛᴏ ᴛᴏ ꜱᴇᴇ ʟɪꜱᴛ)"
        )
    try:
        num = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("❌ ɢɪᴠᴇ ɴᴜᴍʙᴇʀ: /picspam 1")
    photos = chat_photos.get(chat_id, [])
    if not photos:
        return await update.message.reply_text("❌ ɴᴏ ᴘʜᴏᴛᴏ ꜱᴀᴠᴇᴅ ; ᴜꜱᴇ /savephoto ᴛᴏ ꜱᴀᴠᴇ.")
    if num < 1 or num > len(photos):
        return await update.message.reply_text(f"❌ ᴘʜᴏᴛᴏ #{num} ɴᴀʜɪ ʜᴀɪ! ʀᴀɴɢᴇ: 1 ᴛᴏ {len(photos)}")
    file_id = photos[num - 1]
    _cancel_tasks(pic_spam_tasks, chat_id)

    async def _pic_loop(bot):
        while True:
            try:
                await bot.send_photo(chat_id=chat_id, photo=file_id)
                await asyncio.sleep(0.02)
            except asyncio.CancelledError:
                break
            except RetryAfter as e:
                await asyncio.sleep(float(e.retry_after) + 0.01)
            except Exception:
                await asyncio.sleep(0.05)

    pic_spam_tasks[chat_id] = [asyncio.create_task(_pic_loop(b)) for b in bots]
    await update.message.reply_text(
        f"📸 ᴘʜᴏᴛᴏ #{num} ꜱᴘᴀᴍ ꜱᴛᴀʀᴛᴇᴅ!\n"
        f"🤖 {len(bots)} ʙᴏᴛ ᴀʀᴇ ꜱᴘᴀᴍᴍɪɴɢɴ\n"
        f"🛑 ꜰᴏʀ ꜱᴛᴏᴘ: /stoppicspam"
    )

@sudo_only
async def stoppicspam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id in pic_spam_tasks:
        _cancel_tasks(pic_spam_tasks, chat_id)
        await update.message.reply_text("🛑 ᴘʜᴏᴛᴏ ꜱᴘᴀᴍ ʜᴀꜱ ʙᴇᴇɴ ꜱᴛᴏᴘᴘᴇᴅ!")
    else:
        await update.message.reply_text("❌ ɴᴏ ᴘʜᴏᴛᴏ ꜱᴘᴀᴍ ɪꜱ ᴀᴄᴛɪᴠᴇ.")

# ===========================================================
# BURN — report user to @SpamBot from all bots
# ===========================================================
@sudo_only
async def burn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        return await update.message.reply_text("❌ ʀᴇᴘʟʏ ᴛᴏ ᴀɴʏ ᴍᴇꜱꜱᴀɢᴇ ꜰᴏʀ ᴄᴏᴍᴍᴀɴᴅ /burn")
    target = update.message.reply_to_message.from_user
    target_msg = update.message.reply_to_message
    uid = target.id
    uname = f"@{target.username}" if target.username else target.first_name
    await update.message.reply_text(f"🔥 Burning {uname} ({uid})... ᴀʟʟ ʙᴏᴛꜱ ᴀʀᴇ ʀᴇᴘᴏʀᴛɪɴɢ!")
    success = 0
    for bot in bots:
        try:
            await bot.forward_message(
                chat_id='@SpamBot',
                from_chat_id=target_msg.chat_id,
                message_id=target_msg.message_id
            )
            success += 1
            await asyncio.sleep(0.02)
        except Exception:
            pass
    await update.message.reply_text(
        f"✅ Burn complete!\n"
        f"🔥 {success}/{len(bots)} ʙᴏᴛꜱ ʀᴇᴘᴏʀᴛᴇᴅ\n"
        f"👤 ᴛᴀʀɢᴇᴛ: {uname} ({uid})"
    )

# ===========================================================
# AI COMMAND
# ===========================================================
@sudo_only
async def ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args and not (update.message.reply_to_message and update.message.reply_to_message.text):
        return await update.message.reply_text("❌ ᴜꜱᴀɢᴇ: /ai <question>")
    user_text = " ".join(context.args) if context.args else update.message.reply_to_message.text
    if uid not in AI_HISTORY:
        AI_HISTORY[uid] = []
    AI_HISTORY[uid].append({'role': 'user', 'parts': [{'text': user_text}]})
    if len(AI_HISTORY[uid]) > 20:
        AI_HISTORY[uid] = AI_HISTORY[uid][-20:]
    thinking = await update.message.reply_text("🤔 Thinking...")
    reply = await gemini_ask(AI_HISTORY[uid])
    AI_HISTORY[uid].append({'role': 'model', 'parts': [{'text': reply}]})
    try:
        await thinking.edit_text(reply)
    except Exception:
        await update.message.reply_text(reply)

@sudo_only
async def clearai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    AI_HISTORY.pop(uid, None)
    await update.message.reply_text("✅ AI chat history cleared!")

# ===========================================================
# VOICE COMMAND
# ===========================================================
@sudo_only
async def voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not EDGE_TTS_AVAILABLE:
        return await update.message.reply_text("❌ edge_tts not installed. Run: pip install edge-tts")
    if not context.args:
        chars_list = "\n".join([f"{k}. {v[0]} {v[1]}" for k, v in ANIME_CHARACTERS.items()])
        return await update.message.reply_text(f"❌ ᴜꜱᴀɢᴇ: /voice <char_num> <ᴛᴇxᴛ>\n\nCharacters:\n{chars_list}")
    try:
        char_num = int(context.args[0])
        text = " ".join(context.args[1:])
    except (ValueError, IndexError):
        return await update.message.reply_text("ᴜꜱᴀɢᴇ: /voice <1-10> <ᴛᴇxᴛ>")
    if not text:
        return await update.message.reply_text("❌ Please provide text after the character number!")
    if char_num not in ANIME_CHARACTERS:
        return await update.message.reply_text(f"❌ Character {char_num} not found! Choose 1-{len(ANIME_CHARACTERS)}")
    char = ANIME_CHARACTERS[char_num]
    thinking = await update.message.reply_text(f"🎙️ Generating {char[0]} {char[1]} voice...")
    audio_data = await generate_voice_mp3(text, char_num)
    if not audio_data:
        return await thinking.edit_text("❌ Voice generation failed! edge-tts install karo: pip install edge-tts")
    try:
        caption = f"{char[0]} {char[1]}: {text[:50]}{'...' if len(text) > 50 else ''}"
        await update.message.reply_audio(audio=io.BytesIO(audio_data), filename="voice.mp3", caption=caption)
        await thinking.delete()
    except Exception as e:
        await thinking.edit_text(f"❌ Failed to send audio: {e}")

# ===========================================================
# CONTROL COMMANDS
# ===========================================================
@sudo_only
async def delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global GLOBAL_DELAY
    if not context.args:
        await update.message.reply_text(f"⏱ Current delay: {GLOBAL_DELAY:.6f}s\nᴜꜱᴀɢᴇ: /delay <ꜱᴇᴄᴏɴᴅꜱ> (ᴍɪɴ: 0.001, ɴᴏ ᴍᴀx ʟɪᴍɪᴛ)")
        return
    try:
        new_delay = float(context.args[0])
        if new_delay < 0.0001:
            await update.message.reply_text("❌ ᴅᴇʟᴀʏ ᴍɪɴɪᴍᴜᴍ ɪꜱ 0.0001ꜱ.")
            return
        GLOBAL_DELAY = new_delay
        await update.message.reply_text(
            f"✅ ᴅᴇʟᴀʏ ꜱᴇᴛ ᴛᴏ {GLOBAL_DELAY:.4f}s\n"
            f"⚡ ᴀᴘᴘʟɪᴇᴅ ᴛᴏ ᴀʟʟ ɴᴄꜱ, ꜱᴘᴀᴍ, ꜱʟɪᴅᴇꜱ!"
        )
    except ValueError:
        await update.message.reply_text("❌ ɪɴᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ.")

@sudo_only
async def hi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "╭──☣️ Tҽαɱ Sυρɾҽɱҽ ιʂ Aʅιʋҽ ☣️──╮\n"
        f"🤖 ʙᴏᴛꜱ 𝗔𝗰𝘁𝗶𝘃𝗲 : {len(bots)}\n"
        "⚡ 𝗠𝗮𝘅 𝗦𝗽𝗲𝗲𝗱 𝗠𝗼𝗱𝗲 🚀\n"
        "╰👑 ​🇸​​🇺​​🇵​​🇷​​🇪​​🇲​​🇪​ ​🇴​​🇸 v6.3.4.1──╯"
    )

# ===========================================================
# SUDO MANAGEMENT
# ===========================================================
@owner_only
async def addsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        return await update.message.reply_text("❌ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜꜱᴇʀ'ꜱ ᴍᴇꜱꜱᴀɢᴇ ᴛᴏ ᴀᴅᴅ ᴛʜᴇᴍ ᴀꜱ ᴄᴏʟʟᴀʙᴏʀᴀᴛᴏʀ!")
    target_user = update.message.reply_to_message.from_user
    uid = target_user.id
    username = target_user.username or target_user.first_name
    SUDO_USERS.add(uid)
    save_sudo()
    await update.message.reply_text(f"✅ ᴀᴅᴅᴇᴅ ᴄᴏʟʟᴀʙᴏʀᴀᴛᴏʀ ᴜꜱᴇʀ: {username} (ɪᴅ: {uid})")

@owner_only
async def delsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        return await update.message.reply_text("❌ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜꜱᴇʀ'ꜱ ᴍᴇꜱꜱᴀɢᴇ ᴛᴏ ʀᴇᴍᴏᴠᴇ ᴛʜᴇᴍ ғʀᴏᴍ ᴄᴏʟʟᴀʙᴏʀᴀᴛᴏʀ!")
    target_user = update.message.reply_to_message.from_user
    uid = target_user.id
    username = target_user.username or target_user.first_name
    if uid == OWNER_ID:
        return await update.message.reply_text("❌ ᴄᴀɴɴᴏᴛ ʀᴇᴍᴏᴠᴇ ᴛʜᴇ ᴏᴡɴᴇʀ ꜰʀᴏᴍ ᴄᴏʟʟᴀʙᴏʀᴀᴛᴏʀꜱ ʟɪꜱᴛ!")
    if uid in SUDO_USERS:
        SUDO_USERS.remove(uid)
        save_sudo()
        await update.message.reply_text(f"✅ ʀᴇᴍᴏᴠᴇᴅ ᴄᴏʟʟᴀʙᴏʀᴀᴛᴏʀ ᴜꜱᴇʀ: {username} (ɪᴅ: {uid})")
    else:
        await update.message.reply_text(f"❌ {username} ɪꜱ ɴᴏᴛ ɪɴ ᴛʜᴇ ᴄᴏʟʟᴀʙᴏʀᴀᴛᴏʀꜱ ʟɪꜱᴛ!")

@owner_only
async def sudos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not SUDO_USERS:
        return await update.message.reply_text("📋 ɴᴏ ᴄᴏʟʟᴀʙᴏʀᴀᴛᴏʀ ᴜꜱᴇʀꜱ ᴀᴅᴅᴇᴅ ʏᴇᴛ.")
    lines = [f"👑 *{uid}* (Owner)" if uid == OWNER_ID else f"🛡️ `{uid}`" for uid in SUDO_USERS]
    await update.message.reply_text(f"*📋 ᴄᴏʟʟᴀʙᴏʀᴀᴛᴏʀ ᴜꜱᴇʀꜱ ʟɪꜱᴛ*\n\n" + "\n".join(lines) + f"\n\n*Total:* {len(SUDO_USERS)}", parse_mode="Markdown")

# ===========================================================
# ADMIN MANAGEMENT
# ===========================================================
@sudo_only
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    promoter_bot = context.bot
    promoter_id = promoter_bot.id
    other_bots = [b for b in bots if b.id != promoter_id]
    if not other_bots:
        return await update.message.reply_text("❌ ɴᴏ ᴏᴛʜᴇʀ ʙᴏᴛꜱ ғᴏᴜɴᴅ ᴛᴏ ᴘʀᴏᴍᴏᴛᴇ!")
    permissions = {
        'can_change_info': True, 'can_post_messages': True, 'can_edit_messages': True,
        'can_delete_messages': True, 'can_invite_users': True, 'can_restrict_members': True,
        'can_pin_messages': True, 'can_promote_members': True, 'can_manage_video_chats': True,
        'can_manage_chat': True
    }
    promoted_count = 0
    status_msg = await update.message.reply_text("🔄 ᴘʀᴏᴍᴏᴛɪɴɢ ʙᴏᴛꜱ ᴛᴏ ᴀᴅᴍɪɴ...")
    for bot in other_bots:
        try:
            await promoter_bot.promote_chat_member(chat_id=chat_id, user_id=bot.id, **permissions)
            promoted_count += 1
            await asyncio.sleep(0.02)
        except Exception as e:
            pass  # silent — expected on permission errors
    if promoted_count > 0:
        await status_msg.edit_text(f"✅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴘʀᴏᴍᴏᴛᴇᴅ {promoted_count}ʙᴏᴛ(ꜱ) ᴛᴏ ᴀᴅᴍɪɴ!")
    else:
        await status_msg.edit_text("❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴘʀᴏᴍᴏᴛᴇ ᴀɴʏ ʙᴏᴛꜱ!\n\nᴍᴀᴋᴇ ꜱᴜʀᴇ ᴛʜᴇ ɪꜱꜱᴜɪɴɢ ʙᴏᴛ ʜᴀꜱ 'ᴀᴅᴅ ɴᴇᴡ ᴀᴅᴍɪɴꜱ' ᴘᴇʀᴍɪꜱꜱɪᴏɴ!")

@sudo_only
async def checkadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    status_msg = await update.message.reply_text("🔄 ᴄʜᴇᴄᴋɪɴɢ ʙᴏᴛ ᴀᴅᴍɪɴ ꜱᴛᴀᴛᴜꜱ...")
    admin_bots = []
    non_admin_bots = []
    for bot in bots:
        try:
            chat_member = await bot.get_chat_member(chat_id, bot.id)
            if chat_member.status in ['administrator', 'creator']:
                admin_bots.append(f"✅ {str(bot.id)[:10]}... - {chat_member.status}")
            else:
                non_admin_bots.append(f"❌ {str(bot.id)[:10]}... - {chat_member.status}")
        except Exception:
            non_admin_bots.append(f"⚠️ {str(bot.id)[:10]}... - ᴄᴀɴ'ᴛ ᴄʜᴇᴄᴋ")
    result = f"*📊 BOT ADMIN STATUS*\n\n"
    result += f"*Admins ({len(admin_bots)}):*\n" + "\n".join(admin_bots) if admin_bots else "ɴᴏ ᴀᴅᴍɪɴ ʙᴏᴛꜱ ꜰᴏᴜɴᴅ"
    result += f"\n\n*ɴᴏɴ-ᴀᴅᴍɪɴꜱ ({len(non_admin_bots)}):*\n" + "\n".join(non_admin_bots[:10])
    await status_msg.edit_text(result, parse_mode="Markdown")

# ===========================================================
# GCNC LOCK — /gcnclock / /gcncunlock (owner only)
# After /gcnclock: only OWNER can use NC in that GC.
# Anyone else who tries NC commands → gets muted instantly.
# ===========================================================
@owner_only
async def gcnclock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    ɢᴄ ɴᴄ ʟᴏᴄᴋ — ʀᴇᴀʟ ᴛᴇʟᴇɢʀᴀᴍ-ʟᴇᴠᴇʟ ʙʟᴏᴄᴋ.
    1. ᴀᴅᴅꜱ ᴄʜᴀᴛ ᴛᴏ ɢᴄɴᴄ_ʟᴏᴄᴋᴇᴅ (ʙʟᴏᴄᴋꜱ ᴏᴜʀ ɴᴄ ᴄᴏᴍᴍᴀɴᴅꜱ ꜰᴏʀ ᴏᴛʜᴇʀꜱ)
    2. ɢᴇᴛꜱ ᴀʟʟ ᴀᴅᴍɪɴꜱ ɪɴ ɢʀᴏᴜᴘ
    3. ꜱᴛʀɪᴘꜱ ᴄᴀɴ_ᴄʜᴀɴɢᴇ_ɪɴꜰᴏ ꜰʀᴏᴍ ᴇᴠᴇʀʏ ᴀᴅᴍɪɴ ᴡʜᴏ ɪꜱ ɴᴏᴛ ᴏᴜʀ ʙᴏᴛ
       → ᴛʜᴇɪʀ ʙᴏᴛꜱ ᴀʟꜱᴏ ᴄᴀɴɴᴏᴛ ᴄʜᴀɴɢᴇ ᴛɪᴛʟᴇ ᴠɪᴀ ᴀᴘɪ ᴀɴʏᴍᴏʀᴇ
    ᴀʟʟ ᴅᴇᴍᴏᴛɪᴏɴꜱ ʜᴀᴘᴘᴇɴ ɪɴ ᴘᴀʀᴀʟʟᴇʟ ꜰʀᴏᴍ ᴀʟʟ ᴏᴜʀ ʙᴏᴛꜱ ꜰᴏʀ ꜱᴘᴇᴇᴅ.
    """
    chat_id = update.message.chat_id
    gcnc_locked.add(chat_id)

    # collect our own bot IDs
    bot_ids = set()
    for b in bots:
        try:
            bot_ids.add(b.id)
        except Exception:
            pass

    status_msg = await update.message.reply_text(
        "╔══════════════════╗\n"
        "║  🔒 ɢᴄɴᴄ ʟᴏᴄᴋᴇᴅ 🔒  ║\n"
        "╚══════════════════╝\n"
        "🔍 ꜱᴀʙᴋᴇ ᴀᴅᴍɪɴ ᴘᴇʀᴍɪꜱꜱɪᴏɴꜱ ᴄʜᴇᴄᴋ ʜᴏ ʀᴀʜɪ ʜᴀɪɴ...\n"
        "⏳ ᴛʜᴏᴅᴀ ᴡᴀɪᴛ ᴋᴀʀᴏ..."
    )

    # fetch admin list
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
    except Exception as e:
        await status_msg.edit_text(f"❌ ᴀᴅᴍɪɴ ʟɪꜱᴛ ɴᴀʜɪ ᴍɪʟɪ: {e}")
        return

    demoted_ids = []
    strip_targets = []   # admins who have can_change_info and are NOT our bots / owner

    for admin in admins:
        uid = admin.user.id
        # skip owner, our own bots, and bots that can't change info anyway
        if uid == OWNER_ID or uid in bot_ids:
            continue
        # ChatMemberAdministrator has can_change_info attr
        can_change = getattr(admin, 'can_change_info', False)
        if not can_change:
            continue
        strip_targets.append(admin)

    # strip can_change_info from each target via ALL our bots in parallel
    async def _strip_one_admin(target_admin):
        uid = target_admin.user.id
        async def _do_strip(b):
            try:
                await b.promote_chat_member(
                    chat_id=chat_id,
                    user_id=uid,
                    can_change_info=False,
                    can_delete_messages=getattr(target_admin, 'can_delete_messages', False),
                    can_invite_users=getattr(target_admin, 'can_invite_users', False),
                    can_restrict_members=getattr(target_admin, 'can_restrict_members', False),
                    can_pin_messages=getattr(target_admin, 'can_pin_messages', False),
                    can_promote_members=getattr(target_admin, 'can_promote_members', False),
                    can_manage_chat=getattr(target_admin, 'can_manage_chat', False),
                    can_manage_video_chats=getattr(target_admin, 'can_manage_video_chats', False),
                )
                return True
            except Exception:
                return False
        results = await asyncio.gather(*[_do_strip(b) for b in bots])
        if any(results):
            demoted_ids.append(uid)

    if strip_targets:
        await asyncio.gather(*[_strip_one_admin(a) for a in strip_targets])

    # store demoted list so gcncunlock can restore
    gcnc_demoted[chat_id] = demoted_ids

    demote_text = f"☣️ {len(demoted_ids)} ᴘᴇʀᴍɪꜱꜱɪᴏɴ 'ᴄᴀɴ_ᴄʜᴀɴɢᴇ_ɪɴꜰᴏ' ɪꜱ ᴅᴇɴɪᴇᴅ ꜰᴏʀ ᴀᴅᴍɪɴ!" if demoted_ids else "ℹ️ ᴘᴇʀᴍɪꜱꜱɪᴏɴ 'ᴄᴀɴ_ᴄʜᴀɴɢᴇ_ɪɴꜰᴏ' ɪꜱ ɴᴏᴛ ᴀʟʟᴏᴡᴇᴅ ꜰᴏʀ ᴀᴅᴍɪɴ."
    await status_msg.edit_text(
        "╔══════════════════╗\n"
        "║  🔒 ɢᴄɴᴄ ʟᴏᴄᴋᴇᴅ 🔒  ║\n"
        "╚══════════════════╝\n"
        "✅ ᴏɴʟʏ ꜱᴜᴘʀᴇᴍᴇ ᴏꜱ ꜰᴇᴀᴛᴜʀᴇᴅ ʙᴏᴛ ᴄᴀɴ ᴍᴏᴅᴇʀᴀᴛᴇ ɪɴ ᴛʜɪꜱ ɢʀᴏᴜᴘ!\n"
        f"{demote_text}\n"
        "🚫 ᴄʜᴀɴɢɪɴɢ ᴛɪᴛʟᴇ ᴏꜰ ᴀɴᴏᴛʜᴇʀ ʙᴏᴛ ɪꜱ ʙʟᴏᴄᴋᴇᴅ ᴀɴᴅ ɪᴍᴘᴘᴏꜱꜱɪʙʟᴇ!\n"
        "🔓 ᴜɴʟᴏᴄᴋ: /gcncunlock\n"
        "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
    )

@owner_only
async def gcncunlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes gcnclock and restores can_change_info for previously demoted admins."""
    chat_id = update.message.chat_id
    gcnc_locked.discard(chat_id)

    # restore can_change_info for demoted admins
    prev_demoted = gcnc_demoted.pop(chat_id, [])

    if prev_demoted:
        status_msg = await update.message.reply_text(
            "╔══════════════════╗\n"
            "║  🔓 ɢᴄɴᴄ ᴜɴʟᴏᴄᴋᴇᴅ 🔓  ║\n"
            "╚══════════════════╝\n"
            f"🔄 {len(prev_demoted)} ᴘᴇʀᴍɪꜱꜱɪᴏɴ 'ᴄᴀɴ_ᴄʜᴀɴɢᴇ_ɪɴꜰᴏ' ɪꜱ ᴇɴᴀʙʟᴇᴅ ꜰᴏʀ ᴀᴅᴍɪɴꜱ...\n"
            "⏳ ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ..."
        )
        async def _restore_one(uid):
            async def _do_restore(b):
                try:
                    await b.promote_chat_member(
                        chat_id=chat_id,
                        user_id=uid,
                        can_change_info=True,
                    )
                except Exception:
                    pass
            await asyncio.gather(*[_do_restore(b) for b in bots])

        await asyncio.gather(*[_restore_one(uid) for uid in prev_demoted])
        await status_msg.edit_text(
            "╔══════════════════╗\n"
            "║  🔓 ɢcɴᴄ ᴜɴʟᴏᴄᴋᴇᴅ 🔓  ║\n"
            "╚══════════════════╝\n"
            "✅ ʟᴏᴄᴋ ɪꜱ ᴅɪꜱᴀʙʟᴇᴅ ! ᴀʟʟ ᴘᴇʀᴍɪꜱꜱᴏɴꜱ ᴀʀᴇ ʀᴇꜱᴛᴏʀᴇᴅ ꜰᴏʀ ᴇᴠᴇʀʏᴏɴᴇ!\n"
            "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
        )
    else:
        await update.message.reply_text(
            "╔══════════════════╗\n"
            "║  🔓 ɢcɴᴄ ᴜɴʟᴏᴄᴋᴇᴅ 🔓  ║\n"
            "╚══════════════════╝\n"
            "✅ ɢcɴᴄ ʟᴏᴄᴋ ʜᴀꜱ ʙᴇᴇɴ ʀᴇᴍᴏᴠᴇᴅ!\n"
            "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
        )

# ===========================================================
# LEAVE ALL BOTS
# ===========================================================
@sudo_only
async def leaveall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Use provided chat_id arg, else leave current chat
    if context.args:
        try:
            target_cid = int(context.args[0])
        except ValueError:
            return await update.message.reply_text("❌ ɢɪᴠᴇ ᴠᴀʟɪᴅ ᴄʜᴀᴛ ɪᴅ : /ʟᴇᴀᴠᴇᴀʟʟ <ᴄʜᴀᴛ_ɪᴅ>")
    else:
        target_cid = update.message.chat_id

    status_msg = await update.message.reply_text(
        "╔══════════════════╗\n"
        "║  🚪 ʟᴇᴀᴠᴇᴀʟʟ 🚪  ║\n"
        "╚══════════════════╝\n"
        f"🤖 {len(bots)}ʙᴏᴛꜱ ᴀʀᴇ ʟᴇᴀᴠɪɴɢ...\n"
        f"🏠 ᴄʜᴀᴛ: {target_cid}"
    )
    left = 0
    failed = 0
    for bot in bots:
        try:
            await bot.leave_chat(chat_id=target_cid)
            left += 1
            await asyncio.sleep(0.02)
        except Exception as e:
            failed += 1
            pass  # silent — expected on permission errors
    try:
        await status_msg.edit_text(
            "╔══════════════════╗\n"
            "║  ✅ ʟᴇᴀᴠᴇᴀʟʟ ᴅᴏɴᴇ ✅  ║\n"
            "╚══════════════════╝\n"
            f"🚪 ʟᴇꜰᴛ: {left} ʙᴏᴛꜱ\n"
            f"❌ ꜰᴀɪʟᴇᴅ: {failed} ʙᴏᴛꜱ\n"
            "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
        )
    except Exception:
        pass

# ===========================================================
# AUTO REPLY (slide2 style)
# ===========================================================
@sudo_only
async def autoreply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        return await update.message.reply_text(
            "❌ ʀᴇᴘʟʏ ᴛʜᴇ ᴍᴇꜱꜱᴀɢᴇ ᴏꜰ ᴀɴʏ ᴜꜱᴇʀ:\n"
            "📌 /autoreply\n"
            "⚡ ᴛʜᴇɴ ᴇᴠᴇʀʏ ʙᴏᴛ ᴡɪʟʟ ʀᴇᴘʟʏ ᴡɪᴛʜ ꜱʟɪᴅᴇ2 ᴛᴏ ᴛᴀʀɢᴇᴛ ᴜꜱᴇʀ!"
        )
    cid = update.message.chat_id
    target_user = update.message.reply_to_message.from_user
    uid = target_user.id
    uname = target_user.first_name or str(uid)

    if cid not in autoreply_users:
        autoreply_users[cid] = {}
    if cid not in autoreply_slide2_idx:
        autoreply_slide2_idx[cid] = {}

    autoreply_users[cid][uid] = True
    autoreply_slide2_idx[cid][uid] = 0

    await update.message.reply_text(
        "╔══════════════════╗\n"
        "║  🤖 ᴀᴜᴛᴏʀᴇᴘʟʏ ᴏɴ 🤖  ║\n"
        "╚══════════════════╝\n"
        f"🎯 ᴛᴀʀɢᴇᴛ: {uname} (ɪᴅ: {uid})\n"
        f"💬 ᴡʜᴇɴᴇᴠᴇʀ ᴜꜱᴇʀ ᴡɪʟʟ ᴍᴇꜱꜱᴀɢᴇ - ʙᴏᴛꜱ ᴡɪʟʟ ʀᴘᴇʟʏ ᴡɪᴛʜ ꜱʟɪᴅᴇ2!\n"
        "🛑 ꜰᴏʀ ꜱᴛᴏᴘ: /stopautoreply\n"
        "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
    )

@sudo_only
async def stopautoreply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.message.chat_id
    # If replied to specific user, stop only that user
    if update.message.reply_to_message:
        uid = update.message.reply_to_message.from_user.id
        uname = update.message.reply_to_message.from_user.first_name or str(uid)
        if cid in autoreply_users and uid in autoreply_users[cid]:
            del autoreply_users[cid][uid]
            if cid in autoreply_slide2_idx:
                autoreply_slide2_idx[cid].pop(uid, None)
            return await update.message.reply_text(f"✅ ᴀᴜᴛᴏʀᴇᴘʟʏ ᴅɪꜱᴀʙʟᴇᴅ: {uname}")
        return await update.message.reply_text("❌ ᴀᴜᴛᴏʀᴇᴘʟʏ ᴡᴀꜱ ɴᴏᴛ ᴀᴄᴛɪᴠᴇ ꜰᴏʀ ᴛʜɪꜱ ᴜꜱᴇʀ!")
    # Stop all autoreply in this chat
    if cid in autoreply_users:
        autoreply_users.pop(cid, None)
        autoreply_slide2_idx.pop(cid, None)
        await update.message.reply_text(
            "╔══════════════════╗\n"
            "║  🛑 ᴀᴜᴛᴏʀᴇᴘʟʏ ᴏꜰꜰ 🛑  ║\n"
            "╚══════════════════╝\n"
            "✅ ᴀʟʟ ᴀᴜᴛᴏʀᴇᴘʟʏ ɪꜱ ᴅɪꜱᴀʙʟᴇᴅ ꜰᴏʀ ᴛʜɪꜱ ᴄʜᴀᴛ!\n"
            "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️"
        )
    else:
        await update.message.reply_text("❌ᴀɴʏ ᴀᴜᴛᴏʀᴇᴘʟʏ ᴡᴀꜱ ɴᴏᴛ ᴀᴄᴛɪᴠᴇ ꜰᴏʀ ᴛʜɪꜱ ᴄʜᴀᴛ!")

# ===========================================================
# ADD ALL RUNNING BOTS — lead bot + OWNER only
# ===========================================================
ADDALLBOTS_BATCH_SIZE = 8
BOT_ADMIN_DEEP_LINK_RIGHTS = "+".join((
    "change_info", "delete_messages", "restrict_members", "invite_users",
    "pin_messages", "promote_members", "manage_video_chats", "manage_chat",
))


def _running_bot_add_buttons():
    """Build one official add-to-group button for each polling bot."""
    buttons = []
    seen_bot_ids = set()
    for app in apps:
        try:
            if not app.running or not app.updater or not app.updater.running:
                continue
            bot = app.bot
            bot_id = bot.id
            username = bot.username
        except (AttributeError, RuntimeError):
            continue

        if not username or bot_id in seen_bot_ids:
            continue
        seen_bot_ids.add(bot_id)
        username = username.lstrip('@')
        add_url = (
            f"https://t.me/{username}?startgroup&admin="
            f"{BOT_ADMIN_DEEP_LINK_RIGHTS}"
        )
        buttons.append(InlineKeyboardButton(
            text=f"➕ @{username} ko admin add karein",
            url=add_url,
        ))
    return buttons


@lead_bot_only
@owner_only
async def addallbots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send batched admin add-to-group links for every running bot."""
    buttons = _running_bot_add_buttons()
    if not buttons:
        await update.message.reply_text(
            "❌ ᴀᴛ ᴛʜɪꜱ ᴛɪᴍᴇ , ɴᴏ ʀᴜɴɴɪɴɢ ʙᴏᴛ ɪꜱ ᴀᴠᴀɪʟᴀʙʟᴇ ᴛᴏ ᴀᴅᴅ."
        )
        return

    total_batches = (len(buttons) + ADDALLBOTS_BATCH_SIZE - 1) // ADDALLBOTS_BATCH_SIZE
    for offset in range(0, len(buttons), ADDALLBOTS_BATCH_SIZE):
        batch = buttons[offset:offset + ADDALLBOTS_BATCH_SIZE]
        batch_number = offset // ADDALLBOTS_BATCH_SIZE + 1
        keyboard = InlineKeyboardMarkup([[button] for button in batch])
        await update.message.reply_text(
            "🤖 *ᴀᴅᴅ ʀᴜɴɴɪɴɢ ʙᴏᴛꜱ ɪɴ ɢʀᴏᴜᴘ ᴄʜᴀᴛ*\n\n"
            "ᴛᴇʟᴇɢʀᴀᴍ ʙᴏᴛ ᴀᴘɪ ɪꜱ ɴᴏᴛ ᴀʟʟᴏᴡɪɴɢ ʙᴏᴛ ᴛᴏ ᴊᴏɪɴ ᴏʀ ᴀᴄᴄᴇᴘᴛ ɢʀᴏᴜᴘ ʙʏ ɪɴᴠɪᴛᴇ ʟɪɴᴋ"
            "ᴛᴀᴘ ʙᴜᴛᴛᴏɴꜱ ᴀɴᴅ ᴄʜᴏᴏꜱᴇ ɢʀᴏᴜᴘꜱ "
            "ᴄᴏɴꜰɪʀᴍ ᴀᴅᴍɪɴꜱ ᴘᴇʀᴍɪꜱꜱɪᴏɴ - ᴍᴀɴᴅᴀɴᴛᴏʀʏ.\n\n"
            f"📦 ʙᴀᴛᴄʜ {batch_number}/{total_batches} • "
            f"ʙᴏᴛꜱ {offset + 1}-{offset + len(batch)} ᴏꜰ {len(buttons)}",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


# Backwards-compatible command/callback name.  Both command aliases are
# registered only on the lead bot in ``build_app``.
allbotadd = addallbots

# ===========================================================
# REACT LOOP
# ===========================================================
async def react_loop(bot, chat_id, msg_id):
    i = 0
    while True:
        try:
            emoji = REACT_EMOJIS_ALL[i % len(REACT_EMOJIS_ALL)]
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=msg_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
                is_big=True
            )
            i += 1
            await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            break
        except RetryAfter as e:
            await asyncio.sleep(float(e.retry_after) + 0.01)
        except (TimedOut, NetworkError):
            await asyncio.sleep(0.05)
        except Exception:
            i += 1
            await asyncio.sleep(0.05)

@sudo_only
async def react(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        return await update.message.reply_text("❌ ʀᴇᴘʟʏ ᴀɴʏ ᴍᴇꜱꜱᴀɢᴇ ᴡɪᴛʜ /react ᴄᴏᴍᴍᴀɴᴅ!")
    chat_id = update.message.chat_id
    msg_id = update.message.reply_to_message.message_id
    _cancel_tasks(react_tasks, chat_id)
    react_tasks[chat_id] = [asyncio.create_task(react_loop(b, chat_id, msg_id)) for b in bots]
    await update.message.reply_text(f"✅ ʀᴇᴀᴄᴛ ʟᴏᴏᴘ ꜱᴛᴀʀᴛᴇᴅ ᴏɴ ᴍᴇꜱꜱᴀɢᴇ {msg_id}!\n⚡ {len(bots)} ʙᴏᴛꜱ ʀᴇᴀᴄᴛɪɴɢ!")

@sudo_only
async def stopreact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    _cancel_tasks(react_tasks, chat_id)
    await update.message.reply_text("🛑 ʀᴇᴀᴄᴛ ʟᴏᴏᴘ sᴛᴏᴘᴘᴇᴅ!")

# ===========================================================
# REACTALL — sabke ᴍᴇꜱꜱᴀɢᴇꜱ pe auto react
# ===========================================================
@sudo_only
async def reactall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    emoji = context.args[0] if context.args else "🔥"
    reactall_chats[chat_id] = emoji
    await update.message.reply_text(f"✅ ʀᴇᴀᴄᴛᴀʟʟ ᴏɴ!\n⚡ ꜱᴀʙᴋᴇ ᴍᴇꜱꜱᴀɢᴇꜱ ᴘᴇ ʀᴇᴀᴄᴛ ʜᴏɢᴀ: {emoji}")

@sudo_only
async def stopreactall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    reactall_chats.pop(chat_id, None)
    await update.message.reply_text("🛑 ʀᴇᴀᴄᴛᴀʟʟ sᴛᴏᴘᴘᴇᴅ!")

# ===========================================================
# REACTUSER — specific user ke ᴍᴇꜱꜱᴀɢᴇꜱ pe auto react
# ===========================================================
@sudo_only
async def reactuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = None
    user_name = "Unknown"
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        u = update.message.reply_to_message.from_user
        user_id = u.id
        user_name = u.first_name or str(u.id)
    elif context.args and context.args[0].lstrip('-').isdigit():
        user_id = int(context.args[0])
        user_name = str(user_id)
        context.args = context.args[1:]
    if not user_id:
        return await update.message.reply_text("❌ ʀᴇᴘʟʏ ᴀɴʏ ᴍᴇꜱꜱᴀɢᴇ ᴡɪᴛʜ /reactuser <ᴇᴍᴏᴊɪ>\nᴏʀ: /reactuser <ᴜꜱᴇʀ_ɪᴅ> <ᴇᴍᴏᴊɪ>")
    emoji = context.args[0] if context.args else "🔥"
    if chat_id not in reactuser_chats:
        reactuser_chats[chat_id] = {}
    reactuser_chats[chat_id][user_id] = emoji
    await update.message.reply_text(f"✅ ʀᴇᴀᴄᴛᴜꜱᴇʀ ᴏɴ!\n👤 ᴜꜱᴇʀ: {user_name}\n⚡ ʀᴇᴀᴄᴛ ᴇᴍᴏᴊɪ: {emoji}")

@sudo_only
async def stopreactuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        user_id = update.message.reply_to_message.from_user.id
    elif context.args and context.args[0].lstrip('-').isdigit():
        user_id = int(context.args[0])
    if user_id:
        if chat_id in reactuser_chats:
            reactuser_chats[chat_id].pop(user_id, None)
            if not reactuser_chats[chat_id]:
                del reactuser_chats[chat_id]
        await update.message.reply_text(f"🛑 ʀᴇᴀᴄᴛᴜꜱᴇʀ sᴛᴏᴘᴘᴇᴅ ꜰᴏʀ {user_id}!")
    else:
        reactuser_chats.pop(chat_id, None)
        await update.message.reply_text("🛑 ʀᴇᴀᴄᴛᴜꜱᴇʀ sᴛᴏᴘᴘᴇᴅ ꜰᴏʀ ᴇᴠᴇʀʏᴏɴᴇ!")

# ===========================================================
# BYE
# ===========================================================
@sudo_only
async def bye(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    try:
        await update.message.delete()
    except Exception:
        pass
    for bot in bots:
        try:
            await bot.leave_chat(chat_id)
            await asyncio.sleep(0.02)
        except Exception as e:
            pass  # silent — expected on permission errors

# ===========================================================
# PING
# ===========================================================
@sudo_only
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import time
    t1 = time.monotonic()
    msg = await update.message.reply_text("🏓 Pinging...")
    t2 = time.monotonic()
    latency_ms = round((t2 - t1) * 1000, 2)
    await msg.edit_text(
        f"╔══════════════════╗\n"
        f"║  🏓 ᴘɪɴɢ ʀᴇꜱᴜʟᴛ  ║\n"
        f"╚══════════════════╝\n"
        f"⚡ ʟᴀᴛᴇɴᴄʏ: {latency_ms} ms\n"
        f"🤖 ʙᴏᴛꜱ ᴀᴄᴛɪᴠᴇ: {len(bots)}\n"
        f"☣️ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ᴏɴʟɪɴᴇ ✅"
    )

# ===========================================================
# UPTIME COMMAND
# ===========================================================
@sudo_only
async def uptime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    delta = datetime.now() - BOT_START_TIME
    days    = delta.days
    hours   = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    seconds = delta.seconds % 60
    await update.message.reply_text(
        "╔══════════════════════╗\n"
        "║  ⏱️ ᴜᴘᴛɪᴍᴇ ʀᴇᴘᴏʀᴛ ⏱️  ║\n"
        "╚══════════════════════╝\n"
        f"📅 ᴅᴀʏs  : {days}d\n"
        f"🕐 ᴛɪᴍᴇ  : {hours:02d}h {minutes:02d}m {seconds:02d}s\n"
        f"🤖 ʙᴏᴛꜱ  : {len(bots)} ᴀᴄᴛɪᴠᴇ\n"
        f"⚡ ᴅᴇʟᴀʏ : {GLOBAL_DELAY:.4f}s\n"
        "☣️ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ᴏꜱ ✅"
    )

# ===========================================================
# STATS COMMAND — total active tasks across all chats
# ===========================================================
@sudo_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nc_count    = sum(len(v) if isinstance(v, list) else 1 for v in nc_tasks.values())
    spam_count  = sum(len(v) if isinstance(v, list) else 1 for v in spam_tasks.values())
    slide_count = sum(len(v) if isinstance(v, list) else 1 for v in slider_tasks.values())
    react_count = sum(len(v) if isinstance(v, list) else 1 for v in react_tasks.values())
    photo_count = sum(len(v) if isinstance(v, list) else 1 for v in photo_tasks.values())
    total = nc_count + spam_count + slide_count + react_count + photo_count
    delta = datetime.now() - BOT_START_TIME
    await update.message.reply_text(
        "╔═══════════════════════╗\n"
        "║  📊 ɢʟᴏʙᴀʟ ꜱᴛᴀᴛꜱ 📊  ║\n"
        "╚═══════════════════════╝\n"
        f"🤖 ʙᴏᴛꜱ ᴀᴄᴛɪᴠᴇ     : {len(bots)}\n"
        f"☣️ ɴᴄ ᴛᴀꜱᴋꜱ          : {nc_count}\n"
        f"💥 ꜱᴘᴀᴍ ᴛᴀꜱᴋꜱ        : {spam_count}\n"
        f"🌀 ꜱʟɪᴅᴇ ᴛᴀꜱᴋꜱ       : {slide_count}\n"
        f"😈 ʀᴇᴀᴄᴛ ᴛᴀꜱᴋꜱ       : {react_count}\n"
        f"📸 ᴘɪᴄꜱ ᴛᴀꜱᴋꜱ       : {photo_count}\n"
        f"🔥 ᴛᴏᴛᴀʟ ᴛᴀꜱᴋꜱ        : {total}\n"
        f"⏱️ ᴜᴘᴛɪᴍᴇ             : {delta.days}d {delta.seconds//3600}h\n"
        f"👑 ꜱᴜᴅᴏ ᴜꜱᴇʀꜱ         : {len(SUDO_USERS)}\n"
        "⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ 𝗦𝗰𝗿𝗶𝗽𝐭 ☣️"
    )

# ===========================================================
# GOD MODE — keepalive for all bots
# ===========================================================
async def _god_mode_keepalive():
    while GOD_MODE:
        # Fire all get_me() in parallel — don't stall on slow bots
        await asyncio.gather(*[b.get_me() for b in bots], return_exceptions=True)
        await asyncio.sleep(30)

@owner_only
async def godmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global GOD_MODE, god_mode_task
    if GOD_MODE:
        await update.message.reply_text(
            "⚡ ɢᴏᴅ ᴍᴏᴅᴇ ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴀᴄᴛɪᴠᴇ!\n"
            "☣️ /stopgodmode ꜰᴏʀ ꜱᴛᴏᴘ."
        )
        return
    GOD_MODE = True
    god_mode_task = asyncio.create_task(_god_mode_keepalive())
    await update.message.reply_text(
        "╔══════════════════╗\n"
        "║  ☣️ ɢᴏᴅ ᴍᴏᴅᴇ ᴏɴ  ║\n"
        "╚══════════════════╝\n"
        f"💀 ᴀʟʟ {len(bots)} ʙᴏᴛꜱ ᴡɪʟʟ ʙᴇ ᴀʟɪᴠᴇ!\n"
        "⚡ ɪɴ ᴇᴠᴇʀʏ 30 ꜱᴇᴄᴏɴᴅꜱ ᴀᴜᴛᴏ-ᴘɪɴɢᴡɪʟʟ ʙᴇ ᴛʀɪɢɢɢᴇʀ ☣️"
    )

@owner_only
async def stopgodmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global GOD_MODE, god_mode_task
    GOD_MODE = False
    if god_mode_task:
        god_mode_task.cancel()
        god_mode_task = None
    await update.message.reply_text("🛑 ɢᴏᴅ ᴍᴏᴅᴇ ᴅɪꜱᴀʙʟᴇᴅ!")

# ===========================================================
# BOT STATUS
# ===========================================================
@sudo_only
async def botstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    lines = [
        "╔══════════════════════╗",
        "║  ⚙️ ꜱᴜᴘʀᴇᴍᴇ ꜱᴛᴀᴛᴜꜱ ║",
        "╚══════════════════════╝",
        f"🤖 ᴛᴏᴛᴀʟ ʙᴏᴛꜱ: {len(bots)}",
        f"⏱ ᴅᴇʟᴀʏ: {GLOBAL_DELAY:.4f}s",
        f"☣️ ɢᴏᴅ ᴍᴏᴅᴇ: {'🟢 ᴏɴ' if GOD_MODE else '🔴 ᴏꜰꜰ'}",
        "",
        "📋 ᴀᴄᴛɪᴠᴇ ᴛᴀꜱᴋꜱ (ᴛʜɪꜱ ᴄʜᴀᴛ):",
    ]
    task_dicts = {
        "NC": nc_tasks,
        "Spam": spam_tasks,
        "Slide": slider_tasks,
        "React": react_tasks,
        "Photo": photo_tasks,
        "GC Photo": gc_photo_tasks,
    }
    any_task = False
    for name, td in task_dicts.items():
        tasks = td.get(chat_id, [])
        if isinstance(tasks, list) and tasks:
            any_task = True
            lines.append(f"  ✅ {name}: {len(tasks)} task(s)")
        elif not isinstance(tasks, list) and tasks:
            any_task = True
            lines.append(f"  ✅ {name}: active")
    if not any_task:
        lines.append("  💤 ɴᴏ ᴛᴀꜱᴋ ɪɴ ᴀᴄᴛɪᴠᴇ ɪɴ ᴛʜɪꜱ ᴄʜᴀᴛ")
    if chat_id in reactall_chats:
        lines.append(f"  ✅ ʀᴇᴀᴄᴛᴀʟʟ: {reactall_chats[chat_id]}")
    if chat_id in reactuser_chats:
        lines.append(f"  ✅ ʀᴇᴀᴄᴛᴜꜱᴇʀ: {len(reactuser_chats[chat_id])} user(s)")
    lines.append("\n⚡ ᴛᴇᴀᴍ ꜱᴜᴘʀᴇᴍᴇ ☣️")
    await update.message.reply_text("\n".join(lines))

# ===========================================================
# PFPSWIPE — reply to photo → GC pfp loop
# ===========================================================
@sudo_only
async def pfpswipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    photo = None
    if update.message.reply_to_message:
        r = update.message.reply_to_message
        if r.photo:
            photo = r.photo[-1]
        elif r.document and r.document.mime_type and r.document.mime_type.startswith("image/"):
            photo = r.document
    if not photo:
        await update.message.reply_text("❌ ʀᴇᴘʟʏ ᴀɴʏ ᴘʜᴏᴛᴏ: /pfpswipe")
        return
    file = await context.bot.get_file(photo.file_id)
    import io
    photo_bytes = await file.download_as_bytearray()
    _cancel_tasks(gc_photo_tasks, chat_id)
    _pfp_bytes = bytes(photo_bytes)  # convert bytearray → bytes once
    async def pfp_loop():
        while True:
            for b in bots:
                try:
                    await b.set_chat_photo(chat_id=chat_id, photo=io.BytesIO(_pfp_bytes))
                    await asyncio.sleep(0.02)
                except asyncio.CancelledError:
                    return
                except RetryAfter as e:
                    await asyncio.sleep(float(e.retry_after) + 0.01)
                except (TimedOut, NetworkError):
                    await asyncio.sleep(0.05)
                except Exception:
                    await asyncio.sleep(0.05)
    task = asyncio.create_task(pfp_loop())
    gc_photo_tasks[chat_id] = [task]
    await update.message.reply_text(
        "╔══════════════════╗\n"
        "║  📸 ᴘꜰᴘ ꜱᴡɪᴘᴇ ᴏɴ  ║\n"
        "╚══════════════════╝\n"
        f"🤖 {len(bots)} ʙᴏᴛꜱ ᴀʀᴇ ᴄʜᴀɴɢɪɴɢ ᴘꜰᴘ ᴏꜰ ɢʀᴏᴜᴘ ☣️\n"
        "🛑 /stopgc ꜰᴏʀ ꜱᴛᴏᴘ"
    )

# ===========================================================
# HELP / START
# ===========================================================
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bot_id = _context_bot_id(context)
    on_lead_bot = _is_lead_bot(context)
    if is_owner_or_sudo(uid, bot_id):
        visible_bot_count = len(bots) if _is_global_operator(uid) else 1
        delta = datetime.now() - BOT_START_TIME
        uptime_str = f"{delta.days}d {delta.seconds//3600}h {(delta.seconds%3600)//60}m"
        lead_help_text = ""
        if on_lead_bot:
            addallbots_help = (
                "│ 🤖 /addallbots  (alias /allbotadd) │\n"
                if uid == OWNER_ID else ""
            )
            lead_help_text = (
                "╭─⭐ ᴏꜱ ʙᴏᴛ ᴏɴʟʏ ─────────╮\n"
                "│ 🧬 /clone <ᴛᴏᴋᴇɴ>              │\n"
                "│ 🪞 /mirror <ᴛᴏᴋᴇɴ>             │\n"
                f"{addallbots_help}"
                "╰──────────────────────────╯\n"
            )
        help_text = (
            "┏━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            "┃  👑 ꜱᴜᴘʀᴇᴍᴇ ᴏꜱ 👑  ┃\n"
            "┃  ⚡ ᴘʀᴇᴍɪᴜᴍ ᴇᴅɪᴛɪᴏɴ ᴠ6.9.2 ⚡  ┃\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
            f"🤖 ʙᴏᴛꜱ: {visible_bot_count}  ⏱️ ᴜᴘ: {uptime_str}  ⚡ ᴅᴇʟᴀʏ: {GLOBAL_DELAY:.3f}s\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "╭─🌐 ʟᴀɴɢᴜᴀɢᴇ ɴᴄ ──────────╮\n"
            "│ 🩸 /hindinc   🔥 /biharinc  │\n"
            "│ ☠️ /urdunc    🌊 /engnc     │\n"
            "│ 🎌 /bengalnc  🇨🇳 /chinesenc │\n"
            "│ ⚔️ /goodncraid               │\n"
            "╰──────────────────────────╯\n"
            "╭─💥 ɴᴄ ᴍᴏᴅᴇ 𝟭 ─────────────╮\n"
            "│ 🎭 /emonc  👊 /nc1  🗡 /nc2 │\n"
            "│ ⚡ /nc3  💀 /nc4  🌪 /nc5  │\n"
            "│ ☣️ /knc  🦅 /anc  🐍 /fnc  │\n"
            "│ 🎯 /mync <ᴛᴇxᴛ> — ᴄᴜꜱᴛᴏᴍ    │\n"
            "╰──────────────────────────╯\n"
            "╭─🌀 ɴᴄ ᴍᴏᴅᴇ 𝟮 ─────────────╮\n"
            "│ 💗/ncheart 🚩/ncflag ✦/dotzkeng│\n"
            "│ 🌀/nccurly ⏰/timenc 🌸/flowernc│\n"
            "│ 🏷/namenc 🧙/wizard ⬜/whitenc │\n"
            "│ ⬛/blacknc 🎌/flagemo 🔥/firenc│\n"
            "│ 🌶/hotnc 💧/waternc 🌋/lavanc  │\n"
            "│ 👹/hellnc ⚙️/symbolnc 🏳/flagncnc│\n"
            "│ 🎮/gamenc 🔧/toolnc 🔁/loopnc  │\n"
            "│ 🚗/carnc 🤚/handnc 🧠/humannc  │\n"
            "│ 🌙/moonnc 💋/kissnc 🍔/foodnc  │\n"
            "│ 🦁/animalnc                  │\n"
            "╰──────────────────────────╯\n"
            "╭─🌊 ꜱʟɪᴅᴇʀ ───────────────╮\n"
            "│ 🌀/slide1  💫/slide2  🎯/slide3│\n"
            "╰──────────────────────────╯\n"
            "╭─🔒 ɢᴄ ɴᴄ ʟᴏᴄᴋ ──────────╮\n"
            "│ 🔒 /gcnclock  🔓 /gcncunlock  │\n"
            "╰──────────────────────────╯\n"
            "╭─💣 ꜱᴘᴀᴍ & ʀᴀɪᴅ ──────────╮\n"
            "│ 💥/spam1 🔥/spam2 ⚡/spam3 🌪/spam4│\n"
            "│ 🎯 /myspam <ᴛᴇxᴛ>             │\n"
            "│ 🦅 /raidspam  🛑 /stopraidspam│\n"
            "╰──────────────────────────╯\n"
            "╭─📸 ᴘʜᴏᴛᴏ ʟᴏᴏᴘ ──────────╮\n"
            "│ 💾/savephoto ▶️/startphoto    │\n"
            "│ ⏹/stopphoto 🗑/clearphoto    │\n"
            "│ 📋/listphoto 🖼/gc 🛑/stopgc  │\n"
            "│ 🔄/pfpswipe 🚀/picspam <n>   │\n"
            "│ 🛑 /stoppicspam               │\n"
            "╰──────────────────────────╯\n"
            "╭─😈 ʀᴇᴀᴄᴛ ────────────────╮\n"
            "│ 😈/react 🛑/stopreact         │\n"
            "│ 🎭/reactall <ᴇᴍᴏᴊɪ> 🛑/stopreactall│\n"
            "│ 👁/reactuser <ᴇ> 🛑/stopreactuser│\n"
            "╰──────────────────────────╯\n"
            "╭─🛡 ᴍᴏᴅᴇ𝗥𝗔𝗧𝗜ᴏɴ ──────────╮\n"
            "│ 🔨/ban ✅/unban 🔇/mute 🔊/unmute│\n"
            "│ ⚠️/warn 📋/warnings 🗑/clearwarn│\n"
            "│ 🔢/setwarnlimit                │\n"
            "│ 🗑/del 💣/purge ☠️/purgeoff   │\n"
            "│ 📜/deleteallhistory           │\n"
            "│ 👤/deluser 🛑/stopdeluser     │\n"
            "│ 🔥/delall 🛑/stopdelall       │\n"
            "│ 🚫/blocknc ✅/stopblocknc     │\n"
            "╰──────────────────────────╯\n"
            "╭─🧠 ᴀɪ & ᴠᴏɪᴄᴇ ──────────╮\n"
            "│ 🧠 /ai <question>  🗑 /clearai │\n"
            "│ 🎙 /voice <1-10> <ᴛᴇxᴛ>        │\n"
            "╰──────────────────────────╯\n"
            "╭─📊 ꜱᴛᴀᴛᴜꜱ & 𝗖ᴏɴ𝗧𝗥𝗢𝗟 ────╮\n"
            "│ 📡/ping  📊/botstatus  ⏱️/uptime│\n"
            "│ 📈/stats  ⚡/delay <sec>       │\n"
            "│ 🔄/refresh  🛑/stopall         │\n"
            "│ 🛑/stopnc  🛑/stopspam         │\n"
            "│ 🛑/stopslide                   │\n"
            "╰──────────────────────────╯\n"
            "╭─👑 ​🇸​​🇺​​🇵​​🇷​​🇪​​🇲​​🇪​ ᴏɴ𝗟𝗬 ────╮\n"
            "│ ☣️/godmode  🛑/stopgodmode     │\n"
            "│ ➕/addsudo  ➖/delsudo  📋/sudos│\n"
            "│ 🔑/admin  👀/checkadmin  👋/bye│\n"
            "│ 🚪/leaveall [chat_id]          │\n"
            "│ 👤/adduser  🔑/addtoken        │\n"
            "│ 🚀/startall <ᴛᴇxᴛ>             │\n"
            "│ 🔥/burn (reply to report)      │\n"
            "╰──────────────────────────╯\n"
            f"{lead_help_text}"
            "╭─💬 ᴀᴜᴛᴏ ʀᴇᴘʟʏ ──────────╮\n"
            "│ 💬/autoreply  🛑/stopautoreply  │\n"
            "╰──────────────────────────╯\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "☣️ ​🇸​​🇺​​🇵​​🇷​​🇪​​🇲​​🇪​ ​🇴​​🇸 ⚡ ᴘʀᴇᴍɪᴜᴍ 👑"
        )
        await update.message.reply_text(help_text)
    else:
        public_lead_help = (
            "🧬 Bot host: /clone <ʙᴏᴛ_ᴛᴏᴋᴇɴ>\n"
            "🪞 Alias: /mirror <BOT_TOKEN>\n"
            if on_lead_bot else ""
        )
        await update.message.reply_text(
            "┏━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            "┃  👑 ​🇸​​🇺​​🇵​​🇷​​🇪​​🇲​​🇪​ ​🇴​​🇸  ┃\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━━┛\n"
            "🚫 ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴘᴇʀᴍɪᴛᴛᴇᴅ 💀\n"
            "🔒 ᴛᴀᴋᴇ ᴘᴇʀᴍɪꜱꜱᴏɴ ꜰʀᴏᴍ ᴀᴅᴍɪɴꜱ ꜰɪʀꜱʟᴛʏ 😈\n"
            f"{public_lead_help}"
            "⚡ ​🇸​​🇺​​🇵​​🇷​​🇪​​🇲​​🇪​ ​🇴​​🇸 ☣️"
        )

# ===========================================================
# AUTO-DELETE MESSAGE HANDLER
# ===========================================================
async def auto_delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user:
        return
    cid = msg.chat_id
    uid = msg.from_user.id

    if cid in auto_delete_users and uid in auto_delete_users[cid]:
        try:
            await context.bot.delete_message(chat_id=cid, message_id=msg.message_id)
        except Exception:
            pass

    # Passive handlers on a user-owned clone must never fan out through bots
    # belonging to other users.
    passive_bots = (
        [context.bot] if _context_bot_id(context) in CLONE_OWNERS else list(bots)
    )

    async def _do_react(emoji):
        for b in passive_bots:
            try:
                await b.set_message_reaction(
                    chat_id=cid,
                    message_id=msg.message_id,
                    reaction=[ReactionTypeEmoji(emoji=emoji)],
                    is_big=True
                )
            except Exception:
                pass

    if cid in reactall_chats:
        asyncio.create_task(_do_react(reactall_chats[cid]))

    elif cid in reactuser_chats and uid in reactuser_chats[cid]:
        asyncio.create_task(_do_react(reactuser_chats[cid][uid]))

    # AutoReply — slide2 style reply on every message from target user
    if cid in autoreply_users and uid in autoreply_users[cid]:
        _ar_bots = passive_bots  # clone-safe local target list
        async def _do_autoreply(message_id=msg.message_id, chat=cid, user=uid):
            idx = autoreply_slide2_idx.get(chat, {}).get(user, 0)
            text = SLIDE2_MESSAGES[idx % len(SLIDE2_MESSAGES)]
            if chat not in autoreply_slide2_idx:
                autoreply_slide2_idx[chat] = {}
            autoreply_slide2_idx[chat][user] = idx + 1
            for b in _ar_bots:
                try:
                    await b.send_message(
                        chat_id=chat,
                        text=text,
                        reply_to_message_id=message_id
                    )
                    await asyncio.sleep(0.02)
                except RetryAfter as e:
                    await asyncio.sleep(float(e.retry_after) + 0.01)
                except (TimedOut, NetworkError):
                    await asyncio.sleep(0.05)
                except Exception:
                    await asyncio.sleep(0.05)
        asyncio.create_task(_do_autoreply())

# ===========================================================
# BOT SETUP
# ===========================================================
def build_app(token):
    req = HTTPXRequest(
        connection_pool_size=50,
        read_timeout=4.0,
        write_timeout=4.0,
        connect_timeout=2.0,
        pool_timeout=2.0,
    )
    app = Application.builder().token(token).request(req).build()

    # Language NC
    app.add_handler(CommandHandler("hindinc", hindinc))
    app.add_handler(CommandHandler("urdunc", urdunc))
    app.add_handler(CommandHandler("bengalnc", bengalnc))
    app.add_handler(CommandHandler("biharinc", biharinc))
    app.add_handler(CommandHandler("chinesenc", chinesenc))
    app.add_handler(CommandHandler("engnc", engnc))

    # NC Mode 1
    app.add_handler(CommandHandler("emonc", emonc))
    app.add_handler(CommandHandler("nc1", nc1))
    app.add_handler(CommandHandler("nc2", nc2))
    app.add_handler(CommandHandler("nc3", nc3))
    app.add_handler(CommandHandler("nc4", nc4))
    app.add_handler(CommandHandler("nc5", nc5))
    app.add_handler(CommandHandler("knc", knc))
    app.add_handler(CommandHandler("anc", anc))
    app.add_handler(CommandHandler("fnc", fnc))

    # NC Mode 2
    app.add_handler(CommandHandler("ncheart", ncheart))
    app.add_handler(CommandHandler("ncflag", ncflag))
    app.add_handler(CommandHandler("dotzkeng", dotzkeng))
    app.add_handler(CommandHandler("nccurly", nccurly))
    app.add_handler(CommandHandler("timenc", timenc))
    app.add_handler(CommandHandler("flowernc", flowernc))
    app.add_handler(CommandHandler("namenc", namenc))
    app.add_handler(CommandHandler("wizard", wizard))
    app.add_handler(CommandHandler("whitenc", whitenc))
    app.add_handler(CommandHandler("blacknc", blacknc_cmd))
    app.add_handler(CommandHandler("flagemo", flagemo))
    app.add_handler(CommandHandler("firenc", firenc))
    app.add_handler(CommandHandler("hotnc", hotnc))
    app.add_handler(CommandHandler("waternc", waternc))
    app.add_handler(CommandHandler("lavanc", lavanc))
    app.add_handler(CommandHandler("hellnc", hellnc))
    app.add_handler(CommandHandler("symbolnc", symbolnc))
    app.add_handler(CommandHandler("flagncnc", flagncnc))
    app.add_handler(CommandHandler("gamenc", gamenc))
    app.add_handler(CommandHandler("toolnc", toolnc))
    app.add_handler(CommandHandler("loopnc", loopnc))
    app.add_handler(CommandHandler("carnc", carnc))
    app.add_handler(CommandHandler("handnc", handnc))
    app.add_handler(CommandHandler("humannc", humannc))
    app.add_handler(CommandHandler("moonnc", moonnc))
    app.add_handler(CommandHandler("kissnc", kissnc))
    app.add_handler(CommandHandler("foodnc", foodnc))
    app.add_handler(CommandHandler("animalnc", animalnc))

    # Slider
    app.add_handler(CommandHandler("slide1", slide1))
    app.add_handler(CommandHandler("slide2", slide2))
    app.add_handler(CommandHandler("slide3", slide3))

    # Spam & Raid
    app.add_handler(CommandHandler("spam1", spam1))
    app.add_handler(CommandHandler("spam2", spam2))
    app.add_handler(CommandHandler("spam3", spam3))
    app.add_handler(CommandHandler("spam4", spam4))
    app.add_handler(CommandHandler("raidspam", raidspam))
    app.add_handler(CommandHandler("stopraidspam", stopraidspam))

    # Photo / GC
    app.add_handler(CommandHandler("savephoto", savephoto))
    app.add_handler(CommandHandler("startphoto", startphoto))
    app.add_handler(CommandHandler("stopphoto", stopphoto))
    app.add_handler(CommandHandler("clearphotos", clearphotos))
    app.add_handler(CommandHandler("listphotos", listphotos))
    app.add_handler(CommandHandler("clearphoto", clearphotos))
    app.add_handler(CommandHandler("listphoto", listphotos))
    app.add_handler(CommandHandler("gc", gc))
    app.add_handler(CommandHandler("stopgc", stopgc))

    # Stop Commands
    app.add_handler(CommandHandler("stopnc", stopnc))
    app.add_handler(CommandHandler("stopspam", stopspam))
    app.add_handler(CommandHandler("stopslide", stopslide))
    app.add_handler(CommandHandler("stopall", stopall))

    # Moderation
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("unmute", unmute))
    app.add_handler(CommandHandler("warn", warn))
    app.add_handler(CommandHandler("warnings", warnings))
    app.add_handler(CommandHandler("clearwarns", clearwarns))
    app.add_handler(CommandHandler("clearwarn", clearwarns))
    app.add_handler(CommandHandler("setwarnlimit", setwarnlimit))
    app.add_handler(CommandHandler("del", del_cmd))
    app.add_handler(CommandHandler("purge", purge))
    app.add_handler(CommandHandler("deleteallhistory", deleteallhistory))
    app.add_handler(CommandHandler("stopdeleteallhistory", stopdeleteallhistory))
    app.add_handler(CommandHandler("deluser", deluser))
    app.add_handler(CommandHandler("stopdeluser", stopdeluser))
    app.add_handler(CommandHandler("delall", delall))
    app.add_handler(CommandHandler("stopdelall", stopdelall))
    app.add_handler(CommandHandler("blocknc", blocknc))
    app.add_handler(CommandHandler("stopblocknc", stopblocknc))

    # AI & Voice
    app.add_handler(CommandHandler("ai", ai))
    app.add_handler(CommandHandler("clearai", clearai))
    app.add_handler(CommandHandler("voice", voice))

    # Control
    app.add_handler(CommandHandler("delay", delay))
    app.add_handler(CommandHandler("hi", hi))

    # Sudo Management
    app.add_handler(CommandHandler("addsudo", addsudo))
    app.add_handler(CommandHandler("delsudo", delsudo))
    app.add_handler(CommandHandler("sudos", sudos))

    # Admin Management
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("checkadmin", checkadmin))
    app.add_handler(CommandHandler("leaveall", leaveall))
    app.add_handler(CommandHandler("autoreply", autoreply))
    app.add_handler(CommandHandler("stopautoreply", stopautoreply))
    app.add_handler(CommandHandler("myspam", myspam))
    app.add_handler(CommandHandler("mync", mync))
    app.add_handler(CommandHandler("startall", startall))
    app.add_handler(CommandHandler("adduser", adduser))
    app.add_handler(CommandHandler("addtoken", addtoken))

    # Control-plane commands must not even be registered on secondary bots.
    # Runtime decorators on both callbacks provide a second layer of safety.
    if token == LEAD_BOT_TOKEN:
        app.add_handler(CommandHandler("clone", clone_bot))
        app.add_handler(CommandHandler("mirror", clone_bot))
        app.add_handler(CommandHandler("addallbots", addallbots))
        app.add_handler(CommandHandler("allbotadd", addallbots))

    app.add_handler(CommandHandler("goodncraid", goodncraid))
    app.add_handler(CommandHandler("purgeoff", purgeoff))
    app.add_handler(CommandHandler("refresh", refresh))
    app.add_handler(CommandHandler("picspam", picspam))
    app.add_handler(CommandHandler("stoppicspam", stoppicspam))
    app.add_handler(CommandHandler("burn", burn))

    # React
    app.add_handler(CommandHandler("react", react))
    app.add_handler(CommandHandler("stopreact", stopreact))
    app.add_handler(CommandHandler("reactall", reactall))
    app.add_handler(CommandHandler("stopreactall", stopreactall))
    app.add_handler(CommandHandler("reactuser", reactuser))
    app.add_handler(CommandHandler("stopreactuser", stopreactuser))

    # Ping / Status / God Mode / PfpSwipe
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("botstatus", botstatus))
    app.add_handler(CommandHandler("godmode", godmode))
    app.add_handler(CommandHandler("stopgodmode", stopgodmode))
    app.add_handler(CommandHandler("pfpswipe", pfpswipe))

    # Uptime & Stats (new v6.9.2)
    app.add_handler(CommandHandler("uptime", uptime))
    app.add_handler(CommandHandler("stats", stats))

    # GC NC Lock
    app.add_handler(CommandHandler("gcnclock", gcnclock))
    app.add_handler(CommandHandler("gcncunlock", gcncunlock))

    # Leave
    app.add_handler(CommandHandler("bye", bye))

    # Help
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("start", help_cmd))

    # Auto-delete message handler (must be last)
    app.add_handler(MessageHandler(filters.ALL, auto_delete_handler))

    return app

# ===========================================================
# MAIN
# ===========================================================
async def run_all_bots():
    global NC_SEMAPHORE
    if not TOKENS:
        logging.error('No bot tokens added!')
        return

    NC_SEMAPHORE = asyncio.Semaphore(100)

    for token in TOKENS:
        try:
            app = build_app(token)
            apps.append(app)
            bots.append(app.bot)
        except Exception as e:
            logging.error(f'Failed to build bot {token[:10]}...: {e}')

    # Staggered startup — 8ms between each bot avoids reconnect storm
    async def _start_one(app, idx=0):
        await asyncio.sleep(idx * 0.008)
        for attempt in range(3):
            try:
                await app.initialize()
                await app.start()
                await app.updater.start_polling(
                    drop_pending_updates=True,
                    allowed_updates=["message", "callback_query"],
                    poll_interval=0.0,
                    timeout=10,
                )
                return
            except Exception as e:
                logging.error(f'Bot {app.bot.token[:10]} start attempt {attempt+1}: {e}')
                if attempt < 2:
                    await asyncio.sleep(0.04 * (attempt + 1))

    await asyncio.gather(*[_start_one(app, i) for i, app in enumerate(apps)])

    asyncio.create_task(_watchdog_loop())
    await _restore_active_tasks()

    pass  # silent startup
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(run_all_bots())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.error(f'Fatal: {e}')
