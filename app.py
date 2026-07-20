import os, subprocess, asyncio, tempfile, json, threading, time
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
pending = {}

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

    await event.reply(f"⬇️ Downloading & merging: {filename}.mp4 (may take 5-30 mins)")
    print(f"[DEBUG] Starting download for user {event.sender_id}")

    # Run in a thread to avoid blocking the event loop
    def download_and_upload():
        try:
            with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
                os.chdir(tmp)
                cmd = [
                    "yt-dlp",
                    "--add-header", "Referer: https://www.patreon.com/",
                    "--user-agent", "Mozilla/5.0",
                    "--merge-output-format", "mp4",
                    "--no-cache-dir",
                    "--no-playlist",
                    "--no-check-certificate",
                    "-v",
                    url,
                    "-o", f"{filename}.mp4"
                ]
                print(f"[DEBUG] Running: {' '.join(cmd)}")
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
                if proc.returncode != 0:
                    asyncio.run_coroutine_threadsafe(
                        event.reply(f"❌ yt-dlp error:\n{proc.stderr[-500:]}"),
                        bot.loop
                    )
                    return
                print("[DEBUG] yt-dlp completed.")
                asyncio.run_coroutine_threadsafe(
                    event.reply("✅ Download & merge complete. Now uploading to GDrive..."),
                    bot.loop
                )
                files = [f for f in os.listdir(".") if f.endswith(".mp4")]
                if not files:
                    asyncio.run_coroutine_threadsafe(
                        event.reply("❌ No MP4 generated."),
                        bot.loop
                    )
                    return
                fpath = files[0]
                gfile = drive.CreateFile({"title": fpath, "parents": [{"id": GDRIVE_FOLDER_ID}]})
                gfile.SetContentFile(fpath)
                gfile.Upload()
                link = gfile.get("alternateLink") or gfile.get("webContentLink") or "Check your Drive"
                asyncio.run_coroutine_threadsafe(
                    event.reply(f"✅ **Success!**\n📁 {fpath}\n🔗 {link}"),
                    bot.loop
                )
        except subprocess.TimeoutExpired:
            asyncio.run_coroutine_threadsafe(
                event.reply("❌ Download timed out after 1 hour."),
                bot.loop
            )
        except Exception as e:
            asyncio.run_coroutine_threadsafe(
                event.reply(f"❌ Error: {str(e)[:500]}"),
                bot.loop
            )
            print(f"[EXCEPTION] {e}")

    # Start download in a background thread
    threading.Thread(target=download_and_upload, daemon=True).start()

async def main():
    await bot.start(bot_token=BOT_TOKEN)
    print("🐱 Bot RUNNING on Render! Now listening...")
    await bot.run_until_disconnected()

# ---------- FLASK ----------
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
