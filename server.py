"""
SaveVid -- Real Video Downloader Backend
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
    print("yt-dlp not found. Run: pip install yt-dlp")
    sys.exit(1)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder=".", static_url_path="")
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
# Avoids passing format specs (with special chars) through URL params.
# ---------------------------------------------------------------------------
FORMAT_CACHE = {}
FORMAT_CACHE_LOCK = threading.Lock()


def cache_formats(formats: list) -> str:
    """Store format list, return a session_id for later retrieval."""
    sid = str(uuid.uuid4())
    mapping = {str(i): f["format_spec"] for i, f in enumerate(formats)}
    with FORMAT_CACHE_LOCK:
        FORMAT_CACHE[sid] = mapping
        # Evict old sessions (keep last 50)
        if len(FORMAT_CACHE) > 50:
            oldest = list(FORMAT_CACHE.keys())[0]
            del FORMAT_CACHE[oldest]
    return sid


def get_format_spec(sid: str, fmt_id: str) -> str | None:
    with FORMAT_CACHE_LOCK:
        return FORMAT_CACHE.get(sid, {}).get(fmt_id)


# ---------------------------------------------------------------------------
# Browser / Android UA
# ---------------------------------------------------------------------------
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

ANDROID_UA = (
    "com.google.android.youtube/19.09.37 "
    "(Linux; U; Android 11) gzip"
)

# ---------------------------------------------------------------------------
# Shared network / retry / header settings (used by both configs below)
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
    "http_chunk_size":     1024 * 1024 * 2,   # 2 MB chunks
    "buffersize":          1024 * 16,
    "concurrent_fragment_downloads": 1,
}

# ---------------------------------------------------------------------------
# INFO opts — used for /api/info (extracting metadata only)
# Uses web + mweb clients: these can always decrypt the player response.
# NEVER set player_skip here — skipping JS breaks extraction entirely.
# ---------------------------------------------------------------------------
INFO_YDL_OPTS = {
    **_NETWORK_OPTS,
    "skip_download": True,
    "extractor_args": {
        "youtube": {
            # android, ios, mweb, tv bypass YouTube's web-client bot token requirement
            "player_client": ["android", "ios", "mweb", "tv", "web"],
        },
    },
}

# ---------------------------------------------------------------------------
# DOWNLOAD opts — used for /api/download (actual file download)
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Cookie source & Automatic Fallback Handler
#
# YouTube sometimes requires cookies or po_token. But extracting cookies
# from browsers can fail if the browser isn't installed, DPAPI fails (Chrome 127+),
# or profile databases are missing.
#
# Solution:
#   Try cookies if available (e.g. cookies.txt or non-chrome browser).
#   If any cookie-related error occurs, AUTOMATICALLY catch it and retry without cookies.
# ---------------------------------------------------------------------------
COOKIES_TXT = os.path.join(os.path.dirname(__file__), "cookies.txt")

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
    """
    Executes action_fn(ydl_opts).
    If a cookie-related error occurs, strips cookie settings and retries action_fn.
    """
    # Build list of cookie attempts to try in order
    attempts = []

    # Attempt 1: cookies.txt if present
    if os.path.isfile(COOKIES_TXT):
        attempts.append({**opts, "cookiefile": COOKIES_TXT})

    # Attempt 2: Try edge/firefox if user has them (without throwing unhandled error)
    # We will attempt with no cookies as the main safe fallback
    attempts.append({**opts})  # No cookies

    last_error = None
    for attempt_opts in attempts:
        try:
            with yt_dlp.YoutubeDL(attempt_opts) as ydl:
                return action_fn(ydl)
        except yt_dlp.utils.DownloadError as e:
            msg = str(e)
            if is_cookie_error(msg):
                print(f"  [cookie-fallback] Cookie error caught ({msg[:60]}...). Retrying without cookies.")
                last_error = e
                continue
            raise e
        except Exception as e:
            msg = str(e)
            if is_cookie_error(msg):
                print(f"  [cookie-fallback] Cookie error caught ({msg[:60]}...). Retrying without cookies.")
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
    """Return list of {label, format_spec, icon, size_hint} dicts."""
    if has_ff:
        specs = [
            (2160, "4K UHD",   "bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=2160]+bestaudio/best[height<=2160]", "~2-5 GB"),
            (1080, "1080p HD", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]", "~500 MB-1 GB"),
            (720,  "720p HD",  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]",   "~200-400 MB"),
            (480,  "480p",     "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480]",   "~80-150 MB"),
        ]
    else:
        # Without ffmpeg: pre-merged streams only (video+audio in one file)
        specs = [
            (2160, "4K UHD",   "best[height<=2160][ext=mp4]/best[height<=2160]",   "~2-5 GB"),
            (1080, "1080p HD", "best[height<=1080][ext=mp4]/best[height<=1080]",   "~300-700 MB"),
            (720,  "720p HD",  "best[height<=720][ext=mp4]/best[height<=720]",     "~100-300 MB"),
            (480,  "480p",     "best[height<=480][ext=mp4]/best[height<=480]",     "~50-120 MB"),
        ]

    opts = []
    for h, label, spec, hint in specs:
        # Include if the video has at least 75% of this resolution
        if max_height == 0 or max_height >= h * 0.75:
            opts.append({"label": f"{label} (MP4)", "format_spec": spec, "icon": "video", "size": hint})

    # Best available fallback
    best_spec = (
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        if has_ff else "best[ext=mp4]/best"
    )
    opts.append({"label": "Best Available (MP4)", "format_spec": best_spec, "icon": "video", "size": "Varies"})

    # Audio only
    opts.append({"label": "MP3 Audio Only", "format_spec": "bestaudio/best", "icon": "audio", "size": "~5-30 MB"})

    return opts


def friendly_error(msg: str) -> str:
    """Convert raw yt-dlp error message to a human-friendly string."""
    m = msg.lower()
    if "dpapi" in m or "failed to decrypt with dpapi" in m:
        return ("Chrome 127+ App-Bound Encryption blocked cookie extraction. "
                "The server has skipped Chrome and is using fallback settings or another browser.")
    if "10054" in msg or "forcibly closed" in m or "winerror" in m:
        return ("Connection reset by platform CDN (WinError 10054). "
                "Please try again in a few seconds.")
    if "failed to extract any player response" in m or "player response" in m:
        return ("YouTube could not be reached (player extraction failed). "
                "This sometimes fixes itself — please try again.")
    if "private" in m or "not available" in m:
        return "This video is private or unavailable in your region."
    if "age" in m and "restricted" in m:
        return "Age-restricted video — cannot download without a logged-in account."
    if "unsupported url" in m or "not supported" in m:
        return "This platform or URL is not supported."
    if "403" in msg or "forbidden" in m:
        return "Access forbidden (HTTP 403). The platform blocked this request — try again in a moment."
    if "copyright" in m:
        return "This video is blocked due to copyright restrictions."
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
        "yt_dlp":  yt_dlp.version.__version__,
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

        # Try to get real file sizes from format metadata
        def pick_size(h):
            for f in reversed(fmts):
                fh = f.get("height") or 0
                if fh <= h and f.get("filesize"):
                    return fmt_size(f["filesize"])
                if fh <= h and f.get("filesize_approx"):
                    return fmt_size(f["filesize_approx"])
            return None

        format_options = build_format_options(max_height, FFMPEG)

        # Overlay real sizes where we can get them
        for opt in format_options:
            label = opt["label"]
            real  = None
            if "4K"    in label: real = pick_size(2160)
            elif "1080" in label: real = pick_size(1080)
            elif "720"  in label: real = pick_size(720)
            elif "480"  in label: real = pick_size(480)
            if real:
                opt["size"] = real

        # Cache format specs so download endpoint gets them by ID
        session_id = cache_formats(format_options)

        # Strip format_spec from response (client only needs label+id+size+icon)
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

    # Resolve format spec from cache
    format_spec = get_format_spec(session_id, fmt_id)
    if not format_spec:
        # Fallback if cache expired
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
                print(f"  [format-fallback] Specific format not found for {url[:50]}. Retrying with format='best'.")
                fallback_opts = {**ydl_opts, "format": "bestaudio/best" if is_audio else "best"}
                run_ydl_with_fallback(fallback_opts, lambda ydl: ydl.download([url]))
            else:
                raise e

        files = [f for f in os.listdir(tmpdir) if not f.endswith(".part")]
        if not files:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return jsonify({"error": "Download failed — no output file was created. Try a lower quality."}), 500

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

        # Clean up temp dir 5 minutes after response completes
        def cleanup():
            time.sleep(300)
            shutil.rmtree(tmpdir, ignore_errors=True)
        threading.Thread(target=cleanup, daemon=True).start()

        return send_file(filepath, mimetype=mime, as_attachment=True, download_name=dl_name)

    except yt_dlp.utils.DownloadError as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({"error": friendly_error(str(e))}), 400
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({"error": friendly_error(str(e))}), 500


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 55)
    print("  SaveVid -- Real Video Downloader")
    print("=" * 55)
    print(f"  yt-dlp   : v{yt_dlp.version.__version__}")
    print(f"  ffmpeg   : {'found' if FFMPEG else 'NOT found (4K merge disabled)'}")
    print(f"  cookies  : automatic fallback enabled")
    print(f"  URL      : http://localhost:3000")
    print("=" * 55)
    app.run(host="0.0.0.0", port=3000, debug=False, threaded=True)
