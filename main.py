import os
import json
import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path
from threading import Thread

from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from mutagen.id3 import ID3, TIT2, TPE1, ID3NoHeaderError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("media-bot")

TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", "10000"))
MAX_BYTES = 18 * 1024 * 1024
DATA_FILE = Path("/tmp/media_bot_users.json")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

web = Flask(__name__)
waiting_for_name = set()


@web.get("/")
def health():
    return "Media bot is running", 200


def load_users():
    try:
        return json.loads(DATA_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_user(user_id, name):
    users = load_users()
    users[str(user_id)] = name
    DATA_FILE.write_text(json.dumps(users))


def get_name(user):
    return load_users().get(str(user.id), user.first_name or "User")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    waiting_for_name.add(uid)
    await update.message.reply_text(
        "Welcome! 🎧\n\n"
        "Aapka naam kya hai? Ye naam MP3 ke Artist tag mein use hoga."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "1. /start - naam set karein\n"
        "2. Audio ya video file upload karein\n"
        "3. MP3 conversion ke liye /mp3 bhejein\n\n"
        "Sirf apni ya authorized media files use karein."
    )


async def convert_audio(update, context, source, name):
    output = source.with_name("converted.mp3")
    result = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-y", "-i", str(source),
            "-vn", "-codec:a", "libmp3lame", "-q:a", "3",
            str(output),
        ],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0 or not output.exists():
        raise RuntimeError("Audio conversion failed")

    try:
        tags = ID3(str(output))
    except ID3NoHeaderError:
        tags = ID3()

    tags.add(TPE1(encoding=3, text=[name]))
    tags.add(TIT2(encoding=3, text=[source.stem]))
    tags.save(str(output))

    with output.open("rb") as audio:
        await update.message.reply_audio(
            audio=audio,
            title=source.stem[:64],
            performer=name[:64],
            filename=f"{name}_audio.mp3",
            caption=f"Artist: {name}",
        )


async def process_file(update, context):
    message = update.message
    user = update.effective_user

    if user.id in waiting_for_name:
        name = (message.text or "").strip()
        if not name or len(name) > 50:
            await message.reply_text("Naam 1 se 50 characters ka rakhein.")
            return
        save_user(user.id, name)
        waiting_for_name.discard(user.id)
        await message.reply_text(
            f"Naam save ho gaya, {name}! 🎵\n"
            "Ab apni authorized audio ya video file bhejein."
        )
        return

    name = get_name(user)
    media = message.document or message.audio or message.video

    if not media:
        await message.reply_text(
            "Audio/video file upload karein. Naam badalne ke liye /start."
        )
        return

    if not media.file_name and not (message.audio or message.video):
        await message.reply_text("File ka naam available nahi hai.")
        return

    if media.file_size and media.file_size > MAX_BYTES:
        await message.reply_text("Testing limit: file 18 MB se chhoti honi chahiye.")
        return

    ext = Path(media.file_name or "upload.bin").suffix or ".bin"

    try:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / f"source{ext}"
            tg_file = await context.bot.get_file(media.file_id)
            await tg_file.download_to_drive(custom_path=str(source))

            if not source.exists() or source.stat().st_size > MAX_BYTES:
                await message.reply_text("File size limit exceed hui.")
                return

            await message.reply_text("Processing... ⚙️")

            # Uploaded audio/video se MP3 banane ke liye /mp3 use karein.
            context.user_data["last_file"] = str(source)
            context.user_data["last_folder"] = folder

            if message.audio:
                await convert_audio(update, context, source, name)
            elif message.video:
                await message.reply_text(
                    "Video receive ho gaya. MP3 nikalne ke liye /mp3 bhejein."
                )
            else:
                await message.reply_text(
                    "File receive ho gayi. Supported audio/video upload karein."
                )

    except Exception:
        log.exception("File processing failed")
        await message.reply_text(
            "Processing fail hui. Chhoti, supported media file se retry karein."
        )


async def mp3_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "MP3 conversion ke liye audio/video ko dobara document ke roop mein "
        "bhejein. /start se pehle apna naam set karna zaroori hai."
    )


async def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("mp3", mp3_command))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND, process_file
        )
    )
    app.add_handler(
        MessageHandler(
            filters.AUDIO | filters.VIDEO | filters.Document.ALL,
            process_file
        )
    )

    Thread(
        target=lambda: web.run(
            host="0.0.0.0", port=PORT, use_reloader=False
        ),
        daemon=True,
    ).start()

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=False)
    log.info("Bot started successfully")

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
