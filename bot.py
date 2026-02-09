
import os
import logging
import asyncio
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from helper.database import db
from helper.ffmpeg import FFmpeg
from helper.progress import progress_for_pyrogram
from helper.set import set_thumbnail, view_thumbnail, del_thumbnail
from helper.date import check_expi
import time
from datetime import datetime
from plugins import about, admin, broadcast, caption, cb_data, filedetect, myplane, refer, start, thumbfunction, upgrade, metadata_renamer

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Bot Client
bot = Client(
    "RenamerBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    plugins=dict(root="plugins")
)

# Middleware for Force Join
async def force_join_middleware(client, message):
    if message.from_user:
        user_id = message.from_user.id
        try:
            member = await client.get_chat_member(Config.CHANNEL, user_id)
            if member.status not in ["member", "administrator", "creator"]:
                await message.reply_text(
                    f"**⚠️ Please join our channel first!**\n\n"
                    f"Channel: @{Config.CHANNEL}\n\n"
                    "Join and then try again!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("Join Channel", url=f"https://t.me/{Config.CHANNEL}")]
                    ])
                )
                return False
        except Exception as e:
            logger.error(f"Force join check error: {e}")
    return True

# Start Command Handler
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    # Check force join
    if not await force_join_middleware(client, message):
        return
    
    # Check if user exists in database
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id)
    
    # Send start message with thumbnail if available
    if Config.LAZY_PIC:
        await message.reply_photo(
            photo=Config.LAZY_PIC,
            caption=f"**👋 Hello {message.from_user.mention}!\n\n"
                    "I'm a Fast 4GB File Renamer Bot!**\n\n"
                    "🔹 **Features:**\n"
                    "• Rename files up to 4GB\n"
                    "• Permanent Thumbnail Support\n"
                    "• Custom Caption\n"
                    "• Force Join System\n"
                    "• Broadcast Support\n"
                    "• Fast Processing\n\n"
                    "Use /help to see all commands!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Channel", url=f"https://t.me/{Config.CHANNEL}")],
                [InlineKeyboardButton("🆘 Help", callback_data="help"),
                 InlineKeyboardButton("📊 About", callback_data="about")]
            ])
        )
    else:
        await message.reply_text(
            f"**👋 Hello {message.from_user.mention}!\n\n"
            "I'm a Fast 4GB File Renamer Bot!**\n\n"
            "Use /help to see all commands!"
        )

# Help Command
@bot.on_message(filters.command("help") & filters.private)
async def help_command(client, message):
    help_text = """
    **🤖 Available Commands:**

    👤 **User Commands:**
    /start - Check if bot is running
    /viewthumb - View current thumbnail
    /delthumb - Delete current thumbnail
    /set_caption - Set custom caption
    /see_caption - View custom caption
    /del_caption - Delete custom caption
    /myplan - View your current plan
    /about - Bot status
    /upgrade - View plans & pricing
    /help - This message

    👑 **Admin Commands:**
    /lazyusers - List all users
    /broadcast - Broadcast message
    /ceasepower - Downgrade user capacity
    /resetpower - Reset to default capacity
    /addpremium - Upgrade user plan
    /stats - Bot statistics

    **📌 How to use:**
    1. Send me any file
    2. Reply with new filename
    3. That's it! I'll rename it fast! 🚀
    """
    await message.reply_text(help_text)

# File Rename Handler
@bot.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def rename_file(client, message):
    # Check force join
    if not await force_join_middleware(client, message):
        return
    
    user_id = message.from_user.id
    
    # Check user's plan and remaining capacity
    user_data = await db.get_user(user_id)
    if not user_data:
        await message.reply_text("Please use /start first!")
        return
    
    # Check if user can rename (plan validation)
    if not await check_user_capacity(user_id, message.document.file_size if message.document else message.video.file_size):
        await message.reply_text("You've reached your daily limit! Upgrade plan with /upgrade")
        return
    
    # Save file for renaming
    download_path = await message.download()
    file_size = os.path.getsize(download_path)
    
    # Save file info for later renaming
    await db.save_temp_file(user_id, download_path, file_size)
    
    await message.reply_text(
        "**📝 Please send me the new filename:**\n"
        "Example: `MyVideo.mp4`\n\n"
        "You can also add custom caption:\n"
        "`filename.mp4|custom caption here`"
    )

# Handle text messages for filename input
@bot.on_message(filters.private & filters.text)
async def handle_filename(client, message):
    if not message.reply_to_message:
        return
    
    user_id = message.from_user.id
    replied_msg = message.reply_to_message
    
    # Check if it's a file rename request
    if not (replied_msg.document or replied_msg.video or replied_msg.audio):
        return
    
    # Get file info from database
    file_info = await db.get_temp_file(user_id)
    if not file_info:
        await message.reply_text("Session expired. Please send file again!")
        return
    
    input_text = message.text.strip()
    
    # Parse filename and caption
    if "|" in input_text:
        new_filename, custom_caption = input_text.split("|", 1)
        new_filename = new_filename.strip()
        custom_caption = custom_caption.strip()
    else:
        new_filename = input_text
        custom_caption = None
    
    # Validate filename
    if not new_filename:
        await message.reply_text("Please provide a valid filename!")
        return
    
    # Process renaming
    try:
        await process_rename(
            client, 
            message, 
            user_id, 
            file_info['file_path'],
            new_filename,
            custom_caption
        )
    except Exception as e:
        await message.reply_text(f"Error: {str(e)}")
        logger.error(f"Rename error: {e}")

async def process_rename(client, message, user_id, file_path, new_filename, custom_caption=None):
    # Start processing message
    status_msg = await message.reply_text("⏳ Processing your file...")
    
    # Get user's thumbnail
    thumb_path = await db.get_thumbnail(user_id)
    if not thumb_path or not os.path.exists(thumb_path):
        thumb_path = None
    
    # Get user's caption
    if not custom_caption:
        user_caption = await db.get_caption(user_id)
    else:
        user_caption = custom_caption
    
    # Prepare final caption
    if user_caption:
        final_caption = f"{user_caption}\n\n📁 Renamed by @{Config.BOT_USERNAME}"
    else:
        final_caption = f"📁 Renamed by @{Config.BOT_USERNAME}"
    
    # Create output path
    output_dir = "downloads"
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, new_filename)
    
    # Use FFmpeg for processing (for videos)
    if file_path.endswith(('.mp4', '.mkv', '.avi', '.mov')):
        ffmpeg = FFmpeg()
        output_path = await ffmpeg.rename_video(file_path, output_path, thumb_path)
    else:
        # For documents, just rename
        os.rename(file_path, output_path)
    
    # Get file size
    file_size = os.path.getsize(output_path)
    
    # Upload with progress
    start_time = time.time()
    
    if output_path.endswith(('.mp4', '.mkv', '.avi', '.mov', '.flv')):
        sent_message = await client.send_video(
            chat_id=user_id,
            video=output_path,
            caption=final_caption,
            thumb=thumb_path,
            progress=progress_for_pyrogram,
            progress_args=(
                "📤 Uploading...",
                status_msg,
                start_time
            )
        )
    elif output_path.endswith(('.mp3', '.wav', '.flac')):
        sent_message = await client.send_audio(
            chat_id=user_id,
            audio=output_path,
            caption=final_caption,
            thumb=thumb_path,
            progress=progress_for_pyrogram,
            progress_args=(
                "📤 Uploading...",
                status_msg,
                start_time
            )
        )
    else:
        sent_message = await client.send_document(
            chat_id=user_id,
            document=output_path,
            caption=final_caption,
            thumb=thumb_path,
            progress=progress_for_pyrogram,
            progress_args=(
                "📤 Uploading...",
                status_msg,
                start_time
            )
        )
    
    # Update user stats
    await db.update_user_stats(user_id, file_size)
    
    # Cleanup
    await status_msg.delete()
    if os.path.exists(file_path):
        os.remove(file_path)
    if os.path.exists(output_path):
        os.remove(output_path)
    
    await message.reply_text("✅ File renamed and sent successfully!")

async def check_user_capacity(user_id, file_size):
    """Check if user can rename file based on plan"""
    user_data = await db.get_user(user_id)
    if not user_data:
        return False
    
    plan = user_data.get('plan', 'FREE')
    used_today = user_data.get('used_today', 0)
    
    # Plan limits (in bytes)
    limits = {
        'FREE': 1.2 * 1024 * 1024 * 1024,  # 1.2GB
        'SILVER': 50 * 1024 * 1024 * 1024,  # 50GB
        'GOLD': 100 * 1024 * 1024 * 1024,  # 100GB
        'DIAMOND': 4 * 1024 * 1024 * 1024,  # 4GB per file
    }
    
    if used_today + file_size > limits.get(plan, limits['FREE']):
        return False
    
    return True

# Run bot
if __name__ == "__main__":
    print("🚀 Starting Renamer Bot...")
    bot.run()
