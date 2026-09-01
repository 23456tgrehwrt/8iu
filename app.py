from flask import Flask, request, jsonify, Response
import subprocess
import shutil
import yt_dlp
import signal

app = Flask(__name__)


# =========================================================
# Helpers
# =========================================================

def error_response(message, status=400, details=None):
    data = {
        "success": False,
        "error": message
    }

    if details:
        data["details"] = details

    return jsonify(data), status


def get_audio_info(url):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "format": "bestaudio/best",
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


# =========================================================
# Health
# =========================================================

@app.get("/")
def home():
    return jsonify({
        "success": True,
        "service": "Music Streaming API",
        "status": "online"
    })


@app.get("/api/health")
def health():
    return jsonify({
        "success": True,
        "status": "online"
    })


# =========================================================
# Search
# =========================================================

@app.get("/api/search")
def search():
    query = request.args.get("q", "").strip()

    if not query:
        return error_response("Missing q")

    try:
        limit = int(request.args.get("limit", 10))
        limit = max(1, min(limit, 20))

        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
        }

        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(
                f"ytsearch{limit}:{query}",
                download=False
            )

        results = []

        for item in data.get("entries", []):
            if not item:
                continue

            video_id = item.get("id")

            results.append({
                "id": video_id,
                "title": item.get("title"),
                "artist": (
                    item.get("channel")
                    or item.get("uploader")
                ),
                "duration": item.get("duration"),
                "thumbnail": (
                    item.get("thumbnail")
                    or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                ),
                "url": (
                    item.get("webpage_url")
                    or f"https://www.youtube.com/watch?v={video_id}"
                )
            })

        return jsonify({
            "success": True,
            "query": query,
            "count": len(results),
            "results": results
        })

    except Exception as e:
        return error_response(
            "Search failed",
            500,
            str(e)
        )


# =========================================================
# Info
# =========================================================

@app.get("/api/info")
def info():
    url = request.args.get("url", "").strip()

    if not url:
        return error_response("Missing url")

    try:
        data = get_audio_info(url)

        return jsonify({
            "success": True,
            "id": data.get("id"),
            "title": data.get("title"),
            "artist": (
                data.get("channel")
                or data.get("uploader")
            ),
            "duration": data.get("duration"),
            "thumbnail": data.get("thumbnail"),
            "url": data.get("webpage_url"),
        })

    except Exception as e:
        return error_response(
            "Could not get information",
            500,
            str(e)
        )


# =========================================================
# Stream
# =========================================================

@app.get("/api/stream")
def stream():
    url = request.args.get("url", "").strip()

    if not url:
        return error_response("Missing url")

    if not shutil.which("ffmpeg"):
        return error_response(
            "FFmpeg is not installed",
            500
        )

    process = None

    try:
        # -------------------------------------------------
        # Get direct audio URL
        # -------------------------------------------------

        data = get_audio_info(url)
        audio_url = data.get("url")

        if not audio_url:
            return error_response(
                "No audio stream found",
                404
            )

        # -------------------------------------------------
        # FFmpeg -> stdout
        # No MP3 is written to disk
        # -------------------------------------------------

        command = [
            "ffmpeg",

            "-hide_banner",
            "-loglevel", "error",

            "-i", audio_url,

            "-vn",

            "-acodec", "libmp3lame",
            "-b:a", "192k",

            # MP3 stream
            "-f", "mp3",

            # Write directly to stdout
            "pipe:1"
        ]

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0
        )

        def generate():
            nonlocal process

            try:
                while True:
                    chunk = process.stdout.read(64 * 1024)

                    if not chunk:
                        break

                    yield chunk

            finally:
                if process:
                    try:
                        process.kill()
                    except Exception:
                        pass

                    try:
                        process.wait(timeout=2)
                    except Exception:
                        pass

        headers = {
            "Content-Type": "audio/mpeg",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Accel-Buffering": "no",
        }

        return Response(
            generate(),
            headers=headers,
            direct_passthrough=True
        )

    except Exception as e:

        if process:
            try:
                process.kill()
            except Exception:
                pass

        return error_response(
            "Streaming failed",
            500,
            str(e)
        )


# =========================================================
# Graceful shutdown
# =========================================================

def shutdown_handler(signum, frame):
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


# =========================================================
# Local development
# =========================================================

if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )
