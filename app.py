import os, subprocess, asyncio, tempfile, json, threading
from flask import Flask
from telethon import TelegramClient, events
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from google.oauth2 import service_account

# ---------- LOAD SECRETS ----------
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")

if not all([API_ID, API_HASH, BOT_TOKEN, GDRIVE_FOLDER_ID, GOOGLE_CREDENTIALS_JSON]):
    raise ValueError("Missing environment variables.")

cred_json = json.loads(GOOGLE_CREDENTIALS_JSON.strip())

# ---------- AUTH GDRIVE ----------
creds = service_account.Credentials.from_service_account_info(
    cred_json,
    scopes=['https://www.googleapis.com/auth/drive']
)
gauth = GoogleAuth()
gauth.credentials = creds
gauth.auth_method = 'service'
drive = GoogleDrive(gauth)
print("✅ Google Drive authenticated.")

# ---------- TELEGRAM BOT ----------
bot = TelegramClient("bot", API_ID, API_HASH)
pending = {}  # user_id -> {'url': url}

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    await event.reply("Hello! Use /mirror <url>")

@bot.on(events.NewMessage(pattern='/ping'))
async def ping(event):
    await event.reply("pong!")

@bot.on(events.NewMessage(pattern='/mirror'))
async def mirror(event):
    args = event.message.text.split(maxsplit=1)
    if len(args) < 2:
        await event.reply("Usage: /mirror <patreon_stream_url>")
        return
    pending[event.sender_id] = {"url": args[1]}
    await event.reply("✅ Link received! Now send filename (without .mp4) or 'skip'.")

@bot.on(events.NewMessage)
async def handle_download(event):
    if event.message.text.startswith('/'):
        return
    if event.sender_id not in pending:
        return
    data = pending.pop(event.sender_id)
    url = data["url"]
    filename = event.message.text.strip()
    if filename.lower() == "skip" or not filename:
        filename = "video"

    # Step 1: Check ffmpeg
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.wait()
        if proc.returncode != 0:
            await event.reply("❌ ffmpeg not installed. Please check Render Build Command.")
            return
    except FileNotFoundError:
        await event.reply("❌ ffmpeg missing. Add to build command.")
        return

    await event.reply(f"⬇️ Downloading & merging: {filename}.mp4 (this may take 5-30 mins)")

    try:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            os.chdir(tmp)

            # Step 2: Build yt-dlp command
            cmd = [
                "yt-dlp",
                "--add-header", "Referer: https://www.patreon.com/",
                "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "--merge-output-format", "mp4",
                "--no-cache-dir",
                "--no-playlist",
                "--no-check-certificate",
                "-v",  # verbose for logs
                url,
                "-o", f"{filename}.mp4"
            ]

            # Step 3: Run async subprocess
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # Optional: send periodic status? We'll just wait with timeout
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=3600)
            except asyncio.TimeoutError:
                process.kill()
                await event.reply("❌ Download timed out after 1 hour.")
                return

            if process.returncode != 0:
                err_msg = stderr.decode()[-500:]
                await event.reply(f"❌ yt-dlp error:\n{err_msg}")
                # Also print to Render logs for debugging
                print(f"yt-dlp stderr: {stderr.decode()}")
                return

            print("yt-dlp completed successfully.")
            await event.reply("✅ Download & merge complete. Now uploading...")

            # Step 4: Upload
            files = [f for f in os.listdir(".") if f.endswith(".mp4")]
            if not files:
                await event.reply("❌ No MP4 file generated.")
                return
            fpath = files[0]
            gfile = drive.CreateFile({"title": fpath, "parents": [{"id": GDRIVE_FOLDER_ID}]})
            gfile.SetContentFile(fpath)
            gfile.Upload()
            link = gfile.get("alternateLink") or gfile.get("webContentLink") or "Check your Drive"
            await event.reply(f"✅ **Success!**\n📁 {fpath}\n🔗 {link}")

    except Exception as e:
        await event.reply(f"❌ Unexpected error:\n{str(e)[:500]}")
        print(f"Exception: {e}")

async def main():
    await bot.start(bot_token=BOT_TOKEN)
    print("🐱 Bot RUNNING on Render! Now listening...")
    await bot.run_until_disconnected()

# ---------- FLASK APP ----------
app = Flask(__name__)
@app.route('/')
def home():
    return "Patreon Mirror Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    asyncio.run(main())
