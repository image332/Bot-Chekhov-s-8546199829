# VIDEO & MUSIC DOWNLOADS — ONE-FILE HOSTING EDITION
# Upload this single bot.py to your hosting service and run: python bot.py
# Required environment variables: API_ID, API_HASH, BOT_TOKEN, ADMIN_IDS
# Optional: PORT, MAX_FILE_SIZE_MB, WORKERS, DOWNLOAD_DIR, DB_PATH

import os as _os, sys as _sys, subprocess as _subprocess, importlib.util as _importlib_util

_BOOT_FLAG = "_VMD_BOOTSTRAPPED"
_REQUIRED = {
    "pyrogram": "pyrogram==2.0.106",
    "yt_dlp": "yt-dlp>=2026.8.19",
    "dotenv": "python-dotenv>=1.0.1",
    "tgcrypto": "TgCrypto>=1.2.5",
    "static_ffmpeg": "static-ffmpeg>=2.8",
}

def _install_missing():
    missing = [pkg for mod, pkg in _REQUIRED.items() if _importlib_util.find_spec(mod) is None]
    if not missing:
        return
    print("[BOOT] Installing missing Python dependencies:", ", ".join(missing), flush=True)
    cmd = [_sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input"] + missing
    _subprocess.check_call(cmd)

def _ensure_runtime_files():
    # These files are generated automatically so no requirements.txt / folders are needed.
    try:
        with open("requirements.txt", "w", encoding="utf-8") as f:
            f.write("pyrogram==2.0.106\nyt-dlp>=2026.8.19\npython-dotenv>=1.0.1\nTgCrypto>=1.2.5\nstatic-ffmpeg>=2.8\n")
    except Exception:
        pass
    for folder in ("downloads", "data"):
        try: _os.makedirs(folder, exist_ok=True)
        except Exception: pass

if _BOOT_FLAG not in _os.environ:
    _os.environ[_BOOT_FLAG] = "1"
    _ensure_runtime_files()
    _install_missing()

# static-ffmpeg downloads/provides an ffmpeg binary when the host has none.
try:
    import static_ffmpeg as _static_ffmpeg
    _static_ffmpeg.add_paths()
    print("[BOOT] FFmpeg runtime ready", flush=True)
except Exception as _ffmpeg_error:
    print("[BOOT] FFmpeg bootstrap warning:", _ffmpeg_error, flush=True)

import asyncio
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yt_dlp
from dotenv import load_dotenv
from pyrogram import Client, filters, idle
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

load_dotenv()

# ============================================================================
#                  VIDEO & MUSIC DOWNLOADS — BOT CONFIG
# ============================================================================
# Edit ONLY this section if you want to put your credentials/settings directly
# inside this single Python file. Values left blank can still be supplied as
# hosting Environment Variables with the same names.
#
# BOT_URL is intentionally AUTO-DETECTED from the bot username at startup.
# You normally do NOT need to enter it manually.
# ============================================================================

API_ID = int(os.getenv("API_ID", "34552415"))
API_HASH = os.getenv("API_HASH", "a41d007169ca0f05f8cb99cd1b3273ec")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8849800814:AAHye72876lFxh6c5fEvsnV7aNrBdgNL33k")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "8546199829").split(",") if x.strip().isdigit()]

# General settings
WORKERS = int(os.getenv("WORKERS", "4"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "2000"))
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads"))
DB_PATH = os.getenv("DB_PATH", "bot.db")

# Optional: leave empty. The bot automatically creates https://t.me/<username>.
BOT_URL = os.getenv("BOT_URL", "")
BOT_USERNAME = ""

# Optional branding/config values (admin controls can change these later).
BOT_NAME = os.getenv("BOT_NAME", "VIDEO & MUSIC DOWNLOADS")

DOWNLOAD_DIR.mkdir(exist_ok=True)

if not API_ID or not API_HASH or not BOT_TOKEN:
    raise RuntimeError(
        "Configure API_ID, API_HASH and BOT_TOKEN in the BOT CONFIG section "
        "or as hosting Environment Variables."
    )

app = Client("VideoMusicDownloadsBot", api_id=API_ID, api_hash=API_HASH,
             bot_token=BOT_TOKEN, workers=WORKERS)

URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
DB_LOCK = threading.Lock()
DOWNLOAD_STATE = {}
USER_FLOW = {}

# ------------------------- Database / settings -------------------------

def db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with DB_LOCK, db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, joined_at INTEGER)")
        con.execute("CREATE TABLE IF NOT EXISTS bans (user_id INTEGER PRIMARY KEY)")
        con.commit()


def get_setting(key, default=""):
    with DB_LOCK, db() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with DB_LOCK, db() as con:
        con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        con.commit()


def admin_ids():
    return set(ADMIN_IDS)


def is_admin(user_id):
    return user_id in admin_ids()


def is_banned(user_id):
    with DB_LOCK, db() as con:
        return con.execute("SELECT 1 FROM bans WHERE user_id=?", (user_id,)).fetchone() is not None


def save_user(user):
    with DB_LOCK, db() as con:
        con.execute("INSERT INTO users(user_id,username,first_name,joined_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name", (user.id, user.username or "", user.first_name or "", int(time.time())))
        con.commit()


def users_count():
    with DB_LOCK, db() as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def banned_count():
    with DB_LOCK, db() as con:
        return con.execute("SELECT COUNT(*) FROM bans").fetchone()[0]


init_db()

# Defaults
if not get_setting("welcome_text"):
    set_setting("welcome_text", "Welcome to our downloader 👋\\n\\nDownload videos and audio from supported platforms quickly and cleanly.")
if not get_setting("welcome_photo_enabled"):
    set_setting("welcome_photo_enabled", "0")
if not get_setting("force_join_enabled"):
    set_setting("force_join_enabled", "0")
if not get_setting("developer_enabled"):
    set_setting("developer_enabled", "0")
if not get_setting("developer_url"):
    set_setting("developer_url", "")
if not get_setting("developer_text"):
    set_setting("developer_text", "👨‍💻 Developer")

# ------------------------- UI helpers -------------------------

def clean_url(url):
    return url.strip().rstrip(")]>.!,\"'")


def safe_name(name, fallback="download"):
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name).strip(" .")
    return name[:180] or fallback


def human_bytes(value):
    if value is None:
        return "Unknown"
    value = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024


def yt_base_opts():
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "restrictfilenames": True,
        "extractor_args": {"youtube": {"player_client": ["android_vr", "web_safari"]}},
    }


def extract_info(url):
    opts = yt_base_opts()
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def available_video_qualities(info):
    heights = set()
    for f in info.get("formats") or []:
        h = f.get("height")
        vcodec = f.get("vcodec")
        if h and h > 0 and vcodec and vcodec != "none":
            heights.add(int(h))
    # Some extractors provide only one combined format.
    if not heights and info.get("height"):
        heights.add(int(info["height"]))
    return sorted(heights, reverse=True)


def site_name(info):
    return info.get("extractor_key") or info.get("extractor") or "Media"


def media_caption(info, filepath, mode):
    title = safe_name(info.get("title") or "Download")
    uploader = info.get("uploader") or info.get("channel") or info.get("creator")
    site = site_name(info)
    lines = [f"🎬 **{title}**"]
    if uploader:
        lines.append(f"👤 {safe_name(str(uploader), 'Unknown')}")
    if site:
        lines.append(f"🌐 {site}")
    lines.append(f"📦 {human_bytes(filepath.stat().st_size)}")
    if mode == "audio":
        lines.append("🎵 MP3")
    return "\n".join(lines)


def user_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📥 Download"), KeyboardButton("ℹ️ Help")],
         [KeyboardButton(get_setting("developer_text", "👨‍💻 Developer"))]],
        resize_keyboard=True,
        is_persistent=True,
    )


def welcome_buttons():
    rows = []
    channel = get_setting("welcome_channel_url", "")
    if channel:
        rows.append([InlineKeyboardButton(get_setting("welcome_channel_text", "📢 Channel"), url=channel)])
    custom = get_setting("welcome_button_url", "")
    if custom:
        rows.append([InlineKeyboardButton(get_setting("welcome_button_text", "🔗 Open"), url=custom)])
    return InlineKeyboardMarkup(rows) if rows else None


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Stats", callback_data="adm:stats"), InlineKeyboardButton("⚙️ Settings", callback_data="adm:settings")],
        [InlineKeyboardButton("🖼 Welcome", callback_data="adm:welcome"), InlineKeyboardButton("📢 Force Join", callback_data="adm:force")],
        [InlineKeyboardButton("📣 Broadcast", callback_data="adm:broadcast"), InlineKeyboardButton("👨‍💻 Developer", callback_data="adm:developer")],
        [InlineKeyboardButton("🚫 Ban User", callback_data="adm:ban"), InlineKeyboardButton("✅ Unban User", callback_data="adm:unban")],
    ])

# ------------------------- Force join -------------------------

async def force_join_ok(client, user_id):
    if get_setting("force_join_enabled", "0") != "1":
        return True
    chat = get_setting("force_join_chat", "")
    if not chat:
        return True
    try:
        member = await client.get_chat_member(chat, user_id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER)
    except Exception:
        return False


async def force_join_message(message):
    chat = get_setting("force_join_chat", "")
    url = get_setting("force_join_url", "") or (f"https://t.me/{chat.lstrip('@')}" if chat.startswith("@") else "")
    buttons = [[InlineKeyboardButton(get_setting("force_join_button_text", "📢 Join Channel"), url=url)]] if url else None
    await message.reply_text(get_setting("force_join_text", "Please join our channel first to use the downloader."), reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)

# ------------------------- Download engine -------------------------

def quality_selector(height):
    h = int(height)
    return f"bestvideo[height={h}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height={h}]+bestaudio/best[height={h}][ext=mp4]/best[height={h}]/best"


def progress_hook(message, state):
    last = [0.0]
    def hook(data):
        if data.get("status") != "downloading":
            return
        now = time.time()
        if now - last[0] < 2:
            return
        last[0] = now
        done = data.get("downloaded_bytes", 0)
        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        pct = done * 100 / total if total else 0
        speed = data.get("speed") or 0
        eta = data.get("eta")
        text = f"⬇️ **Downloading…**\n\n`{pct:.1f}%`  {human_bytes(done)} / {human_bytes(total)}\n⚡ {human_bytes(speed)}/s   ⏱ {eta}s" if eta is not None else f"⬇️ **Downloading…**\n\n`{pct:.1f}%`  {human_bytes(done)} / {human_bytes(total)}\n⚡ {human_bytes(speed)}/s"
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(message.edit_text(text))
        except RuntimeError:
            pass
    return hook


def download_media(url, mode, quality, message, info):
    workdir = Path(tempfile.mkdtemp(prefix="media_", dir=DOWNLOAD_DIR))
    output = str(workdir / "%(title)s.%(ext)s")
    state = {}
    if mode == "audio":
        fmt = "bestaudio/best"
    else:
        fmt = quality_selector(quality) if quality else "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    opts = yt_base_opts()
    opts.update({
        "format": fmt,
        "outtmpl": output,
        "progress_hooks": [progress_hook(message, state)],
        "merge_output_format": "mp4" if mode == "video" else None,
    })
    if mode == "audio":
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"},
            {"key": "FFmpegMetadata"},
            {"key": "EmbedThumbnail"},
        ]
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = sorted([p for p in workdir.rglob("*") if p.is_file()], key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        raise RuntimeError("Download completed but no media file was produced.")
    return workdir, files[0]


async def do_download(message, item, mode, quality=None):
    status = await message.reply_text("⏳ **Preparing your download…**")
    workdir = None
    try:
        await status.edit_text("🔎 **Preparing media…**")
        info = await asyncio.to_thread(extract_info, item["url"])
        await status.edit_text("📥 **Downloading…**")
        workdir, filepath = await asyncio.to_thread(download_media, item["url"], mode, quality, status, info)
        size = filepath.stat().st_size
        if size > MAX_FILE_SIZE_MB * 1024 * 1024:
            raise RuntimeError(f"File is {human_bytes(size)}; limit is {MAX_FILE_SIZE_MB} MB.")
        await status.edit_text("📤 **Uploading to Telegram…**")
        caption = media_caption(info, filepath, mode)
        if mode == "video":
            await message.reply_video(video=str(filepath), caption=caption, supports_streaming=True)
        else:
            await message.reply_audio(audio=str(filepath), caption=caption, title=info.get("title"), performer=info.get("uploader"))
        await status.delete()
    except Exception as exc:
        err = str(exc).replace("`", "")
        low = err.lower()
        if any(x in low for x in ("sign in to confirm", "not a bot", "cookies-from-browser", "login_required")):
            friendly = "❌ **Site verification required**\n\nThis media is protected by the website. Try another public video or a supported public URL."
        else:
            friendly = f"❌ **Download failed**\n\n`{err[-1200:]}`"
        try:
            await status.edit_text(friendly)
        except Exception:
            pass
    finally:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)

# ------------------------- Admin flows -------------------------

async def admin_panel(message):
    await message.reply_text("🛠 **Admin Control Panel**\n\nControl welcome screen, force join, buttons, broadcasts and users.", reply_markup=admin_keyboard())


@app.on_callback_query(filters.regex(r"^adm:(stats|settings|welcome|force|developer|broadcast|ban|unban)$"))
async def admin_callbacks(_, query):
    if not is_admin(query.from_user.id):
        await query.answer("Admin only.", show_alert=True); return
    action = query.data.split(":")[1]
    if action == "stats":
        await query.message.edit_text(f"📊 **Bot Stats**\n\n👥 Users: `{users_count()}`\n🚫 Banned: `{banned_count()}`", reply_markup=admin_keyboard())
    elif action == "settings":
        await query.message.edit_text(
            f"⚙️ **Settings**\n\n🖼 Welcome photo: `{get_setting('welcome_photo_enabled','0')}`\n📢 Force join: `{get_setting('force_join_enabled','0')}`\n📝 Welcome text: `{get_setting('welcome_text')[:250]}`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🖼 Welcome", callback_data="adm:welcome"), InlineKeyboardButton("📢 Force Join", callback_data="adm:force")]])
        )
    elif action == "welcome":
        await query.message.edit_text(
            "🖼 **Welcome Control**\n\nUse these commands:\n`/welcome_text your text`\n`/welcome_photo_on` then send photo URL\n`/welcome_photo_off`\n`/welcome_button Button Name | https://...`\n`/welcome_channel Button Name | https://t.me/...`",
            reply_markup=admin_keyboard())
    elif action == "force":
        await query.message.edit_text(
            "📢 **Force Join Control**\n\n`/force_on` → enable\n`/force_off` → disable\n`/force_channel @channel` → set channel\n`/force_url https://t.me/channel` → join URL\n`/force_text Your message` → change text\n`/force_button Button Name` → change button text",
            reply_markup=admin_keyboard())
    elif action == "developer":
        enabled = get_setting("developer_enabled", "0") == "1"
        url = get_setting("developer_url", "")
        label = get_setting("developer_text", "👨‍💻 Developer")
        status = "🟢 ON" if enabled and url else "🔴 OFF"
        await query.message.edit_text(
            f"👨‍💻 **Developer Button**\n\nStatus: `{status}`\nButton: `{label}`\nLink: `{url or 'Not configured'}`\n\nTurn it on only after a valid link has been saved.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Set / Change Link", callback_data="dev:set")],
                [InlineKeyboardButton("✏️ Change Button Name", callback_data="dev:name")],
                [InlineKeyboardButton("🟢 Enable", callback_data="dev:on"), InlineKeyboardButton("🔴 Disable", callback_data="dev:off")],
                [InlineKeyboardButton("🗑 Remove Link", callback_data="dev:remove")],
                [InlineKeyboardButton("⬅️ Back", callback_data="adm:back")],
            ])
        )
    elif action == "broadcast":
        USER_FLOW[query.from_user.id] = {"flow": "broadcast"}
        await query.message.reply_text("📣 Send the message you want to broadcast to all users.\n\nSend `/cancel` to stop.")
    else:
        flow = "ban" if action == "ban" else "unban"
        USER_FLOW[query.from_user.id] = {"flow": flow}
        await query.message.reply_text(f"Send the numeric Telegram user ID to {flow}.")
    await query.answer()


@app.on_callback_query(filters.regex(r"^dev:(set|name|on|off|remove)$"))
async def developer_callbacks(_, query):
    if not is_admin(query.from_user.id):
        await query.answer("Admin only.", show_alert=True); return
    action = query.data.split(":", 1)[1]
    uid = query.from_user.id
    if action == "set":
        USER_FLOW[uid] = {"flow": "developer_url"}
        await query.message.reply_text("🔗 Send the Developer URL now.\n\nExample: `https://t.me/username` or your website URL.\nSend /cancel to stop.")
    elif action == "name":
        USER_FLOW[uid] = {"flow": "developer_name"}
        await query.message.reply_text("✏️ Send the text you want on the Developer button.\n\nExample: `👨‍💻 Developer`\nSend /cancel to stop.")
    elif action == "on":
        if not get_setting("developer_url", ""):
            await query.answer("Set a Developer URL first.", show_alert=True); return
        set_setting("developer_enabled", "1")
        await query.answer("Developer button enabled.")
    elif action == "off":
        set_setting("developer_enabled", "0")
        await query.answer("Developer button disabled.")
    elif action == "remove":
        set_setting("developer_url", "")
        set_setting("developer_enabled", "0")
        await query.answer("Developer link removed.")
    # refresh panel
    enabled = get_setting("developer_enabled", "0") == "1"
    url = get_setting("developer_url", "")
    label = get_setting("developer_text", "👨‍💻 Developer")
    status = "🟢 ON" if enabled and url else "🔴 OFF"
    await query.message.edit_text(
        f"👨‍💻 **Developer Button**\n\nStatus: `{status}`\nButton: `{label}`\nLink: `{url or 'Not configured'}`",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Set / Change Link", callback_data="dev:set")],
            [InlineKeyboardButton("✏️ Change Button Name", callback_data="dev:name")],
            [InlineKeyboardButton("🟢 Enable", callback_data="dev:on"), InlineKeyboardButton("🔴 Disable", callback_data="dev:off")],
            [InlineKeyboardButton("🗑 Remove Link", callback_data="dev:remove")],
            [InlineKeyboardButton("⬅️ Back", callback_data="adm:back")],
        ])
    )
    await query.answer()

@app.on_callback_query(filters.regex(r"^adm:back$"))
async def admin_back(_, query):
    if not is_admin(query.from_user.id):
        await query.answer("Admin only.", show_alert=True); return
    await query.message.edit_text("🛠 **Admin Control Panel**\n\nChoose a section to manage.", reply_markup=admin_keyboard())
    await query.answer()

@app.on_message(filters.command("admin"))
async def admin_cmd(_, message):
    if is_admin(message.from_user.id):
        await admin_panel(message)
    else:
        await message.reply_text("❌ Admin only.")


@app.on_message(filters.command(["cancel"]))
async def cancel_cmd(_, message):
    USER_FLOW.pop(message.from_user.id, None)
    await message.reply_text("Cancelled.")


@app.on_message(filters.command("welcome_text"))
async def welcome_text_cmd(_, message):
    if not is_admin(message.from_user.id): return
    text = message.text.split(" ", 1)[1] if " " in message.text else ""
    if text:
        set_setting("welcome_text", text)
        await message.reply_text("✅ Welcome text saved.")
    else:
        await message.reply_text("Usage: `/welcome_text Your welcome message`")


@app.on_message(filters.command("welcome_photo_on"))
async def welcome_photo_on(_, message):
    if not is_admin(message.from_user.id): return
    set_setting("welcome_photo_enabled", "pending")
    USER_FLOW[message.from_user.id] = {"flow": "welcome_photo"}
    await message.reply_text("🖼 Send the direct image URL now. It will be saved automatically.\n\nSend `/cancel` to stop.")


@app.on_message(filters.command("welcome_photo_off"))
async def welcome_photo_off(_, message):
    if not is_admin(message.from_user.id): return
    set_setting("welcome_photo_enabled", "0")
    set_setting("welcome_photo_url", "")
    await message.reply_text("✅ Welcome photo disabled.")


@app.on_message(filters.command("welcome_button"))
async def welcome_button(_, message):
    if not is_admin(message.from_user.id): return
    raw = message.text.split(" ", 1)[1] if " " in message.text else ""
    if "|" not in raw:
        await message.reply_text("Usage: `/welcome_button Button Name | https://example.com`"); return
    name, url = [x.strip() for x in raw.split("|", 1)]
    set_setting("welcome_button_text", name); set_setting("welcome_button_url", url)
    await message.reply_text("✅ Welcome button saved.")


@app.on_message(filters.command("welcome_channel"))
async def welcome_channel(_, message):
    if not is_admin(message.from_user.id): return
    raw = message.text.split(" ", 1)[1] if " " in message.text else ""
    if "|" not in raw:
        await message.reply_text("Usage: `/welcome_channel Channel | https://t.me/channel`"); return
    name, url = [x.strip() for x in raw.split("|", 1)]
    set_setting("welcome_channel_text", name); set_setting("welcome_channel_url", url)
    await message.reply_text("✅ Welcome channel button saved.")


@app.on_message(filters.command("force_on"))
async def force_on(_, message):
    if not is_admin(message.from_user.id): return
    set_setting("force_join_enabled", "1")
    await message.reply_text("✅ Force join enabled. Set the channel with `/force_channel @channel` if needed.")


@app.on_message(filters.command("force_off"))
async def force_off(_, message):
    if not is_admin(message.from_user.id): return
    set_setting("force_join_enabled", "0")
    await message.reply_text("✅ Force join disabled.")


@app.on_message(filters.command("force_channel"))
async def force_channel(_, message):
    if not is_admin(message.from_user.id): return
    raw = message.text.split(" ", 1)[1].strip() if " " in message.text else ""
    if not raw:
        await message.reply_text("Usage: `/force_channel @channel` or `/force_channel -1001234567890`"); return
    set_setting("force_join_chat", raw)
    await message.reply_text("✅ Force-join channel saved.")


@app.on_message(filters.command("force_url"))
async def force_url(_, message):
    if not is_admin(message.from_user.id): return
    raw = message.text.split(" ", 1)[1].strip() if " " in message.text else ""
    set_setting("force_join_url", raw)
    await message.reply_text("✅ Force-join URL saved.")


@app.on_message(filters.command("force_text"))
async def force_text(_, message):
    if not is_admin(message.from_user.id): return
    raw = message.text.split(" ", 1)[1] if " " in message.text else ""
    set_setting("force_join_text", raw)
    await message.reply_text("✅ Force-join message saved.")


@app.on_message(filters.command("force_button"))
async def force_button(_, message):
    if not is_admin(message.from_user.id): return
    raw = message.text.split(" ", 1)[1] if " " in message.text else ""
    set_setting("force_join_button_text", raw or "📢 Join Channel")
    await message.reply_text("✅ Force-join button text saved.")


@app.on_message(filters.command("ban"))
async def ban_cmd(_, message):
    if not is_admin(message.from_user.id): return
    raw = message.text.split(" ", 1)[1].strip() if " " in message.text else ""
    if not raw.isdigit(): await message.reply_text("Usage: `/ban USER_ID`"); return
    with DB_LOCK, db() as con:
        con.execute("INSERT OR IGNORE INTO bans(user_id) VALUES(?)", (int(raw),)); con.commit()
    await message.reply_text("✅ User banned.")


@app.on_message(filters.command("unban"))
async def unban_cmd(_, message):
    if not is_admin(message.from_user.id): return
    raw = message.text.split(" ", 1)[1].strip() if " " in message.text else ""
    if not raw.isdigit(): await message.reply_text("Usage: `/unban USER_ID`"); return
    with DB_LOCK, db() as con:
        con.execute("DELETE FROM bans WHERE user_id=?", (int(raw),)); con.commit()
    await message.reply_text("✅ User unbanned.")

# ------------------------- User flow -------------------------

async def send_welcome(message):
    text = get_setting("welcome_text").replace("\\n", "\n")
    markup = welcome_buttons()
    photo = get_setting("welcome_photo_url", "") if get_setting("welcome_photo_enabled") == "1" else ""
    if photo:
        try:
            await message.reply_photo(photo, caption=text, reply_markup=markup)
        except Exception:
            await message.reply_text(text, reply_markup=markup)
    else:
        await message.reply_text(text, reply_markup=markup)
    await message.reply_text("Choose an option below to begin.", reply_markup=user_keyboard())


@app.on_message(filters.command("start"))
async def start(_, message):
    save_user(message.from_user)
    if is_banned(message.from_user.id):
        await message.reply_text("🚫 You are not allowed to use this bot."); return
    if not await force_join_ok(app, message.from_user.id):
        await force_join_message(message); return
    await send_welcome(message)


@app.on_message(filters.regex(r"^📥 Download$"))
async def download_button(_, message):
    if is_banned(message.from_user.id): return
    if not await force_join_ok(app, message.from_user.id):
        await force_join_message(message); return
    USER_FLOW[message.from_user.id] = {"flow": "url"}
    await message.reply_text("🔗 **Send the video/audio link now.**\n\nI’ll detect the site and show the available download options.", reply_markup=ReplyKeyboardRemove())


@app.on_message(filters.regex(r"^ℹ️ Help$"))
async def help_button(_, message):
    await message.reply_text("📥 Tap **Download**, then send a public media URL.\n\nFor video, I’ll show the qualities actually available from that media.\nFor audio, I’ll send high-quality MP3.")


@app.on_message(filters.regex(r"^👨‍💻 Developer$"))
async def developer_button(_, message):
    url = get_setting("developer_url", "")
    if get_setting("developer_enabled", "0") == "1" and url:
        await message.reply_text("👨‍💻 **Developer**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_setting("developer_text", "👨‍💻 Developer"), url=url)]]))
    else:
        await message.reply_text("Developer link is not configured.")


@app.on_message(filters.command("help"))
async def help_cmd(_, message):
    await message.reply_text("📥 Tap **Download** → send a link → choose Video/Audio → choose the available video quality → receive the file.", reply_markup=user_keyboard())


@app.on_message(filters.text & ~filters.command(["start", "help", "admin", "cancel", "welcome_text", "welcome_photo_on", "welcome_photo_off", "welcome_button", "welcome_channel", "force_on", "force_off", "force_channel", "force_url", "force_text", "force_button", "ban", "unban"]))
async def text_router(_, message):
    uid = message.from_user.id
    save_user(message.from_user)
    if is_banned(uid) or not await force_join_ok(app, uid):
        if not is_banned(uid): await force_join_message(message)
        return
    flow = USER_FLOW.get(uid)
    if is_admin(uid) and flow:
        f = flow.get("flow")
        if f == "broadcast":
            USER_FLOW.pop(uid, None)
            sent = 0
            with DB_LOCK, db() as con:
                rows = con.execute("SELECT user_id FROM users").fetchall()
            for row in rows:
                try:
                    await app.copy_message(row["user_id"], message.chat.id, message.id)
                    sent += 1
                except Exception:
                    pass
            await message.reply_text(f"📣 Broadcast complete. Sent: `{sent}`")
            return
        if f == "ban" or f == "unban":
            USER_FLOW.pop(uid, None)
            if not (message.text or "").strip().isdigit():
                await message.reply_text("Please send a numeric Telegram user ID."); return
            target = int(message.text.strip())
            with DB_LOCK, db() as con:
                if f == "ban": con.execute("INSERT OR IGNORE INTO bans(user_id) VALUES(?)", (target,))
                else: con.execute("DELETE FROM bans WHERE user_id=?", (target,))
                con.commit()
            await message.reply_text(f"✅ `{target}` updated.")
            return
        if f == "welcome_photo":
            url = (message.text or "").strip()
            if not URL_RE.match(url):
                await message.reply_text("Please send a valid direct image URL."); return
            set_setting("welcome_photo_url", url); set_setting("welcome_photo_enabled", "1"); USER_FLOW.pop(uid, None)
            await message.reply_text("✅ Welcome photo saved and enabled.")
            return
        if f == "developer_url":
            url = (message.text or "").strip()
            if not URL_RE.match(url):
                await message.reply_text("❌ Please send a valid http/https URL."); return
            set_setting("developer_url", url); set_setting("developer_enabled", "1"); USER_FLOW.pop(uid, None)
            await message.reply_text("✅ Developer link saved and enabled.")
            return
        if f == "developer_name":
            name = (message.text or "").strip()
            if not name or len(name) > 64:
                await message.reply_text("❌ Send a button name up to 64 characters."); return
            set_setting("developer_text", name); USER_FLOW.pop(uid, None)
            await message.reply_text("✅ Developer button name saved.")
            return

    if flow and flow.get("flow") == "url":
        match = URL_RE.search(message.text or "")
        if not match:
            await message.reply_text("❌ I couldn't find a valid http/https link. Send the link again."); return
        USER_FLOW.pop(uid, None)
        await process_url(message, clean_url(match.group(0)))


async def process_url(message, url):
    wait = await message.reply_text("🔎 **Scanning the link…**")
    try:
        info = await asyncio.to_thread(extract_info, url)
        title = safe_name(info.get("title") or "Media")
        uploader = info.get("uploader") or info.get("channel") or info.get("creator")
        site = site_name(info)
        text = f"🎬 **{title}**\n🌐 `{site}`"
        if uploader: text += f"\n👤 `{safe_name(str(uploader))}`"
        duration = info.get("duration")
        if duration: text += f"\n⏱ {int(duration)//60}:{int(duration)%60:02d}"
        text += "\n\nChoose the format you want:"
        DOWNLOAD_STATE[message.from_user.id] = {"url": url, "info": info}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 Video", callback_data="dl:video"), InlineKeyboardButton("🎵 Audio", callback_data="dl:audio")],
            [InlineKeyboardButton("✖️ Cancel", callback_data="dl:cancel")],
        ])
        await wait.edit_text(text, reply_markup=kb)
    except Exception as exc:
        err = str(exc).replace("`", "")
        await wait.edit_text(f"❌ **Unsupported or unavailable URL**\n\n`{err[-1200:]}`")


@app.on_callback_query(filters.regex(r"^dl:(video|audio|cancel)$"))
async def download_type(_, query):
    uid = query.from_user.id
    item = DOWNLOAD_STATE.get(uid)
    action = query.data.split(":", 1)[1]
    if action == "cancel":
        DOWNLOAD_STATE.pop(uid, None); await query.message.edit_text("✖️ Cancelled."); await query.answer(); return
    if not item:
        await query.answer("Request expired. Send the link again.", show_alert=True); return
    if action == "audio":
        DOWNLOAD_STATE.pop(uid, None)
        await query.answer()
        await query.message.edit_reply_markup(None)
        await do_download(query.message, item, "audio")
        return
    heights = available_video_qualities(item["info"])
    if not heights:
        heights = [int(item["info"].get("height") or 0)] if item["info"].get("height") else []
    if not heights:
        await query.answer("No video quality information was found.", show_alert=True); return
    rows = []
    for h in heights[:8]:
        rows.append(InlineKeyboardButton(f"{h}p", callback_data=f"q:{h}"))
    keyboard = [rows[i:i+2] for i in range(0, len(rows), 2)]
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="dl:back")])
    await query.message.edit_text("🎬 **Choose video quality**\n\nThese are the resolutions available from this media:", reply_markup=InlineKeyboardMarkup(keyboard))
    await query.answer()


@app.on_callback_query(filters.regex(r"^q:\d+$"))
async def quality_callback(_, query):
    uid = query.from_user.id
    item = DOWNLOAD_STATE.pop(uid, None)
    if not item:
        await query.answer("Request expired. Send the link again.", show_alert=True); return
    quality = int(query.data.split(":")[1])
    await query.answer(f"Starting {quality}p…")
    await query.message.edit_reply_markup(None)
    await do_download(query.message, item, "video", quality)


@app.on_callback_query(filters.regex(r"^dl:back$"))
async def quality_back(_, query):
    item = DOWNLOAD_STATE.get(query.from_user.id)
    if not item: await query.answer("Request expired.", show_alert=True); return
    await query.message.edit_text("Choose what you want to download:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎬 Video", callback_data="dl:video"), InlineKeyboardButton("🎵 Audio", callback_data="dl:audio")]]))
    await query.answer()

# ------------------------- Render health server -------------------------

def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"OK"
            self.send_response(200 if self.path in ("/", "/health", "/healthz") else 404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        def log_message(self, *args): return
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Health server listening on 0.0.0.0:{port}")


async def _run_bot():
    global BOT_URL, BOT_USERNAME
    await app.start()
    me = await app.get_me()
    BOT_USERNAME = me.username or ""
    if not BOT_URL and BOT_USERNAME:
        BOT_URL = f"https://t.me/{BOT_USERNAME}"
    print(f"[BOT] @{BOT_USERNAME or 'unknown'} online", flush=True)
    print(f"[BOT] BOT_URL = {BOT_URL or 'not available'}", flush=True)
    await idle()
    await app.stop()


if __name__ == "__main__":
    start_health_server()
    asyncio.run(_run_bot())
