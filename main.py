import os
import json
import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

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
waiting_for_name = set()

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

web = Flask(__name__)


@web.get("/")
def health():
    return "Media bot is running", 200


def read_users():
    try:
        return json.loads(DATA_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_name(user_id, name):
    users = read_users()
    users[str(user_id)] = name
    DATA_FILE.write_text(json.dumps(users))


def get_name(user):
    return read_users().get(str(user.id), "")


def detect_media_link(text):
    if not text:
        return None

    for word in text.split():
        candidate = word.strip("()[]{}<>,\"'")

        if not candidate.startswith(("https://", "http://")):
            continue

        try:
            parsed = urlparse(candidate)
            host = (parsed.hostname or "").lower()
        except ValueError:
            continue

        if host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be":
            return "YouTube", candidate

        if host == "instagram.com" or host.endswith(".instagram.com"):
            return "Instagram", candidate

        if host == "pinterest.com" or host.endswith(".pinterest.com") or host == "pin.it":
            return "Pinterest", candidate

        if parsed.scheme == "https" and host:
            return "Direct URL", candidate

    return None


def run_ffmpeg(args):
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-y", *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1000:])


def tag_mp3(path, name, song_title=None):
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        tags = ID3()

    # Sirf Artist tag update hoga (User ka naam set hoga)
    tags.delall("TPE1")
    tags.add(TPE1(encoding=3, text=[name]))

    # Original Song Title preserving logic:
    # Agar pehle se TIT2 tag hai toh use rehne do, nahi toh extracted title use karo
    if song_title and not tags.getall("TIT2"):
        tags.add(TIT2(encoding=3, text=[song_title]))

    tags.save(str(path))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    waiting_for_name.add(uid)
    await update.effective_message.reply_text(
        "Welcome! 🎧\nAapka naam kya hai?\n"
        "Ye naam MP3 ke Artist tag mein use hoga."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "/start — naam set karein\n"
        "/help — help\n\n"
        "Apni ya authorized audio/video file upload karein.\n"
        "Supported links ko bot identify karega, lekin "
        "third-party platform links ki downloading enabled nahi hai."
    )


async def process_upload(update, context, name):
    msg = update.effective_message
    media = msg.document or msg.audio or msg.video

    if not media:
        return

    if media.file_size and media.file_size > MAX_BYTES:
        await msg.reply_text("Testing limit 18 MB hai. Chhoti file bhejein.")
        return

    # Extract original file name and title from media metadata
    raw_filename = getattr(media, "file_name", None)
    
    # Audio attribute metadata (telegram title)
    audio_title = getattr(media, "title", None)
    audio_performer = getattr(media, "performer", None)

    if not raw_filename:
        if audio_title:
            raw_filename = f"{audio_title}.mp3"
        else:
            raw_filename = "audio.mp3"

    ext = Path(raw_filename).suffix.lower()
    song_display_name = audio_title or Path(raw_filename).stem

    allowed = {
        ".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus",
        ".flac", ".mp4", ".mov", ".mkv", ".webm"
    }

    if ext not in allowed:
        await msg.reply_text("Unsupported file format.")
        return

    await msg.reply_text("Processing media... ⚙️")

    try:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / f"input{ext}"
            tg_file = await context.bot.get_file(media.file_id)
            await tg_file.download_to_drive(custom_path=str(source))

            if source.stat().st_size > MAX_BYTES:
                await msg.reply_text("File size limit exceed hui.")
                return

            mp3 = Path(folder) / "converted.mp3"
            run_ffmpeg([
                "-i", str(source), "-vn",
                "-codec:a", "libmp3lame", "-q:a", "3",
                str(mp3)
            ])
            
            # ID3 metadata tag update
            tag_mp3(mp3, name, song_title=song_display_name)

            # Output filename original file ke name par base hoga (not "input.mp3" or "name.mp3")
            out_filename = Path(raw_filename).stem + ".mp3"

            with mp3.open("rb") as f:
                await msg.reply_audio(
                    audio=f,
                    filename=out_filename,
                    title=song_display_name,  # Original song title preserve kiya gaya hai
                    performer=name[:64],       # Sirf artist update hoga
                    caption=f"Artist: {name}",
                )

            if msg.video or ext in {".mp4", ".mov", ".mkv", ".webm"}:
                mp4 = Path(folder) / "processed.mp4"
                run_ffmpeg([
                    "-i", str(source),
                    "-map", "0:v:0", "-map", "0:a?",
                    "-c:v", "libx264", "-c:a", "aac",
                    "-metadata", f"artist={name}",
                    "-metadata", f"title={song_display_name}",
                    "-movflags", "+faststart",
                    str(mp4)
                ])

                with mp4.open("rb") as f:
                    await msg.reply_document(
                        document=f,
                        filename=raw_filename,
                        caption=f"Processed video | Artist: {name}",
                    )

    except Exception:
        log.exception("Media processing failed")
        await msg.reply_text(
            "Processing fail hua. Chhoti aur supported media file se retry karein."
        )


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    if not msg or not user:
        return

    if user.id in waiting_for_name:
        name = (msg.text or "").strip()

        if not name or len(name) > 50:
            await msg.reply_text("Naam 1–50 characters ka hona chahiye.")
            return

        save_name(user.id, name)
        waiting_for_name.discard(user.id)
        await msg.reply_text(
            f"Naam save ho gaya, {name}! 🎵\n"
            "Ab apni authorized audio/video file upload karein."
        )
        return

    name = get_name(user)

    if not name:
        await msg.reply_text("Pehle /start bhejkar apna naam set karein.")
        return

    if msg.text:
        detected = detect_media_link(msg.text)

        if detected:
            platform, url = detected

            if platform in {"YouTube", "Instagram", "Pinterest"}:
                await msg.reply_text(
                    f"{platform} link detected! 🔗\n\n"
                    "Is platform ki direct downloading enabled nahi hai. "
                    "Apni authorized media file Telegram par upload karein."
                )
                return

            await msg.reply_text(
                "Direct URL detect hua. Sirf trusted, authorized media "
                "sources ka use karein; arbitrary URL downloads enabled nahi hain."
            )
            return

        await msg.reply_text(
            "YouTube, Instagram ya Pinterest link bhejein, "
            "ya authorized media file upload karein."
        )
        return

    await process_upload(update, context, name)


async def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND, handle
        )
    )
    app.add_handler(
        MessageHandler(
            filters.AUDIO | filters.VIDEO | filters.Document.ALL,
            handle
        )
    )

    Thread(
        target=lambda: web.run(
            host="0.0.0.0",
            port=PORT,
            use_reloader=False,
        ),
        daemon=True,
    ).start()

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    log.info("Bot started successfully")

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
