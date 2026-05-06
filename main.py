import logging
import uuid
import os
import sys
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from decimal import Decimal

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
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))

if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    logger.error("Please set your BOT_TOKEN in main.py or as environment variable!")
    sys.exit(1)
if ADMIN_ID == 0:
    logger.error("Please set your ADMIN_ID in main.py or as environment variable!")
    sys.exit(1)

DB_PATH = "senzo_bot_database.db"

# Senzo Branding
SENZO_BRAND = """
🌟 *SENZO REFERRAL SYSTEM* 🌟
*Powered by Senzo Technologies*
━━━━━━━━━━━━━━━━━━━━━━
"""

# Session management for multi-product upload
admin_sessions: Dict[int, Dict] = {}

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
        
        # Product access table (users who unlocked products)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_products (
                user_id INTEGER,
                product_id TEXT,
                unlocked_at TIMESTAMP,
                PRIMARY KEY (user_id, product_id)
            )
        """)
        
        # Transactions table for credit system
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
        
        # Update user rank based on total credits
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
    """Register new user with welcome credits"""
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


# ==================== MAIN MENU ====================

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int = None):
    """Display main menu with Senzo branding"""
    if not user_id:
        user_id = update.effective_user.id
    
    profile = await get_user_profile(user_id)
    if not profile:
        return
    
    # Create professional menu
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
        [InlineKeyboardButton("ℹ️ Help & Info", callback_data="help_info")]
    ]
    
    if update.effective_user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if isinstance(update, Update) and update.message:
        await update.message.reply_text(menu_text, reply_markup=reply_markup, parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.edit_message_text(menu_text, reply_markup=reply_markup, parse_mode='Markdown')


# ==================== BROWSE PRODUCTS ====================

async def browse_products(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    """Browse available products with pagination"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Get active products
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
                f"{SENZO_BRAND}\n{EMOJIS['warning']} *No products available yet!*\n\nCheck back later for exciting content!",
                parse_mode='Markdown'
            )
            return
        
        text = f"{SENZO_BRAND}{EMOJIS['product']} *Available Products*\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for product in products:
            product_id, name, desc, req_refs, req_credits, views, unlocks = product
            
            # Check if user already unlocked
            cursor2 = await db.execute(
                "SELECT 1 FROM user_products WHERE user_id = ? AND product_id = ?",
                (user_id, product_id)
            )
            unlocked = await cursor2.fetchone()
            
            status = "✅ *UNLOCKED*" if unlocked else f"{EMOJIS['lock']} *LOCKED*"
            req_text = f"🔗 {req_refs} Referrals" if req_refs > 0 else f"{EMOJIS['credit']} {req_credits} Credits"
            
            text += f"*{name}*\n"
            text += f"└ {desc[:50]}...\n"
            text += f"└ {status}\n"
            text += f"└ Requires: {req_text}\n"
            text += f"└ 📊 {views} views | 🔓 {unlocks} unlocks\n\n"
        
        # Pagination buttons
        keyboard = []
        nav_buttons = []
        
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◀️ Previous", callback_data=f"browse_page_{page-1}"))
        if (page + 1) * 5 < total_products:
            nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"browse_page_{page+1}"))
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def view_product_details(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """View detailed product information"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT name, description, required_refs, required_credits, file_type FROM products WHERE id = ? AND is_active = 1",
            (product_id,)
        )
        product = await cursor.fetchone()
        
        if not product:
            await query.answer("Product not found!", show_alert=True)
            return
        
        name, desc, req_refs, req_credits, file_type = product
        
        # Check if unlocked
        cursor = await db.execute(
            "SELECT 1 FROM user_products WHERE user_id = ? AND product_id = ?",
            (user_id, product_id)
        )
        unlocked = await cursor.fetchone()
        
        # Get user's progress
        cursor = await db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND product_id = ? AND status = 'completed'",
            (user_id, product_id)
        )
        current_refs = await cursor.fetchone()
        current_refs = current_refs[0] if current_refs else 0
        
        # Get channels
        cursor = await db.execute(
            "SELECT channel_username FROM product_channels WHERE product_id = ?",
            (product_id,)
        )
        channels = await cursor.fetchall()
        
        text = f"""
{SENZO_BRAND}
*📦 {name}*
━━━━━━━━━━━━━━━━━━━━━━

*Description:*
{desc}

*Requirements:*
"""
        if req_refs > 0:
            text += f"• {EMOJIS['referral']} {req_refs} Referrals"
            if not unlocked:
                text += f"\n  Your progress: {current_refs}/{req_refs}"
        if req_credits > 0:
            text += f"\n• {EMOJIS['credit']} {req_credits} Credits"
        
        if channels:
            text += f"\n\n*Mandatory Channels:*\n"
            for channel in channels:
                text += f"• {channel[0]}\n"
        
        text += f"\n*File Type:* {file_type.upper()}"
        
        keyboard = []
        
        if unlocked:
            keyboard.append([InlineKeyboardButton("📥 Download Now", callback_data=f"download_{product_id}")])
        else:
            if req_refs > 0:
                if channels:
                    keyboard.append([InlineKeyboardButton("✅ Verify Channels", callback_data=f"verify_{product_id}")])
                keyboard.append([InlineKeyboardButton(f"{EMOJIS['referral']} Get Referral Link", callback_data=f"get_ref_link_{product_id}")])
            if req_credits > 0:
                keyboard.append([InlineKeyboardButton(f"{EMOJIS['credit']} Unlock with Credits", callback_data=f"unlock_credits_{product_id}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Back to Products", callback_data="browse_products")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # Update view count
        await db.execute(
            "UPDATE products SET views = views + 1 WHERE id = ?",
            (product_id,)
        )
        await db.commit()


# ==================== REFERRAL SYSTEM ====================

async def get_referral_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """Generate and show referral link"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    bot_username = (await context.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}_{product_id}"
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT required_refs, name FROM products WHERE id = ?",
            (product_id,)
        )
        product = await cursor.fetchone()
        
        if product:
            req_refs, name = product
            cursor = await db.execute(
                "SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND product_id = ? AND status = 'completed'",
                (user_id, product_id)
            )
            current = await cursor.fetchone()
            current_refs = current[0] if current else 0
            
            text = f"""
{SENZO_BRAND}
{EMOJIS['referral']} *Your Referral Link*

*Product:* {name}
*Progress:* {current_refs}/{req_refs}

`{ref_link}`

━━━━━━━━━━━━━━━━━━━━━━
*Share this link with your friends!*

✨ *Bonus:* Each successful referral gives you 5 credits!
💎 *Pro Tip:* More referrals = Higher rank = More benefits!

🔗 Share on:
• Telegram
• WhatsApp  
• Instagram
• Anywhere!
"""
            
            keyboard = [
                [InlineKeyboardButton("📊 Check Progress", callback_data=f"check_progress_{product_id}")],
                [InlineKeyboardButton("🔙 Back to Product", callback_data=f"product_{product_id}")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def verify_channels_and_unlock(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """Verify user joined channels and unlock referral system"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT channel_username FROM product_channels WHERE product_id = ?",
            (product_id,)
        )
        channels = await cursor.fetchall()
        
        if not channels:
            await get_referral_link_callback(update, context, product_id)
            return
        
        all_joined = True
        not_joined = []
        
        for (channel,) in channels:
            channel_name = channel[1:] if channel.startswith('@') else channel
            try:
                member = await context.bot.get_chat_member(f"@{channel_name}", user_id)
                if member.status not in ['member', 'administrator', 'creator']:
                    all_joined = False
                    not_joined.append(channel)
            except:
                all_joined = False
                not_joined.append(channel)
        
        if all_joined:
            # Mark pending referrals as completed
            cursor = await db.execute(
                "SELECT referrer_id FROM referrals WHERE referred_id = ? AND product_id = ? AND status = 'pending'",
                (user_id, product_id)
            )
            pending = await cursor.fetchall()
            
            for (referrer_id,) in pending:
                await db.execute(
                    "UPDATE referrals SET status = 'completed' WHERE referred_id = ? AND product_id = ? AND referrer_id = ?",
                    (user_id, product_id, referrer_id)
                )
                
                # Add credits to referrer
                await add_credits(referrer_id, 5, f"Referral bonus for product {product_id}")
                
                # Update total referrals count
                await db.execute(
                    "UPDATE users SET total_referrals = total_referrals + 1 WHERE user_id = ?",
                    (referrer_id,)
                )
                
                # Check if referrer unlocked the product
                cursor2 = await db.execute(
                    "SELECT required_refs FROM products WHERE id = ?",
                    (product_id,)
                )
                product = await cursor2.fetchone()
                if product:
                    req_refs = product[0]
                    cursor2 = await db.execute(
                        "SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND product_id = ? AND status = 'completed'",
                        (referrer_id, product_id)
                    )
                    completed = await cursor2.fetchone()
                    
                    if completed[0] >= req_refs:
                        # Auto-unlock for referrer
                        await db.execute(
                            "INSERT OR IGNORE INTO user_products (user_id, product_id, unlocked_at) VALUES (?, ?, ?)",
                            (referrer_id, product_id, datetime.now())
                        )
                        await db.execute(
                            "UPDATE products SET unlocks = unlocks + 1 WHERE id = ?",
                            (product_id,)
                        )
                        await db.commit()
                        
                        await context.bot.send_message(
                            chat_id=referrer_id,
                            text=f"{EMOJIS['trophy']} *CONGRATULATIONS!*\n\nYou've unlocked the product!\nUse /start to download it!",
                            parse_mode='Markdown'
                        )
            
            await db.commit()
            
            # Now give current user their referral link
            await get_referral_link_callback(update, context, product_id)
        else:
            channels_text = "\n".join([f"• {ch}" for ch in not_joined])
            await query.answer(f"Please join these channels first:\n{channels_text}", show_alert=True)


# ==================== USER COMMANDS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command with deep linking"""
    user_id = update.effective_user.id
    username = update.effective_user.username
    full_name = update.effective_user.full_name
    
    is_new = await register_user(user_id, username, full_name)
    
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


async def my_referrals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's referrals"""
    user_id = update.effective_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT r.product_id, p.name, r.status, r.date, r.credits_earned 
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
                product_id, name, status, date, credits = ref
                status_icon = "✅" if status == 'completed' else "⏳"
                text += f"{status_icon} *{name[:30]}*\n"
                text += f"└ Status: {status.upper()}\n"
                if credits > 0:
                    text += f"└ Earned: {credits} credits\n"
                text += f"└ Date: {date[:10]}\n\n"
        
        # Get stats
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
        
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show top users leaderboard"""
    query = update.callback_query if update.callback_query else None
    
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
        
        # Get user's rank
        cursor = await db.execute(
            "SELECT COUNT(*) + 1 FROM users WHERE total_referrals > (SELECT total_referrals FROM users WHERE user_id = ?)",
            (update.effective_user.id,)
        )
        user_rank = await cursor.fetchone()
        
        text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"*Your Rank:* #{user_rank[0] if user_rank else 'N/A'}\n"
        text += f"*Keep referring to climb the ranks!* 🚀"
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def my_credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's credits and transaction history"""
    user_id = update.effective_user.id
    query = update.callback_query if update.callback_query else None
    
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
                icon = "+" if amount > 0 else ""
                arrow = "📈" if amount > 0 else "📉"
                text += f"{arrow} {desc[:30]}\n"
                text += f"└ {icon}{amount} credits | {date[:10]}\n\n"
        
        text += f"\n💡 *How to earn credits:*\n"
        text += f"• 5 credits per successful referral\n"
        text += f"• Daily login bonus\n"
        text += f"• Complete special tasks\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')


# ==================== ADMIN PANEL ====================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel for managing products"""
    if update.effective_user.id != ADMIN_ID:
        await update.callback_query.answer("Access denied!", show_alert=True)
        return
    
    query = update.callback_query
    
    # Get stats
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        total_users = await cursor.fetchone()
        
        cursor = await db.execute("SELECT COUNT(*) FROM products")
        total_products = await cursor.fetchone()
        
        cursor = await db.execute("SELECT COUNT(*) FROM referrals WHERE status = 'completed'")
        total_refs = await cursor.fetchone()
        
        cursor = await db.execute("SELECT SUM(credits) FROM users")
        total_credits = await cursor.fetchone()
        
        text = f"""
{SENZO_BRAND}
⚙️ *Admin Control Panel*
━━━━━━━━━━━━━━━━━━━━━━

📊 *System Statistics*
• 👥 Users: {total_users[0]}
• 📦 Products: {total_products[0]}
• 🔗 Completed Referrals: {total_refs[0]}
• {EMOJIS['credit']} Total Credits: {total_credits[0] or 0}

━━━━━━━━━━━━━━━━━━━━━━
*Admin Actions*
"""
    
    keyboard = [
        [InlineKeyboardButton("➕ Add New Product", callback_data="admin_add_product")],
        [InlineKeyboardButton("📋 Manage Products", callback_data="admin_manage_products")],
        [InlineKeyboardButton("👥 View Users", callback_data="admin_view_users")],
        [InlineKeyboardButton("📊 Full Statistics", callback_data="admin_full_stats")],
        [InlineKeyboardButton("💎 Broadcast Message", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def admin_add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start multi-product addition process"""
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id != ADMIN_ID:
        return
    
    admin_sessions[ADMIN_ID] = {
        'step': 'name',
        'products': []
    }
    
    await query.edit_message_text(
        f"{SENZO_BRAND}\n"
        f"{EMOJIS['product']} *Add New Product*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Step 1/6:* Enter product name\n\n"
        f"Example: *Premium Video Course 2024*\n\n"
        f"Type /cancel to abort.",
        parse_mode='Markdown'
    )


async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle multi-step product addition"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if ADMIN_ID not in admin_sessions:
        return
    
    session = admin_sessions[ADMIN_ID]
    step = session.get('step')
    text = update.message.text
    
    if text == '/cancel':
        del admin_sessions[ADMIN_ID]
        await update.message.reply_text(f"{EMOJIS['error']} Product addition cancelled!")
        await main_menu(update, context)
        return
    
    if step == 'name':
        session['name'] = text
        session['step'] = 'description'
        await update.message.reply_text(
            f"{EMOJIS['success']} *Product name set:* {text}\n\n"
            f"*Step 2/6:* Enter product description\n\n"
            f"Describe what users will get (max 200 chars)",
            parse_mode='Markdown'
        )
    
    elif step == 'description':
        session['description'] = text[:200]
        session['step'] = 'file'
        await update.message.reply_text(
            f"{EMOJIS['success']} Description saved!\n\n"
            f"*Step 3/6:* Send the file\n\n"
            f"Supported formats: Document, Video, Photo, Audio",
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
                f"{EMOJIS['success']} Referrals required: {refs}\n\n"
                f"*Step 5/6:* Credits required (0 for free)\n\n"
                f"How many credits should users pay?",
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
                f"{EMOJIS['success']} Credits required: {credits}\n\n"
                f"*Step 6/6:* Channels (optional)\n\n"
                f"Enter channel usernames (space-separated) or type 'skip'\n\n"
                f"Example: @channel1 @channel2",
                parse_mode='Markdown'
            )
        except:
            await update.message.reply_text(f"{EMOJIS['error']} Please enter a valid number!")


async def handle_file_for_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file upload for product"""
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
        f"{EMOJIS['success']} File uploaded successfully!\n\n"
        f"*Step 4/6:* Required referrals\n\n"
        f"How many referrals to unlock this product?",
        parse_mode='Markdown'
    )


async def handle_channels_for_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle channels input and save product"""
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
    
    # Ask if admin wants to add another product
    keyboard = [
        [InlineKeyboardButton("✅ Add Another Product", callback_data="admin_add_product")],
        [InlineKeyboardButton("📋 View All Products", callback_data="admin_manage_products")],
        [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"{EMOJIS['trophy']} *PRODUCT ADDED SUCCESSFULLY!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Product:* {session['name']}\n"
        f"*ID:* `{product_id}`\n"
        f"*Link:* {product_link}\n"
        f"*Referrals:* {session['required_refs']}\n"
        f"*Credits:* {session['required_credits']}\n"
        f"*Channels:* {len(channels)}\n\n"
        f"Share this link with users to start earning!\n\n"
        f"*What would you like to do next?*",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def admin_manage_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage existing products"""
    query = update.callback_query
    
    if update.effective_user.id != ADMIN_ID:
        await query.answer("Access denied!", show_alert=True)
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, name, is_active, views, unlocks FROM products ORDER BY created_at DESC"
        )
        products = await cursor.fetchall()
        
        if not products:
            await query.edit_message_text(
                f"{EMOJIS['warning']} No products found!\n\nUse 'Add New Product' to get started.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")
                ]]),
                parse_mode='Markdown'
            )
            return
        
        text = f"""
{SENZO_BRAND}
{EMOJIS['product']} *Manage Products*
━━━━━━━━━━━━━━━━━━━━━━

"""
        
        keyboard = []
        for product in products:
            product_id, name, is_active, views, unlocks = product
            status = "✅ Active" if is_active else "❌ Inactive"
            text += f"*{name[:30]}*\n"
            text += f"└ ID: `{product_id}`\n"
            text += f"└ Status: {status}\n"
            text += f"└ 📊 {views} views | 🔓 {unlocks} unlocks\n\n"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"{'🔴' if is_active else '🟢'} {'Disable' if is_active else 'Enable'}",
                    callback_data=f"toggle_product_{product_id}"
                ),
                InlineKeyboardButton("📊 Stats", callback_data=f"product_stats_{product_id}")
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def toggle_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """Toggle product active status"""
    query = update.callback_query
    
    if update.effective_user.id != ADMIN_ID:
        await query.answer("Access denied!", show_alert=True)
        return
    
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
            await query.answer(f"Product {status_text}!", show_alert=True)
    
    await admin_manage_products(update, context)


async def admin_full_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show full statistics for admin"""
    query = update.callback_query
    
    if update.effective_user.id != ADMIN_ID:
        await query.answer("Access denied!", show_alert=True)
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        # User stats
        cursor = await db.execute("SELECT COUNT(*), SUM(credits), SUM(total_referrals) FROM users")
        user_stats = await cursor.fetchone()
        
        # Product stats
        cursor = await db.execute("SELECT COUNT(*), SUM(views), SUM(unlocks) FROM products")
        product_stats = await cursor.fetchone()
        
        # Referral stats
        cursor = await db.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending
            FROM referrals
        """)
        ref_stats = await cursor.fetchone()
        
        # Top products
        cursor = await db.execute(
            "SELECT name, views, unlocks FROM products ORDER BY unlocks DESC LIMIT 5"
        )
        top_products = await cursor.fetchall()
        
        text = f"""
{SENZO_BRAND}
📊 *FULL SYSTEM STATISTICS*
━━━━━━━━━━━━━━━━━━━━━━

👥 *User Statistics*
• Total Users: {user_stats[0] or 0}
• Total Credits: {user_stats[1] or 0}
• Total Referrals: {user_stats[2] or 0}

━━━━━━━━━━━━━━━━━━━━━━

📦 *Product Statistics*
• Total Products: {product_stats[0] or 0}
• Total Views: {product_stats[1] or 0}
• Total Unlocks: {product_stats[2] or 0}

━━━━━━━━━━━━━━━━━━━━━━

🔗 *Referral Statistics*
• Total Referrals: {ref_stats[0] or 0}
• Completed: {ref_stats[1] or 0}
• Pending: {ref_stats[2] or 0}
• Conversion Rate: {((ref_stats[1] or 0) / (ref_stats[0] or 1) * 100):.1f}%

━━━━━━━━━━━━━━━━━━━━━━

🏆 *Top Products*
"""
        
        for i, product in enumerate(top_products[:3]):
            name, views, unlocks = product
            text += f"{i+1}. {name[:25]}\n"
            text += f"   └ {views} views | {unlocks} unlocks\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def admin_view_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View users list for admin"""
    query = update.callback_query
    
    if update.effective_user.id != ADMIN_ID:
        await query.answer("Access denied!", show_alert=True)
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, username, full_name, credits, total_referrals, rank FROM users ORDER BY total_referrals DESC LIMIT 20"
        )
        users = await cursor.fetchall()
        
        text = f"""
{SENZO_BRAND}
👥 *Top Users*
━━━━━━━━━━━━━━━━━━━━━━

"""
        
        for i, user in enumerate(users[:10]):
            user_id, username, full_name, credits, refs, rank = user
            name = full_name or username or f"User_{user_id}"
            medals = ["🥇", "🥈", "🥉", "📌", "📌"]
            medal = medals[i] if i < 5 else f"{i+1}."
            text += f"{medal} *{name[:20]}*\n"
            text += f"└ {EMOJIS['credit']} {credits} | {EMOJIS['referral']} {refs}\n"
            text += f"└ Rank: {rank}\n\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


# ==================== CALLBACK HANDLER ====================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback queries"""
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id
    
    await query.answer()
    
    # Navigation
    if data == "back_to_menu":
        await main_menu(update, context, user_id)
    elif data == "browse_products":
        await browse_products(update, context, 0)
    elif data.startswith("browse_page_"):
        page = int(data.split("_")[2])
        await browse_products(update, context, page)
    elif data.startswith("product_"):
        product_id = data[8:]
        await view_product_details(update, context, product_id)
    elif data == "my_referrals":
        await my_referrals_command(update, context)
    elif data == "leaderboard":
        await leaderboard_command(update, context)
    elif data == "my_credits":
        await my_credits_command(update, context)
    elif data == "help_info":
        await help_command(update, context)
    
    # Product actions
    elif data.startswith("verify_"):
        product_id = data[7:]
        await verify_channels_and_unlock(update, context, product_id)
    elif data.startswith("get_ref_link_"):
        product_id = data[13:]
        await get_referral_link_callback(update, context, product_id)
    elif data.startswith("check_progress_"):
        product_id = data[15:]
        await view_product_details(update, context, product_id)
    
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
    elif data == "admin_broadcast":
        await query.edit_message_text("🚀 Broadcast feature coming soon!")
    elif data.startswith("toggle_product_"):
        product_id = data[14:]
        await toggle_product(update, context, product_id)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    text = f"""
{SENZO_BRAND}
ℹ️ *Help & Information*
━━━━━━━━━━━━━━━━━━━━━━

*How it works:*
1️⃣ Browse products from the menu
2️⃣ Choose a product you want
3️⃣ Share your referral link with friends
4️⃣ Each referral earns you 5 credits
5️⃣ Unlock products with referrals or credits

*Commands:*
/start - Open main menu
/myrefs - View your referrals
/help - Show this message

*Earning Credits:*
• +5 credits per successful referral
• Daily bonuses
• Special promotions

*Support:*
Contact @senzo_support for assistance

━━━━━━━━━━━━━━━━━━━━━━
*Your Success = Our Success!* 🚀
"""
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


# ==================== MAIN ====================

async def post_init(application: Application) -> None:
    """Post-init hook: runs inside the event loop managed by run_polling()"""
    await init_db()


def main():
    """Start the bot"""
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("myrefs", my_referrals_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # Callback handler
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    # Admin input handlers
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
    
    logger.info(f"🚀 Senzo Bot Started! Admin: {ADMIN_ID}")
    application.run_polling()


if __name__ == "__main__":
    main()
