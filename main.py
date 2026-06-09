"""
SENZO PREMIUM BOT v5.0 - Turso Cloud Database Edition
All features + all bugs fixed + Turso HTTP API (no special driver needed)
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
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)
from telegram.error import BadRequest, Forbidden

# ════════════════════════════════════════
#            CONFIGURATION
# ════════════════════════════════════════

BOT_TOKEN  = "8863632618:AAHybJVTAKAGoLGrF9CP_SvYhdUwo8j_eQg"
ADMIN_ID   = 8105949422

# --- Turso Config ---
# Get these from https://app.turso.tech
# After creating a database, go to: Database > Connect > copy URL and Token
TURSO_URL   = "libsql://hosting-bot-filehosting.aws-ap-south-1.turso.io"   # e.g. https://senzo-abc123.turso.io
TURSO_TOKEN = "YOUR_AUTH_TOKEN_HERE"              # the token you generated

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

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


# ════════════════════════════════════════
#        TURSO HTTP API WRAPPER
# ════════════════════════════════════════

class TursoDB:
    def __init__(self, url: str, token: str):
        self.url     = url.rstrip("/") + "/v2/pipeline"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self._sess: Optional[aiohttp.ClientSession] = None

    async def _session(self) -> aiohttp.ClientSession:
        if not self._sess or self._sess.closed:
            self._sess = aiohttp.ClientSession(headers=self.headers)
        return self._sess

    def _arg(self, v) -> dict:
        if v is None:              return {"type": "null",    "value": None}
        if isinstance(v, bool):    return {"type": "integer", "value": "1" if v else "0"}
        if isinstance(v, int):     return {"type": "integer", "value": str(v)}
        if isinstance(v, float):   return {"type": "float",   "value": str(v)}
        return {"type": "text", "value": str(v)}

    def _stmt(self, sql: str, params: tuple = ()) -> dict:
        return {"sql": sql, "args": [self._arg(p) for p in params]}

    async def _run(self, requests: list) -> list:
        requests.append({"type": "close"})
        sess = await self._session()
        async with sess.post(self.url, json={"requests": requests}) as r:
            r.raise_for_status()
            data = await r.json()
        out = []
        for res in data["results"]:
            if res["type"] == "error":
                raise Exception(f"Turso: {res.get('error', res)}")
            out.append(res["response"]["result"])
        return out

    def _rows(self, result: dict) -> list[dict]:
        cols = [c["name"] for c in result.get("cols", [])]
        rows = []
        for raw in result.get("rows", []):
            obj = {}
            for i, col in enumerate(cols):
                cell = raw[i]
                val  = cell.get("value") if isinstance(cell, dict) else cell
                typ  = cell.get("type")  if isinstance(cell, dict) else None
                if typ == "integer" and val is not None:
                    try: val = int(val)
                    except: pass
                elif typ == "float" and val is not None:
                    try: val = float(val)
                    except: pass
                obj[col] = val
            rows.append(obj)
        return rows

    async def execute(self, sql: str, params: tuple = ()) -> dict:
        results = await self._run([{"type": "execute", "stmt": self._stmt(sql, params)}])
        return results[0]

    async def executemany(self, stmts: list) -> list:
        reqs = [{"type": "execute", "stmt": self._stmt(s, p)} for s, p in stmts]
        return await self._run(reqs)

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        result = await self.execute(sql, params)
        return self._rows(result)

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        rows = await self.fetchall(sql, params)
        return rows[0] if rows else None

    async def fetchval(self, sql: str, params: tuple = (), default=0):
        row = await self.fetchone(sql, params)
        if row:
            v = list(row.values())[0]
            return v if v is not None else default
        return default


db = TursoDB(TURSO_URL, TURSO_TOKEN)


# ════════════════════════════════════════
#           DATABASE INIT
# ════════════════════════════════════════

async def init_db():
    logger.info(f"Connecting to Turso: {TURSO_URL}")
    stmts = [
        ("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '',
            full_name TEXT DEFAULT '', credits INTEGER DEFAULT 0,
            total_referrals INTEGER DEFAULT 0, rank TEXT DEFAULT 'Bronze 🥉',
            joined_date TEXT DEFAULT (datetime('now')),
            last_active TEXT DEFAULT (datetime('now')), is_banned INTEGER DEFAULT 0)""", ()),
        ("""CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
            file_id TEXT NOT NULL, file_type TEXT NOT NULL,
            required_refs INTEGER DEFAULT 1, required_credits INTEGER DEFAULT 0,
            admin_id INTEGER, is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            views INTEGER DEFAULT 0, unlocks INTEGER DEFAULT 0)""", ()),
        ("""CREATE TABLE IF NOT EXISTS product_channels (
            id INTEGER PRIMARY KEY, product_id TEXT NOT NULL,
            channel_username TEXT NOT NULL)""", ()),
        ("""CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY, referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL, product_id TEXT NOT NULL,
            status TEXT DEFAULT 'completed', date TEXT DEFAULT (datetime('now')),
            UNIQUE(referrer_id, referred_id, product_id))""", ()),
        ("""CREATE TABLE IF NOT EXISTS user_unlocks (
            user_id INTEGER NOT NULL, product_id TEXT NOT NULL,
            unlocked_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, product_id))""", ()),
        ("""CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL, type TEXT NOT NULL,
            description TEXT DEFAULT '', date TEXT DEFAULT (datetime('now')))""", ()),
        ("""CREATE TABLE IF NOT EXISTS redeem_codes (
            id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL,
            points INTEGER NOT NULL, max_uses INTEGER NOT NULL,
            used_count INTEGER DEFAULT 0, created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT, is_active INTEGER DEFAULT 1)""", ()),
        ("""CREATE TABLE IF NOT EXISTS redeem_usage (
            id INTEGER PRIMARY KEY, code TEXT NOT NULL,
            user_id INTEGER NOT NULL, redeemed_at TEXT DEFAULT (datetime('now')),
            UNIQUE(code, user_id))""", ()),
        ("""CREATE TABLE IF NOT EXISTS broadcast_history (
            id INTEGER PRIMARY KEY, message_type TEXT, target_type TEXT,
            total_sent INTEGER DEFAULT 0, total_failed INTEGER DEFAULT 0,
            sent_by INTEGER, sent_at TEXT DEFAULT (datetime('now')))""", ()),
    ]
    await db.executemany(stmts)
    logger.info("Database ready.")


# ════════════════════════════════════════
#              HELPERS
# ════════════════════════════════════════

def S() -> str:
    return "━━━━━━━━━━━━━━━━━━━━━━"

def get_rank(refs: int) -> str:
    r = RANKS[0][1]
    for thr, name in RANKS:
        if refs >= thr: r = name
    return r

def gen_pid() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def gen_code() -> str:
    return "SENZO-" + "-".join(
        "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
        for _ in range(2)
    )

async def bot_name(bot) -> str:
    global _BOT_USERNAME
    if not _BOT_USERNAME:
        _BOT_USERNAME = (await bot.get_me()).username
    return _BOT_USERNAME

def bk(cb: str = "main_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=cb)]])


# ════════════════════════════════════════
#           DB FUNCTIONS
# ════════════════════════════════════════

async def ensure_user(uid: int, uname: str, fname: str):
    try:
        await db.execute("""
            INSERT INTO users (user_id, username, full_name) VALUES (?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username, full_name=excluded.full_name,
            last_active=datetime('now')""", (uid, uname or "", fname or ""))
    except Exception as e:
        logger.error(f"ensure_user: {e}")

async def get_user(uid: int) -> Optional[dict]:
    try: return await db.fetchone("SELECT * FROM users WHERE user_id=?", (uid,))
    except Exception as e: logger.error(f"get_user: {e}"); return None

async def add_credits(uid: int, amt: int, desc: str):
    try:
        await db.executemany([
            ("UPDATE users SET credits=credits+? WHERE user_id=?", (amt, uid)),
            ("INSERT INTO transactions (user_id,amount,type,description) VALUES (?,?,'credit',?)", (uid, amt, desc)),
        ])
    except Exception as e: logger.error(f"add_credits: {e}")

async def deduct_credits(uid: int, amt: int, desc: str):
    try:
        await db.executemany([
            ("UPDATE users SET credits=credits-? WHERE user_id=?", (amt, uid)),
            ("INSERT INTO transactions (user_id,amount,type,description) VALUES (?,?,'debit',?)", (uid, amt, desc)),
        ])
    except Exception as e: logger.error(f"deduct_credits: {e}")

async def upd_rank(uid: int):
    try:
        u = await get_user(uid)
        if u: await db.execute("UPDATE users SET rank=? WHERE user_id=?", (get_rank(u["total_referrals"]), uid))
    except Exception as e: logger.error(f"upd_rank: {e}")

async def get_product(pid: str, inc_inactive: bool = False) -> Optional[dict]:
    try:
        q = "SELECT * FROM products WHERE id=?" if inc_inactive else "SELECT * FROM products WHERE id=? AND is_active=1"
        return await db.fetchone(q, (pid,))
    except Exception as e: logger.error(f"get_product: {e}"); return None

async def get_channels(pid: str) -> list:
    try:
        rows = await db.fetchall("SELECT channel_username FROM product_channels WHERE product_id=?", (pid,))
        return [r["channel_username"] for r in rows]
    except Exception as e: logger.error(f"get_channels: {e}"); return []

async def count_refs(rid: int, pid: str) -> int:
    try: return await db.fetchval("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND product_id=? AND status='completed'", (rid, pid), default=0)
    except Exception as e: logger.error(f"count_refs: {e}"); return 0

async def is_unlocked(uid: int, pid: str) -> bool:
    try: return (await db.fetchone("SELECT 1 FROM user_unlocks WHERE user_id=? AND product_id=?", (uid, pid))) is not None
    except Exception as e: logger.error(f"is_unlocked: {e}"); return False

async def do_unlock(uid: int, pid: str):
    try:
        await db.executemany([
            ("INSERT OR IGNORE INTO user_unlocks (user_id,product_id) VALUES (?,?)", (uid, pid)),
            ("UPDATE products SET unlocks=unlocks+1 WHERE id=?", (pid,)),
        ])
    except Exception as e: logger.error(f"do_unlock: {e}")

async def chk_joined(bot, uid: int, ch: str) -> bool:
    try:
        c = ch if ch.startswith("@") else f"@{ch}"
        m = await asyncio.wait_for(bot.get_chat_member(chat_id=c, user_id=uid), timeout=8.0)
        return m.status not in ("left","kicked","banned")
    except asyncio.TimeoutError: return False
    except (BadRequest, Forbidden): return True
    except Exception: return True

async def send_file(bot, uid: int, prod: dict) -> bool:
    try:
        fid = prod["file_id"]; ft = prod["file_type"]
        cap = f"📦 *{prod['name']}*\n\n✅ _Powered by Senzo Premium_"
        kw  = dict(chat_id=uid, caption=cap, parse_mode="Markdown")
        if   ft == "document": await bot.send_document(document=fid, **kw)
        elif ft == "video":    await bot.send_video(video=fid, **kw)
        elif ft == "photo":    await bot.send_photo(photo=fid, **kw)
        elif ft == "audio":    await bot.send_audio(audio=fid, **kw)
        elif ft == "voice":    await bot.send_voice(voice=fid, **kw)
        else:                  await bot.send_document(document=fid, **kw)
        return True
    except Forbidden: logger.warning(f"User {uid} blocked bot"); return False
    except Exception as e: logger.error(f"send_file {uid}: {e}"); return False

async def stats() -> dict:
    try:
        s = {}
        for k, q in [
            ("users",   "SELECT COUNT(*) FROM users"),
            ("files",   "SELECT COUNT(*) FROM products WHERE is_active=1"),
            ("refs",    "SELECT COUNT(*) FROM referrals WHERE status='completed'"),
            ("credits", "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='credit'"),
            ("codes",   "SELECT COUNT(*) FROM redeem_codes WHERE is_active=1"),
        ]:
            s[k] = await db.fetchval(q, default=0)
        r = await db.fetchone("SELECT sent_at FROM broadcast_history ORDER BY sent_at DESC LIMIT 1")
        s["last_bc"] = r["sent_at"] if r else "Never"
        return s
    except Exception as e: logger.error(f"stats: {e}"); return {k:0 for k in ["users","files","refs","credits","codes","last_bc"]}


# ════════════════════════════════════════
#              UI BUILDERS
# ════════════════════════════════════════

def menu_text(u: dict) -> str:
    return (
        f"🌟 *SENZO PREMIUM* 🌟\n{S()}\n"
        f"*Welcome, {u.get('full_name') or 'User'}!*\n\n"
        f"👤 *YOUR STATS*\n"
        f"• Rank: {u.get('rank','Bronze 🥉')}\n"
        f"• Credits: {u.get('credits',0):,} 💰\n"
        f"• Referrals: {u.get('total_referrals',0):,} 🔗\n\n{S()}\n\n📱 *MAIN MENU*"
    )

def menu_kb(admin: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if admin: rows.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    rows += [
        [InlineKeyboardButton("📦 Browse Files", callback_data="browse_files")],
        [InlineKeyboardButton("🔗 My Referrals", callback_data="my_referrals"),
         InlineKeyboardButton("🏆 Leaderboard",  callback_data="leaderboard")],
        [InlineKeyboardButton("💰 Redeem Code",  callback_data="redeem_info"),
         InlineKeyboardButton("👤 My Profile",   callback_data="my_profile")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    return InlineKeyboardMarkup(rows)

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload File",          callback_data="admin_upload")],
        [InlineKeyboardButton("📋 Manage Files",         callback_data="admin_manage"),
         InlineKeyboardButton("👥 View Users",           callback_data="admin_users")],
        [InlineKeyboardButton("🎫 Generate Code",        callback_data="admin_gen_code")],
        [InlineKeyboardButton("🔗 All Codes",            callback_data="admin_list_codes")],
        [InlineKeyboardButton("📢 Broadcast",            callback_data="admin_broadcast")],
        [InlineKeyboardButton("📊 Full Stats",           callback_data="admin_stats")],
        [InlineKeyboardButton("🔙 Main Menu",            callback_data="main_menu")],
    ])


# ════════════════════════════════════════
#         PRODUCT PAGE HELPERS
# ════════════════════════════════════════

def prod_text(prod: dict, ref_count: int, my_cred: int) -> str:
    req_refs = prod["required_refs"]; req_cred = prod["required_credits"]
    bar_f = min(10, int(ref_count/req_refs*10)) if req_refs else 10
    bar   = "▓"*bar_f + "░"*(10-bar_f)
    return (
        f"📦 *{prod['name']}*\n{S()}\n\n"
        f"📝 {prod['description']}\n\n"
        f"*REQUIREMENTS:*\n"
        f"• 🔗 Referrals: {ref_count}/{req_refs}  [{bar}]\n"
        f"• 💰 Credits: {req_cred} (You have: {my_cred})\n\n{S()}\n"
        f"💡 Each referral = {CREDITS_PER_REFERRAL} credits + progress"
    )

def prod_kb(pid: str, req_cred: int, my_cred: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔗 Get Referral Link", callback_data=f"getref_{pid}")],
        [InlineKeyboardButton("📊 Check Progress",    callback_data=f"progress_{pid}")],
    ]
    if req_cred > 0 and my_cred >= req_cred:
        rows.append([InlineKeyboardButton("💰 Unlock with Credits", callback_data=f"unlockc_{pid}")])
    rows.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)

async def show_prod_msg(target, context, user, pid: str):
    prod = await get_product(pid)
    if not prod:
        txt = "❌ Product not found."
        if target: await target.reply_text(txt)
        else:      await context.bot.send_message(user.id, txt)
        return
    already = await is_unlocked(user.id, pid)
    if already:
        txt = "🎉 Already unlocked! Sending again..."
        if target: await target.reply_text(txt)
        else:      await context.bot.send_message(user.id, txt)
        await send_file(context.bot, user.id, prod); return
    rc   = await count_refs(user.id, pid)
    ud   = await get_user(user.id)
    mc   = ud["credits"] if ud else 0
    text = prod_text(prod, rc, mc)
    kb   = prod_kb(pid, prod["required_credits"], mc)
    if target: await target.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    else:      await context.bot.send_message(user.id, text, parse_mode="Markdown", reply_markup=kb)

async def show_prod_edit(query, context, user, pid: str):
    prod = await get_product(pid)
    if not prod:
        ia = await get_product(pid, inc_inactive=True)
        await query.edit_message_text("⚠️ Unavailable." if ia else "❌ Not found.", reply_markup=bk()); return
    already = await is_unlocked(user.id, pid)
    if already:
        await query.edit_message_text("🎉 Already unlocked! Sending...", parse_mode="Markdown")
        await send_file(context.bot, user.id, prod); return
    rc   = await count_refs(user.id, pid)
    ud   = await get_user(user.id)
    mc   = ud["credits"] if ud else 0
    await query.edit_message_text(prod_text(prod, rc, mc), parse_mode="Markdown",
                                  reply_markup=prod_kb(pid, prod["required_credits"], mc))


# ════════════════════════════════════════
#           /start COMMAND
# ════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")
    for k in ("step","bc_target","bc_msg_data","fname","fdesc","frefs","fcred","fchannels","file_id","file_type"):
        context.user_data.pop(k, None)

    args = context.args or []
    if args:
        arg = args[0]
        if arg.startswith("product_"):
            await handle_product(update, context, user, arg[8:]); return
        if arg.startswith("ref_"):
            parts = arg.split("_", 2)
            if len(parts) == 3:
                try: await handle_ref_join(update, context, user, int(parts[1]), parts[2]); return
                except ValueError: pass

    u = await get_user(user.id)
    await update.message.reply_text(menu_text(u), parse_mode="Markdown",
                                    reply_markup=menu_kb(user.id == ADMIN_ID))


# ════════════════════════════════════════
#        PRODUCT & REFERRAL FLOW
# ════════════════════════════════════════

async def handle_product(update, context, user, pid: str):
    prod = await get_product(pid)
    if not prod:
        ia = await get_product(pid, inc_inactive=True)
        await update.message.reply_text("⚠️ Product unavailable." if ia else "❌ Invalid link.", parse_mode="Markdown"); return
    asyncio.create_task(_views(pid))
    chs = await get_channels(pid)
    if chs: await show_gate(update.message, context, user, pid, chs, prod["name"])
    else:   await show_prod_msg(update.message, context, user, pid)

async def _views(pid):
    try: await db.execute("UPDATE products SET views=views+1 WHERE id=?", (pid,))
    except: pass

async def show_gate(target, context, user, pid: str, chs: list, name: str):
    btns = [[InlineKeyboardButton(f"📢 Join {ch}", url=f"https://t.me/{ch.lstrip('@')}")] for ch in chs]
    btns.append([InlineKeyboardButton("✅ I've Joined – Verify Now", callback_data=f"verify_{pid}")])
    text = (f"🔒 *CHANNEL VERIFICATION*\n{S()}\n\n📦 *{name}*\n\n"
            f"Join ALL channels:\n\n" + "\n".join(f"📢 {ch}" for ch in chs) +
            f"\n\n{S()}\n⚠️ Tap Verify after joining all.")
    kb = InlineKeyboardMarkup(btns)
    if target: await target.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    else:      await context.bot.send_message(user.id, text, parse_mode="Markdown", reply_markup=kb)

async def handle_ref_join(update, context, user, ref_id: int, pid: str):
    if ref_id == user.id:
        await update.message.reply_text("❌ *Cannot refer yourself!*", parse_mode="Markdown")
        u = await get_user(user.id)
        await update.message.reply_text(menu_text(u), parse_mode="Markdown", reply_markup=menu_kb(user.id == ADMIN_ID)); return

    prod = await get_product(pid)
    if not prod:
        u = await get_user(user.id)
        await update.message.reply_text(menu_text(u), parse_mode="Markdown", reply_markup=menu_kb(user.id == ADMIN_ID)); return

    new = False
    try:
        await db.executemany([
            ("INSERT INTO referrals (referrer_id,referred_id,product_id,status) VALUES (?,?,?,'completed')", (ref_id, user.id, pid)),
            ("UPDATE users SET total_referrals=total_referrals+1 WHERE user_id=?", (ref_id,)),
        ])
        new = True
    except: pass

    if new:
        await add_credits(ref_id, CREDITS_PER_REFERRAL, f"Referral – {prod['name']}")
        await upd_rank(ref_id)
        rc = await count_refs(ref_id, pid)
        rq = prod["required_refs"]
        if rc >= rq and not await is_unlocked(ref_id, pid):
            await do_unlock(ref_id, pid)
            try:
                await context.bot.send_message(ref_id,
                    f"🎉 *File Unlocked!*\n{S()}\n\n📦 *{prod['name']}*\n\n✅ All {rq} referrals done!\nHere is your file:",
                    parse_mode="Markdown")
                await send_file(context.bot, ref_id, prod)
            except Exception as e: logger.warning(f"notify {ref_id}: {e}")
        else:
            try:
                await context.bot.send_message(ref_id,
                    f"🎉 *New Referral! +{CREDITS_PER_REFERRAL} credits*\n\n📦 *{prod['name']}*\nProgress: {rc}/{rq}",
                    parse_mode="Markdown")
            except Exception as e: logger.warning(f"notify {ref_id}: {e}")
        await update.message.reply_text("✅ *Welcome to Senzo Premium!*\n\nYou joined via referral! 🎉", parse_mode="Markdown")
    else:
        await update.message.reply_text("👋 *Welcome back!*\n\nYou already used this referral link.", parse_mode="Markdown")

    u = await get_user(user.id)
    await update.message.reply_text(menu_text(u), parse_mode="Markdown", reply_markup=menu_kb(user.id == ADMIN_ID))


# ════════════════════════════════════════
#        CALLBACK QUERY HANDLER
# ════════════════════════════════════════

async def cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    user = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")
    try: await q.answer()
    except: pass

    try:
        # ── Main Menu
        if data == "main_menu":
            u = await get_user(user.id)
            await q.edit_message_text(menu_text(u), parse_mode="Markdown", reply_markup=menu_kb(user.id == ADMIN_ID))

        # ── Browse Files
        elif data == "browse_files":
            bn = await bot_name(context.bot)
            rows = await db.fetchall("SELECT id,name,required_refs,required_credits FROM products WHERE is_active=1 ORDER BY created_at DESC LIMIT 20")
            if not rows:
                await q.edit_message_text("📦 No files yet.", reply_markup=bk()); return
            btns = []
            for r in rows:
                lbl = f"📦 {r['name']}  (🔗{r['required_refs']}"
                if r["required_credits"]: lbl += f" | 💰{r['required_credits']}"
                lbl += ")"
                btns.append([InlineKeyboardButton(lbl, url=f"https://t.me/{bn}?start=product_{r['id']}")])
            btns.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
            await q.edit_message_text(f"📦 *AVAILABLE FILES*\n{S()}\n\nTap any file:", parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup(btns))

        # ── My Referrals
        elif data == "my_referrals":
            rows = await db.fetchall("""
                SELECT r.product_id, p.name, COUNT(*) as cnt, p.required_refs
                FROM referrals r LEFT JOIN products p ON r.product_id=p.id
                WHERE r.referrer_id=? AND r.status='completed' GROUP BY r.product_id""", (user.id,))
            if not rows:
                await q.edit_message_text(f"🔗 *MY REFERRALS*\n{S()}\n\nNo referrals yet!", parse_mode="Markdown", reply_markup=bk()); return
            lines = [f"🔗 *MY REFERRALS*\n{S()}\n"]
            for r in rows:
                cnt = r["cnt"]; req = r["required_refs"] or 1
                bf  = min(10, int(cnt/req*10)); bar = "▓"*bf + "░"*(10-bf)
                lines.append(f"📦 *{r['name'] or r['product_id']}*\n[{bar}] {'✅' if cnt>=req else f'{cnt}/{req}'}\n")
            await q.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=bk())

        # ── Leaderboard
        elif data == "leaderboard":
            rows = await db.fetchall("SELECT user_id,full_name,username,total_referrals FROM users ORDER BY total_referrals DESC LIMIT 10")
            myrank = await db.fetchval("SELECT COUNT(*)+1 FROM users WHERE total_referrals>(SELECT total_referrals FROM users WHERE user_id=?)", (user.id,), default=1)
            ur = await db.fetchone("SELECT total_referrals FROM users WHERE user_id=?", (user.id,))
            myrefs = ur["total_referrals"] if ur else 0
            medals = ["🥇","🥈","🥉"]+[""]*7
            lines = [f"🏆 *TOP REFERRERS*\n{S()}\n"]
            for i, r in enumerate(rows):
                d = r["username"] or r["full_name"] or f"User{r['user_id']}"
                lines.append(f"{medals[i] if i<3 else f'{i+1}.'} {d} – {r['total_referrals']:,}")
            lines += [f"\n{S()}", f"📌 *YOUR RANK: #{myrank}*", f"🔗 Your referrals: {myrefs:,}"]
            await q.edit_message_text("\n".join(lines), parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh", callback_data="leaderboard"),
                     InlineKeyboardButton("🔙 Back",    callback_data="main_menu")]]))

        # ── Redeem Info
        elif data == "redeem_info":
            await q.edit_message_text(
                f"💰 *REDEEM CODE*\n{S()}\n\nUsage:\n`/redeem YOUR-CODE`\n\n"
                f"Example:\n`/redeem SENZO-AB12C-D34EF`\n\n💡 Get codes from events & giveaways!",
                parse_mode="Markdown", reply_markup=bk())

        # ── My Profile
        elif data == "my_profile":
            u = await get_user(user.id)
            if not u: return
            unlocked = await db.fetchval("SELECT COUNT(*) FROM user_unlocks WHERE user_id=?", (user.id,), default=0)
            last7    = await db.fetchval("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND status='completed' AND date>=datetime('now','-7 days')", (user.id,), default=0)
            joined   = (u.get("joined_date") or "")[:10] or "Unknown"
            uname    = f"@{u['username']}" if u.get("username") else "No username"
            await q.edit_message_text(
                f"👤 *MY PROFILE*\n{S()}\n\n• ID: `{user.id}`\n• Username: {uname}\n"
                f"• Joined: {joined}\n• Rank: {u['rank']}\n\n{S()}\n\n"
                f"💰 Credits: {u['credits']:,}\n🔗 Referrals: {u['total_referrals']:,}\n"
                f"📦 Unlocked: {unlocked}\n\n📊 Last 7 days: {last7} referrals",
                parse_mode="Markdown", reply_markup=bk())

        # ── Help
        elif data == "help":
            await q.edit_message_text(
                f"❓ *HELP*\n{S()}\n\n*Commands:*\n• `/start` – Menu\n• `/redeem CODE`\n"
                f"• `/myrefs`\n• `/profile`\n\n*How to unlock:*\n1. Open file link\n"
                f"2. Join channels\n3. Share referral link\n4. Collect referrals\n5. File auto-sent ✅\n\n"
                f"Each referral = *{CREDITS_PER_REFERRAL} credits* 💰\n\n_Powered by Senzo Technologies_ 🌟",
                parse_mode="Markdown", reply_markup=bk())

        # ── Channel Verify
        elif data.startswith("verify_"):
            pid  = data[7:]
            chs  = await get_channels(pid)
            prod = await get_product(pid)
            if not prod:
                await q.edit_message_text("❌ Product no longer available.", reply_markup=bk()); return
            bad = [ch for ch in chs if not await chk_joined(context.bot, user.id, ch)]
            if not bad:
                await show_prod_edit(q, context, user, pid)
            else:
                btns = []
                for ch in chs:
                    icon = "❌" if ch in bad else "✅"
                    btns.append([InlineKeyboardButton(f"{icon} Join {ch}", url=f"https://t.me/{ch.lstrip('@')}")])
                btns.append([InlineKeyboardButton("✅ Verify Now", callback_data=f"verify_{pid}")])
                st = "\n".join(f"{'❌' if ch in bad else '✅'} {ch}" for ch in chs)
                await q.edit_message_text(
                    f"🔒 *VERIFICATION*\n{S()}\n\n📦 *{prod['name']}*\n\n{st}\n\n❌ Join missing channels then tap Verify.",
                    parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

        # ── Get Referral Link
        elif data.startswith("getref_"):
            pid  = data[7:]
            prod = await get_product(pid)
            name = prod["name"] if prod else "File"
            bn   = await bot_name(context.bot)
            rl   = f"https://t.me/{bn}?start=ref_{user.id}_{pid}"
            await q.edit_message_text(
                f"🔗 *YOUR REFERRAL LINK*\n{S()}\n\n📦 *{name}*\n\n`{rl}`\n\n"
                f"Tap to copy & share! Each join = *{CREDITS_PER_REFERRAL} credits* 💰",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 Progress",    callback_data=f"progress_{pid}")],
                    [InlineKeyboardButton("🔙 Back to File", callback_data=f"viewprod_{pid}")],
                ]))

        # ── Progress
        elif data.startswith("progress_"):
            pid  = data[9:]
            prod = await get_product(pid)
            rc   = await count_refs(user.id, pid)
            rq   = prod["required_refs"] if prod else 1
            name = prod["name"] if prod else "File"
            bf   = min(10, int(rc/rq*10)) if rq else 10
            bar  = "▓"*bf + "░"*(10-bf)
            rem  = max(0, rq-rc)
            st   = "✅ *Complete!*" if rem == 0 else f"⏳ Need *{rem}* more!"
            await q.edit_message_text(
                f"📊 *PROGRESS*\n{S()}\n\n📦 *{name}*\n\n[{bar}] {rc}/{rq}\n\n{st}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 Referral Link", callback_data=f"getref_{pid}")],
                    [InlineKeyboardButton("🔄 Refresh",       callback_data=f"progress_{pid}")],
                    [InlineKeyboardButton("🔙 Back",          callback_data=f"viewprod_{pid}")],
                ]))

        # ── View Product
        elif data.startswith("viewprod_"):
            await show_prod_edit(q, context, user, data[9:])

        # ── Credit Unlock
        elif data.startswith("unlockc_"):
            pid  = data[8:]
            prod = await get_product(pid)
            if not prod: await q.answer("❌ Not found!", show_alert=True); return
            rc   = prod["required_credits"]
            u    = await get_user(user.id)
            mc   = u["credits"] if u else 0
            if mc < rc: await q.answer(f"❌ Need {rc}, you have {mc}.", show_alert=True); return
            await deduct_credits(user.id, rc, f"Unlocked: {prod['name']}")
            await do_unlock(user.id, pid)
            await q.edit_message_text(f"✅ *Unlocked!*\n📦 *{prod['name']}*\n💰 {rc} credits used.\n\nSending file...", parse_mode="Markdown")
            await send_file(context.bot, user.id, prod)

        # ════ ADMIN CALLBACKS ════

        elif data == "admin_panel":
            if user.id != ADMIN_ID: await q.answer("🔒 Denied!", show_alert=True); return
            s = await stats()
            await q.edit_message_text(
                f"⚙️ *ADMIN DASHBOARD*\n{S()}\n\n"
                f"• 👥 Users: {s['users']:,}\n• 📦 Files: {s['files']:,}\n"
                f"• 🔗 Referrals: {s['refs']:,}\n• 💰 Credits: {s['credits']:,}\n"
                f"• 🎫 Codes: {s['codes']:,}\n\n{S()}\n🛠️ *ACTIONS*",
                parse_mode="Markdown", reply_markup=admin_kb())

        elif data == "admin_upload":
            if user.id != ADMIN_ID: await q.answer("🔒 Denied!", show_alert=True); return
            for k in ("step","fname","fdesc","frefs","fcred","fchannels","file_id","file_type"):
                context.user_data.pop(k, None)
            context.user_data["step"] = "file"
            await q.edit_message_text(
                f"📤 *UPLOAD FILE*\n{S()}\n\n*Step 1/6:* Send the file\n"
                f"(document, video, photo, audio, voice)\n\n_/start to cancel._", parse_mode="Markdown")

        elif data == "admin_manage":
            if user.id != ADMIN_ID: await q.answer("🔒 Denied!", show_alert=True); return
            rows = await db.fetchall("SELECT id,name,is_active,views,unlocks FROM products ORDER BY created_at DESC LIMIT 15")
            if not rows:
                await q.edit_message_text("📋 No files.", reply_markup=bk("admin_panel")); return
            btns = [[InlineKeyboardButton(f"{'✅' if r['is_active'] else '❌'} {r['name'][:28]}  V:{r['views']} U:{r['unlocks']}", callback_data=f"toggle_{r['id']}")] for r in rows]
            btns.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
            await q.edit_message_text(f"📋 *MANAGE FILES*\n{S()}\nTap to toggle ✅/❌:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

        elif data == "admin_users":
            if user.id != ADMIN_ID: await q.answer("🔒 Denied!", show_alert=True); return
            rows = await db.fetchall("SELECT user_id,full_name,username,credits,total_referrals FROM users ORDER BY joined_date DESC LIMIT 15")
            lines = [f"👥 *RECENT USERS*\n{S()}\n"]
            for r in rows:
                d = r["username"] or r["full_name"] or f"User{r['user_id']}"
                lines.append(f"• {d}  💰{r['credits']}  🔗{r['total_referrals']}")
            await q.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=bk("admin_panel"))

        elif data == "admin_gen_code":
            if user.id != ADMIN_ID: await q.answer("🔒 Denied!", show_alert=True); return
            await q.edit_message_text(
                f"🎫 *GENERATE CODE*\n{S()}\n\nCommand:\n`/createredeem [pts] [uses] [days]`\n\nExample:\n`/createredeem 50 100 30`",
                parse_mode="Markdown", reply_markup=bk("admin_panel"))

        elif data == "admin_list_codes":
            if user.id != ADMIN_ID: await q.answer("🔒 Denied!", show_alert=True); return
            rows = await db.fetchall("SELECT code,points,max_uses,used_count,expires_at,is_active FROM redeem_codes ORDER BY created_at DESC LIMIT 15")
            if not rows:
                await q.edit_message_text("🎫 No codes.", reply_markup=bk("admin_panel")); return
            lines = [f"🎫 *REDEEM CODES*\n{S()}\n"]
            for r in rows:
                s  = "✅" if r["is_active"] else "❌"
                e  = (r["expires_at"] or "")[:10] or "No expiry"
                lines.append(f"{s} `{r['code']}`\n  +{r['points']}pts | {r['used_count']}/{r['max_uses']} | Left:{r['max_uses']-r['used_count']} | Exp:{e}\n")
            await q.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=bk("admin_panel"))

        elif data == "admin_broadcast":
            if user.id != ADMIN_ID: await q.answer("🔒 Denied!", show_alert=True); return
            await q.edit_message_text(f"📢 *BROADCAST*\n{S()}\n\nSelect target:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📝 All Users",          callback_data="bc_all")],
                    [InlineKeyboardButton("🎯 Active (7d)",        callback_data="bc_active")],
                    [InlineKeyboardButton("🏆 Top 50 Referrers",   callback_data="bc_top")],
                    [InlineKeyboardButton("💎 Premium (500+ cred)",callback_data="bc_premium")],
                    [InlineKeyboardButton("🔙 Back",               callback_data="admin_panel")],
                ]))

        elif data in ("bc_all","bc_active","bc_top","bc_premium"):
            if user.id != ADMIN_ID: await q.answer("🔒 Denied!", show_alert=True); return
            tmap = {"bc_all":"all","bc_active":"active","bc_top":"top","bc_premium":"premium"}
            context.user_data["bc_target"] = tmap[data]
            context.user_data["step"]      = "bc_msg"
            await q.edit_message_text(
                f"📢 Target: *{tmap[data].upper()}*\n\nSend your message (text/photo/video/doc):\n\n_/start to cancel._",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]]))

        elif data == "bc_yes":
            if user.id != ADMIN_ID: return
            await do_broadcast(q, context)

        elif data == "bc_no":
            if user.id != ADMIN_ID: return
            for k in ("bc_msg_data","bc_target","step"): context.user_data.pop(k, None)
            await q.edit_message_text("❌ Broadcast cancelled.", reply_markup=bk("admin_panel"))

        elif data == "admin_stats":
            if user.id != ADMIN_ID: await q.answer("🔒 Denied!", show_alert=True); return
            s      = await stats()
            new7   = await db.fetchval("SELECT COUNT(*) FROM users WHERE joined_date>=datetime('now','-7 days')", default=0)
            act24  = await db.fetchval("SELECT COUNT(*) FROM users WHERE last_active>=datetime('now','-1 day')", default=0)
            unlks  = await db.fetchval("SELECT COUNT(*) FROM user_unlocks", default=0)
            bcast  = await db.fetchval("SELECT COUNT(*) FROM broadcast_history", default=0)
            await q.edit_message_text(
                f"📊 *FULL STATS*\n{S()}\n\n"
                f"👥 Users: {s['users']:,}\n🆕 New (7d): {new7:,}\n🟢 Active (24h): {act24:,}\n\n"
                f"📦 Files: {s['files']:,}\n🔓 Unlocks: {unlks:,}\n\n"
                f"🔗 Referrals: {s['refs']:,}\n💰 Credits: {s['credits']:,}\n\n"
                f"🎫 Codes: {s['codes']:,}\n📢 Broadcasts: {bcast:,}\n\n💾 DB: Turso Cloud ✅",
                parse_mode="Markdown", reply_markup=bk("admin_panel"))

        elif data.startswith("toggle_"):
            if user.id != ADMIN_ID: await q.answer("🔒 Denied!", show_alert=True); return
            pid = data[7:]
            row = await db.fetchone("SELECT is_active FROM products WHERE id=?", (pid,))
            if row:
                await db.execute("UPDATE products SET is_active=? WHERE id=?", (0 if row["is_active"] else 1, pid))
            # refresh manage page
            rows2 = await db.fetchall("SELECT id,name,is_active,views,unlocks FROM products ORDER BY created_at DESC LIMIT 15")
            btns2 = [[InlineKeyboardButton(f"{'✅' if r['is_active'] else '❌'} {r['name'][:28]}  V:{r['views']} U:{r['unlocks']}", callback_data=f"toggle_{r['id']}")] for r in rows2]
            btns2.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
            await q.edit_message_text(f"📋 *MANAGE FILES*\n{S()}\nTap to toggle ✅/❌:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns2))

    except Exception as e:
        logger.error(f"cb [{data}]: {e}", exc_info=True)
        try: await context.bot.send_message(user.id, "⚠️ Error. Send /start and try again.")
        except: pass


# ════════════════════════════════════════
#           BROADCAST
# ════════════════════════════════════════

async def do_broadcast(q, context):
    tgt  = context.user_data.get("bc_target","all")
    md   = context.user_data.get("bc_msg_data")
    if not md:
        await q.edit_message_text("❌ No message.", reply_markup=bk("admin_panel")); return

    qmap = {
        "all":     "SELECT user_id FROM users WHERE is_banned=0",
        "active":  "SELECT user_id FROM users WHERE is_banned=0 AND last_active>=datetime('now','-7 days')",
        "top":     "SELECT user_id FROM users WHERE is_banned=0 ORDER BY total_referrals DESC LIMIT 50",
        "premium": "SELECT user_id FROM users WHERE is_banned=0 AND credits>=500",
    }
    try: rows = await db.fetchall(qmap.get(tgt, qmap["all"]))
    except Exception as e:
        await q.edit_message_text(f"❌ {e}", reply_markup=bk("admin_panel")); return

    uids  = [r["user_id"] for r in rows]
    total = len(uids); sent = 0; failed = 0
    mtype = md.get("type","text")

    sm = await q.edit_message_text(f"📢 *Broadcasting...*\n\nTarget: {total:,}\nSent: 0 | Failed: 0", parse_mode="Markdown")

    for i, uid in enumerate(uids):
        try:
            async def _s():
                kw = {"chat_id": uid}
                if   mtype=="text":     await context.bot.send_message(**kw, text=md["content"])
                elif mtype=="photo":    await context.bot.send_photo(**kw,    photo=md["file_id"],    caption=md.get("caption",""))
                elif mtype=="video":    await context.bot.send_video(**kw,    video=md["file_id"],    caption=md.get("caption",""))
                elif mtype=="document": await context.bot.send_document(**kw, document=md["file_id"], caption=md.get("caption",""))
            await asyncio.wait_for(_s(), timeout=15.0)
            sent += 1
        except: failed += 1
        if (i+1)%50==0:
            try: await sm.edit_text(f"📢 *Broadcasting...*\n\nTarget: {total:,}\nSent: {sent:,} | Failed: {failed:,}", parse_mode="Markdown")
            except: pass
        if (i+1)%25==0: await asyncio.sleep(1)

    try:
        await db.execute("INSERT INTO broadcast_history (message_type,target_type,total_sent,total_failed,sent_by) VALUES (?,?,?,?,?)",
                         (mtype, tgt, sent, failed, ADMIN_ID))
    except: pass
    for k in ("bc_msg_data","bc_target","step"): context.user_data.pop(k, None)
    await sm.edit_text(f"✅ *Done!*\n\n• ✅ Sent: {sent:,}\n• ❌ Failed: {failed:,}\n• 📋 Total: {total:,}",
                       parse_mode="Markdown", reply_markup=bk("admin_panel"))


# ════════════════════════════════════════
#          MESSAGE HANDLER
# ════════════════════════════════════════

async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg  = update.message
    await ensure_user(user.id, user.username or "", user.full_name or "")
    step = context.user_data.get("step")
    if user.id == ADMIN_ID and step == "bc_msg":   await bc_input(update, context); return
    if user.id == ADMIN_ID and step in ("file","name","desc","refs","credits","channels"): await upload_step(update, context); return
    u = await get_user(user.id)
    await msg.reply_text(menu_text(u), parse_mode="Markdown", reply_markup=menu_kb(user.id == ADMIN_ID))

async def bc_input(update, context):
    msg = update.message; md = {}
    if msg.text:         md = {"type":"text",     "content":  msg.text}
    elif msg.photo:      md = {"type":"photo",    "file_id":  msg.photo[-1].file_id, "caption": msg.caption or ""}
    elif msg.video:      md = {"type":"video",    "file_id":  msg.video.file_id,     "caption": msg.caption or ""}
    elif msg.document:   md = {"type":"document", "file_id":  msg.document.file_id,  "caption": msg.caption or ""}
    else: await msg.reply_text("❌ Send text, photo, video, or document."); return
    context.user_data["bc_msg_data"] = md; context.user_data.pop("step", None)
    tgt = context.user_data.get("bc_target","all")
    prv = md.get("content","") or md.get("caption","") or f"[{md['type']}]"
    await msg.reply_text(
        f"📢 *PREVIEW*\n{S()}\n\nTarget: *{tgt.upper()}*\nType: {md['type']}\nPreview: {prv[:120]}\n\nConfirm?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ YES – Send", callback_data="bc_yes"),
             InlineKeyboardButton("❌ NO",         callback_data="bc_no")]]))

async def upload_step(update, context):
    msg  = update.message
    step = context.user_data.get("step")

    if step == "file":
        if msg.document:   context.user_data.update({"file_id": msg.document.file_id,      "file_type":"document"})
        elif msg.video:    context.user_data.update({"file_id": msg.video.file_id,          "file_type":"video"})
        elif msg.photo:    context.user_data.update({"file_id": msg.photo[-1].file_id,      "file_type":"photo"})
        elif msg.audio:    context.user_data.update({"file_id": msg.audio.file_id,          "file_type":"audio"})
        elif msg.voice:    context.user_data.update({"file_id": msg.voice.file_id,          "file_type":"voice"})
        else: await msg.reply_text("❌ Please send a file (document/video/photo/audio/voice)."); return
        context.user_data["step"] = "name"
        await msg.reply_text("✅ File received!\n\n*Step 2/6:* Enter the *name/title*:", parse_mode="Markdown")

    elif step == "name":
        if not msg.text or not msg.text.strip(): await msg.reply_text("❌ Send a text name."); return
        context.user_data["fname"] = msg.text.strip(); context.user_data["step"] = "desc"
        await msg.reply_text(f"✅ Name: *{context.user_data['fname']}*\n\n*Step 3/6:* Enter *description*:", parse_mode="Markdown")

    elif step == "desc":
        if not msg.text or not msg.text.strip(): await msg.reply_text("❌ Send a text description."); return
        context.user_data["fdesc"] = msg.text.strip(); context.user_data["step"] = "refs"
        await msg.reply_text("✅ Saved!\n\n*Step 4/6:* Enter *required referrals* (e.g. `5`):", parse_mode="Markdown")

    elif step == "refs":
        try:
            refs = int((msg.text or "").strip())
            if refs < 0: raise ValueError
        except: await msg.reply_text("❌ Enter a valid number."); return
        context.user_data["frefs"] = refs; context.user_data["step"] = "credits"
        await msg.reply_text(f"✅ Referrals: *{refs}*\n\n*Step 5/6:* Enter *required credits* (`0` for free):", parse_mode="Markdown")

    elif step == "credits":
        try:
            cred = int((msg.text or "").strip())
            if cred < 0: raise ValueError
        except: await msg.reply_text("❌ Enter a valid number."); return
        context.user_data["fcred"] = cred; context.user_data["step"] = "channels"
        await msg.reply_text(
            f"✅ Credits: *{cred}*\n\n*Step 6/6:* Enter *mandatory channels*\n"
            f"Format: `@ch1 @ch2`\nOr type `skip`\n\n⚠️ Bot must be *admin* in those channels!",
            parse_mode="Markdown")

    elif step == "channels":
        text = (msg.text or "").strip()
        chs  = [] if text.lower()=="skip" else [
            (ch if ch.startswith("@") else f"@{ch}") for ch in text.split()
        ]
        context.user_data["fchannels"] = chs
        await finalize_upload(update, context)

async def finalize_upload(update, context):
    msg = update.message; d = context.user_data; pid = gen_pid()
    try:
        stmts = [(
            "INSERT INTO products (id,name,description,file_id,file_type,required_refs,required_credits,admin_id) VALUES (?,?,?,?,?,?,?,?)",
            (pid, d["fname"], d["fdesc"], d["file_id"], d["file_type"], d["frefs"], d["fcred"], ADMIN_ID)
        )] + [("INSERT INTO product_channels (product_id,channel_username) VALUES (?,?)", (pid, ch)) for ch in d.get("fchannels",[])]
        await db.executemany(stmts)
        bn   = await bot_name(context.bot)
        link = f"https://t.me/{bn}?start=product_{pid}"
        chs  = ", ".join(d.get("fchannels",[])) or "None"
        for k in ("step","fname","fdesc","frefs","fcred","fchannels","file_id","file_type"): context.user_data.pop(k, None)
        await msg.reply_text(
            f"✅ *FILE UPLOADED!*\n{S()}\n\n📦 Name: *{d['fname']}*\n🔑 ID: `{pid}`\n"
            f"🔗 Refs: {d['frefs']}\n💰 Credits: {d['fcred']}\n📢 Channels: {chs}\n\n"
            f"🌐 *Link:*\n`{link}`\n\n🚀 Share with users!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")]]))
    except Exception as e:
        for k in ("step","fname","fdesc","frefs","fcred","fchannels","file_id","file_type"): context.user_data.pop(k, None)
        await msg.reply_text(f"❌ Upload failed: {e}\n\nPlease try again.")


# ════════════════════════════════════════
#            COMMANDS
# ════════════════════════════════════════

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: await update.message.reply_text("🔒 *Access Denied!*", parse_mode="Markdown"); return
    await ensure_user(update.effective_user.id, update.effective_user.username or "", update.effective_user.full_name or "")
    s = await stats()
    await update.message.reply_text(
        f"⚙️ *ADMIN DASHBOARD*\n{S()}\n\n• 👥 Users: {s['users']:,}\n• 📦 Files: {s['files']:,}\n"
        f"• 🔗 Referrals: {s['refs']:,}\n• 💰 Credits: {s['credits']:,}\n• 🎫 Codes: {s['codes']:,}",
        parse_mode="Markdown", reply_markup=admin_kb())

async def cmd_createredeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: await update.message.reply_text("🔒 Access Denied!"); return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("❌ Usage: `/createredeem [pts] [uses] [days]`\nExample: `/createredeem 50 100 30`", parse_mode="Markdown"); return
    try:
        pts, uses, days = int(args[0]), int(args[1]), int(args[2])
        if pts<=0 or uses<=0 or days<=0: raise ValueError
    except: await update.message.reply_text("❌ All values must be positive integers."); return
    code = gen_code()
    exp  = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        await db.execute("INSERT INTO redeem_codes (code,points,max_uses,created_by,expires_at) VALUES (?,?,?,?,?)", (code, pts, uses, ADMIN_ID, exp))
        await update.message.reply_text(
            f"✅ *REDEEM CODE CREATED!*\n{S()}\n\n🎫 Code: `{code}`\n💰 Points: {pts}\n👥 Max Uses: {uses}\n📅 Expires: {exp[:10]}",
            parse_mode="Markdown")
    except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

async def cmd_listcodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: await update.message.reply_text("🔒 Access Denied!"); return
    rows = await db.fetchall("SELECT code,points,max_uses,used_count,expires_at,is_active FROM redeem_codes ORDER BY created_at DESC")
    if not rows: await update.message.reply_text("🎫 No codes yet."); return
    lines = [f"🎫 *ALL CODES*\n{S()}\n"]
    for r in rows:
        s = "✅" if r["is_active"] else "❌"
        e = (r["expires_at"] or "")[:10] or "No expiry"
        lines.append(f"{s} `{r['code']}` | +{r['points']}pts | {r['used_count']}/{r['max_uses']} | Exp:{e}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_deletecode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: await update.message.reply_text("🔒 Access Denied!"); return
    if not context.args: await update.message.reply_text("❌ Usage: `/deletecode CODE`", parse_mode="Markdown"); return
    code = context.args[0].upper()
    await db.execute("UPDATE redeem_codes SET is_active=0 WHERE code=?", (code,))
    await update.message.reply_text(f"✅ Code `{code}` deactivated.", parse_mode="Markdown")

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: await update.message.reply_text("🔒 Access Denied!"); return
    await update.message.reply_text(f"📢 *BROADCAST*\n{S()}\n\nSelect target:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 All Users",          callback_data="bc_all")],
            [InlineKeyboardButton("🎯 Active (7d)",        callback_data="bc_active")],
            [InlineKeyboardButton("🏆 Top 50 Referrers",   callback_data="bc_top")],
            [InlineKeyboardButton("💎 Premium (500+ cred)",callback_data="bc_premium")],
        ]))

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: await update.message.reply_text("🔒 Access Denied!"); return
    s = await stats()
    await update.message.reply_text(
        f"📊 *STATS*\n{S()}\n\n👥 Users: {s['users']:,}\n📦 Files: {s['files']:,}\n"
        f"🔗 Referrals: {s['refs']:,}\n💰 Credits: {s['credits']:,}\n🎫 Codes: {s['codes']:,}\n💾 Turso Cloud ✅",
        parse_mode="Markdown")

async def cmd_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")
    if not context.args:
        await update.message.reply_text("💰 Usage: `/redeem YOUR-CODE`", parse_mode="Markdown"); return
    code = context.args[0].upper()
    try:
        row = await db.fetchone("SELECT points,max_uses,used_count,expires_at,is_active FROM redeem_codes WHERE code=?", (code,))
        if not row:                           await update.message.reply_text("❌ *Invalid code!*",             parse_mode="Markdown"); return
        if not row["is_active"]:              await update.message.reply_text("❌ *Code no longer active!*",    parse_mode="Markdown"); return
        if row["used_count"] >= row["max_uses"]: await update.message.reply_text("❌ *Code reached max uses!*", parse_mode="Markdown"); return
        if row["expires_at"]:
            try:
                if datetime.now() > datetime.strptime(row["expires_at"][:19], "%Y-%m-%d %H:%M:%S"):
                    await update.message.reply_text("❌ *Code expired!*", parse_mode="Markdown"); return
            except: pass
        already = await db.fetchone("SELECT 1 FROM redeem_usage WHERE code=? AND user_id=?", (code, user.id))
        if already: await update.message.reply_text("❌ *Already used by you!*", parse_mode="Markdown"); return
        await db.executemany([
            ("INSERT INTO redeem_usage (code,user_id) VALUES (?,?)",          (code, user.id)),
            ("UPDATE redeem_codes SET used_count=used_count+1 WHERE code=?",  (code,)),
        ])
        await add_credits(user.id, row["points"], f"Redeem: {code}")
        u = await get_user(user.id)
        await update.message.reply_text(
            f"✅ *CODE REDEEMED!*\n{S()}\n\n• Code: `{code}`\n• +{row['points']} Credits! 💰\n"
            f"• New balance: *{u['credits']:,}*\n\nThank you! 🌟",
            parse_mode="Markdown", reply_markup=bk())
    except Exception as e:
        logger.error(f"cmd_redeem: {e}")
        await update.message.reply_text("❌ Error. Please try again.")

async def cmd_myrefs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")
    rows = await db.fetchall("""
        SELECT r.product_id, p.name, COUNT(*) as cnt, p.required_refs
        FROM referrals r LEFT JOIN products p ON r.product_id=p.id
        WHERE r.referrer_id=? AND r.status='completed' GROUP BY r.product_id""", (user.id,))
    if not rows: await update.message.reply_text("🔗 No referrals yet. Browse files and share!"); return
    lines = [f"🔗 *MY REFERRALS*\n{S()}\n"]
    for r in rows:
        cnt = r["cnt"]; req = r["required_refs"] or 1
        bf  = min(10, int(cnt/req*10)); bar = "▓"*bf + "░"*(10-bf)
        lines.append(f"📦 *{r['name'] or r['product_id']}*\n[{bar}] {'✅' if cnt>=req else f'{cnt}/{req}'}\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")
    u = await get_user(user.id)
    if not u: return
    unlocked = await db.fetchval("SELECT COUNT(*) FROM user_unlocks WHERE user_id=?", (user.id,), default=0)
    joined   = (u.get("joined_date") or "")[:10] or "Unknown"
    uname    = f"@{u['username']}" if u.get("username") else "No username"
    await update.message.reply_text(
        f"👤 *MY PROFILE*\n{S()}\n\n• ID: `{user.id}`\n• Username: {uname}\n• Joined: {joined}\n• Rank: {u['rank']}\n\n"
        f"💰 Credits: {u['credits']:,}\n🔗 Referrals: {u['total_referrals']:,}\n📦 Unlocked: {unlocked}",
        parse_mode="Markdown")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"❓ *HELP*\n{S()}\n\n• `/start` – Menu\n• `/redeem CODE`\n• `/myrefs`\n• `/profile`\n\n"
        f"_Powered by Senzo Technologies_ 🌟", parse_mode="Markdown")

async def err_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}", exc_info=context.error)


# ════════════════════════════════════════
#                MAIN
# ════════════════════════════════════════

async def post_init(app):
    await init_db()
    await bot_name(app.bot)
    logger.info("Senzo Premium Bot v5.0 (Turso) started!")

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    for cmd, fn in [
        ("start",cmd_admin if False else start), ("admin",cmd_admin),
        ("createredeem",cmd_createredeem), ("listcodes",cmd_listcodes),
        ("deletecode",cmd_deletecode), ("broadcast",cmd_broadcast),
        ("stats",cmd_stats), ("redeem",cmd_redeem),
        ("myrefs",cmd_myrefs), ("profile",cmd_profile), ("help",cmd_help),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(
        filters.TEXT | filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE,
        msg_handler))
    app.add_error_handler(err_handler)

    logger.info("Polling started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
