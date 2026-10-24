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
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
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
    raise RuntimeError("BOT_TOKEN is missing")


web = Flask(__name__)


@web.get("/")
def health():
    return "Media bot is running", 200


def read_users():
    try:
        return json.loads(DATA_FILE.read_text())
    except (OSError, ValueError):
        return {}


def user_name(user):
    return read_users().get(str(user.id), "")


def save_name(user, name):
    users = read_users()
    users[str(user.id)] = name
    DATA_FILE.write_text(json.dumps(users))


def run_ffmpeg(args):
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-y", *args],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1500:])


def tag_mp3(path, name):
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        tags = ID3()
    tags.add(TPE1(encoding=3, text=[name]))
    tags.add(TIT2(encoding=3, text=[path.stem]))
    tags.save(str(path))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    waiting_for_name.add(update.effective_user.id)
    await update.message.reply_text(
        "Welcome! 🎧 Aapka naam kya hai?\n"
        "Ye naam MP3 ke Artist tag mein add hoga."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "• /start — apna naam set karein\n"
        "• Audio/video file upload karein\n"
        "• Video se MP3 aur processed MP4 mil sakta hai\n"
        "Testing file limit: 18 MB\n"
        "Sirf apni ya authorized files use karein."
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
        save_name(user, name)
        waiting_for_name.discard(user.id)
        await msg.reply_text(
            f"Naam save ho gaya, {name}! Ab audio/video upload karein."
        )
        return

    name = user_name(user)
    if not name:
        await msg.reply_text("Pehle /start bhejkar apna naam set karein.")
        return

    media = msg.document or msg.audio or msg.video
    if not media:
        await msg.reply_text("Audio ya video file upload karein.")
        return

    if media.file_size and media.file_size > MAX_BYTES:
        await msg.reply_text("Testing limit 18 MB hai. Chhoti file bhejein.")
        return

    ext = Path(getattr(media, "file_name", None) or "upload.bin").suffix
    if ext.lower() not in {
        ".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus",
        ".flac", ".mp4", ".mov", ".mkv", ".webm"
    }:
        await msg.reply_text("Ye file format supported list mein nahi hai.")
        return

    await msg.reply_text("Processing... ⚙️")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / f"input{ext.lower()}"
            tg_file = await context.bot.get_file(media.file_id)
            await tg_file.download_to_drive(custom_path=str(source))

            if source.stat().st_size > MAX_BYTES:
                await msg.reply_text("File size limit exceed hui.")
                return

            mp3 = Path(tmp) / "converted.mp3"
            run_ffmpeg([
                "-i", str(source), "-vn",
                "-codec:a", "libmp3lame", "-q:a", "3",
                str(mp3)
            ])
            tag_mp3(mp3, name)

            with mp3.open("rb") as f:
                await msg.reply_audio(
                    audio=f,
                    filename=f"{name}_audio.mp3",
                    title=source.stem[:64],
                    performer=name[:64],
                    caption=f"Artist: {name}"
                )

            if msg.video or ext.lower() in {".mp4", ".mov", ".mkv", ".webm"}:
                mp4 = Path(tmp) / "processed.mp4"
                run_ffmpeg([
                    "-i", str(source),
                    "-map", "0:v:0", "-map", "0:a?",
                    "-c", "copy",
                    "-metadata", f"artist={name}",
                    "-metadata", f"title={source.stem}",
                    "-movflags", "+faststart",
                    str(mp4)
                ])
                with mp4.open("rb") as f:
                    await msg.reply_document(
                        document=f,
                        filename=f"{name}_video.mp4",
                        caption=f"Processed video | Artist: {name}"
                    )

    except Exception:
        log.exception("Media processing failed")
        await msg.reply_text(
            "Processing fail hua. File format check karke chhoti file se retry karein."
        )


async def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND, handle
        )
    )
    app.add_handler(
        MessageHandler(
            filters.AUDIO | filters.VIDEO | filters.Document.ALL, handle
        )
    )

    Thread(
        target=lambda: web.run(
            host="0.0.0.0", port=PORT, use_reloader=False
        ),
        daemon=True
    ).start()

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    log.info("Bot started")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
    from urllib.parse import urlparse

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

        if host == "youtu.be" or host.endswith(".youtube.com") or host == "youtube.com":
            return ("YouTube", candidate)

        if host == "instagram.com" or host.endswith(".instagram.com"):
            return ("Instagram", candidate)

        if host == "pin.it" or host == "pinterest.com" or host.endswith(".pinterest.com"):
            return ("Pinterest", candidate)

    return None
    
