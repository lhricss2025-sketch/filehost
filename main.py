import logging
import uuid
import sqlite3
import asyncio
from datetime import datetime
from typing import Dict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================
BOT_TOKEN = "8480004123:AAEmDVAia46G5ggfDqLDEIXNy5Zy4erXsOo"
ADMIN_ID = 6070145287

# Database
DB_PATH = "bot.db"

# Session for admin upload (simple)
admin_upload_session = {}

# Professional Branding
BRAND = """
✨ *SENZO FILES* ✨
━━━━━━━━━━━━━━━━
"""

# ==================== DATABASE FUNCTIONS ====================

def init_db():
    """Initialize database"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        credits INTEGER DEFAULT 0,
        joined_date TEXT
    )''')
    
    # Products table
    c.execute('''CREATE TABLE IF NOT EXISTS products (
        id TEXT PRIMARY KEY,
        file_id TEXT,
        file_type TEXT,
        required_refs INTEGER,
        created_date TEXT
    )''')
    
    # Referrals table
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER,
        product_id TEXT,
        status TEXT DEFAULT 'pending',
        date TEXT,
        UNIQUE(referrer_id, referred_id, product_id)
    )''')
    
    # User unlocks table
    c.execute('''CREATE TABLE IF NOT EXISTS user_unlocks (
        user_id INTEGER,
        product_id TEXT,
        unlocked_date TEXT,
        PRIMARY KEY (user_id, product_id)
    )''')
    
    conn.commit()
    conn.close()
    logger.info("Database ready")

def get_user(user_id):
    """Get or create user"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    
    if not user:
        c.execute("INSERT INTO users (user_id, username, credits, joined_date) VALUES (?, ?, ?, ?)",
                  (user_id, "", 0, datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()
        return {'user_id': user_id, 'credits': 0}
    
    conn.close()
    return {'user_id': user[0], 'credits': user[2]}

def add_credits(user_id, amount):
    """Add credits to user"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET credits = credits + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def get_referral_count(referrer_id, product_id):
    """Get completed referrals count"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND product_id = ? AND status = 'completed'",
              (referrer_id, product_id))
    count = c.fetchone()[0]
    conn.close()
    return count

def is_unlocked(user_id, product_id):
    """Check if user unlocked product"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM user_unlocks WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    unlocked = c.fetchone()
    conn.close()
    return unlocked is not None

def mark_unlocked(user_id, product_id):
    """Mark product as unlocked for user"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO user_unlocks (user_id, product_id, unlocked_date) VALUES (?, ?, ?)",
              (user_id, product_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def get_all_products():
    """Get all products"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, file_id, file_type, required_refs FROM products ORDER BY created_date DESC")
    products = c.fetchall()
    conn.close()
    return products

def delete_all_products():
    """Delete all products (for clean testing)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM products")
    c.execute("DELETE FROM referrals")
    c.execute("DELETE FROM user_unlocks")
    conn.commit()
    conn.close()

# ==================== MAIN MENU ====================

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show main menu"""
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    text = f"""
{BRAND}
👤 *Welcome to Senzo Files!*

━━━━━━━━━━━━━━━━
💰 *Your Credits:* `{user['credits']}`
🔗 *Your Referrals:* Track from below
━━━━━━━━━━━━━━━━

📦 *What would you like to do?*
"""
    
    keyboard = [
        [InlineKeyboardButton("📦 Browse Files", callback_data="browse_files")],
        [InlineKeyboardButton("🔗 My Referrals", callback_data="my_refs")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
        [InlineKeyboardButton("💰 My Credits", callback_data="my_credits")]
    ]
    
    if update.effective_user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# ==================== PRODUCT BROWSING ====================

async def browse_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all available files"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    products = get_all_products()
    
    if not products:
        text = f"""
{BRAND}
📭 *No files available yet!*

Check back later for exciting content.
"""
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return
    
    text = f"""
{BRAND}
📦 *Available Files*
━━━━━━━━━━━━━━━━
"""
    
    keyboard = []
    for product in products:
        product_id, file_id, file_type, req_refs = product
        unlocked = is_unlocked(user_id, product_id)
        
        if unlocked:
            status = "✅ UNLOCKED"
            button_text = f"📥 Download {file_type.upper()}"
            callback = f"download_{product_id}"
        else:
            status = f"🔒 LOCKED ({req_refs} referrals)"
            button_text = f"🔓 Unlock {file_type.upper()}"
            callback = f"view_{product_id}"
        
        text += f"\n*{file_type.upper()} File*\n└ Status: {status}\n"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback)])
    
    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def view_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """View product details and get referral link"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT required_refs, file_type FROM products WHERE id = ?", (product_id,))
    product = c.fetchone()
    conn.close()
    
    if not product:
        await query.answer("File not found!", show_alert=True)
        return
    
    req_refs, file_type = product
    current_refs = get_referral_count(user_id, product_id)
    
    if current_refs >= req_refs:
        await query.answer("You already unlocked this! Downloading...")
        await download_file(update, context, product_id)
        return
    
    bot_username = (await context.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}_{product_id}"
    
    text = f"""
{BRAND}
🔗 *Your Referral Link*

📁 *File:* {file_type.upper()}
🎯 *Required:* {req_refs} referrals
📊 *Your Progress:* {current_refs}/{req_refs}

`{ref_link}`

━━━━━━━━━━━━━━━━
✨ *Share this link with your friends!*
💎 *Each referral = 5 credits for you!*
"""
    
    keyboard = [
        [InlineKeyboardButton("📊 Check Progress", callback_data=f"progress_{product_id}")],
        [InlineKeyboardButton("🔙 Back to Files", callback_data="browse_files")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def download_file(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """Download unlocked file"""
    user_id = update.effective_user.id
    
    if not is_unlocked(user_id, product_id):
        # Check if should be unlocked now
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT file_id, file_type, required_refs FROM products WHERE id = ?", (product_id,))
        product = c.fetchone()
        conn.close()
        
        if product:
            file_id, file_type, req_refs = product
            current_refs = get_referral_count(user_id, product_id)
            
            if current_refs >= req_refs:
                mark_unlocked(user_id, product_id)
                await send_file(update, context, file_id, file_type, product_id)
                return
        
        await update.callback_query.answer("Please unlock this file first!", show_alert=True)
        return
    
    # Send already unlocked file
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT file_id, file_type FROM products WHERE id = ?", (product_id,))
    product = c.fetchone()
    conn.close()
    
    if product:
        await send_file(update, context, product[0], product[1], product_id)

async def send_file(update, context, file_id, file_type, product_id):
    """Send file to user"""
    query = update.callback_query
    
    try:
        if file_type == 'document':
            await context.bot.send_document(
                chat_id=update.effective_user.id,
                document=file_id,
                caption=f"✅ *File Unlocked!*\n\n{BRAND}Thank you for using Senzo Files!",
                parse_mode='Markdown'
            )
        elif file_type == 'video':
            await context.bot.send_video(
                chat_id=update.effective_user.id,
                video=file_id,
                caption=f"✅ *File Unlocked!*\n\n{BRAND}Thank you for using Senzo Files!",
                parse_mode='Markdown'
            )
        elif file_type == 'photo':
            await context.bot.send_photo(
                chat_id=update.effective_user.id,
                photo=file_id,
                caption=f"✅ *File Unlocked!*\n\n{BRAND}Thank you for using Senzo Files!",
                parse_mode='Markdown'
            )
        elif file_type == 'audio':
            await context.bot.send_audio(
                chat_id=update.effective_user.id,
                audio=file_id,
                caption=f"✅ *File Unlocked!*\n\n{BRAND}Thank you for using Senzo Files!",
                parse_mode='Markdown'
            )
        
        await query.answer("File sent! Check your chat.")
        
    except Exception as e:
        logger.error(f"Error sending file: {e}")
        await query.answer("Error sending file!", show_alert=True)

async def check_progress(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """Check referral progress"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT required_refs FROM products WHERE id = ?", (product_id,))
    product = c.fetchone()
    conn.close()
    
    if not product:
        await query.answer("File not found!", show_alert=True)
        return
    
    req_refs = product[0]
    current_refs = get_referral_count(user_id, product_id)
    
    text = f"""
{BRAND}
📊 *Your Progress*

🎯 Required: {req_refs}
✅ Completed: {current_refs}
💎 Remaining: {req_refs - current_refs}

━━━━━━━━━━━━━━━━
💡 Keep sharing your referral link!
"""
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data=f"view_{product_id}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# ==================== USER COMMANDS ====================

async def my_refs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's referrals"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT r.product_id, r.status, r.date, p.file_type
        FROM referrals r
        JOIN products p ON r.product_id = p.id
        WHERE r.referrer_id = ?
        ORDER BY r.date DESC
    """, (user_id,))
    referrals = c.fetchall()
    
    c.execute("SELECT COUNT(*), SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) FROM referrals WHERE referrer_id = ?", (user_id,))
    stats = c.fetchone()
    conn.close()
    
    text = f"""
{BRAND}
🔗 *My Referrals*
━━━━━━━━━━━━━━━━

📊 *Statistics:*
• Total: {stats[0] or 0}
• Completed: {stats[1] or 0}
• Pending: {(stats[0] or 0) - (stats[1] or 0)}

"""
    
    if referrals:
        text += "\n*Recent Activity:*\n"
        for ref in referrals[:5]:
            product_id, status, date, file_type = ref
            icon = "✅" if status == 'completed' else "⏳"
            text += f"{icon} {file_type.upper()} - {status.upper()} ({date[:10]})\n"
    else:
        text += "\nNo referrals yet.\nShare your links to earn credits!"
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show leaderboard"""
    query = update.callback_query
    await query.answer()
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT user_id, username, credits, 
               (SELECT COUNT(*) FROM referrals WHERE referrer_id = users.user_id AND status = 'completed') as refs
        FROM users 
        ORDER BY refs DESC 
        LIMIT 10
    """)
    top_users = c.fetchall()
    conn.close()
    
    text = f"""
{BRAND}
🏆 *Leaderboard*
━━━━━━━━━━━━━━━━

"""
    
    medals = ["🥇", "🥈", "🥉"]
    for i, user in enumerate(top_users):
        user_id, username, credits, refs = user
        medal = medals[i] if i < 3 else f"{i+1}."
        name = f"User_{user_id}" if not username else username[:15]
        text += f"{medal} *{name}*\n└ 🔗 {refs} refs | 💰 {credits} credits\n\n"
    
    # Get user's rank
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) + 1 FROM users 
        WHERE (SELECT COUNT(*) FROM referrals WHERE referrer_id = users.user_id AND status = 'completed') >
              (SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND status = 'completed')
    """, (update.effective_user.id,))
    rank = c.fetchone()
    conn.close()
    
    text += f"━━━━━━━━━━━━━━━━\n📌 *Your Rank:* #{rank[0] if rank else 'N/A'}"
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def my_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's credits"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    user = get_user(user_id)
    ref_count = get_referral_count(user_id, "all")
    
    text = f"""
{BRAND}
💰 *My Credits*
━━━━━━━━━━━━━━━━

💎 *Balance:* `{user['credits']} credits`

📊 *Stats:*
• Total Referrals: {ref_count}
• Credits per referral: 5

━━━━━━━━━━━━━━━━
💡 *How to earn more:*
• Share your referral links
• Each referral = 5 credits
• More credits = Higher rank!
"""
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# ==================== ADMIN FUNCTIONS ====================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel"""
    if update.effective_user.id != ADMIN_ID:
        await update.callback_query.answer("Access denied!", show_alert=True)
        return
    
    query = update.callback_query
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM products")
    total_products = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM referrals WHERE status = 'completed'")
    total_refs = c.fetchone()[0]
    conn.close()
    
    text = f"""
{BRAND}
⚙️ *Admin Panel*
━━━━━━━━━━━━━━━━

📊 *Statistics:*
• 👥 Users: {total_users}
• 📦 Files: {total_products}
• 🔗 Completed Refs: {total_refs}

━━━━━━━━━━━━━━━━
*Actions:*
"""
    
    keyboard = [
        [InlineKeyboardButton("➕ Upload New File", callback_data="admin_upload")],
        [InlineKeyboardButton("📋 Manage Files", callback_data="admin_manage")],
        [InlineKeyboardButton("👥 View Users", callback_data="admin_users")],
        [InlineKeyboardButton("🗑️ Delete All Files", callback_data="admin_delete_all")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start file upload process"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    query = update.callback_query
    await query.answer()
    
    admin_upload_session[ADMIN_ID] = {'step': 'file'}
    
    await query.edit_message_text(
        f"{BRAND}\n"
        f"📤 *Upload New File*\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"*Step 1/2:* Send me the file\n\n"
        f"Supported: Document, Video, Photo, Audio\n"
        f"Type /cancel to abort",
        parse_mode='Markdown'
    )

async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file upload from admin"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if ADMIN_ID not in admin_upload_session or admin_upload_session[ADMIN_ID].get('step') != 'file':
        return
    
    # Get file
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
        await update.message.reply_text("❌ Unsupported file type!")
        return
    
    admin_upload_session[ADMIN_ID]['file_id'] = file_id
    admin_upload_session[ADMIN_ID]['file_type'] = file_type
    admin_upload_session[ADMIN_ID]['step'] = 'refs'
    
    await update.message.reply_text(
        f"✅ File received!\n\n"
        f"*Step 2/2:* How many referrals required?\n"
        f"Send a number (e.g., 3)",
        parse_mode='Markdown'
    )

async def handle_refs_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle referrals input and save product"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if ADMIN_ID not in admin_upload_session or admin_upload_session[ADMIN_ID].get('step') != 'refs':
        return
    
    try:
        req_refs = int(update.message.text.strip())
        if req_refs <= 0:
            raise ValueError
    except:
        await update.message.reply_text("❌ Please send a valid positive number!")
        return
    
    session = admin_upload_session[ADMIN_ID]
    product_id = str(uuid.uuid4())[:8]
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO products (id, file_id, file_type, required_refs, created_date) VALUES (?, ?, ?, ?, ?)",
              (product_id, session['file_id'], session['file_type'], req_refs, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    
    del admin_upload_session[ADMIN_ID]
    
    bot_username = (await context.bot.get_me()).username
    product_link = f"https://t.me/{bot_username}?start=product_{product_id}"
    
    await update.message.reply_text(
        f"✅ *File Uploaded Successfully!*\n\n"
        f"📁 Type: {session['file_type'].upper()}\n"
        f"🔗 Referrals Required: {req_refs}\n"
        f"🔗 Link: {product_link}\n\n"
        f"Share this link with users!",
        parse_mode='Markdown'
    )

async def admin_manage_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage uploaded files"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    query = update.callback_query
    await query.answer()
    
    products = get_all_products()
    
    if not products:
        await query.edit_message_text("No files found!", reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="admin_panel")
        ]]))
        return
    
    text = f"{BRAND}📋 *Uploaded Files*\n━━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    
    for product in products:
        product_id, file_id, file_type, req_refs = product
        text += f"📁 *{file_type.upper()}*\n└ ID: `{product_id}`\n└ Refs: {req_refs}\n\n"
        keyboard.append([InlineKeyboardButton(f"🗑️ Delete {file_type.upper()}", callback_data=f"delete_{product_id}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    """Delete a product"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM products WHERE id = ?", (product_id,))
    c.execute("DELETE FROM referrals WHERE product_id = ?", (product_id,))
    c.execute("DELETE FROM user_unlocks WHERE product_id = ?", (product_id,))
    conn.commit()
    conn.close()
    
    await update.callback_query.answer("File deleted!", show_alert=True)
    await admin_manage_files(update, context)

async def admin_view_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View all users"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    query = update.callback_query
    await query.answer()
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT user_id, username, credits, 
               (SELECT COUNT(*) FROM referrals WHERE referrer_id = users.user_id AND status = 'completed') as refs
        FROM users 
        ORDER BY refs DESC 
        LIMIT 20
    """)
    users = c.fetchall()
    conn.close()
    
    text = f"{BRAND}👥 *Users List*\n━━━━━━━━━━━━━━━━\n\n"
    
    for i, user in enumerate(users[:10]):
        user_id, username, credits, refs = user
        name = f"User_{user_id}" if not username else username[:15]
        text += f"{i+1}. *{name}*\n└ 🔗 {refs} refs | 💰 {credits} credits\n\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_delete_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete all files"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    query = update.callback_query
    
    keyboard = [
        [InlineKeyboardButton("✅ Yes, Delete All", callback_data="confirm_delete_all")],
        [InlineKeyboardButton("❌ No, Go Back", callback_data="admin_panel")]
    ]
    
    await query.edit_message_text(
        f"{BRAND}\n⚠️ *WARNING!*\n\nAre you sure you want to delete ALL files?\nThis action cannot be undone!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def confirm_delete_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm delete all files"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    delete_all_products()
    await update.callback_query.answer("All files deleted!", show_alert=True)
    await admin_panel(update, context)

# ==================== START COMMAND WITH DEEP LINKING ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    
    # Register user
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
    conn.commit()
    conn.close()
    
    get_user(user_id)  # Creates if not exists
    
    # Handle deep links
    if context.args:
        param = context.args[0]
        
        # Product deep link
        if param.startswith("product_"):
            product_id = param[8:]
            await view_product(update, context, product_id)
            return
        
        # Referral deep link
        elif param.startswith("ref_"):
            parts = param[4:].split("_")
            if len(parts) == 2:
                referrer_id = int(parts[0])
                product_id = parts[1]
                
                if referrer_id != user_id:
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    try:
                        c.execute("INSERT INTO referrals (referrer_id, referred_id, product_id, status, date) VALUES (?, ?, ?, ?, ?)",
                                  (referrer_id, user_id, product_id, 'pending', datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                        conn.commit()
                        
                        # Check if referrer completed requirements
                        req_refs = c.execute("SELECT required_refs FROM products WHERE id = ?", (product_id,)).fetchone()
                        if req_refs:
                            current_refs = get_referral_count(referrer_id, product_id)
                            
                            if current_refs >= req_refs[0] and not is_unlocked(referrer_id, product_id):
                                mark_unlocked(referrer_id, product_id)
                                add_credits(referrer_id, 10)
                                
                                await context.bot.send_message(
                                    chat_id=referrer_id,
                                    text=f"🎉 *Congratulations!*\n\nYou've unlocked the file!\n\n{BRAND}Use /start to download it!",
                                    parse_mode='Markdown'
                                )
                        
                        # Add credits to referrer
                        add_credits(referrer_id, 5)
                        
                        await update.message.reply_text(
                            f"✅ *Referral Tracked!*\n\nYou were referred to this file!\nComplete {req_refs[0] if req_refs else 0} referrals to unlock it.",
                            parse_mode='Markdown'
                        )
                    except:
                        pass
                    conn.close()
    
    await main_menu(update, context)

# ==================== CALLBACK HANDLER ====================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all button clicks"""
    query = update.callback_query
    data = query.data
    
    if data == "back_menu":
        await main_menu(update, context)
    elif data == "browse_files":
        await browse_files(update, context)
    elif data == "my_refs":
        await my_refs(update, context)
    elif data == "leaderboard":
        await leaderboard(update, context)
    elif data == "my_credits":
        await my_credits(update, context)
    elif data == "admin_panel":
        await admin_panel(update, context)
    elif data == "admin_upload":
        await admin_upload_start(update, context)
    elif data == "admin_manage":
        await admin_manage_files(update, context)
    elif data == "admin_users":
        await admin_view_users(update, context)
    elif data == "admin_delete_all":
        await admin_delete_all(update, context)
    elif data == "confirm_delete_all":
        await confirm_delete_all(update, context)
    elif data.startswith("view_"):
        product_id = data[5:]
        await view_product(update, context, product_id)
    elif data.startswith("download_"):
        product_id = data[9:]
        await download_file(update, context, product_id)
    elif data.startswith("progress_"):
        product_id = data[9:]
        await check_progress(update, context, product_id)
    elif data.startswith("delete_"):
        product_id = data[7:]
        await delete_product(update, context, product_id)

# ==================== MAIN ====================

async def main():
    """Start the bot"""
    init_db()
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", start_command))
    
    # Callback handler
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    # Admin message handlers
    application.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO | filters.PHOTO | filters.AUDIO, handle_file_upload))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_refs_input))
    
    logger.info(f"🚀 Bot Started! Admin ID: {ADMIN_ID}")
    
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
