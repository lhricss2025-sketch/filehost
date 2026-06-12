"""
╔══════════════════════════════════════════════════════════════╗
║           🌟 SENZO PREMIUM BOT v6.0 FINAL 🌟                 ║
║      Professional File Sharing & Referral System             ║
║           Powered by Senzo Technologies                      ║
╚══════════════════════════════════════════════════════════════╝

v6.0 Changes:
  1. Universal referral link — one link per user (not per product)
     Format: t.me/bot?start=ref_USERID
     Each join = +5 credits to referrer, no product dependency
  2. Custom channel names in upload flow
     Admin enters: "Name https://t.me/link | Name2 https://t.me/link2"
     Buttons show custom display names

Database  : Turso Cloud (LibSQL HTTP API)
Platform  : Railway / Any hosting
"""

import asyncio
import aiohttp
import logging
import random
import string
from datetime import datetime, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import BadRequest, Forbidden

# ═══════════════════════════════════════════════════════════════
#                        CONFIGURATION
# ═══════════════════════════════════════════════════════════════

BOT_TOKEN  = "8480004123:AAEmDVAia46G5ggfDqLDEIXNy5Zy4erXsOo"
ADMIN_ID   = 6070145287

# ── Turso Config ───────────────────────────────────────────────
# Get from https://app.turso.tech → your DB → Connect
TURSO_URL   = "https://hosting-bot-filehosting.aws-ap-south-1.turso.io"
TURSO_TOKEN = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3ODExNjA0OTQsImlkIjoiMDE5ZWFiMmMtM2YwMS03ZGUwLWFiMTEtMGZhODBjYzc0Yjk0IiwicmlkIjoiNGRjZWRjYjEtZWMyMC00MWU1LTk1ZTItZDRjZWIzNjM0YjFkIn0.FJ82icyxhrOldS1OuT3RIfvs-L2Eg74y7ftfx_wuGvROR5bubLL_msczMdf82UPDRjz_znASbpHFrDHmOWBeBQ"

CREDITS_PER_REFERRAL = 5

RANKS = [
    (0,   "Bronze 🥉"),
    (10,  "Silver 🥈"),
    (50,  "Gold 🥇"),
    (100, "Platinum 💎"),
    (250, "Diamond 👑"),
    (500, "Legend 🌟"),
]

_BOT_USERNAME: Optional[str] = None

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#                   TURSO HTTP API WRAPPER
# ═══════════════════════════════════════════════════════════════

class TursoDB:
    def __init__(self, url: str, token: str):
        self._url  = url.rstrip("/") + "/v2/pipeline"
        self._hdrs = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._sess: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._sess or self._sess.closed:
            self._sess = aiohttp.ClientSession(headers=self._hdrs)
        return self._sess

    @staticmethod
    def _arg(v) -> dict:
        if v is None:            return {"type": "null",    "value": None}
        if isinstance(v, bool):  return {"type": "integer", "value": "1" if v else "0"}
        if isinstance(v, int):   return {"type": "integer", "value": str(v)}
        if isinstance(v, float): return {"type": "float",   "value": str(v)}
        return {"type": "text", "value": str(v)}

    def _stmt(self, sql: str, params: tuple = ()) -> dict:
        return {"sql": sql, "args": [self._arg(p) for p in params]}

    async def _pipeline(self, requests: list) -> list:
        reqs = list(requests) + [{"type": "close"}]
        sess = await self._get_session()
        async with sess.post(self._url, json={"requests": reqs}) as resp:
            resp.raise_for_status()
            data = await resp.json()
        results = []
        for r in data.get("results", []):
            if r.get("type") == "error":
                raise Exception(f"Turso error: {r.get('error', r)}")
            results.append(r["response"]["result"])
        return results

    def _to_rows(self, result: dict) -> list:
        cols = [c["name"] for c in result.get("cols", [])]
        out  = []
        for raw_row in result.get("rows", []):
            obj = {}
            for i, col in enumerate(cols):
                cell = raw_row[i]
                val  = cell.get("value") if isinstance(cell, dict) else cell
                typ  = cell.get("type")  if isinstance(cell, dict) else None
                if val is not None:
                    if typ == "integer":
                        try: val = int(val)
                        except (ValueError, TypeError): pass
                    elif typ == "float":
                        try: val = float(val)
                        except (ValueError, TypeError): pass
                obj[col] = val
            out.append(obj)
        return out

    async def run(self, sql: str, params: tuple = ()):
        """Execute single statement."""
        await self._pipeline([{"type": "execute", "stmt": self._stmt(sql, params)}])

    async def run_many(self, stmts: list):
        """Execute multiple statements in one HTTP call. stmts = [(sql, params), ...]"""
        reqs = [{"type": "execute", "stmt": self._stmt(s, p)} for s, p in stmts]
        await self._pipeline(reqs)

    async def fetchall(self, sql: str, params: tuple = ()) -> list:
        res = await self._pipeline([{"type": "execute", "stmt": self._stmt(sql, params)}])
        return self._to_rows(res[0])

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        rows = await self.fetchall(sql, params)
        return rows[0] if rows else None

    async def fetchval(self, sql: str, params: tuple = (), default=0):
        row = await self.fetchone(sql, params)
        if row:
            v = list(row.values())[0]
            return v if v is not None else default
        return default


DB = TursoDB(TURSO_URL, TURSO_TOKEN)


# ═══════════════════════════════════════════════════════════════
#                       DATABASE INIT
# ═══════════════════════════════════════════════════════════════

async def init_db():
    logger.info(f"Connecting to Turso: {TURSO_URL}")
    await DB.run_many([
        # Users table
        ("""CREATE TABLE IF NOT EXISTS users (
            user_id         INTEGER PRIMARY KEY,
            username        TEXT    DEFAULT '',
            full_name       TEXT    DEFAULT '',
            credits         INTEGER DEFAULT 0,
            total_referrals INTEGER DEFAULT 0,
            rank            TEXT    DEFAULT 'Bronze 🥉',
            joined_date     TEXT    DEFAULT (datetime('now')),
            last_active     TEXT    DEFAULT (datetime('now')),
            is_banned       INTEGER DEFAULT 0)""", ()),

        # Products table
        ("""CREATE TABLE IF NOT EXISTS products (
            id               TEXT PRIMARY KEY,
            name             TEXT    NOT NULL,
            description      TEXT    DEFAULT '',
            file_id          TEXT    NOT NULL,
            file_type        TEXT    NOT NULL,
            required_refs    INTEGER DEFAULT 1,
            required_credits INTEGER DEFAULT 0,
            admin_id         INTEGER,
            is_active        INTEGER DEFAULT 1,
            created_at       TEXT    DEFAULT (datetime('now')),
            views            INTEGER DEFAULT 0,
            unlocks          INTEGER DEFAULT 0)""", ()),

        # Product channels — now stores display_name + url separately
        # display_name : shown on button  e.g. "My Channel"
        # channel_url  : full link        e.g. "https://t.me/mychannel"
        # channel_user : @username for membership check (extracted from url)
        ("""CREATE TABLE IF NOT EXISTS product_channels (
            id           INTEGER PRIMARY KEY,
            product_id   TEXT    NOT NULL,
            display_name TEXT    NOT NULL,
            channel_url  TEXT    NOT NULL,
            channel_user TEXT    NOT NULL)""", ()),

        # Universal referrals — no product_id, just referrer→referred
        # UNIQUE on (referrer_id, referred_id) prevents double counting
        ("""CREATE TABLE IF NOT EXISTS referrals (
            id          INTEGER PRIMARY KEY,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL,
            date        TEXT    DEFAULT (datetime('now')),
            UNIQUE(referrer_id, referred_id))""", ()),

        # File unlocks
        ("""CREATE TABLE IF NOT EXISTS user_unlocks (
            user_id     INTEGER NOT NULL,
            product_id  TEXT    NOT NULL,
            unlocked_at TEXT    DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, product_id))""", ()),

        # Credit/debit log
        ("""CREATE TABLE IF NOT EXISTS transactions (
            id          INTEGER PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            amount      INTEGER NOT NULL,
            type        TEXT    NOT NULL,
            description TEXT    DEFAULT '',
            date        TEXT    DEFAULT (datetime('now')))""", ()),

        # Redeem codes
        ("""CREATE TABLE IF NOT EXISTS redeem_codes (
            id          INTEGER PRIMARY KEY,
            code        TEXT    UNIQUE NOT NULL,
            points      INTEGER NOT NULL,
            max_uses    INTEGER NOT NULL,
            used_count  INTEGER DEFAULT 0,
            created_by  INTEGER,
            created_at  TEXT    DEFAULT (datetime('now')),
            expires_at  TEXT,
            is_active   INTEGER DEFAULT 1)""", ()),

        # Per-user redeem tracking
        ("""CREATE TABLE IF NOT EXISTS redeem_usage (
            id          INTEGER PRIMARY KEY,
            code        TEXT    NOT NULL,
            user_id     INTEGER NOT NULL,
            redeemed_at TEXT    DEFAULT (datetime('now')),
            UNIQUE(code, user_id))""", ()),

        # Broadcast log
        ("""CREATE TABLE IF NOT EXISTS broadcast_history (
            id           INTEGER PRIMARY KEY,
            message_type TEXT,
            target_type  TEXT,
            total_sent   INTEGER DEFAULT 0,
            total_failed INTEGER DEFAULT 0,
            sent_by      INTEGER,
            sent_at      TEXT    DEFAULT (datetime('now')))""", ()),
    ])
    logger.info("Database ready.")


# ═══════════════════════════════════════════════════════════════
#                        SMALL HELPERS
# ═══════════════════════════════════════════════════════════════

def SEP() -> str:
    return "━━━━━━━━━━━━━━━━━━━━━━"


def rank_for(refs: int) -> str:
    r = RANKS[0][1]
    for thr, name in RANKS:
        if refs >= thr:
            r = name
    return r


def new_product_id() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def new_redeem_code() -> str:
    parts = ["SENZO"] + [
        "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
        for _ in range(2)
    ]
    return "-".join(parts)


async def get_bot_username(bot) -> str:
    """Cache bot username so we never call get_me() more than once."""
    global _BOT_USERNAME
    if not _BOT_USERNAME:
        _BOT_USERNAME = (await bot.get_me()).username
    return _BOT_USERNAME


def back_btn(cb: str = "main_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=cb)]])


def extract_username_from_url(url: str) -> str:
    """
    Extract @username from a t.me URL for membership checking.
    https://t.me/mychannel  →  @mychannel
    https://t.me/joinchat/xxx  →  private link, return "" (can't check)
    https://t.me/+xxxxx      →  private link, return "" (can't check)
    """
    url = url.strip().rstrip("/")
    # Private invite links — cannot check membership
    if "/joinchat/" in url or "/+" in url:
        return ""
    if "t.me/" in url:
        parts = url.split("t.me/")
        if len(parts) == 2:
            username = parts[1].split("/")[0].strip()
            if username and not username.startswith("+"):
                return f"@{username}"
    return ""


def parse_channels_input(raw: str) -> list:
    """
    Parse admin channel input.
    Format: "Name https://t.me/link | Name2 https://t.me/link2"
    Returns: [{"display_name": str, "channel_url": str, "channel_user": str}, ...]
    Supports both | and newline as separators.
    """
    channels = []
    # Split by | or newline
    parts = [p.strip() for p in raw.replace("\n", "|").split("|") if p.strip()]
    for part in parts:
        tokens = part.split()
        if len(tokens) < 2:
            continue
        # Last token that looks like a URL is the link
        url_idx = None
        for i, t in enumerate(tokens):
            if t.startswith("http") or t.startswith("t.me"):
                url_idx = i
                break
        if url_idx is None:
            continue
        display_name = " ".join(tokens[:url_idx]).strip()
        channel_url  = tokens[url_idx].strip()
        if not display_name:
            display_name = channel_url
        channel_user = extract_username_from_url(channel_url)
        channels.append({
            "display_name": display_name,
            "channel_url":  channel_url,
            "channel_user": channel_user,
        })
    return channels


# ═══════════════════════════════════════════════════════════════
#                     DATABASE HELPERS
# ═══════════════════════════════════════════════════════════════

async def ensure_user(uid: int, uname: str, fname: str):
    try:
        await DB.run("""
            INSERT INTO users (user_id, username, full_name) VALUES (?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                username    = excluded.username,
                full_name   = excluded.full_name,
                last_active = datetime('now')
        """, (uid, uname or "", fname or ""))
    except Exception as e:
        logger.error(f"ensure_user: {e}")


async def get_user(uid: int) -> Optional[dict]:
    try:
        return await DB.fetchone("SELECT * FROM users WHERE user_id=?", (uid,))
    except Exception as e:
        logger.error(f"get_user: {e}")
        return None


async def add_credits(uid: int, amount: int, desc: str):
    try:
        await DB.run_many([
            ("UPDATE users SET credits=credits+? WHERE user_id=?", (amount, uid)),
            ("INSERT INTO transactions (user_id,amount,type,description) VALUES (?,?,'credit',?)",
             (uid, amount, desc)),
        ])
    except Exception as e:
        logger.error(f"add_credits: {e}")


async def deduct_credits(uid: int, amount: int, desc: str):
    try:
        await DB.run_many([
            ("UPDATE users SET credits=credits-? WHERE user_id=?", (amount, uid)),
            ("INSERT INTO transactions (user_id,amount,type,description) VALUES (?,?,'debit',?)",
             (uid, amount, desc)),
        ])
    except Exception as e:
        logger.error(f"deduct_credits: {e}")


async def refresh_rank(uid: int):
    try:
        u = await get_user(uid)
        if u:
            await DB.run(
                "UPDATE users SET rank=? WHERE user_id=?",
                (rank_for(u["total_referrals"]), uid))
    except Exception as e:
        logger.error(f"refresh_rank: {e}")


async def get_product(pid: str, include_inactive: bool = False) -> Optional[dict]:
    try:
        sql = ("SELECT * FROM products WHERE id=?"
               if include_inactive else
               "SELECT * FROM products WHERE id=? AND is_active=1")
        return await DB.fetchone(sql, (pid,))
    except Exception as e:
        logger.error(f"get_product: {e}")
        return None


async def get_channels(pid: str) -> list:
    """Returns list of dicts: {display_name, channel_url, channel_user}"""
    try:
        return await DB.fetchall(
            "SELECT display_name, channel_url, channel_user "
            "FROM product_channels WHERE product_id=?", (pid,))
    except Exception as e:
        logger.error(f"get_channels: {e}")
        return []


async def get_user_ref_count(uid: int) -> int:
    """Total number of people this user has referred (universal)."""
    try:
        return await DB.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id=?",
            (uid,), default=0)
    except Exception as e:
        logger.error(f"get_user_ref_count: {e}")
        return 0


async def already_unlocked(uid: int, pid: str) -> bool:
    try:
        row = await DB.fetchone(
            "SELECT 1 FROM user_unlocks WHERE user_id=? AND product_id=?", (uid, pid))
        return row is not None
    except Exception as e:
        logger.error(f"already_unlocked: {e}")
        return False


async def mark_unlocked(uid: int, pid: str):
    try:
        await DB.run_many([
            ("INSERT OR IGNORE INTO user_unlocks (user_id,product_id) VALUES (?,?)", (uid, pid)),
            ("UPDATE products SET unlocks=unlocks+1 WHERE id=?", (pid,)),
        ])
    except Exception as e:
        logger.error(f"mark_unlocked: {e}")


async def user_joined_channel(bot, uid: int, channel_user: str) -> bool:
    """Check if user is member of channel. channel_user = @username."""
    if not channel_user:
        return True  # private link — can't check, don't block
    try:
        member = await asyncio.wait_for(
            bot.get_chat_member(chat_id=channel_user, user_id=uid),
            timeout=8.0)
        return member.status not in ("left", "kicked", "banned")
    except asyncio.TimeoutError:
        return False
    except (BadRequest, Forbidden):
        return True  # bot not admin — don't block user
    except Exception:
        return True


async def deliver_file(bot, uid: int, product: dict) -> bool:
    try:
        fid = product["file_id"]
        ft  = product["file_type"]
        cap = f"📦 *{product['name']}*\n\n✅ _Powered by Senzo Premium_"
        kw  = dict(chat_id=uid, caption=cap, parse_mode="Markdown")
        if   ft == "document": await bot.send_document(document=fid, **kw)
        elif ft == "video":    await bot.send_video(video=fid, **kw)
        elif ft == "photo":    await bot.send_photo(photo=fid, **kw)
        elif ft == "audio":    await bot.send_audio(audio=fid, **kw)
        elif ft == "voice":    await bot.send_voice(voice=fid, **kw)
        else:                  await bot.send_document(document=fid, **kw)
        return True
    except Forbidden:
        logger.warning(f"User {uid} blocked bot.")
        return False
    except Exception as e:
        logger.error(f"deliver_file {uid}: {e}")
        return False


async def bot_stats() -> dict:
    try:
        s = {}
        for key, sql in [
            ("users",   "SELECT COUNT(*) FROM users"),
            ("files",   "SELECT COUNT(*) FROM products WHERE is_active=1"),
            ("refs",    "SELECT COUNT(*) FROM referrals"),
            ("credits", "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='credit'"),
            ("codes",   "SELECT COUNT(*) FROM redeem_codes WHERE is_active=1"),
        ]:
            s[key] = await DB.fetchval(sql, default=0)
        row = await DB.fetchone(
            "SELECT sent_at FROM broadcast_history ORDER BY sent_at DESC LIMIT 1")
        s["last_bc"] = row["sent_at"] if row else "Never"
        return s
    except Exception as e:
        logger.error(f"bot_stats: {e}")
        return {k: 0 for k in ["users","files","refs","credits","codes","last_bc"]}


# ═══════════════════════════════════════════════════════════════
#                       UI BUILDERS
# ═══════════════════════════════════════════════════════════════

def main_menu_text(u: dict) -> str:
    return (
        f"🌟 *SENZO PREMIUM* 🌟\n{SEP()}\n"
        f"*Welcome, {u.get('full_name') or 'User'}!*\n\n"
        f"👤 *YOUR STATS*\n"
        f"• Rank: {u.get('rank','Bronze 🥉')}\n"
        f"• Credits: {u.get('credits',0):,} 💰\n"
        f"• Referrals: {u.get('total_referrals',0):,} 🔗\n\n"
        f"{SEP()}\n\n📱 *MAIN MENU*"
    )


def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if is_admin:
        rows.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    rows += [
        [InlineKeyboardButton("📦 Browse Files",  callback_data="browse_files")],
        [InlineKeyboardButton("🔗 My Referrals",  callback_data="my_referrals"),
         InlineKeyboardButton("🏆 Leaderboard",   callback_data="leaderboard")],
        [InlineKeyboardButton("💰 Redeem Code",   callback_data="redeem_info"),
         InlineKeyboardButton("👤 My Profile",    callback_data="my_profile")],
        [InlineKeyboardButton("❓ Help",           callback_data="help")],
    ]
    return InlineKeyboardMarkup(rows)


def admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload New File",       callback_data="adm_upload")],
        [InlineKeyboardButton("📋 Manage Files",          callback_data="adm_manage"),
         InlineKeyboardButton("👥 View Users",            callback_data="adm_users")],
        [InlineKeyboardButton("🎫 Generate Redeem Code",  callback_data="adm_gencode")],
        [InlineKeyboardButton("🔗 View All Codes",        callback_data="adm_listcodes")],
        [InlineKeyboardButton("📢 Send Broadcast",        callback_data="adm_broadcast")],
        [InlineKeyboardButton("📊 Full Statistics",       callback_data="adm_stats")],
        [InlineKeyboardButton("🔙 Main Menu",             callback_data="main_menu")],
    ])


def product_page_text(prod: dict, my_cred: int) -> str:
    req_refs = prod["required_refs"]
    req_cred = prod["required_credits"]
    return (
        f"📦 *{prod['name']}*\n{SEP()}\n\n"
        f"📝 {prod['description']}\n\n"
        f"*REQUIREMENTS:*\n"
        f"• 🔗 Referrals needed: {req_refs}\n"
        f"• 💰 Credits needed: {req_cred}  (You have: {my_cred})\n\n"
        f"{SEP()}\n"
        f"💡 Share your referral link to earn credits!"
    )


def product_page_kb(pid: str, req_cred: int, my_cred: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📊 Check My Progress", callback_data=f"progress_{pid}")],
    ]
    if req_cred > 0 and my_cred >= req_cred:
        rows.append([InlineKeyboardButton(
            "💰 Unlock with Credits", callback_data=f"unlockc_{pid}")])
    rows.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


# ═══════════════════════════════════════════════════════════════
#               PRODUCT PAGE SEND / EDIT HELPERS
# ═══════════════════════════════════════════════════════════════

async def send_product_page(dest, context, uid: int, pid: str):
    """Send product page as NEW message."""
    prod = await get_product(pid)
    if not prod:
        ia = await get_product(pid, include_inactive=True)
        await dest.reply_text(
            "⚠️ Product is currently unavailable." if ia else "❌ Invalid product link.")
        return
    if await already_unlocked(uid, pid):
        await dest.reply_text("🎉 You already unlocked this! Sending your file again...")
        await deliver_file(context.bot, uid, prod)
        return
    u  = await get_user(uid)
    mc = u["credits"] if u else 0
    await dest.reply_text(
        product_page_text(prod, mc), parse_mode="Markdown",
        reply_markup=product_page_kb(pid, prod["required_credits"], mc))


async def edit_product_page(query, context, uid: int, pid: str):
    """Edit current message to show product page."""
    prod = await get_product(pid)
    if not prod:
        ia = await get_product(pid, include_inactive=True)
        await query.edit_message_text(
            "⚠️ Product is currently unavailable." if ia else "❌ Product not found.",
            reply_markup=back_btn())
        return
    if await already_unlocked(uid, pid):
        await query.edit_message_text("🎉 Already unlocked! Sending your file...")
        await deliver_file(context.bot, uid, prod)
        return
    u  = await get_user(uid)
    mc = u["credits"] if u else 0
    await query.edit_message_text(
        product_page_text(prod, mc), parse_mode="Markdown",
        reply_markup=product_page_kb(pid, prod["required_credits"], mc))


async def send_channel_gate(dest, context, uid: int,
                             pid: str, channels: list, prod_name: str):
    """
    Show channel verification gate.
    channels = list of {display_name, channel_url, channel_user}
    """
    btns = [
        [InlineKeyboardButton(
            f"📢 {ch['display_name']}",
            url=ch["channel_url"])]
        for ch in channels
    ]
    btns.append([InlineKeyboardButton(
        "✅ I've Joined – Verify Now",
        callback_data=f"verify_{pid}")])

    ch_list = "\n".join(
        f"📢 [{ch['display_name']}]({ch['channel_url']})" for ch in channels)
    text = (
        f"🔒 *CHANNEL VERIFICATION*\n{SEP()}\n\n"
        f"📦 *{prod_name}*\n\n"
        f"Join ALL channels below:\n\n"
        f"{ch_list}\n\n"
        f"{SEP()}\n⚠️ After joining, tap *Verify Now*."
    )
    kb = InlineKeyboardMarkup(btns)
    if dest:
        await dest.reply_text(text, parse_mode="Markdown",
                              reply_markup=kb, disable_web_page_preview=True)
    else:
        await context.bot.send_message(
            uid, text, parse_mode="Markdown",
            reply_markup=kb, disable_web_page_preview=True)


# ═══════════════════════════════════════════════════════════════
#                        /start COMMAND
# ═══════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")

    # Clear any lingering state
    for k in ("step","bc_target","bc_msg",
              "up_fname","up_fdesc","up_frefs","up_fcred",
              "up_fchannels","up_file_id","up_file_type"):
        context.user_data.pop(k, None)

    args = context.args or []
    if args:
        arg = args[0]

        # ── Product deep link ──────────────────────────────
        if arg.startswith("product_"):
            pid  = arg[8:]
            prod = await get_product(pid)
            if not prod:
                ia = await get_product(pid, include_inactive=True)
                await update.message.reply_text(
                    "⚠️ Product is currently unavailable."
                    if ia else "❌ Invalid product link.")
            else:
                asyncio.create_task(_bump_views(pid))
                chs = await get_channels(pid)
                if chs:
                    await send_channel_gate(
                        update.message, context, user.id, pid, chs, prod["name"])
                else:
                    await send_product_page(update.message, context, user.id, pid)
            return

        # ── Universal referral deep link ───────────────────
        # Format: ref_USERID  (no product dependency)
        if arg.startswith("ref_"):
            parts = arg.split("_", 1)
            if len(parts) == 2:
                try:
                    referrer_id = int(parts[1])
                    await handle_referral_join(update, context, user, referrer_id)
                    return
                except ValueError:
                    pass

    u = await get_user(user.id)
    await update.message.reply_text(
        main_menu_text(u), parse_mode="Markdown",
        reply_markup=main_menu_kb(user.id == ADMIN_ID))


async def _bump_views(pid: str):
    try:
        await DB.run("UPDATE products SET views=views+1 WHERE id=?", (pid,))
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
#             UNIVERSAL REFERRAL JOIN HANDLER
# ═══════════════════════════════════════════════════════════════

async def handle_referral_join(update, context, user, referrer_id: int):
    """
    Universal referral — no product linked.
    Referrer gets CREDITS_PER_REFERRAL credits for each unique join.
    """
    referred_id = user.id

    # Self-referral guard
    if referrer_id == referred_id:
        await update.message.reply_text(
            "❌ *You cannot refer yourself!*", parse_mode="Markdown")
        u = await get_user(user.id)
        await update.message.reply_text(
            main_menu_text(u), parse_mode="Markdown",
            reply_markup=main_menu_kb(user.id == ADMIN_ID))
        return

    # Check referrer exists
    referrer = await get_user(referrer_id)
    if not referrer:
        # Referrer not found — just show normal welcome
        u = await get_user(user.id)
        await update.message.reply_text(
            main_menu_text(u), parse_mode="Markdown",
            reply_markup=main_menu_kb(user.id == ADMIN_ID))
        return

    # Try to insert referral (UNIQUE constraint prevents duplicates)
    new_ref = False
    try:
        await DB.run_many([
            ("INSERT INTO referrals (referrer_id, referred_id) VALUES (?,?)",
             (referrer_id, referred_id)),
            ("UPDATE users SET total_referrals=total_referrals+1 WHERE user_id=?",
             (referrer_id,)),
        ])
        new_ref = True
    except Exception:
        pass  # Already referred — silent ignore

    if new_ref:
        await add_credits(referrer_id, CREDITS_PER_REFERRAL, "Referral bonus")
        await refresh_rank(referrer_id)

        # Notify referrer
        updated_ref = await get_user(referrer_id)
        total_refs  = updated_ref["total_referrals"] if updated_ref else 0
        try:
            await context.bot.send_message(
                chat_id=referrer_id,
                text=(
                    f"🎉 *New Referral! +{CREDITS_PER_REFERRAL} credits*\n\n"
                    f"Someone joined using your link!\n"
                    f"🔗 Your total referrals: *{total_refs}*\n"
                    f"💰 Your credits: *{updated_ref['credits']:,}*"
                ),
                parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Could not notify referrer {referrer_id}: {e}")

        await update.message.reply_text(
            "✅ *Welcome to Senzo Premium!*\n\n"
            "You joined via a referral link! 🎉\n"
            "Your friend just earned some credits.",
            parse_mode="Markdown")
    else:
        # Already counted — welcome back
        await update.message.reply_text(
            "👋 *Welcome back!*\n\nYou already joined via this referral before.",
            parse_mode="Markdown")

    u = await get_user(user.id)
    await update.message.reply_text(
        main_menu_text(u), parse_mode="Markdown",
        reply_markup=main_menu_kb(user.id == ADMIN_ID))


# ═══════════════════════════════════════════════════════════════
#                   CALLBACK QUERY HANDLER
# ═══════════════════════════════════════════════════════════════

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data  = query.data
    user  = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")

    # Single answer at the very top — no double-answer crash
    try:
        await query.answer()
    except Exception:
        pass

    try:

        # ══ USER SECTION ════════════════════════════════════

        if data == "main_menu":
            u = await get_user(user.id)
            await query.edit_message_text(
                main_menu_text(u), parse_mode="Markdown",
                reply_markup=main_menu_kb(user.id == ADMIN_ID))

        elif data == "browse_files":
            bn   = await get_bot_username(context.bot)
            rows = await DB.fetchall(
                "SELECT id,name,required_refs,required_credits "
                "FROM products WHERE is_active=1 ORDER BY created_at DESC LIMIT 20")
            if not rows:
                await query.edit_message_text(
                    "📦 No files available yet. Check back later!",
                    reply_markup=back_btn())
                return
            btns = []
            for r in rows:
                lbl = f"📦 {r['name']}  (🔗{r['required_refs']}"
                if r["required_credits"]:
                    lbl += f" | 💰{r['required_credits']}"
                lbl += ")"
                btns.append([InlineKeyboardButton(
                    lbl,
                    url=f"https://t.me/{bn}?start=product_{r['id']}")])
            btns.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
            await query.edit_message_text(
                f"📦 *AVAILABLE FILES*\n{SEP()}\n\nTap any file to view:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(btns))

        elif data == "my_referrals":
            total_refs = await get_user_ref_count(user.id)
            bn         = await get_bot_username(context.bot)
            ref_link   = f"https://t.me/{bn}?start=ref_{user.id}"
            await query.edit_message_text(
                f"🔗 *MY REFERRALS*\n{SEP()}\n\n"
                f"👥 Total people you referred: *{total_refs}*\n"
                f"💰 Credits earned: *{total_refs * CREDITS_PER_REFERRAL:,}*\n\n"
                f"{SEP()}\n\n"
                f"🔗 *Your Referral Link:*\n`{ref_link}`\n\n"
                f"Share this link — each person who joins\n"
                f"gives you *+{CREDITS_PER_REFERRAL} credits*! 💰",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]))

        elif data == "leaderboard":
            rows = await DB.fetchall(
                "SELECT user_id,full_name,username,total_referrals "
                "FROM users ORDER BY total_referrals DESC LIMIT 10")
            my_rank = await DB.fetchval("""
                SELECT COUNT(*)+1 FROM users
                WHERE total_referrals>(
                    SELECT total_referrals FROM users WHERE user_id=?)""",
                (user.id,), default=1)
            ur = await DB.fetchone(
                "SELECT total_referrals FROM users WHERE user_id=?", (user.id,))
            my_refs = ur["total_referrals"] if ur else 0
            medals  = ["🥇","🥈","🥉"] + [""]*7
            lines   = [f"🏆 *TOP REFERRERS*\n{SEP()}\n"]
            for i, r in enumerate(rows):
                d = r["username"] or r["full_name"] or f"User{r['user_id']}"
                lines.append(
                    f"{medals[i] if i<3 else f'{i+1}.'} {d} – "
                    f"{r['total_referrals']:,} referrals")
            lines += [f"\n{SEP()}",
                      f"📌 *YOUR RANK: #{my_rank}*",
                      f"🔗 Your referrals: {my_refs:,}"]
            await query.edit_message_text(
                "\n".join(lines), parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh", callback_data="leaderboard"),
                     InlineKeyboardButton("🔙 Back",    callback_data="main_menu")]]))

        elif data == "redeem_info":
            await query.edit_message_text(
                f"💰 *REDEEM CODE*\n{SEP()}\n\n"
                "Use the command:\n`/redeem YOUR-CODE`\n\n"
                "Example:\n`/redeem SENZO-AB12C-D34EF`\n\n"
                "💡 Get codes from events & admin giveaways!",
                parse_mode="Markdown", reply_markup=back_btn())

        elif data == "my_profile":
            u = await get_user(user.id)
            if not u: return
            unlocked = await DB.fetchval(
                "SELECT COUNT(*) FROM user_unlocks WHERE user_id=?",
                (user.id,), default=0)
            last7 = await DB.fetchval(
                "SELECT COUNT(*) FROM referrals WHERE referrer_id=? "
                "AND date>=datetime('now','-7 days')",
                (user.id,), default=0)
            bn       = await get_bot_username(context.bot)
            ref_link = f"https://t.me/{bn}?start=ref_{user.id}"
            joined   = (u.get("joined_date") or "")[:10] or "Unknown"
            uname    = f"@{u['username']}" if u.get("username") else "No username"
            await query.edit_message_text(
                f"👤 *MY PROFILE*\n{SEP()}\n\n"
                f"• User ID: `{user.id}`\n"
                f"• Username: {uname}\n"
                f"• Joined: {joined}\n"
                f"• Rank: {u['rank']}\n\n"
                f"{SEP()}\n\n"
                f"💰 Credits: {u['credits']:,}\n"
                f"🔗 Total Referrals: {u['total_referrals']:,}\n"
                f"📦 Unlocked Files: {unlocked}\n\n"
                f"📊 Last 7 days: {last7} referrals\n\n"
                f"{SEP()}\n\n"
                f"🔗 *Your Referral Link:*\n`{ref_link}`",
                parse_mode="Markdown", reply_markup=back_btn())

        elif data == "help":
            bn       = await get_bot_username(context.bot)
            ref_link = f"https://t.me/{bn}?start=ref_{user.id}"
            await query.edit_message_text(
                f"❓ *HELP & COMMANDS*\n{SEP()}\n\n"
                "*User Commands:*\n"
                "• `/start` – Main menu\n"
                "• `/redeem CODE` – Redeem a code\n"
                "• `/myrefs` – My referral link & stats\n"
                "• `/profile` – My profile\n\n"
                "*How to earn credits:*\n"
                f"Share your link: `{ref_link}`\n"
                f"Each person who joins = *+{CREDITS_PER_REFERRAL} credits* 💰\n\n"
                "*How to unlock files:*\n"
                "1. Open a file link\n"
                "2. Join required channels\n"
                "3. File unlocks automatically!\n\n"
                "_Powered by Senzo Technologies_ 🌟",
                parse_mode="Markdown", reply_markup=back_btn())

        # ── Channel verification ──────────────────────────
        elif data.startswith("verify_"):
            pid      = data[7:]
            channels = await get_channels(pid)
            prod     = await get_product(pid)
            if not prod:
                await query.edit_message_text(
                    "❌ This product is no longer available.",
                    reply_markup=back_btn())
                return

            not_joined = []
            for ch in channels:
                if ch["channel_user"]:  # only check if we have a username
                    joined = await user_joined_channel(
                        context.bot, user.id, ch["channel_user"])
                    if not joined:
                        not_joined.append(ch)

            if not not_joined:
                await edit_product_page(query, context, user.id, pid)
            else:
                btns = []
                for ch in channels:
                    is_bad = ch in not_joined
                    icon   = "❌" if is_bad else "✅"
                    btns.append([InlineKeyboardButton(
                        f"{icon} {ch['display_name']}",
                        url=ch["channel_url"])])
                btns.append([InlineKeyboardButton(
                    "✅ Verify Now", callback_data=f"verify_{pid}")])
                status = "\n".join(
                    f"{'❌' if ch in not_joined else '✅'} {ch['display_name']}"
                    for ch in channels)
                await query.edit_message_text(
                    f"🔒 *CHANNEL VERIFICATION*\n{SEP()}\n\n"
                    f"📦 *{prod['name']}*\n\n"
                    f"*Status:*\n{status}\n\n"
                    "❌ Please join the missing channels then tap Verify.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(btns))

        # ── Progress check ────────────────────────────────
        elif data.startswith("progress_"):
            pid      = data[9:]
            prod     = await get_product(pid)
            if not prod:
                await query.edit_message_text(
                    "❌ Product not found.", reply_markup=back_btn())
                return
            u        = await get_user(user.id)
            my_cred  = u["credits"] if u else 0
            req_refs = prod["required_refs"]
            req_cred = prod["required_credits"]
            unlocked = await already_unlocked(user.id, pid)
            bn       = await get_bot_username(context.bot)
            ref_link = f"https://t.me/{bn}?start=ref_{user.id}"

            if unlocked:
                status = "✅ *You have already unlocked this file!*"
            else:
                cred_ok = my_cred >= req_cred if req_cred > 0 else True
                status  = (
                    f"💰 Credits: {'✅' if cred_ok else f'❌ Need {req_cred}, have {my_cred}'}\n"
                    f"🔗 Share your referral link to earn credits:\n`{ref_link}`"
                )

            await query.edit_message_text(
                f"📊 *FILE STATUS*\n{SEP()}\n\n"
                f"📦 *{prod['name']}*\n\n"
                f"*Requirements:*\n"
                f"• 🔗 Referrals needed: {req_refs}\n"
                f"• 💰 Credits needed: {req_cred}\n\n"
                f"{status}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh",      callback_data=f"progress_{pid}")],
                    [InlineKeyboardButton("🔙 Back to File", callback_data=f"viewprod_{pid}")],
                ]))

        # ── View product ──────────────────────────────────
        elif data.startswith("viewprod_"):
            await edit_product_page(query, context, user.id, data[9:])

        # ── Credit unlock ─────────────────────────────────
        elif data.startswith("unlockc_"):
            pid  = data[8:]
            prod = await get_product(pid)
            if not prod:
                await query.answer("❌ Product not found!", show_alert=True)
                return
            req_c = prod["required_credits"]
            u     = await get_user(user.id)
            mc    = u["credits"] if u else 0
            if mc < req_c:
                await query.answer(
                    f"❌ Need {req_c} credits, you have {mc}.", show_alert=True)
                return
            await deduct_credits(user.id, req_c, f"Unlocked: {prod['name']}")
            await mark_unlocked(user.id, pid)
            await query.edit_message_text(
                f"✅ *Unlocked with Credits!*\n\n"
                f"📦 *{prod['name']}*\n"
                f"💰 {req_c} credits deducted.\n\nSending your file...",
                parse_mode="Markdown")
            await deliver_file(context.bot, user.id, prod)

        # ══ ADMIN SECTION ══════════════════════════════════

        elif data == "admin_panel":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            s = await bot_stats()
            await query.edit_message_text(
                f"⚙️ *ADMIN DASHBOARD*\n{SEP()}\n\n"
                f"• 👥 Users: {s['users']:,}\n"
                f"• 📦 Files: {s['files']:,}\n"
                f"• 🔗 Total Referrals: {s['refs']:,}\n"
                f"• 💰 Credits Given: {s['credits']:,}\n"
                f"• 🎫 Active Codes: {s['codes']:,}\n\n"
                f"{SEP()}\n🛠️ *ACTIONS*",
                parse_mode="Markdown", reply_markup=admin_panel_kb())

        elif data == "adm_upload":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            for k in ("step","up_fname","up_fdesc","up_frefs","up_fcred",
                      "up_fchannels","up_file_id","up_file_type"):
                context.user_data.pop(k, None)
            context.user_data["step"] = "up_file"
            await query.edit_message_text(
                f"📤 *UPLOAD NEW FILE*\n{SEP()}\n\n"
                "*Step 1 of 6:* Send the file\n"
                "(document, video, photo, audio, or voice)\n\n"
                "_Send /start at any time to cancel._",
                parse_mode="Markdown")

        elif data == "adm_manage":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            rows = await DB.fetchall(
                "SELECT id,name,is_active,views,unlocks "
                "FROM products ORDER BY created_at DESC LIMIT 15")
            if not rows:
                await query.edit_message_text(
                    "📋 No files uploaded yet.",
                    reply_markup=back_btn("admin_panel")); return
            btns = []
            for r in rows:
                icon = "✅" if r["is_active"] else "❌"
                btns.append([InlineKeyboardButton(
                    f"{icon} {r['name'][:28]}  V:{r['views']} U:{r['unlocks']}",
                    callback_data=f"toggle_{r['id']}")])
            btns.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
            await query.edit_message_text(
                f"📋 *MANAGE FILES*\n{SEP()}\n✅=Active  ❌=Inactive\nTap to toggle:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(btns))

        elif data == "adm_users":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            rows = await DB.fetchall(
                "SELECT user_id,full_name,username,credits,total_referrals "
                "FROM users ORDER BY joined_date DESC LIMIT 15")
            lines = [f"👥 *RECENT USERS*\n{SEP()}\n"]
            for r in rows:
                d = r["username"] or r["full_name"] or f"User{r['user_id']}"
                lines.append(f"• {d}  💰{r['credits']}  🔗{r['total_referrals']}")
            await query.edit_message_text(
                "\n".join(lines), parse_mode="Markdown",
                reply_markup=back_btn("admin_panel"))

        elif data == "adm_gencode":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            await query.edit_message_text(
                f"🎫 *GENERATE REDEEM CODE*\n{SEP()}\n\n"
                "Command:\n`/createredeem [points] [max_uses] [expiry_days]`\n\n"
                "Example:\n`/createredeem 50 100 30`",
                parse_mode="Markdown",
                reply_markup=back_btn("admin_panel"))

        elif data == "adm_listcodes":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            rows = await DB.fetchall(
                "SELECT code,points,max_uses,used_count,expires_at,is_active "
                "FROM redeem_codes ORDER BY created_at DESC LIMIT 15")
            if not rows:
                await query.edit_message_text(
                    "🎫 No codes yet.\n\nUse `/createredeem pts uses days`",
                    parse_mode="Markdown",
                    reply_markup=back_btn("admin_panel")); return
            lines = [f"🎫 *REDEEM CODES*\n{SEP()}\n"]
            for r in rows:
                s    = "✅" if r["is_active"] else "❌"
                exp  = (r["expires_at"] or "")[:10] or "No expiry"
                left = r["max_uses"] - r["used_count"]
                lines.append(
                    f"{s} `{r['code']}`\n"
                    f"  +{r['points']}pts | {r['used_count']}/{r['max_uses']}"
                    f" | Left:{left} | Exp:{exp}\n")
            await query.edit_message_text(
                "\n".join(lines), parse_mode="Markdown",
                reply_markup=back_btn("admin_panel"))

        elif data == "adm_broadcast":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            await query.edit_message_text(
                f"📢 *BROADCAST PANEL*\n{SEP()}\n\nSelect target audience:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📝 All Users",            callback_data="bc_all")],
                    [InlineKeyboardButton("🎯 Active (last 7 days)", callback_data="bc_active")],
                    [InlineKeyboardButton("🏆 Top Referrers (50)",   callback_data="bc_top")],
                    [InlineKeyboardButton("💎 Premium (500+ cred)",  callback_data="bc_premium")],
                    [InlineKeyboardButton("🔙 Back",                 callback_data="admin_panel")],
                ]))

        elif data in ("bc_all","bc_active","bc_top","bc_premium"):
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            tmap = {
                "bc_all":     "all",
                "bc_active":  "active",
                "bc_top":     "top",
                "bc_premium": "premium",
            }
            context.user_data["bc_target"] = tmap[data]
            context.user_data["step"]      = "bc_msg"
            await query.edit_message_text(
                f"📢 Target: *{tmap[data].upper()}*\n\n"
                "Send your broadcast message:\n"
                "(text, photo, video, or document)\n\n"
                "_Send /start to cancel._",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]]))

        elif data == "bc_yes":
            if user.id != ADMIN_ID: return
            await run_broadcast(query, context)

        elif data == "bc_no":
            if user.id != ADMIN_ID: return
            for k in ("bc_msg","bc_target","step"):
                context.user_data.pop(k, None)
            await query.edit_message_text(
                "❌ Broadcast cancelled.", reply_markup=back_btn("admin_panel"))

        elif data == "adm_stats":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            s      = await bot_stats()
            new7   = await DB.fetchval(
                "SELECT COUNT(*) FROM users WHERE joined_date>=datetime('now','-7 days')",
                default=0)
            act24  = await DB.fetchval(
                "SELECT COUNT(*) FROM users WHERE last_active>=datetime('now','-1 day')",
                default=0)
            unlks  = await DB.fetchval("SELECT COUNT(*) FROM user_unlocks", default=0)
            bcast  = await DB.fetchval("SELECT COUNT(*) FROM broadcast_history", default=0)
            await query.edit_message_text(
                f"📊 *FULL STATISTICS*\n{SEP()}\n\n"
                f"👥 Total Users: {s['users']:,}\n"
                f"🆕 New (7 days): {new7:,}\n"
                f"🟢 Active (24h): {act24:,}\n\n"
                f"📦 Files: {s['files']:,}\n"
                f"🔓 Unlocks: {unlks:,}\n\n"
                f"🔗 Total Referrals: {s['refs']:,}\n"
                f"💰 Credits Given: {s['credits']:,}\n\n"
                f"🎫 Active Codes: {s['codes']:,}\n"
                f"📢 Broadcasts: {bcast:,}\n\n"
                "💾 Database: Turso Cloud ✅",
                parse_mode="Markdown",
                reply_markup=back_btn("admin_panel"))

        elif data.startswith("toggle_"):
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            pid = data[7:]
            row = await DB.fetchone(
                "SELECT is_active FROM products WHERE id=?", (pid,))
            if row:
                new_state = 0 if row["is_active"] == 1 else 1
                await DB.run(
                    "UPDATE products SET is_active=? WHERE id=?", (new_state, pid))
            # Refresh manage view
            rows = await DB.fetchall(
                "SELECT id,name,is_active,views,unlocks "
                "FROM products ORDER BY created_at DESC LIMIT 15")
            btns = []
            for r in rows:
                icon = "✅" if r["is_active"] else "❌"
                btns.append([InlineKeyboardButton(
                    f"{icon} {r['name'][:28]}  V:{r['views']} U:{r['unlocks']}",
                    callback_data=f"toggle_{r['id']}")])
            btns.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
            await query.edit_message_text(
                f"📋 *MANAGE FILES*\n{SEP()}\n✅=Active  ❌=Inactive\nTap to toggle:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(btns))

        else:
            logger.warning(f"Unhandled callback: {data}")

    except Exception as e:
        logger.error(f"callback_handler [{data}]: {e}", exc_info=True)
        try:
            await context.bot.send_message(
                user.id,
                "⚠️ Something went wrong. Please send /start and try again.")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
#                     BROADCAST RUNNER
# ═══════════════════════════════════════════════════════════════

async def run_broadcast(query, context):
    target   = context.user_data.get("bc_target", "all")
    msg_data = context.user_data.get("bc_msg")

    if not msg_data:
        await query.edit_message_text(
            "❌ No message to broadcast.", reply_markup=back_btn("admin_panel"))
        return

    qmap = {
        "all":     "SELECT user_id FROM users WHERE is_banned=0",
        "active":  ("SELECT user_id FROM users WHERE is_banned=0 "
                    "AND last_active>=datetime('now','-7 days')"),
        "top":     ("SELECT user_id FROM users WHERE is_banned=0 "
                    "ORDER BY total_referrals DESC LIMIT 50"),
        "premium": "SELECT user_id FROM users WHERE is_banned=0 AND credits>=500",
    }
    try:
        rows = await DB.fetchall(qmap.get(target, qmap["all"]))
        uids = [r["user_id"] for r in rows]
    except Exception as e:
        await query.edit_message_text(
            f"❌ Failed to fetch users: {e}", reply_markup=back_btn("admin_panel"))
        return

    total  = len(uids)
    sent   = 0
    failed = 0
    mtype  = msg_data.get("type", "text")

    status = await query.edit_message_text(
        f"📢 *Broadcasting...*\n\nTarget: {total:,}\nSent: 0 | Failed: 0",
        parse_mode="Markdown")

    for i, uid in enumerate(uids):
        try:
            async def _send_one(chat_id=uid):
                kw = {"chat_id": chat_id}
                if mtype == "text":
                    await context.bot.send_message(**kw, text=msg_data["content"])
                elif mtype == "photo":
                    await context.bot.send_photo(
                        **kw, photo=msg_data["file_id"],
                        caption=msg_data.get("caption", ""))
                elif mtype == "video":
                    await context.bot.send_video(
                        **kw, video=msg_data["file_id"],
                        caption=msg_data.get("caption", ""))
                elif mtype == "document":
                    await context.bot.send_document(
                        **kw, document=msg_data["file_id"],
                        caption=msg_data.get("caption", ""))

            await asyncio.wait_for(_send_one(), timeout=15.0)
            sent += 1
        except Exception:
            failed += 1

        if (i + 1) % 50 == 0:
            try:
                await status.edit_text(
                    f"📢 *Broadcasting...*\n\n"
                    f"Target: {total:,}\nSent: {sent:,} | Failed: {failed:,}",
                    parse_mode="Markdown")
            except Exception:
                pass

        if (i + 1) % 25 == 0:
            await asyncio.sleep(1)

    try:
        await DB.run(
            "INSERT INTO broadcast_history "
            "(message_type,target_type,total_sent,total_failed,sent_by) "
            "VALUES (?,?,?,?,?)",
            (mtype, target, sent, failed, ADMIN_ID))
    except Exception:
        pass

    for k in ("bc_msg","bc_target","step"):
        context.user_data.pop(k, None)

    await status.edit_text(
        f"✅ *Broadcast Complete!*\n{SEP()}\n\n"
        f"• ✅ Sent: {sent:,}\n"
        f"• ❌ Failed: {failed:,}\n"
        f"• 📋 Total: {total:,}",
        parse_mode="Markdown",
        reply_markup=back_btn("admin_panel"))


# ═══════════════════════════════════════════════════════════════
#                    MESSAGE HANDLER
# ═══════════════════════════════════════════════════════════════

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg  = update.message
    await ensure_user(user.id, user.username or "", user.full_name or "")

    step = context.user_data.get("step", "")

    if user.id == ADMIN_ID and step == "bc_msg":
        await handle_broadcast_input(update, context)
        return

    if user.id == ADMIN_ID and step.startswith("up_"):
        await handle_upload_step(update, context)
        return

    u = await get_user(user.id)
    await msg.reply_text(
        main_menu_text(u), parse_mode="Markdown",
        reply_markup=main_menu_kb(user.id == ADMIN_ID))


async def handle_broadcast_input(update, context):
    msg = update.message
    md  = {}

    if msg.text:
        md = {"type": "text",     "content": msg.text}
    elif msg.photo:
        md = {"type": "photo",    "file_id": msg.photo[-1].file_id,
              "caption": msg.caption or ""}
    elif msg.video:
        md = {"type": "video",    "file_id": msg.video.file_id,
              "caption": msg.caption or ""}
    elif msg.document:
        md = {"type": "document", "file_id": msg.document.file_id,
              "caption": msg.caption or ""}
    else:
        await msg.reply_text(
            "❌ Unsupported type.\nPlease send text, photo, video, or document.")
        return

    context.user_data["bc_msg"] = md
    context.user_data.pop("step", None)

    target  = context.user_data.get("bc_target", "all")
    preview = md.get("content", "") or md.get("caption", "") or f"[{md['type']}]"

    await msg.reply_text(
        f"📢 *BROADCAST PREVIEW*\n{SEP()}\n\n"
        f"Target: *{target.upper()}*\n"
        f"Type: {md['type']}\n"
        f"Preview: {preview[:150]}\n\n"
        "Confirm send?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ YES – Send Now", callback_data="bc_yes"),
             InlineKeyboardButton("❌ NO – Cancel",    callback_data="bc_no")],
        ]))


async def handle_upload_step(update, context):
    msg  = update.message
    step = context.user_data.get("step")

    if step == "up_file":
        if   msg.document: context.user_data.update({"up_file_id": msg.document.file_id,      "up_file_type": "document"})
        elif msg.video:    context.user_data.update({"up_file_id": msg.video.file_id,          "up_file_type": "video"})
        elif msg.photo:    context.user_data.update({"up_file_id": msg.photo[-1].file_id,      "up_file_type": "photo"})
        elif msg.audio:    context.user_data.update({"up_file_id": msg.audio.file_id,          "up_file_type": "audio"})
        elif msg.voice:    context.user_data.update({"up_file_id": msg.voice.file_id,          "up_file_type": "voice"})
        else:
            await msg.reply_text(
                "❌ Please send a file (document, video, photo, audio, or voice).")
            return
        context.user_data["step"] = "up_name"
        await msg.reply_text(
            "✅ File received!\n\n*Step 2 of 6:* Enter the *name/title*:",
            parse_mode="Markdown")

    elif step == "up_name":
        if not msg.text or not msg.text.strip():
            await msg.reply_text("❌ Please send a text name."); return
        context.user_data["up_fname"] = msg.text.strip()
        context.user_data["step"]     = "up_desc"
        await msg.reply_text(
            f"✅ Name: *{context.user_data['up_fname']}*\n\n"
            "*Step 3 of 6:* Enter the *description*:",
            parse_mode="Markdown")

    elif step == "up_desc":
        if not msg.text or not msg.text.strip():
            await msg.reply_text("❌ Please send a text description."); return
        context.user_data["up_fdesc"] = msg.text.strip()
        context.user_data["step"]     = "up_refs"
        await msg.reply_text(
            "✅ Description saved!\n\n"
            "*Step 4 of 6:* Enter *required referrals* (e.g. `5`):",
            parse_mode="Markdown")

    elif step == "up_refs":
        try:
            refs = int((msg.text or "").strip())
            if refs < 0: raise ValueError
        except (ValueError, AttributeError):
            await msg.reply_text("❌ Please enter a valid number (e.g. `5`)."); return
        context.user_data["up_frefs"] = refs
        context.user_data["step"]     = "up_credits"
        await msg.reply_text(
            f"✅ Referrals: *{refs}*\n\n"
            "*Step 5 of 6:* Enter *required credits* (`0` for free):",
            parse_mode="Markdown")

    elif step == "up_credits":
        try:
            cred = int((msg.text or "").strip())
            if cred < 0: raise ValueError
        except (ValueError, AttributeError):
            await msg.reply_text("❌ Please enter a valid number (e.g. `0`)."); return
        context.user_data["up_fcred"] = cred
        context.user_data["step"]     = "up_channels"
        await msg.reply_text(
            f"✅ Credits: *{cred}*\n\n"
            f"*Step 6 of 6:* Add *mandatory channels*\n\n"
            f"Format:\n"
            f"`Channel Name https://t.me/username`\n\n"
            f"Multiple channels (use | to separate):\n"
            f"`My Channel https://t.me/mychan | News https://t.me/newschan`\n\n"
            f"Or type `skip` for no channel requirement.\n\n"
            f"⚠️ Bot must be *admin* in those channels!",
            parse_mode="Markdown")

    elif step == "up_channels":
        raw = (msg.text or "").strip()
        if raw.lower() == "skip":
            channels = []
        else:
            channels = parse_channels_input(raw)
            if not channels:
                await msg.reply_text(
                    "❌ Could not parse channels.\n\n"
                    "Use format:\n`Name https://t.me/username`\n\n"
                    "Or type `skip` to add no channels.",
                    parse_mode="Markdown")
                return
        context.user_data["up_fchannels"] = channels
        await finalize_upload(update, context)


async def finalize_upload(update, context):
    msg = update.message
    d   = context.user_data
    pid = new_product_id()

    try:
        stmts = [(
            """INSERT INTO products
               (id,name,description,file_id,file_type,
                required_refs,required_credits,admin_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (pid, d["up_fname"], d["up_fdesc"], d["up_file_id"],
             d["up_file_type"], d["up_frefs"], d["up_fcred"], ADMIN_ID)
        )]
        for ch in d.get("up_fchannels", []):
            stmts.append((
                "INSERT INTO product_channels "
                "(product_id,display_name,channel_url,channel_user) "
                "VALUES (?,?,?,?)",
                (pid, ch["display_name"], ch["channel_url"], ch["channel_user"])))

        await DB.run_many(stmts)

        bn   = await get_bot_username(context.bot)
        link = f"https://t.me/{bn}?start=product_{pid}"
        chs_summary = "\n".join(
            f"  • {ch['display_name']} → {ch['channel_url']}"
            for ch in d.get("up_fchannels", [])
        ) or "  None"

        for k in ("step","up_fname","up_fdesc","up_frefs","up_fcred",
                  "up_fchannels","up_file_id","up_file_type"):
            context.user_data.pop(k, None)

        await msg.reply_text(
            f"✅ *FILE UPLOADED SUCCESSFULLY!*\n{SEP()}\n\n"
            f"📦 Name: *{d['up_fname']}*\n"
            f"🔑 Product ID: `{pid}`\n"
            f"🔗 Required Referrals: {d['up_frefs']}\n"
            f"💰 Required Credits: {d['up_fcred']}\n"
            f"📢 Channels:\n{chs_summary}\n\n"
            f"🌐 *Product Link:*\n`{link}`\n\n"
            "🚀 Share this link with users!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")]]))

    except Exception as e:
        logger.error(f"finalize_upload: {e}")
        for k in ("step","up_fname","up_fdesc","up_frefs","up_fcred",
                  "up_fchannels","up_file_id","up_file_type"):
            context.user_data.pop(k, None)
        await msg.reply_text(
            f"❌ *Upload failed!*\n\nError: {e}\n\nPlease try again.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")]]))


# ═══════════════════════════════════════════════════════════════
#                        COMMANDS
# ═══════════════════════════════════════════════════════════════

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("🔒 *Access Denied!*", parse_mode="Markdown")
        return
    await ensure_user(user.id, user.username or "", user.full_name or "")
    s = await bot_stats()
    await update.message.reply_text(
        f"⚙️ *ADMIN DASHBOARD*\n{SEP()}\n\n"
        f"• 👥 Users: {s['users']:,}\n"
        f"• 📦 Files: {s['files']:,}\n"
        f"• 🔗 Referrals: {s['refs']:,}\n"
        f"• 💰 Credits Given: {s['credits']:,}\n"
        f"• 🎫 Active Codes: {s['codes']:,}\n\n"
        f"{SEP()}\n🛠️ *ACTIONS*",
        parse_mode="Markdown", reply_markup=admin_panel_kb())


async def cmd_createredeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!"); return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "❌ Usage: `/createredeem [points] [max_uses] [expiry_days]`\n\n"
            "Example: `/createredeem 50 100 30`",
            parse_mode="Markdown"); return
    try:
        pts  = int(args[0])
        uses = int(args[1])
        days = int(args[2])
        if pts <= 0 or uses <= 0 or days <= 0: raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text("❌ All values must be positive integers."); return

    code = new_redeem_code()
    exp  = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        await DB.run(
            "INSERT INTO redeem_codes "
            "(code,points,max_uses,created_by,expires_at) VALUES (?,?,?,?,?)",
            (code, pts, uses, ADMIN_ID, exp))
        await update.message.reply_text(
            f"✅ *REDEEM CODE CREATED!*\n{SEP()}\n\n"
            f"🎫 Code: `{code}`\n"
            f"💰 Points: {pts}\n"
            f"👥 Max Uses: {uses}\n"
            f"📅 Expires: {exp[:10]}\n\n"
            "Share this code with users!",
            parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")


async def cmd_listcodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!"); return
    rows = await DB.fetchall(
        "SELECT code,points,max_uses,used_count,expires_at,is_active "
        "FROM redeem_codes ORDER BY created_at DESC")
    if not rows:
        await update.message.reply_text("🎫 No redeem codes yet."); return
    lines = [f"🎫 *ALL CODES*\n{SEP()}\n"]
    for r in rows:
        s   = "✅" if r["is_active"] else "❌"
        exp = (r["expires_at"] or "")[:10] or "No expiry"
        lines.append(
            f"{s} `{r['code']}` | +{r['points']}pts | "
            f"{r['used_count']}/{r['max_uses']} | Exp:{exp}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_deletecode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!"); return
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: `/deletecode CODE`", parse_mode="Markdown"); return
    code = context.args[0].upper()
    await DB.run("UPDATE redeem_codes SET is_active=0 WHERE code=?", (code,))
    await update.message.reply_text(
        f"✅ Code `{code}` deactivated.", parse_mode="Markdown")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!"); return
    await update.message.reply_text(
        f"📢 *BROADCAST PANEL*\n{SEP()}\n\nSelect target audience:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 All Users",            callback_data="bc_all")],
            [InlineKeyboardButton("🎯 Active (last 7 days)", callback_data="bc_active")],
            [InlineKeyboardButton("🏆 Top Referrers (50)",   callback_data="bc_top")],
            [InlineKeyboardButton("💎 Premium (500+ cred)",  callback_data="bc_premium")],
        ]))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!"); return
    s = await bot_stats()
    await update.message.reply_text(
        f"📊 *BOT STATS*\n{SEP()}\n\n"
        f"👥 Users: {s['users']:,}\n"
        f"📦 Files: {s['files']:,}\n"
        f"🔗 Referrals: {s['refs']:,}\n"
        f"💰 Credits: {s['credits']:,}\n"
        f"🎫 Codes: {s['codes']:,}\n\n"
        "💾 Database: Turso Cloud ✅",
        parse_mode="Markdown")


async def cmd_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")

    if not context.args:
        await update.message.reply_text(
            "💰 *REDEEM CODE*\n\nUsage: `/redeem YOUR-CODE`",
            parse_mode="Markdown"); return

    code = context.args[0].upper()

    try:
        row = await DB.fetchone(
            "SELECT points,max_uses,used_count,expires_at,is_active "
            "FROM redeem_codes WHERE code=?", (code,))

        if not row:
            await update.message.reply_text(
                "❌ *Invalid code!*", parse_mode="Markdown"); return
        if not row["is_active"]:
            await update.message.reply_text(
                "❌ *Code is no longer active!*", parse_mode="Markdown"); return
        if row["used_count"] >= row["max_uses"]:
            await update.message.reply_text(
                "❌ *Code reached maximum uses!*", parse_mode="Markdown"); return
        if row["expires_at"]:
            try:
                if datetime.now() > datetime.strptime(
                        row["expires_at"][:19], "%Y-%m-%d %H:%M:%S"):
                    await update.message.reply_text(
                        "❌ *Code expired!*", parse_mode="Markdown"); return
            except Exception:
                pass

        already = await DB.fetchone(
            "SELECT 1 FROM redeem_usage WHERE code=? AND user_id=?",
            (code, user.id))
        if already:
            await update.message.reply_text(
                "❌ *Code already used by you!*", parse_mode="Markdown"); return

        await DB.run_many([
            ("INSERT INTO redeem_usage (code,user_id) VALUES (?,?)", (code, user.id)),
            ("UPDATE redeem_codes SET used_count=used_count+1 WHERE code=?", (code,)),
        ])
        await add_credits(user.id, row["points"], f"Redeem: {code}")
        u = await get_user(user.id)

        await update.message.reply_text(
            f"✅ *CODE REDEEMED SUCCESSFULLY!*\n{SEP()}\n\n"
            f"• Code: `{code}`\n"
            f"• +{row['points']} Credits added! 💰\n"
            f"• New balance: *{u['credits']:,}* credits\n\n"
            "Thank you for using Senzo Premium! 🌟",
            parse_mode="Markdown",
            reply_markup=back_btn())

    except Exception as e:
        logger.error(f"cmd_redeem: {e}")
        await update.message.reply_text("❌ An error occurred. Please try again.")


async def cmd_myrefs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")
    total_refs = await get_user_ref_count(user.id)
    bn         = await get_bot_username(context.bot)
    ref_link   = f"https://t.me/{bn}?start=ref_{user.id}"
    await update.message.reply_text(
        f"🔗 *MY REFERRALS*\n{SEP()}\n\n"
        f"👥 Total people referred: *{total_refs}*\n"
        f"💰 Credits earned: *{total_refs * CREDITS_PER_REFERRAL:,}*\n\n"
        f"{SEP()}\n\n"
        f"🔗 *Your Referral Link:*\n`{ref_link}`\n\n"
        f"Share this — each new join = *+{CREDITS_PER_REFERRAL} credits*! 💰",
        parse_mode="Markdown")


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")
    u = await get_user(user.id)
    if not u: return
    unlocked   = await DB.fetchval(
        "SELECT COUNT(*) FROM user_unlocks WHERE user_id=?",
        (user.id,), default=0)
    bn         = await get_bot_username(context.bot)
    ref_link   = f"https://t.me/{bn}?start=ref_{user.id}"
    joined     = (u.get("joined_date") or "")[:10] or "Unknown"
    uname      = f"@{u['username']}" if u.get("username") else "No username"
    await update.message.reply_text(
        f"👤 *MY PROFILE*\n{SEP()}\n\n"
        f"• User ID: `{user.id}`\n"
        f"• Username: {uname}\n"
        f"• Joined: {joined}\n"
        f"• Rank: {u['rank']}\n\n"
        f"💰 Credits: {u['credits']:,}\n"
        f"🔗 Total Referrals: {u['total_referrals']:,}\n"
        f"📦 Unlocked Files: {unlocked}\n\n"
        f"{SEP()}\n\n"
        f"🔗 *Your Referral Link:*\n`{ref_link}`",
        parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user     = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")
    bn       = await get_bot_username(context.bot)
    ref_link = f"https://t.me/{bn}?start=ref_{user.id}"
    await update.message.reply_text(
        f"❓ *HELP & COMMANDS*\n{SEP()}\n\n"
        "*User Commands:*\n"
        "• `/start` – Main menu\n"
        "• `/redeem CODE` – Redeem a code\n"
        "• `/myrefs` – Your referral link & stats\n"
        "• `/profile` – Your profile\n\n"
        f"*Your Referral Link:*\n`{ref_link}`\n\n"
        f"Share it — each join = *+{CREDITS_PER_REFERRAL} credits* 💰\n\n"
        "_Powered by Senzo Technologies_ 🌟",
        parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════
#                     ERROR HANDLER
# ═══════════════════════════════════════════════════════════════

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)


# ═══════════════════════════════════════════════════════════════
#                          MAIN
# ═══════════════════════════════════════════════════════════════

async def post_init(application):
    await init_db()
    await get_bot_username(application.bot)
    logger.info("🚀 Senzo Premium Bot v6.0 started!")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("admin",        cmd_admin))
    app.add_handler(CommandHandler("createredeem", cmd_createredeem))
    app.add_handler(CommandHandler("listcodes",    cmd_listcodes))
    app.add_handler(CommandHandler("deletecode",   cmd_deletecode))
    app.add_handler(CommandHandler("broadcast",    cmd_broadcast))
    app.add_handler(CommandHandler("stats",        cmd_stats))
    app.add_handler(CommandHandler("redeem",       cmd_redeem))
    app.add_handler(CommandHandler("myrefs",       cmd_myrefs))
    app.add_handler(CommandHandler("profile",      cmd_profile))
    app.add_handler(CommandHandler("help",         cmd_help))

    app.add_handler(CallbackQueryHandler(callback_handler))

    app.add_handler(MessageHandler(
        filters.TEXT
        | filters.Document.ALL
        | filters.PHOTO
        | filters.VIDEO
        | filters.AUDIO
        | filters.VOICE,
        message_handler))

    app.add_error_handler(error_handler)

    logger.info("✅ All handlers registered. Starting polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
