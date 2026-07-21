"""
SaveVid -- Real Video Downloader Backend for Vercel
Fixes WinError 10054 by using Android/iOS player clients for YouTube
and proper browser spoofing for all platforms.
"""

import os
import sys
import uuid
import shutil
import tempfile
import threading
import time
import subprocess
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

try:
    import yt_dlp
except ImportError:
    print("yt-dlp not found.")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder="..", static_url_path="")
CORS(app)

# ---------------------------------------------------------------------------
# ffmpeg detection
# ---------------------------------------------------------------------------
def has_ffmpeg():
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except Exception:
        return False

FFMPEG = has_ffmpeg()

# ---------------------------------------------------------------------------
# In-memory format cache  {session_id: {fmt_id: format_spec, ...}}
# ---------------------------------------------------------------------------
FORMAT_CACHE = {}
FORMAT_CACHE_LOCK = threading.Lock()


def cache_formats(formats: list) -> str:
    sid = str(uuid.uuid4())
    mapping = {str(i): f["format_spec"] for i, f in enumerate(formats)}
    with FORMAT_CACHE_LOCK:
        FORMAT_CACHE[sid] = mapping
        if len(FORMAT_CACHE) > 50:
            oldest = list(FORMAT_CACHE.keys())[0]
            del FORMAT_CACHE[oldest]
    return sid


def get_format_spec(sid: str, fmt_id: str) -> str | None:
    with FORMAT_CACHE_LOCK:
        return FORMAT_CACHE.get(sid, {}).get(fmt_id)


# ---------------------------------------------------------------------------
# Shared network / retry / header settings
# ---------------------------------------------------------------------------
_NETWORK_OPTS = {
    "quiet":               True,
    "no_warnings":         True,
    "no_color":            True,
    "noplaylist":          True,
    "geo_bypass":          True,
    "socket_timeout":      30,
    "retries":             10,
    "fragment_retries":    10,
    "file_access_retries": 5,
    "retry_sleep_functions": {"http": lambda n: min(4 ** n, 60)},
    "http_chunk_size":     1024 * 1024 * 2,
    "buffersize":          1024 * 16,
    "concurrent_fragment_downloads": 1,
}

INFO_YDL_OPTS = {
    **_NETWORK_OPTS,
    "skip_download": True,
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "ios", "mweb", "tv", "web"],
        },
    },
}

DOWNLOAD_YDL_OPTS = {
    **_NETWORK_OPTS,
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "ios", "mweb", "tv", "web"],
        },
        "tiktok": {
            "api_hostname": "api16-normal-c-useast1a.tiktokv.com",
        },
    },
}

COOKIES_TXT = os.path.join(os.path.dirname(__file__), "..", "cookies.txt")

def is_cookie_error(err_msg: str) -> bool:
    m = err_msg.lower()
    return any(k in m for k in [
        "could not find firefox cookies database",
        "cookies database",
        "cookiesfrombrowser",
        "cookie",
        "dpapi",
        "failed to decrypt",
    ])

def run_ydl_with_fallback(opts: dict, action_fn):
    attempts = []
    if os.path.isfile(COOKIES_TXT):
        attempts.append({**opts, "cookiefile": COOKIES_TXT})
    attempts.append({**opts})

    last_error = None
    for attempt_opts in attempts:
        try:
            with yt_dlp.YoutubeDL(attempt_opts) as ydl:
                return action_fn(ydl)
        except yt_dlp.utils.DownloadError as e:
            msg = str(e)
            if is_cookie_error(msg):
                last_error = e
                continue
            raise e
        except Exception as e:
            msg = str(e)
            if is_cookie_error(msg):
                last_error = e
                continue
            raise e

    if last_error:
        raise last_error

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def fmt_duration(secs):
    if not secs:
        return "N/A"
    secs = int(secs)
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_size(b):
    if not b:
        return None
    if b < 1_000_000:
        return f"~{b/1_000:.0f} KB"
    if b < 1_000_000_000:
        return f"~{b/1_000_000:.0f} MB"
    return f"~{b/1_000_000_000:.1f} GB"


def safe_name(title: str, max_len=80) -> str:
    return "".join(c for c in title if c.isalnum() or c in " _-")[:max_len].strip() or "video"


def build_format_options(max_height: int, has_ff: bool) -> list:
    if has_ff:
        specs = [
            (2160, "4K UHD",   "bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=2160]+bestaudio/best[height<=2160]", "~2-5 GB"),
            (1080, "1080p HD", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]", "~500 MB-1 GB"),
            (720,  "720p HD",  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]",   "~200-400 MB"),
            (480,  "480p",     "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480]",   "~80-150 MB"),
        ]
    else:
        specs = [
            (2160, "4K UHD",   "best[height<=2160][ext=mp4]/best[height<=2160]",   "~2-5 GB"),
            (1080, "1080p HD", "best[height<=1080][ext=mp4]/best[height<=1080]",   "~300-700 MB"),
            (720,  "720p HD",  "best[height<=720][ext=mp4]/best[height<=720]",     "~100-300 MB"),
            (480,  "480p",     "best[height<=480][ext=mp4]/best[height<=480]",     "~50-120 MB"),
        ]

    opts = []
    for h, label, spec, hint in specs:
        if max_height == 0 or max_height >= h * 0.75:
            opts.append({"label": f"{label} (MP4)", "format_spec": spec, "icon": "video", "size": hint})

    best_spec = (
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        if has_ff else "best[ext=mp4]/best"
    )
    opts.append({"label": "Best Available (MP4)", "format_spec": best_spec, "icon": "video", "size": "Varies"})

    opts.append({"label": "MP3 Audio Only", "format_spec": "bestaudio/best", "icon": "audio", "size": "~5-30 MB"})

    return opts


def friendly_error(msg: str) -> str:
    m = msg.lower()
    if "dpapi" in m or "failed to decrypt with dpapi" in m:
        return "Encryption blocked cookie extraction."
    if "10054" in msg or "forcibly closed" in m or "winerror" in m:
        return "Connection reset by platform CDN (WinError 10054). Please try again in a few seconds."
    if "failed to extract any player response" in m or "player response" in m:
        return "YouTube could not be reached. Please try again."
    if "private" in m or "not available" in m:
        return "This video is private or unavailable."
    if "age" in m and "restricted" in m:
        return "Age-restricted video — cannot download without account."
    if "unsupported url" in m or "not supported" in m:
        return "This platform or URL is not supported."
    if "403" in msg or "forbidden" in m:
        return "Access forbidden (HTTP 403). Try again in a moment."
    if "copyright" in m:
        return "This video is blocked due to copyright."
    return msg[:300]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/status")
def status():
    return jsonify({
        "status":  "ok",
        "ffmpeg":  FFMPEG,
        "yt_dlp":  yt_dlp.version.__version__ if 'yt_dlp' in sys.modules else "N/A",
    })


@app.route("/api/info", methods=["POST"])
def get_info():
    body = request.get_json(silent=True) or {}
    url  = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "No URL provided."}), 400

    try:
        info = run_ydl_with_fallback(INFO_YDL_OPTS, lambda ydl: ydl.extract_info(url, download=False))

        fmts       = info.get("formats") or []
        heights    = [f.get("height") or 0 for f in fmts if (f.get("vcodec") or "none") != "none"]
        max_height = max(heights) if heights else 0

        def pick_size(h):
            for f in reversed(fmts):
                fh = f.get("height") or 0
                if fh <= h and f.get("filesize"):
                    return fmt_size(f["filesize"])
                if fh <= h and f.get("filesize_approx"):
                    return fmt_size(f["filesize_approx"])
            return None

        format_options = build_format_options(max_height, FFMPEG)

        for opt in format_options:
            label = opt["label"]
            real  = None
            if "4K"    in label: real = pick_size(2160)
            elif "1080" in label: real = pick_size(1080)
            elif "720"  in label: real = pick_size(720)
            elif "480"  in label: real = pick_size(480)
            if real:
                opt["size"] = real

        session_id = cache_formats(format_options)

        safe_formats = []
        for i, opt in enumerate(format_options):
            safe_formats.append({
                "id":   str(i),
                "label": opt["label"],
                "icon":  opt["icon"],
                "size":  opt.get("size", ""),
            })

        return jsonify({
            "title":      info.get("title", "Unknown Video"),
            "thumbnail":  info.get("thumbnail", ""),
            "duration":   fmt_duration(info.get("duration")),
            "uploader":   info.get("uploader") or info.get("channel") or "",
            "platform":   info.get("extractor_key", "Unknown"),
            "max_height": max_height,
            "ffmpeg":     FFMPEG,
            "session_id": session_id,
            "formats":    safe_formats,
            "url":        url,
        })

    except yt_dlp.utils.DownloadError as e:
        return jsonify({"error": friendly_error(str(e))}), 400
    except Exception as e:
        return jsonify({"error": friendly_error(str(e))}), 500


@app.route("/api/download")
def download_video():
    url        = (request.args.get("url")        or "").strip()
    session_id = (request.args.get("session_id") or "").strip()
    fmt_id     = (request.args.get("fmt_id")     or "0").strip()
    title      = (request.args.get("title")      or "video").strip()
    is_audio   = request.args.get("audio") == "1"

    if not url:
        return jsonify({"error": "No URL."}), 400

    format_spec = get_format_spec(session_id, fmt_id)
    if not format_spec:
        format_spec = "bestaudio/best" if is_audio else "best[ext=mp4]/best"

    tmpdir = tempfile.mkdtemp(prefix="vidsnap_")

    ydl_opts = {
        **DOWNLOAD_YDL_OPTS,
        "outtmpl":             os.path.join(tmpdir, "%(title).80s.%(ext)s"),
        "format":              format_spec,
        "merge_output_format": "mp4",
    }

    if is_audio:
        ydl_opts["format"] = "bestaudio/best"
        if FFMPEG:
            ydl_opts["postprocessors"] = [{
                "key":             "FFmpegExtractAudio",
                "preferredcodec":  "mp3",
                "preferredquality": "192",
            }]

    try:
        try:
            run_ydl_with_fallback(ydl_opts, lambda ydl: ydl.download([url]))
        except yt_dlp.utils.DownloadError as e:
            if "Requested format is not available" in str(e):
                fallback_opts = {**ydl_opts, "format": "bestaudio/best" if is_audio else "best"}
                run_ydl_with_fallback(fallback_opts, lambda ydl: ydl.download([url]))
            else:
                raise e

        files = [f for f in os.listdir(tmpdir) if not f.endswith(".part")]
        if not files:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return jsonify({"error": "Download failed — no output file was created."}), 500

        filepath = os.path.join(tmpdir, files[0])
        ext      = os.path.splitext(files[0])[1].lower()
        dl_name  = f"{safe_name(title)}{ext}"

        mime_map = {
            ".mp4":  "video/mp4",
            ".webm": "video/webm",
            ".mkv":  "video/x-matroska",
            ".mp3":  "audio/mpeg",
            ".m4a":  "audio/mp4",
            ".opus": "audio/ogg",
            ".aac":  "audio/aac",
        }
        mime = mime_map.get(ext, "application/octet-stream")

        def cleanup():
            time.sleep(120)
            shutil.rmtree(tmpdir, ignore_errors=True)
        threading.Thread(target=cleanup, daemon=True).start()

        return send_file(filepath, mimetype=mime, as_attachment=True, download_name=dl_name)

    except yt_dlp.utils.DownloadError as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({"error": friendly_error(str(e))}), 400
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({"error": friendly_error(str(e))}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000, debug=False, threaded=True)
