import os, subprocess, asyncio, tempfile, json, threading
from flask import Flask
from telethon import TelegramClient, events
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

# ---------- LOAD SECRETS ----------
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
GDRIVE_FOLDER_ID = os.environ["GDRIVE_FOLDER_ID"]

# credentials.json ni env var nundi write cheyyi (Render lo file upload ledhu kabatti)
cred_json = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
with open("credentials.json", "w") as f:
    json.dump(cred_json, f)
# ----------------------------------

# Auth GDrive
gauth = GoogleAuth()
gauth.LoadCredentialsFile("credentials.json")
drive = GoogleDrive(gauth)

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

    await event.reply(f"⬇️ Downloading & merging: {filename}.mp4 (takes time)")

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

# Flask app (Render ki web service chupinchataniki)
app = Flask(__name__)
@app.route('/')
def home():
    return "Patreon Mirror Bot is running!"

if __name__ == "__main__":
    # Bot ni background thread lo run cheyyi
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    thread = threading.Thread(target=loop.run_until_complete, args=(main(),), daemon=True)
    thread.start()
    # Render port 10000 ni listen cheyyi
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
