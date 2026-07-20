import os, subprocess, asyncio, tempfile, json, threading
from flask import Flask
from telethon import TelegramClient, events
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from google.oauth2 import service_account   # <-- new import

# ---------- LOAD SECRETS ----------
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
GDRIVE_FOLDER_ID = os.environ["GDRIVE_FOLDER_ID"]

# Load service account JSON from env
cred_json = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])

# ---------- AUTH GDRIVE (Service Account with explicit credentials) ----------
creds = service_account.Credentials.from_service_account_info(
    cred_json,
    scopes=['https://www.googleapis.com/auth/drive']
)
gauth = GoogleAuth()
gauth.credentials = creds
gauth.auth_method = 'service'   # just to set the flag
drive = GoogleDrive(gauth)
# ----------------------------------------------------------------------

bot = TelegramClient("bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)
pending = {}

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

    await event.reply(f"⬇️ Downloading & merging: {filename}.mp4 (may take time)")

    try:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            os.chdir(tmp)
            cmd = [
                "yt-dlp",
                "--add-header", "Referer: https://www.patreon.com/",
                "--merge-output-format", "mp4",
                "--no-cache-dir",
                url,
                "-o", f"{filename}.mp4"
            ]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            files = [f for f in os.listdir(".") if f.endswith(".mp4")]
            if not files:
                await event.reply("❌ No MP4 generated.")
                return
            fpath = files[0]
            await event.reply("📤 Uploading to Google Drive...")
            gfile = drive.CreateFile({"title": fpath, "parents": [{"id": GDRIVE_FOLDER_ID}]})
            gfile.SetContentFile(fpath)
            gfile.Upload()
            link = gfile.get("alternateLink") or gfile.get("webContentLink")
            await event.reply(f"✅ **Success!**\n📁 {fpath}\n🔗 {link}")
    except subprocess.CalledProcessError as e:
        await event.reply(f"❌ Error:\n{e.stderr}")

async def main():
    await bot.start()
    print("🐱 Bot RUNNING on Render!")

# Flask app (for Render health check)
app = Flask(__name__)
@app.route('/')
def home():
    return "Patreon Mirror Bot is running!"

if __name__ == "__main__":
    # Start bot in background thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    thread = threading.Thread(target=loop.run_until_complete, args=(main(),), daemon=True)
    thread.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
