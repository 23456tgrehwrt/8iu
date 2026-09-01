import os
import tempfile
from urllib.parse import quote

import requests
import yt_dlp
from flask import Flask, jsonify, request, Response
from flask_cors import CORS


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

CORS(
    app,
    resources={
        r"/api/*": {
            "origins": "*"
        }
    },
    methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=[
        "Range",
        "Content-Type",
        "Accept",
        "Origin",
        "User-Agent",
    ],
    expose_headers=[
        "Content-Length",
        "Content-Range",
        "Accept-Ranges",
        "Content-Type",
        "ETag",
        "Last-Modified",
    ],
)


# =========================================================
# CONFIG
# =========================================================

YOUTUBE_COOKIES = os.environ.get("YOUTUBE_COOKIES", "").strip()


# =========================================================
# HELPERS
# =========================================================

def format_duration(seconds):
    try:
        seconds = int(seconds or 0)

        if seconds <= 0:
            return "00:00"

        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"

        return f"{minutes:02d}:{secs:02d}"

    except Exception:
        return "00:00"


def clean_text(value):
    if not value:
        return ""

    return str(value).strip()


def get_api_base():
    forwarded_host = request.headers.get("X-Forwarded-Host")

    if forwarded_host:
        return f"https://{forwarded_host.split(',')[0].strip()}"

    return request.host_url.rstrip("/")


def make_stream_url(video_url):
    base = get_api_base()

    return (
        f"{base}/api/stream"
        f"?url={quote(video_url, safe='')}"
    )


def create_cookie_file():
    """
    YOUTUBE_COOKIES را موقتاً به cookies.txt تبدیل می‌کند.
    فایل بعد از استفاده حذف می‌شود.
    """

    if not YOUTUBE_COOKIES:
        return None, None

    cookie_data = YOUTUBE_COOKIES.replace("\r\n", "\n").replace("\r", "\n")

    if not cookie_data.startswith("#"):
        raise ValueError(
            "YOUTUBE_COOKIES must be a Mozilla/Netscape cookies.txt file."
        )

    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".txt",
        delete=False,
    )

    try:
        temp_file.write(cookie_data)
        temp_file.flush()
        temp_file.close()

        return temp_file.name, temp_file

    except Exception:
        try:
            temp_file.close()
        except Exception:
            pass

        return None, None


def yt_dlp_options():
    """
    تنظیمات مشترک yt-dlp
    """

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }

    cookie_path = None

    if YOUTUBE_COOKIES:
        cookie_path, _ = create_cookie_file()

        if cookie_path:
            options["cookiefile"] = cookie_path

    return options, cookie_path


def cleanup_cookie_file(cookie_path):
    if not cookie_path:
        return

    try:
        os.remove(cookie_path)
    except Exception:
        pass


# =========================================================
# HOME
# =========================================================

@app.get("/")
def home():
    return jsonify({
        "success": True,
        "service": "Music API",
        "status": "online",
        "youtube_cookies": bool(YOUTUBE_COOKIES)
    })


# =========================================================
# HEALTH
# =========================================================

@app.get("/api/health")
def health():
    return jsonify({
        "success": True,
        "status": "ok",
        "youtube_cookies": bool(YOUTUBE_COOKIES)
    })


# =========================================================
# OPTIONS / CORS
# =========================================================

@app.route("/api/<path:path>", methods=["OPTIONS"])
def cors_preflight(path):
    response = jsonify({
        "success": True
    })

    response.headers["Access-Control-Allow-Origin"] = "*"

    response.headers["Access-Control-Allow-Methods"] = (
        "GET, HEAD, OPTIONS"
    )

    response.headers["Access-Control-Allow-Headers"] = (
        "Range, Content-Type, Accept, Origin, User-Agent"
    )

    response.headers["Access-Control-Expose-Headers"] = (
        "Content-Length, Content-Range, Accept-Ranges, "
        "Content-Type, ETag, Last-Modified"
    )

    return response


# =========================================================
# SEARCH
# =========================================================

@app.get("/api/search")
def search():
    query = request.args.get("q", "").strip()

    if not query:
        return jsonify({
            "success": False,
            "error": "Missing q parameter",
            "query": "",
            "count": 0,
            "results": []
        }), 400

    try:
        limit = int(request.args.get("limit", 10))
    except Exception:
        limit = 10

    limit = max(1, min(limit, 20))

    cookie_path = None

    try:
        options, cookie_path = yt_dlp_options()

        options["extract_flat"] = True
        options["default_search"] = f"ytsearch{limit}"

        with yt_dlp.YoutubeDL(options) as ydl:
            data = ydl.extract_info(
                f"ytsearch{limit}:{query}",
                download=False
            )

        entries = data.get("entries") or []

        results = []

        for item in entries:
            if not item:
                continue

            try:
                video_id = item.get("id")

                video_url = (
                    item.get("webpage_url")
                    or item.get("url")
                )

                if not video_url and video_id:
                    video_url = (
                        f"https://www.youtube.com/watch?v={video_id}"
                    )

                if not video_url:
                    continue

                title = clean_text(
                    item.get("title")
                )

                if not title:
                    continue

                artist = clean_text(
                    item.get("channel")
                    or item.get("uploader")
                    or item.get("creator")
                )

                artist_url = clean_text(
                    item.get("channel_url")
                    or item.get("uploader_url")
                )

                duration = format_duration(
                    item.get("duration")
                )

                image = clean_text(
                    item.get("thumbnail")
                )

                if not image and video_id:
                    image = (
                        "https://i.ytimg.com/vi/"
                        f"{video_id}/hqdefault.jpg"
                    )

                results.append({
                    "artist": artist,
                    "artist_url": artist_url,
                    "duration": duration,
                    "image": image,
                    "stream": make_stream_url(video_url),
                    "title": title,
                    "url": video_url
                })

            except Exception as item_error:
                print(
                    f"[SEARCH] skipped result: {item_error}"
                )
                continue

        return jsonify({
            "success": True,
            "query": query,
            "count": len(results),
            "results": results
        })

    except Exception as e:
        print(f"[SEARCH ERROR] {e}")

        return jsonify({
            "success": False,
            "error": str(e),
            "query": query,
            "count": 0,
            "results": []
        }), 500

    finally:
        cleanup_cookie_file(cookie_path)


# =========================================================
# INFO
# =========================================================

@app.get("/api/info")
def info():
    url = request.args.get("url", "").strip()

    if not url:
        return jsonify({
            "success": False,
            "error": "Missing url parameter"
        }), 400

    cookie_path = None

    try:
        options, cookie_path = yt_dlp_options()

        options["format"] = "bestaudio/best"

        with yt_dlp.YoutubeDL(options) as ydl:
            data = ydl.extract_info(
                url,
                download=False
            )

        return jsonify({
            "success": True,
            "title": data.get("title"),
            "artist": (
                data.get("channel")
                or data.get("uploader")
                or ""
            ),
            "artist_url": (
                data.get("channel_url")
                or data.get("uploader_url")
                or ""
            ),
            "duration": format_duration(
                data.get("duration")
            ),
            "image": data.get("thumbnail"),
            "url": url,
            "stream": make_stream_url(url)
        })

    except Exception as e:
        print(f"[INFO ERROR] {e}")

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

    finally:
        cleanup_cookie_file(cookie_path)


# =========================================================
# STREAM
# =========================================================

@app.get("/api/stream")
def stream():
    url = request.args.get("url", "").strip()

    if not url:
        return jsonify({
            "success": False,
            "error": "Missing url parameter"
        }), 400

    cookie_path = None
    upstream = None

    try:
        # -------------------------------------------------
        # Extract direct audio URL
        # -------------------------------------------------

        options, cookie_path = yt_dlp_options()

        options["format"] = "bestaudio/best"

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                url,
                download=False
            )

        direct_url = info.get("url")

        if not direct_url:
            return jsonify({
                "success": False,
                "error": "Could not extract audio stream"
            }), 500

        # -------------------------------------------------
        # Request headers
        # -------------------------------------------------

        headers = {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/152.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
        }

        range_header = request.headers.get("Range")

        if range_header:
            headers["Range"] = range_header

        # -------------------------------------------------
        # Upstream request
        # -------------------------------------------------

        upstream = requests.get(
            direct_url,
            headers=headers,
            stream=True,
            timeout=(15, 60)
        )

        status_code = upstream.status_code

        if status_code >= 400:
            error_text = ""

            try:
                error_text = upstream.text[:500]
            except Exception:
                pass

            return jsonify({
                "success": False,
                "error": "Upstream stream request failed",
                "status": status_code,
                "details": error_text
            }), status_code

        # -------------------------------------------------
        # Response headers
        # -------------------------------------------------

        content_type = (
            upstream.headers.get("Content-Type")
            or "audio/mpeg"
        )

        content_length = (
            upstream.headers.get("Content-Length")
        )

        content_range = (
            upstream.headers.get("Content-Range")
        )

        accept_ranges = (
            upstream.headers.get("Accept-Ranges")
        )

        etag = upstream.headers.get("ETag")

        last_modified = (
            upstream.headers.get("Last-Modified")
        )

        # -------------------------------------------------
        # Streaming generator
        # -------------------------------------------------

        def generate():
            try:
                for chunk in upstream.iter_content(
                    chunk_size=256 * 1024
                ):
                    if chunk:
                        yield chunk

            finally:
                try:
                    upstream.close()
                except Exception:
                    pass

        response = Response(
            generate(),
            status=status_code,
            content_type=content_type
        )

        # -------------------------------------------------
        # CORS
        # -------------------------------------------------

        response.headers["Access-Control-Allow-Origin"] = "*"

        response.headers["Access-Control-Allow-Headers"] = (
            "Range, Content-Type, Accept, Origin, User-Agent"
        )

        response.headers["Access-Control-Expose-Headers"] = (
            "Content-Length, Content-Range, Accept-Ranges, "
            "Content-Type, ETag, Last-Modified"
        )

        # -------------------------------------------------
        # Range / Seek
        # -------------------------------------------------

        response.headers["Accept-Ranges"] = (
            accept_ranges or "bytes"
        )

        if content_length:
            response.headers["Content-Length"] = (
                content_length
            )

        if content_range:
            response.headers["Content-Range"] = (
                content_range
            )

        if etag:
            response.headers["ETag"] = etag

        if last_modified:
            response.headers["Last-Modified"] = (
                last_modified
            )

        return response

    except Exception as e:
        print(f"[STREAM ERROR] {e}")

        if upstream:
            try:
                upstream.close()
            except Exception:
                pass

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

    finally:
        cleanup_cookie_file(cookie_path)


# =========================================================
# ERROR HANDLERS
# =========================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "success": False,
        "error": "Endpoint not found"
    }), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "success": False,
        "error": "Internal server error"
    }), 500


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", 8080)
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
