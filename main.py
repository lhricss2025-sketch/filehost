import logging
import uuid
import os
import sys
from datetime import datetime
from typing import Dict, Optional, Tuple

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

# Environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))

# Validate environment variables
if not BOT_TOKEN:
    logger.error("BOT_TOKEN environment variable is not set! Exiting...")
    sys.exit(1)
if ADMIN_ID == 0:
    logger.error("ADMIN_ID environment variable is not set or invalid! Exiting...")
    sys.exit(1)

# Database file path
DB_PATH = "bot_database.db"

# Global variables for upload flows
admin_uploads: Dict[int, Dict] = {}


async def init_db():
    """Initialize database tables"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Products table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                file_id TEXT,
                file_type TEXT,
                required_refs INTEGER,
                admin_id INTEGER,
                created_at TIMESTAMP
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
        
        # Users table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                joined_date TIMESTAMP
            )
        """)
        
        # Referrals table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER,
                product_id TEXT,
                status TEXT DEFAULT 'pending',
                date TIMESTAMP,
                UNIQUE(referrer_id, referred_id, product_id)
            )
        """)
        
        await db.commit()


async def register_user(user_id: int, username: str = None):
    """Register a new user if not exists"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, joined_date) VALUES (?, ?, ?)",
            (user_id, username or "", datetime.now())
        )
        await db.commit()


async def get_user_referrals_count(user_id: int, product_id: str) -> int:
    """Get completed referrals count for a user and product"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND product_id = ? AND status = 'completed'",
            (user_id, product_id)
        )
        count = await cursor.fetchone()
        return count[0] if count else 0


async def check_user_unlocked(user_id: int, product_id: str) -> bool:
    """Check if user has unlocked the product"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT required_refs FROM products WHERE id = ?",
            (product_id,)
        )
        product = await cursor.fetchone()
        if not product:
            return False
        
        required_refs = product[0]
        current_refs = await get_user_referrals_count(user_id, product_id)
        return current_refs >= required_refs


async def send_file_to_user(context: ContextTypes.DEFAULT_TYPE, user_id: int, product_id: str):
    """Send the unlocked file to user"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT file_id, file_type FROM products WHERE id = ?",
            (product_id,)
        )
        product = await cursor.fetchone()
        
        if product:
            file_id, file_type = product
            try:
                if file_type == 'document':
                    await context.bot.send_document(
                        chat_id=user_id,
                        document=file_id,
                        caption="✅ Congratulations! File Unlocked 🎉"
                    )
                elif file_type == 'video':
                    await context.bot.send_video(
                        chat_id=user_id,
                        video=file_id,
                        caption="✅ Congratulations! File Unlocked 🎉"
                    )
                elif file_type == 'photo':
                    await context.bot.send_photo(
                        chat_id=user_id,
                        photo=file_id,
                        caption="✅ Congratulations! File Unlocked 🎉"
                    )
                elif file_type == 'audio':
                    await context.bot.send_audio(
                        chat_id=user_id,
                        audio=file_id,
                        caption="✅ Congratulations! File Unlocked 🎉"
                    )
            except Exception as e:
                logger.error(f"Error sending file to user {user_id}: {e}")


# ==================== ADMIN COMMANDS ====================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel with inline keyboard"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    keyboard = [
        [InlineKeyboardButton("📤 Upload New Product", callback_data="admin_upload")],
        [InlineKeyboardButton("📊 View All Products", callback_data="admin_products")],
        [InlineKeyboardButton("📈 Bot Stats", callback_data="admin_stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Admin Panel:", reply_markup=reply_markup)


# ==================== USER COMMANDS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command with deep linking"""
    user_id = update.effective_user.id
    username = update.effective_user.username
    
    await register_user(user_id, username)
    
    # Check if there's a deep link parameter
    if context.args:
        param = context.args[0]
        
        # Handle product deep link
        if param.startswith("product_"):
            product_id = param[8:]  # Remove "product_" prefix
            await show_product_info(update, context, product_id, user_id)
            return
        
        # Handle referral deep link
        elif param.startswith("ref_"):
            parts = param[4:].split("_")  # Remove "ref_" prefix
            if len(parts) == 2:
                referrer_id = int(parts[0])
                product_id = parts[1]
                
                # Check self-referral
                if referrer_id == user_id:
                    await update.message.reply_text("❌ You cannot refer yourself!")
                    return
                
                # Add pending referral
                async with aiosqlite.connect(DB_PATH) as db:
                    try:
                        await db.execute(
                            "INSERT INTO referrals (referrer_id, referred_id, product_id, status, date) VALUES (?, ?, ?, ?, ?)",
                            (referrer_id, user_id, product_id, 'pending', datetime.now())
                        )
                        await db.commit()
                    except:
                        pass  # Duplicate referral ignored
                
                await show_product_info(update, context, product_id, user_id)
                return
    
    # Default start message
    await update.message.reply_text(
        "🎉 Welcome to the Referral Bot!\n\n"
        "This bot allows you to unlock files by referring friends.\n\n"
        "Use /help to learn how it works."
    )


async def myrefs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's progress for all products"""
    user_id = update.effective_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Get all products the user has referrals for
        cursor = await db.execute(
            "SELECT DISTINCT product_id FROM referrals WHERE referrer_id = ?",
            (user_id,)
        )
        products = await cursor.fetchall()
        
        if not products:
            await update.message.reply_text("📭 You haven't started any referrals yet.")
            return
        
        message = "📊 *Your Referral Progress*\n\n"
        for (product_id,) in products:
            # Get product details
            cursor = await db.execute(
                "SELECT required_refs FROM products WHERE id = ?",
                (product_id,)
            )
            product = await cursor.fetchone()
            if product:
                required_refs = product[0]
                current_refs = await get_user_referrals_count(user_id, product_id)
                unlocked = current_refs >= required_refs
                status = "✅ Unlocked" if unlocked else f"⏳ Progress: {current_refs}/{required_refs}"
                message += f"*Product:* {product_id[:8]}...\n{status}\n\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message"""
    await update.message.reply_text(
        "🤖 *How the Bot Works*\n\n"
        "1️⃣ Click a product link sent by the admin\n"
        "2️⃣ If required, join the mandatory channels\n"
        "3️⃣ Get your unique referral link\n"
        "4️⃣ Share the link with friends\n"
        "5️⃣ Once you reach the required referrals, the file is unlocked automatically!\n\n"
        "Commands:\n"
        "/myrefs - Check your progress\n"
        "/help - Show this message",
        parse_mode='Markdown'
    )


async def show_product_info(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str, user_id: int):
    """Show product information and options"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Get product details
        cursor = await db.execute(
            "SELECT required_refs, file_type FROM products WHERE id = ?",
            (product_id,)
        )
        product = await cursor.fetchone()
        
        if not product:
            await update.message.reply_text("❌ Product not found!")
            return
        
        required_refs = product[0]
        
        # Check if user already unlocked
        if await check_user_unlocked(user_id, product_id):
            await send_file_to_user(context, user_id, product_id)
            return
        
        # Get current referrals
        current_refs = await get_user_referrals_count(user_id, product_id)
        
        # Get channels
        cursor = await db.execute(
            "SELECT channel_username FROM product_channels WHERE product_id = ?",
            (product_id,)
        )
        channels = await cursor.fetchall()
        
        message = f"🔒 *File Locked!*\n\nRequired Referrals: {required_refs}\nYour Progress: {current_refs}/{required_refs}\n\n"
        
        keyboard = []
        
        if channels:
            message += "\n📢 *Mandatory Channels:*\n"
            for (channel,) in channels:
                message += f"• {channel}\n"
                keyboard.append([InlineKeyboardButton(f"Join {channel}", url=f"https://t.me/{channel[1:] if channel.startswith('@') else channel}")])
            keyboard.append([InlineKeyboardButton("✅ Check Join Status", callback_data=f"check_{product_id}")])
        else:
            keyboard.append([InlineKeyboardButton("🔗 Get Referral Link", callback_data=f"ref_{product_id}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')


# ==================== ADMIN UPLOAD FLOW ====================

async def admin_upload_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start product upload flow"""
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id != ADMIN_ID:
        return
    
    admin_uploads[ADMIN_ID] = {'step': 'file'}
    await query.edit_message_text("📤 *Upload New Product*\n\nStep 1/3: Send me the file (document, video, photo, or audio)", parse_mode='Markdown')


async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file upload from admin"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if ADMIN_ID not in admin_uploads or admin_uploads[ADMIN_ID].get('step') != 'file':
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
        await update.message.reply_text("❌ Unsupported file type! Please send document, video, photo, or audio.")
        return
    
    admin_uploads[ADMIN_ID]['file_id'] = file_id
    admin_uploads[ADMIN_ID]['file_type'] = file_type
    admin_uploads[ADMIN_ID]['step'] = 'refs'
    await update.message.reply_text("✅ File received!\n\nStep 2/3: Send the number of referrals required to unlock this file (e.g., 5)")


async def handle_refs_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle required referrals input"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if ADMIN_ID not in admin_uploads or admin_uploads[ADMIN_ID].get('step') != 'refs':
        return
    
    try:
        required_refs = int(update.message.text.strip())
        if required_refs <= 0:
            raise ValueError
    except:
        await update.message.reply_text("❌ Please send a valid positive number!")
        return
    
    admin_uploads[ADMIN_ID]['required_refs'] = required_refs
    admin_uploads[ADMIN_ID]['step'] = 'channels'
    await update.message.reply_text(f"✅ Referrals set to {required_refs}\n\nStep 3/3: Send mandatory channels (one per line, space-separated, or type 'skip')\n\nExample: @channel1 @channel2\nOr type 'skip' to continue without channels")


async def handle_channels_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle channels input and save product"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if ADMIN_ID not in admin_uploads or admin_uploads[ADMIN_ID].get('step') != 'channels':
        return
    
    text = update.message.text.strip()
    channels = []
    
    if text.lower() != 'skip':
        # Parse channels (split by space)
        for channel in text.split():
            channel = channel.strip()
            if channel.startswith('@'):
                channels.append(channel)
            elif channel.startswith('https://t.me/'):
                channel = channel.replace('https://t.me/', '@')
                channels.append(channel)
    
    # Generate product ID
    product_id = str(uuid.uuid4())
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Save product
        await db.execute(
            "INSERT INTO products (id, file_id, file_type, required_refs, admin_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (product_id, admin_uploads[ADMIN_ID]['file_id'], admin_uploads[ADMIN_ID]['file_type'], 
             admin_uploads[ADMIN_ID]['required_refs'], ADMIN_ID, datetime.now())
        )
        
        # Save channels
        for channel in channels:
            await db.execute(
                "INSERT INTO product_channels (product_id, channel_username) VALUES (?, ?)",
                (product_id, channel)
            )
        
        await db.commit()
    
    # Clean up
    del admin_uploads[ADMIN_ID]
    
    bot_username = (await context.bot.get_me()).username
    product_link = f"https://t.me/{bot_username}?start=product_{product_id}"
    
    await update.message.reply_text(
        f"✅ *Product uploaded successfully!*\n\n"
        f"Product ID: `{product_id}`\n"
        f"Link: {product_link}\n\n"
        f"Share this link with users to start the referral system!",
        parse_mode='Markdown'
    )


# ==================== CALLBACK HANDLERS ====================

async def check_join_status(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str, user_id: int):
    """Check if user joined all channels"""
    query = update.callback_query
    await query.answer()
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT channel_username FROM product_channels WHERE product_id = ?",
            (product_id,)
        )
        channels = await cursor.fetchall()
        
        if not channels:
            # No channels to check
            await query.edit_message_text("✅ No channels to join! You can get your referral link.")
            keyboard = [[InlineKeyboardButton("🔗 Get Referral Link", callback_data=f"ref_{product_id}")]]
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        all_joined = True
        for (channel,) in channels:
            # Remove @ if present
            channel_name = channel[1:] if channel.startswith('@') else channel
            try:
                member = await context.bot.get_chat_member(f"@{channel_name}", user_id)
                if member.status not in ['member', 'administrator', 'creator']:
                    all_joined = False
                    break
            except Exception as e:
                logger.error(f"Error checking channel {channel_name}: {e}")
                all_joined = False
                break
        
        if all_joined:
            # Update all pending referrals to completed and notify referrers
            cursor = await db.execute(
                "SELECT referrer_id FROM referrals WHERE referred_id = ? AND product_id = ? AND status = 'pending'",
                (user_id, product_id)
            )
            pending_refs = await cursor.fetchall()
            
            for (referrer_id,) in pending_refs:
                # Update status
                await db.execute(
                    "UPDATE referrals SET status = 'completed' WHERE referred_id = ? AND product_id = ? AND referrer_id = ?",
                    (user_id, product_id, referrer_id)
                )
                await db.commit()
                
                # Get updated count for referrer
                current_refs = await get_user_referrals_count(referrer_id, product_id)
                
                # Get required refs
                cursor2 = await db.execute("SELECT required_refs FROM products WHERE id = ?", (product_id,))
                product_data = await cursor2.fetchone()
                if product_data:
                    required_refs = product_data[0]
                    
                    # Notify referrer
                    try:
                        progress_msg = f"🎉 New Referral! Progress: {current_refs}/{required_refs}"
                        if current_refs >= required_refs:
                            progress_msg += "\n\n✅ Congratulations! You've unlocked the file!"
                        await context.bot.send_message(chat_id=referrer_id, text=progress_msg)
                        
                        # Send file if unlocked
                        if current_refs >= required_refs:
                            await send_file_to_user(context, referrer_id, product_id)
                    except Exception as e:
                        logger.error(f"Error notifying referrer {referrer_id}: {e}")
            
            # Get product info for current user
            cursor = await db.execute("SELECT required_refs FROM products WHERE id = ?", (product_id,))
            product = await cursor.fetchone()
            
            if product:
                current_refs_user = await get_user_referrals_count(user_id, product_id)
                required_refs_user = product[0]
                
                await query.edit_message_text(
                    f"✅ Joined all channels!\n\nYour Progress: {current_refs_user}/{required_refs_user}\n\n"
                    f"Share your referral link to unlock the file!"
                )
                
                # Show referral link button
                bot_username = (await context.bot.get_me()).username
                ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}_{product_id}"
                keyboard = [[InlineKeyboardButton("🔗 Share Referral Link", url=ref_link)]]
                await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.answer("❌ Please join all channels first!", show_alert=True)


async def get_referral_link(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str, user_id: int):
    """Generate and show referral link"""
    query = update.callback_query
    await query.answer()
    
    bot_username = (await context.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}_{product_id}"
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT required_refs FROM products WHERE id = ?", (product_id,))
        product = await cursor.fetchone()
        
        if product:
            required_refs = product[0]
            current_refs = await get_user_referrals_count(user_id, product_id)
            
            await query.edit_message_text(
                f"🔗 *Your Referral Link*\n\n"
                f"`{ref_link}`\n\n"
                f"Share this link with friends!\n"
                f"Progress: {current_refs}/{required_refs}\n\n"
                f"Once {required_refs - current_refs} more friends join and verify channels, you'll unlock the file!",
                parse_mode='Markdown'
            )
            keyboard = [[InlineKeyboardButton("📊 Check Progress", callback_data=f"progress_{product_id}")]]
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))


async def show_progress(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str, user_id: int):
    """Show current progress"""
    query = update.callback_query
    await query.answer()
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT required_refs FROM products WHERE id = ?", (product_id,))
        product = await cursor.fetchone()
        
        if product:
            required_refs = product[0]
            current_refs = await get_user_referrals_count(user_id, product_id)
            unlocked = current_refs >= required_refs
            
            if unlocked:
                await query.edit_message_text("✅ You have already unlocked this file!")
                await send_file_to_user(context, user_id, product_id)
            else:
                await query.edit_message_text(
                    f"📊 *Your Progress*\n\n"
                    f"Referrals: {current_refs}/{required_refs}\n"
                    f"Remaining: {required_refs - current_refs}\n\n"
                    f"Keep sharing your referral link!",
                    parse_mode='Markdown'
                )


async def admin_view_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View all products (admin)"""
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id != ADMIN_ID:
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, required_refs, created_at FROM products ORDER BY created_at DESC")
        products = await cursor.fetchall()
        
        if not products:
            await query.edit_message_text("📭 No products found. Use 'Upload New Product' to add one.")
            return
        
        message = "📊 *All Products*\n\n"
        bot_username = (await context.bot.get_me()).username
        
        for product_id, required_refs, created_at in products:
            # Get channel count
            cursor2 = await db.execute("SELECT COUNT(*) FROM product_channels WHERE product_id = ?", (product_id,))
            channel_count = await cursor2.fetchone()
            
            message += f"*ID:* `{product_id}`\n"
            message += f"*Refs Needed:* {required_refs}\n"
            message += f"*Channels:* {channel_count[0]}\n"
            message += f"*Link:* https://t.me/{bot_username}?start=product_{product_id}\n"
            message += f"*Created:* {created_at[:10]}\n\n"
        
        await query.edit_message_text(message, parse_mode='Markdown', disable_web_page_preview=True)


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot statistics (admin)"""
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id != ADMIN_ID:
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Total users
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        total_users = await cursor.fetchone()
        
        # Total products
        cursor = await db.execute("SELECT COUNT(*) FROM products")
        total_products = await cursor.fetchone()
        
        # Completed referrals
        cursor = await db.execute("SELECT COUNT(*) FROM referrals WHERE status = 'completed'")
        completed_refs = await cursor.fetchone()
        
        # Pending referrals
        cursor = await db.execute("SELECT COUNT(*) FROM referrals WHERE status = 'pending'")
        pending_refs = await cursor.fetchone()
        
        message = (
            f"📈 *Bot Statistics*\n\n"
            f"👥 Total Users: {total_users[0]}\n"
            f"📦 Total Products: {total_products[0]}\n"
            f"✅ Completed Referrals: {completed_refs[0]}\n"
            f"⏳ Pending Referrals: {pending_refs[0]}"
        )
        
        await query.edit_message_text(message, parse_mode='Markdown')


# ==================== CALLBACK ROUTER ====================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route all callback queries"""
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id
    
    # Admin callbacks
    if data == "admin_upload":
        await admin_upload_callback(update, context)
    elif data == "admin_products":
        await admin_view_products(update, context)
    elif data == "admin_stats":
        await admin_stats(update, context)
    elif data.startswith("check_"):
        product_id = data[6:]
        await check_join_status(update, context, product_id, user_id)
    elif data.startswith("ref_"):
        product_id = data[4:]
        await get_referral_link(update, context, product_id, user_id)
    elif data.startswith("progress_"):
        product_id = data[9:]
        await show_progress(update, context, product_id, user_id)


# ==================== MAIN ====================

async def post_init(application: Application) -> None:
    """Run async initialisation after the Application is built."""
    await init_db()


def main():
    """Start the bot"""
    # Create application, running init_db via post_init before polling begins
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("myrefs", myrefs_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # Add callback handler
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    # Add message handlers for admin upload flow
    application.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO | filters.PHOTO | filters.AUDIO, handle_file_upload))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_refs_input))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_channels_input))
    
    # Start bot — run_polling() manages its own event loop internally
    logger.info("Bot started!")
    application.run_polling()


if __name__ == "__main__":
    main()
