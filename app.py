import os, subprocess, asyncio, tempfile, json, threading, shutil
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
    raise ValueError("Missing one or more environment variables.")

# Parse credentials safely
try:
    cred_json = json.loads(GOOGLE_CREDENTIALS_JSON.strip())
except json.JSONDecodeError as e:
    print(f"❌ Invalid GOOGLE_CREDENTIALS_JSON: {e}")
    raise

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
bot = TelegramClient("bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)
pending = {}  # user_id -> {'url': url, 'stage': 'waiting_filename'}

@bot.on(events.NewMessage(pattern="/mirror"))
async def mirror(event):
    args = event.message.text.split(maxsplit=1)
    if len(args) < 2:
        await event.reply("Usage: /mirror <patreon_stream_url>")
        return
    pending[event.sender_id] = {"url": args[1]}
    await event.reply("✅ Link received! Now send me the filename (without .mp4) or type 'skip'.")

@bot.on(events.NewMessage)
async def handle_download(event):
    if event.sender_id not in pending:
        return
    if event.message.text.startswith("/"):
        return

    data = pending.pop(event.sender_id)
    url = data["url"]
    filename = event.message.text.strip()
    if filename.lower() == "skip" or not filename:
        filename = "video"

    await event.reply(f"⬇️ Downloading & merging: {filename}.mp4 (may take 5-30 mins)")

    try:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            os.chdir(tmp)
            cmd = [
                "yt-dlp",
                "--add-header", "Referer: https://www.patreon.com/",
                "--merge-output-format", "mp4",
                "--no-cache-dir",
                "--no-playlist",
                url,
                "-o", f"{filename}.mp4"
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)  # 1 hour timeout
            if proc.returncode != 0:
                await event.reply(f"❌ Download error:\n{proc.stderr[-500:]}")
                return

            files = [f for f in os.listdir(".") if f.endswith(".mp4")]
            if not files:
                await event.reply("❌ No MP4 generated. Check if the link is valid.")
                return
            fpath = files[0]
            await event.reply("📤 Uploading to Google Drive... (this may take a while)")

            # Upload
            gfile = drive.CreateFile({"title": fpath, "parents": [{"id": GDRIVE_FOLDER_ID}]})
            gfile.SetContentFile(fpath)
            gfile.Upload()
            link = gfile.get("alternateLink") or gfile.get("webContentLink") or "Check your Drive"
            await event.reply(f"✅ **Success!**\n📁 {fpath}\n🔗 {link}")

    except subprocess.TimeoutExpired:
        await event.reply("❌ Download timed out (1 hour). Try again with a faster connection.")
    except Exception as e:
        await event.reply(f"❌ Unexpected error:\n{str(e)[:500]}")
    finally:
        # Cleanup any leftover files in /tmp (already done by TemporaryDirectory)
        pass

async def main():
    await bot.start()
    print("🐱 Bot RUNNING on Render! Now listening for messages...")
    await bot.run_until_disconnected()

# Flask app (for health checks)
app = Flask(__name__)
@app.route('/')
def home():
    return "Patreon Mirror Bot is running!"

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    thread = threading.Thread(target=loop.run_until_complete, args=(main(),), daemon=True)
    thread.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
