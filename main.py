import logging
import os
import re
import threading
from flask import Flask
import asyncio
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "@my_channel_name")
AUDIO_QUALITY = os.getenv("AUDIO_QUALITY", "192")
PORT = int(os.getenv("PORT", "10000"))
COOKIE_FILE = "cookies.txt"
# =======================================================

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

users_db = set()

# Automatically write cookies from Environment Variable if provided
YOUTUBE_COOKIES_ENV = os.getenv("YOUTUBE_COOKIES")
if YOUTUBE_COOKIES_ENV:
    try:
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            f.write(YOUTUBE_COOKIES_ENV)
        logger.info("Successfully loaded YouTube cookies from environment variable!")
    except Exception as e:
        logger.error(f"Failed to write cookies file: {e}")

# Dummy Web Server for Render Port Binding
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Bot is active and running fine on Render Web Service!"

def run_web_server():
    app_web.run(host='0.0.0.0', port=PORT)

# Base yt-dlp configuration with Anti-Bot bypass clients & cookie file support
BASE_YTDL_OPTS = {
    'quiet': True,
    'no_warnings': True,
    'nocheckcertificate': True,
    'geo_bypass': True,
    'cookiefile': COOKIE_FILE if os.path.exists(COOKIE_FILE) else None,
    'extractor_args': {
        'youtube': {
            'player_client': ['ios', 'android', 'mweb', 'tv'],
            'player_skip': ['webpage', 'configs']
        }
    },
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    }
}

async def is_subscribed(bot, user_id):
    if not REQUIRED_CHANNEL or REQUIRED_CHANNEL == "@my_channel_name":
        return True
    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        return member.status in ['creator', 'administrator', 'member']
    except Exception as e:
        logger.error(f"Subscription check error: {e}")
        return True

async def force_join_markup():
    channel_clean = REQUIRED_CHANNEL.replace('@', '')
    keyboard = [
        [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{channel_clean}")],
        [InlineKeyboardButton("✅ Check Subscription", callback_data="check_sub")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    users_db.add(user.id)
    
    if not await is_subscribed(context.bot, user.id):
        await update.message.reply_text(
            f"👋 Hello {user.first_name}!\n\nBot use karne ke liye pehle hamare channel ko join karein.",
            reply_markup=await force_join_markup()
        )
        return

    welcome_text = (
        f"🎵 Welcome {user.first_name}!\n\n"
        "✨ **How to use me:**\n"
        "1. Send any YouTube Link (Video, Short, Song)\n"
        "2. Or type any Song Name (e.g. *Arijit Singh Kesariya*)\n"
        "3. Select **MP3 Audio** or **MP4 Video** to download!"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def check_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await is_subscribed(context.bot, query.from_user.id):
        await query.message.edit_text("✅ Thank you for joining! Ab YouTube link ya song name bhejo.")
    else:
        await query.message.reply_text("❌ Aapne abhi tak channel join nahi kiya hai!")

async def process_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    users_db.add(user_id)

    if not await is_subscribed(context.bot, user_id):
        await update.message.reply_text("⚠️ Pehle channel join karein!", reply_markup=await force_join_markup())
        return

    text = update.message.text
    url_pattern = r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+'
    
    if re.match(url_pattern, text):
        url = text
    else:
        url = f"ytsearch1:{text}"

    keyboard = [
        [InlineKeyboardButton("🎵 Download MP3 Audio", callback_data=f"dl_mp3|{url}")],
        [InlineKeyboardButton("🎬 Download MP4 Video", callback_data=f"dl_mp4|{url}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🔎 YouTube search complete! Format choose karein:", reply_markup=reply_markup)

async def handle_download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data.split("|", 1)
    action = data[0]
    url = data[1]
    
    msg = await query.message.edit_text("⏳ YouTube se download chal raha hai, kripya wait karein...")
    
    if action == "dl_mp3":
        ydl_opts = {
            **BASE_YTDL_OPTS,
            'format': 'ba/ba*/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': AUDIO_QUALITY,
            }],
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if 'entries' in info:
                    info = info['entries'][0]
                
                filename = ydl.prepare_filename(info)
                mp3_filename = os.path.splitext(filename)[0] + ".mp3"
                
                title = info.get('title', 'Audio')
                performer = info.get('uploader', 'YT2MP3 Bot')
                
                with open(mp3_filename, 'rb') as audio:
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=audio,
                        title=title,
                        performer=performer,
                        caption=f"🎵 **{title}**\n\nDownloaded via Bot"
                    )
                
                if os.path.exists(mp3_filename):
                    os.remove(mp3_filename)
                await msg.delete()
        except Exception as e:
            await msg.edit_text(f"❌ Error downloading audio: {str(e)[:150]}")

    elif action == "dl_mp4":
        ydl_opts = {
            **BASE_YTDL_OPTS,
            'format': 'b/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if 'entries' in info:
                    info = info['entries'][0]
                    
                filename = ydl.prepare_filename(info)
                title = info.get('title', 'Video')
                
                with open(filename, 'rb') as video:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=video,
                        caption=f"🎬 **{title}**\n\nDownloaded via Bot"
                    )
                
                if os.path.exists(filename):
                    os.remove(filename)
                await msg.delete()
        except Exception as e:
            await msg.edit_text(f"❌ Error downloading video: {str(e)[:150]}")

def main():
    if not os.path.exists("downloads"):
        os.makedirs("downloads")

    threading.Thread(target=run_web_server, daemon=True).start()
    logger.info(f"Flask Web Server running on port {PORT}")

    if os.path.exists(COOKIE_FILE):
        logger.info(f"Loaded cookie file: {COOKIE_FILE}")
    else:
        logger.warning("Cookie file not found! yt-dlp will run without cookies.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_sub_callback, pattern="^check_sub$"))
    app.add_handler(CallbackQueryHandler(handle_download_callback, pattern="^dl_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_youtube))

    print("🎵 Bot starting on Web Service...")
    app.run_polling()

if __name__ == '__main__':
    main()
