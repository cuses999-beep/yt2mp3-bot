import logging
import os
import re
import asyncio
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ==================== CONFIGURATION ====================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"      # @BotFather se mila token yahan paste karein
ADMIN_ID = 123456789                    # @userinfobot se mila numeric ID yahan paste karein
REQUIRED_CHANNEL = "@my_channel_name"   # (Optional) Apna channel username daalein ya khali chhodein
AUDIO_QUALITY = "192"                   # Audio Quality (128, 192, ya 320)
# =======================================================

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

users_db = set()

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
    keyboard = [
        [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL.replace('@', '')}")],
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
    
    data = query.data.split("|")
    action = data[0]
    url = data[1]
    
    msg = await query.message.edit_text("⏳ YouTube se download chal raha hai, kripya wait karein...")
    
    if action == "dl_mp3":
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': AUDIO_QUALITY,
            }],
            'quiet': True,
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
            await msg.edit_text(f"❌ Error downloading audio: {str(e)[:100]}")

    elif action == "dl_mp4":
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'quiet': True,
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
            await msg.edit_text(f"❌ Error downloading video: {str(e)[:100]}")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(f"👑 Admin Panel:\nTotal Users: {len(users_db)}\nBroadcast command: /broadcast Message")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    msg_text = " ".join(context.args)
    if not msg_text:
        await update.message.reply_text("Usage: /broadcast Hello subscribers!")
        return
        
    count = 0
    for uid in users_db:
        try:
            await context.bot.send_message(chat_id=uid, text=msg_text)
            count += 1
        except:
            pass
    await update.message.reply_text(f"✅ Broadcast sent to {count} users.")

def main():
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
        
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(check_sub_callback, pattern="^check_sub$"))
    app.add_handler(CallbackQueryHandler(handle_download_callback, pattern="^dl_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_youtube))

    print("🎵 @yt2mp3sk8_bot clone starting...")
    app.run_polling()

if __name__ == '__main__':
    main()
