import logging
import uuid
import os
import sys
import asyncio
from datetime import datetime
from typing import Dict, Optional, Tuple, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import aiosqlite

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION - YOUR VALUES ARE HERE ====================
BOT_TOKEN = "8480004123:AAEmDVAia46G5ggfDqLDEIXNy5Zy4erXsOo"
ADMIN_ID = 6070145287

# Database file path
DB_PATH = "senzo_bot_database.db"

# Session management for multi-product upload
admin_sessions: Dict[int, Dict] = {}

# Senzo Branding
SENZO_BRAND = """
🌟 *SENZO REFERRAL SYSTEM* 🌟
*Powered by Senzo Technologies*
━━━━━━━━━━━━━━━━━━━━━━
"""

# Emoji constants
EMOJIS = {
    'product': '📦',
    'user': '👤',
    'stats': '📊',
    'referral': '🔗',
    'success': '✅',
    'error': '❌',
    'warning': '⚠️',
    'lock': '🔒',
    'unlock': '🔓',
    'crown': '👑',
    'star': '⭐',
    'fire': '🔥',
    'diamond': '💎',
    'rocket': '🚀',
    'gift': '🎁',
    'trophy': '🏆',
    'wallet': '💼',
    'credit': '💰'
}


async def init_db():
    """Initialize database with Senzo schema"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Users table with credits system
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                credits INTEGER DEFAULT 0,
                total_referrals INTEGER DEFAULT 0,
                rank TEXT DEFAULT 'Bronze',
                joined_date TIMESTAMP,
                last_active TIMESTAMP
            )
        """)
        
        # Products table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                file_id TEXT,
                file_type TEXT,
                required_refs INTEGER,
                required_credits INTEGER DEFAULT 0,
                admin_id INTEGER,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP,
                views INTEGER DEFAULT 0,
                unlocks INTEGER DEFAULT 0
            )
        """)
        
        # Product channels table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS product_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT,
                channel_username TEXT,
                FOREIGN KEY (product_id) REFERENCES products(id)
            )
        """)
        
        # Referrals table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER,
                product_id TEXT,
                credits_earned INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                date TIMESTAMP,
                UNIQUE(referrer_id, referred_id, product_id)
            )
        """)
        
        # Product access table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_products (
                user_id INTEGER,
                product_id TEXT,
                unlocked_at TIMESTAMP,
                PRIMARY KEY (user_id, product_id)
            )
        """)
        
        # Transactions table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                type TEXT,
                description TEXT,
                date TIMESTAMP
            )
        """)
        
        await db.commit()
        logger.info("Senzo Database initialized successfully")


async def add_credits(user_id: int, amount: int, description: str):
    """Add credits to user"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET credits = credits + ? WHERE user_id = ?",
            (amount, user_id)
        )
        await db.execute(
            "INSERT INTO transactions (user_id, amount, type, description, date) VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, 'earn', description, datetime.now())
        )
        await db.commit()
        
        # Update user rank
        cursor = await db.execute("SELECT credits FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        if result:
            credits = result[0]
            if credits >= 500:
                rank = 'Diamond'
            elif credits >= 200:
                rank = 'Platinum'
            elif credits >= 100:
                rank = 'Gold'
            elif credits >= 50:
                rank = 'Silver'
            else:
                rank = 'Bronze'
            
            await db.execute(
                "UPDATE users SET rank = ? WHERE user_id = ?",
                (rank, user_id)
            )
            await db.commit()


async def register_user(user_id: int, username: str = None, full_name: str = None):
    """Register new user"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        exists = await cursor.fetchone()
        
        if not exists:
            await db.execute(
                "INSERT INTO users (user_id, username, full_name, credits, joined_date, last_active) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, username or "", full_name or "", 10, datetime.now(), datetime.now())
            )
            await db.commit()
            await add_credits(user_id, 10, "Welcome bonus")
            return True
        else:
            await db.execute(
                "UPDATE users SET last_active = ?, username = ?, full_name = ? WHERE user_id = ?",
                (datetime.now(), username or "", full_name or "", user_id)
            )
            await db.commit()
        return False


async def get_user_profile(user_id: int) -> Dict:
    """Get user profile data"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT username, full_name, credits, total_referrals, rank, joined_date FROM users WHERE user_id = ?",
            (user_id,)
        )
        user = await cursor.fetchone()
        if user:
            return {
                'username': user[0],
                'full_name': user[1],
                'credits': user[2],
                'total_referrals': user[3],
                'rank': user[4],
                'joined_date': user[5]
            }
        return None


async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int = None):
    """Display main menu"""
    if not user_id:
        user_id = update.effective_user.id
    
    profile = await get_user_profile(user_id)
    if not profile:
        return
    
    menu_text = f"""
{SENZO_BRAND}
*Welcome {profile['full_name'] or f'User_{user_id}'}!* 
━━━━━━━━━━━━━━━━━━━━━━

{EMOJIS['user']} *Profile Status*
• Rank: {EMOJIS['crown']} *{profile['rank']}*
• {EMOJIS['credit']} Credits: `{profile['credits']}`
• {EMOJIS['referral']} Total Referrals: `{profile['total_referrals']}`
• {EMOJIS['star']} Member since: `{profile['joined_date'][:10]}`

━━━━━━━━━━━━━━━━━━━━━━
*What would you like to do?*
    """
    
    keyboard = [
        [InlineKeyboardButton(f"{EMOJIS['product']} Browse Products", callback_data="browse_products")],
        [InlineKeyboardButton(f"{EMOJIS['referral']} My Referrals", callback_data="my_referrals")],
        [InlineKeyboardButton(f"{EMOJIS['stats']} Leaderboard", callback_data="leaderboard")],
        [InlineKeyboardButton(f"{EMOJIS['wallet']} My Credits", callback_data="my_credits")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help_info")]
    ]
    
    if update.effective_user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(menu_text, reply_markup=reply_markup, parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.edit_message_text(menu_text, reply_markup=reply_markup, parse_mode='Markdown')


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    user_id = update.effective_user.id
    username = update.effective_user.username
    full_name = update.effective_user.full_name
    
    await register_user(user_id, username, full_name)
    
    # Handle deep links
    if context.args:
        param = context.args[0]
        
        if param.startswith("ref_"):
            parts = param[4:].split("_")
            if len(parts) == 2:
                referrer_id = int(parts[0])
                product_id = parts[1]
                
                if referrer_id != user_id:
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute(
                            "INSERT OR IGNORE INTO referrals (referrer_id, referred_id, product_id, status, date) VALUES (?, ?, ?, ?, ?)",
                            (referrer_id, user_id, product_id, 'pending', datetime.now())
                        )
                        await db.commit()
                    
                    await update.message.reply_text(
                        f"{EMOJIS['success']} *Referral Tracked!*\n\n"
                        f"You were referred to this product!\n"
                        f"Complete the requirements to help your friend unlock it!",
                        parse_mode='Markdown'
                    )
    
    await main_menu(update, context)


async def browse_products(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    """Browse available products"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, name, description, required_refs, required_credits, views, unlocks FROM products WHERE is_active = 1 ORDER BY created_at DESC LIMIT 5 OFFSET ?",
            (page * 5,)
        )
        products = await cursor.fetchall()
        
        cursor = await db.execute("SELECT COUNT(*) FROM products WHERE is_active = 1")
        total = await cursor.fetchone()
        total_products = total[0] if total else 0
        
        if not products:
            await query.edit_message_text(
                f"{SENZO_BRAND}\n{EMOJIS['warning']} *No products available yet!*\n\nCheck back later!",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")
                ]])
            )
            return
        
        text = f"{SENZO_BRAND}{EMOJIS['product']} *Available Products*\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for product in products:
            product_id, name, desc, req_refs, req_credits, views, unlocks = product
            
            cursor2 = await db.execute(
                "SELECT 1 FROM user_products WHERE user_id = ? AND product_id = ?",
                (user_id, product_id)
            )
            unlocked = await cursor2.fetchone()
            
            status = "✅ *UNLOCKED*" if unlocked else f"{EMOJIS['lock']} *LOCKED*"
            req_text = f"🔗 {req_refs} Referrals" if req_refs > 0 else f"{EMOJIS['credit']} {req_credits} Credits"
            
            text += f"*{name}*\n"
            text += f"└ {desc[:40]}...\n"
            text += f"└ {status}\n"
            text += f"└ Requires: {req_text}\n\n"
        
        keyboard = []
        nav_buttons = []
        
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"browse_page_{page-1}"))
        if (page + 1) * 5 < total_products:
            nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"browse_page_{page+1}"))
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def my_referrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's referrals"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT r.product_id, p.name, r.status, r.date 
               FROM referrals r 
               JOIN products p ON r.product_id = p.id 
               WHERE r.referrer_id = ? 
               ORDER BY r.date DESC LIMIT 20""",
            (user_id,)
        )
        referrals = await cursor.fetchall()
        
        text = f"""
{SENZO_BRAND}
{EMOJIS['referral']} *My Referrals*
━━━━━━━━━━━━━━━━━━━━━━

"""
        
        if not referrals:
            text += "📭 *No referrals yet*\n\nShare your referral links to earn credits!"
        else:
            for ref in referrals[:10]:
                product_id, name, status, date = ref
                status_icon = "✅" if status == 'completed' else "⏳"
                text += f"{status_icon} *{name[:30]}*\n"
                text += f"└ Status: {status.upper()}\n"
                text += f"└ Date: {date[:10]}\n\n"
        
        cursor = await db.execute(
            "SELECT COUNT(*), SUM(credits_earned) FROM referrals WHERE referrer_id = ? AND status = 'completed'",
            (user_id,)
        )
        stats = await cursor.fetchone()
        total_completed = stats[0] or 0
        total_earned = stats[1] or 0
        
        text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"📊 *Statistics*\n"
        text += f"• Completed: {total_completed}\n"
        text += f"• Credits Earned: {total_earned}\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show leaderboard"""
    query = update.callback_query
    await query.answer()
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, username, full_name, total_referrals, credits, rank FROM users ORDER BY total_referrals DESC LIMIT 10"
        )
        top_users = await cursor.fetchall()
        
        text = f"""
{SENZO_BRAND}
{EMOJIS['trophy']} *🏆 LEADERBOARD 🏆*
━━━━━━━━━━━━━━━━━━━━━━

*Top Referrers*

"""
        
        medals = ["🥇", "🥈", "🥉"]
        for i, user in enumerate(top_users):
            user_id, username, full_name, referrals, credits, rank = user
            medal = medals[i] if i < 3 else f"{i+1}."
            name = full_name or username or f"User_{user_id}"
            name = name[:20]
            text += f"{medal} *{name}*\n"
            text += f"└ {EMOJIS['referral']} {referrals} referrals | {EMOJIS['credit']} {credits} credits\n"
            text += f"└ Rank: {rank}\n\n"
        
        cursor = await db.execute(
            "SELECT COUNT(*) + 1 FROM users WHERE total_referrals > (SELECT total_referrals FROM users WHERE user_id = ?)",
            (update.effective_user.id,)
        )
        user_rank = await cursor.fetchone()
        
        text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"*Your Rank:* #{user_rank[0] if user_rank else 'N/A'}\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def my_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show credits"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    profile = await get_user_profile(user_id)
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT amount, type, description, date FROM transactions WHERE user_id = ? ORDER BY date DESC LIMIT 10",
            (user_id,)
        )
        transactions = await cursor.fetchall()
        
        text = f"""
{SENZO_BRAND}
{EMOJIS['wallet']} *My Credits Dashboard*
━━━━━━━━━━━━━━━━━━━━━━

*Current Balance:* {EMOJIS['credit']} `{profile['credits']} credits`
*Rank:* {EMOJIS['crown']} *{profile['rank']}*
*Total Referrals:* `{profile['total_referrals']}`

━━━━━━━━━━━━━━━━━━━━━━
*Recent Transactions*

"""
        
        if not transactions:
            text += "No transactions yet.\n"
        else:
            for trans in transactions[:5]:
                amount, trans_type, desc, date = trans
                arrow = "📈" if amount > 0 else "📉"
                text += f"{arrow} {desc[:30]}\n"
                text += f"└ {amount} credits | {date[:10]}\n\n"
        
        text += f"\n💡 *How to earn credits:*\n"
        text += f"• 5 credits per successful referral\n"
        text += f"• Daily login bonus\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


# ==================== ADMIN FUNCTIONS ====================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel"""
    if update.effective_user.id != ADMIN_ID:
        await update.callback_query.answer("Access denied!", show_alert=True)
        return
    
    query = update.callback_query
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        total_users = await cursor.fetchone()
        
        cursor = await db.execute("SELECT COUNT(*) FROM products")
        total_products = await cursor.fetchone()
        
        cursor = await db.execute("SELECT COUNT(*) FROM referrals WHERE status = 'completed'")
        total_refs = await cursor.fetchone()
        
        text = f"""
{SENZO_BRAND}
⚙️ *Admin Control Panel*
━━━━━━━━━━━━━━━━━━━━━━

📊 *Statistics*
• 👥 Users: {total_users[0]}
• 📦 Products: {total_products[0]}
• 🔗 Completed Referrals: {total_refs[0]}

━━━━━━━━━━━━━━━━━━━━━━
*Actions*
"""
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Product", callback_data="admin_add_product")],
        [InlineKeyboardButton("📋 Manage Products", callback_data="admin_manage_products")],
        [InlineKeyboardButton("👥 View Users", callback_data="admin_view_users")],
        [InlineKeyboardButton("📊 Full Stats", callback_data="admin_full_stats")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def admin_add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start adding product"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    query = update.callback_query
    await query.answer()
    
    admin_sessions[ADMIN_ID] = {
        'step': 'name',
        'products': []
    }
    
    await query.edit_message_text(
        f"{SENZO_BRAND}\n"
        f"{EMOJIS['product']} *Add New Product*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Step 1/6:* Enter product name\n\n"
        f"Type /cancel to abort.",
        parse_mode='Markdown'
    )


async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin text input"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if ADMIN_ID not in admin_sessions:
        return
    
    session = admin_sessions[ADMIN_ID]
    step = session.get('step')
    text = update.message.text
    
    if text == '/cancel':
        del admin_sessions[ADMIN_ID]
        await update.message.reply_text(f"{EMOJIS['error']} Cancelled!")
        await main_menu(update, context)
        return
    
    if step == 'name':
        session['name'] = text
        session['step'] = 'description'
        await update.message.reply_text(
            f"{EMOJIS['success']} Name set: {text}\n\n"
            f"*Step 2/6:* Enter description",
            parse_mode='Markdown'
        )
    
    elif step == 'description':
        session['description'] = text[:200]
        session['step'] = 'file'
        await update.message.reply_text(
            f"{EMOJIS['success']} Description saved!\n\n"
            f"*Step 3/6:* Send the file (document, video, photo, or audio)",
            parse_mode='Markdown'
        )
    
    elif step == 'refs':
        try:
            refs = int(text)
            if refs < 0:
                raise ValueError
            session['required_refs'] = refs
            session['step'] = 'credits'
            await update.message.reply_text(
                f"{EMOJIS['success']} Referrals: {refs}\n\n"
                f"*Step 5/6:* Credits required (0 for free)",
                parse_mode='Markdown'
            )
        except:
            await update.message.reply_text(f"{EMOJIS['error']} Please enter a valid number!")
    
    elif step == 'credits':
        try:
            credits = int(text)
            if credits < 0:
                raise ValueError
            session['required_credits'] = credits
            session['step'] = 'channels'
            await update.message.reply_text(
                f"{EMOJIS['success']} Credits: {credits}\n\n"
                f"*Step 6/6:* Channels (type 'skip' for none)\n\n"
                f"Example: @channel1 @channel2",
                parse_mode='Markdown'
            )
        except:
            await update.message.reply_text(f"{EMOJIS['error']} Please enter a valid number!")


async def handle_file_for_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file upload"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if ADMIN_ID not in admin_sessions or admin_sessions[ADMIN_ID].get('step') != 'file':
        return
    
    # Get file info
    if update.message.document:
        file_id = update.message.document.file_id
        file_type = 'document'
    elif update.message.video:
        file_id = update.message.video.file_id
        file_type = 'video'
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_type = 'photo'
    elif update.message.audio:
        file_id = update.message.audio.file_id
        file_type = 'audio'
    else:
        await update.message.reply_text(f"{EMOJIS['error']} Unsupported file type!")
        return
    
    session = admin_sessions[ADMIN_ID]
    session['file_id'] = file_id
    session['file_type'] = file_type
    session['step'] = 'refs'
    
    await update.message.reply_text(
        f"{EMOJIS['success']} File uploaded!\n\n"
        f"*Step 4/6:* Required referrals count",
        parse_mode='Markdown'
    )


async def handle_channels_for_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle channels input"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if ADMIN_ID not in admin_sessions or admin_sessions[ADMIN_ID].get('step') != 'channels':
        return
    
    session = admin_sessions[ADMIN_ID]
    text = update.message.text.strip()
    
    channels = []
    if text.lower() != 'skip':
        for channel in text.split():
            channel = channel.strip()
            if channel.startswith('@'):
                channels.append(channel)
    
    # Generate product ID
    product_id = str(uuid.uuid4())[:8]
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Save product
        await db.execute(
            """INSERT INTO products 
               (id, name, description, file_id, file_type, required_refs, required_credits, admin_id, is_active, created_at, views, unlocks) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (product_id, session['name'], session['description'], session['file_id'], 
             session['file_type'], session['required_refs'], session['required_credits'], 
             ADMIN_ID, 1, datetime.now(), 0, 0)
        )
        
        # Save channels
        for channel in channels:
            await db.execute(
                "INSERT INTO product_channels (product_id, channel_username) VALUES (?, ?)",
                (product_id, channel)
            )
        
        await db.commit()
    
    # Clean up session
    del admin_sessions[ADMIN_ID]
    
    bot_username = (await context.bot.get_me()).username
    product_link = f"https://t.me/{bot_username}?start=product_{product_id}"
    
    keyboard = [
        [InlineKeyboardButton("✅ Add Another", callback_data="admin_add_product")],
        [InlineKeyboardButton("📋 Manage Products", callback_data="admin_manage_products")],
        [InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"{EMOJIS['trophy']} *PRODUCT ADDED!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Product:* {session['name']}\n"
        f"*ID:* `{product_id}`\n"
        f"*Link:* {product_link}\n\n"
        f"What would you like to do next?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def admin_manage_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage products"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    query = update.callback_query
    await query.answer()
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, name, is_active FROM products ORDER BY created_at DESC"
        )
        products = await cursor.fetchall()
        
        if not products:
            await query.edit_message_text(
                f"No products found!",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="admin_panel")
                ]])
            )
            return
        
        text = f"{SENZO_BRAND}📋 *Manage Products*\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        keyboard = []
        for product in products:
            product_id, name, is_active = product
            status = "✅ Active" if is_active else "❌ Inactive"
            text += f"*{name[:30]}*\n"
            text += f"└ ID: `{product_id}`\n"
            text += f"└ Status: {status}\n\n"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"{'🔴' if is_active else '🟢'} {'Disable' if is_active else 'Enable'}",
                    callback_data=f"toggle_{product_id}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def toggle_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """Toggle product status"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    query = update.callback_query
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT is_active FROM products WHERE id = ?", (product_id,))
        result = await cursor.fetchone()
        
        if result:
            new_status = 0 if result[0] else 1
            await db.execute(
                "UPDATE products SET is_active = ? WHERE id = ?",
                (new_status, product_id)
            )
            await db.commit()
            
            status_text = "activated" if new_status else "deactivated"
            await query.answer(f"Product {status_text}!")
    
    await admin_manage_products(update, context)


async def admin_full_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Full statistics"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    query = update.callback_query
    await query.answer()
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*), SUM(credits), SUM(total_referrals) FROM users")
        user_stats = await cursor.fetchone()
        
        cursor = await db.execute("SELECT COUNT(*), SUM(views), SUM(unlocks) FROM products")
        product_stats = await cursor.fetchone()
        
        cursor = await db.execute("""
            SELECT COUNT(*), 
                   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)
            FROM referrals
        """)
        ref_stats = await cursor.fetchone()
        
        text = f"""
{SENZO_BRAND}
📊 *FULL STATISTICS*
━━━━━━━━━━━━━━━━━━━━━━

👥 *Users*
• Total: {user_stats[0] or 0}
• Credits: {user_stats[1] or 0}
• Referrals: {user_stats[2] or 0}

━━━━━━━━━━━━━━━━━━━━━━

📦 *Products*
• Total: {product_stats[0] or 0}
• Views: {product_stats[1] or 0}
• Unlocks: {product_stats[2] or 0}

━━━━━━━━━━━━━━━━━━━━━━

🔗 *Referrals*
• Total: {ref_stats[0] or 0}
• Completed: {ref_stats[1] or 0}
• Conversion: {((ref_stats[1] or 0) / (ref_stats[0] or 1) * 100):.1f}%
"""
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def admin_view_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View users"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    query = update.callback_query
    await query.answer()
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, username, full_name, credits, total_referrals, rank FROM users ORDER BY total_referrals DESC LIMIT 20"
        )
        users = await cursor.fetchall()
        
        text = f"{SENZO_BRAND}👥 *Top Users*\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for i, user in enumerate(users[:10]):
            user_id, username, full_name, credits, refs, rank = user
            name = full_name or username or f"User_{user_id}"
            medals = ["🥇", "🥈", "🥉", "📌", "📌"]
            medal = medals[i] if i < 5 else f"{i+1}."
            text += f"{medal} *{name[:20]}*\n"
            text += f"└ {EMOJIS['credit']} {credits} | {EMOJIS['referral']} {refs}\n"
            text += f"└ Rank: {rank}\n\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    text = f"""
{SENZO_BRAND}
ℹ️ *Help & Information*
━━━━━━━━━━━━━━━━━━━━━━

*How it works:*
1️⃣ Browse products from menu
2️⃣ Share your referral link
3️⃣ Each referral earns 5 credits
4️⃣ Unlock products with referrals/credits

*Commands:*
/start - Open main menu

*Support:*
Contact admin for assistance

━━━━━━━━━━━━━━━━━━━━━━
*Your Success = Our Success!* 🚀
"""
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callbacks"""
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id
    
    # Navigation
    if data == "back_to_menu":
        await main_menu(update, context, user_id)
    elif data == "browse_products":
        await browse_products(update, context, 0)
    elif data.startswith("browse_page_"):
        page = int(data.split("_")[2])
        await browse_products(update, context, page)
    elif data == "my_referrals":
        await my_referrals(update, context)
    elif data == "leaderboard":
        await leaderboard(update, context)
    elif data == "my_credits":
        await my_credits(update, context)
    elif data == "help_info":
        await help_command(update, context)
    
    # Admin actions
    elif data == "admin_panel":
        await admin_panel(update, context)
    elif data == "admin_add_product":
        await admin_add_product_start(update, context)
    elif data == "admin_manage_products":
        await admin_manage_products(update, context)
    elif data == "admin_view_users":
        await admin_view_users(update, context)
    elif data == "admin_full_stats":
        await admin_full_stats(update, context)
    elif data.startswith("toggle_"):
        product_id = data[7:]
        await toggle_product(update, context, product_id)


# ==================== MAIN ====================

async def main():
    """Start the bot"""
    try:
        # Initialize database
        await init_db()
        logger.info("Database initialized")
        
        # Create application
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Add command handlers
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        
        # Add callback handler
        application.add_handler(CallbackQueryHandler(callback_handler))
        
        # Add message handlers for admin
        application.add_handler(MessageHandler(
            filters.Document.ALL | filters.VIDEO | filters.PHOTO | filters.AUDIO,
            handle_file_for_product
        ))
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_admin_input
        ))
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_channels_for_product
        ))
        
        # Start bot
        logger.info(f"🚀 Senzo Bot Started Successfully!")
        logger.info(f"👑 Admin ID: {ADMIN_ID}")
        
        # Get bot info
        bot_info = await application.bot.get_me()
        logger.info(f"🤖 Bot Username: @{bot_info.username}")
        
        await application.run_polling()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
