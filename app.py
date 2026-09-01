from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import yt_dlp
import requests
from urllib.parse import quote

app = Flask(__name__)

# =========================================================
# CORS
# =========================================================

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
        "User-Agent"
    ],
    expose_headers=[
        "Content-Length",
        "Content-Range",
        "Accept-Ranges",
        "Content-Type",
        "ETag",
        "Last-Modified"
    ]
)


# =========================================================
# Helpers
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


def get_api_base():
    """
    Railway:
    اگر RAILWAY_PUBLIC_DOMAIN موجود باشد از آن استفاده می‌کنیم.
    در غیر این صورت از host فعلی درخواست استفاده می‌شود.
    """

    domain = request.headers.get("X-Forwarded-Host")

    if domain:
        return f"https://{domain}"

    return request.host_url.rstrip("/")


def make_stream_url(video_url):
    base = get_api_base()
    return f"{base}/api/stream?url={quote(video_url, safe='')}"


def clean_text(value):
    if not value:
        return ""

    return str(value).strip()


# =========================================================
# YouTube info
# =========================================================

def get_yt_info(url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "format": "bestaudio/best",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


# =========================================================
# Health
# =========================================================

@app.get("/")
def home():
    return jsonify({
        "success": True,
        "service": "Music API",
        "status": "online"
    })


@app.get("/api/health")
def health():
    return jsonify({
        "success": True,
        "status": "ok"
    })


# =========================================================
# CORS Preflight
# =========================================================

@app.route("/api/<path:path>", methods=["OPTIONS"])
def cors_preflight(path):
    response = jsonify({
        "success": True
    })

    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
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
    q = request.args.get("q", "").strip()

    if not q:
        return jsonify({
            "success": False,
            "error": "Missing q parameter",
            "query": "",
            "count": 0,
            "results": []
        }), 400

    # limit
    try:
        limit = int(request.args.get("limit", 10))
    except Exception:
        limit = 10

    limit = max(1, min(limit, 20))

    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
            "default_search": f"ytsearch{limit}",
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_info = ydl.extract_info(
                f"ytsearch{limit}:{q}",
                download=False
            )

        entries = search_info.get("entries") or []

        results = []

        for item in entries:
            if not item:
                continue

            try:
                video_url = (
                    item.get("webpage_url")
                    or item.get("url")
                )

                if not video_url:
                    video_id = item.get("id")

                    if video_id:
                        video_url = (
                            f"https://www.youtube.com/watch?v={video_id}"
                        )

                if not video_url:
                    continue

                title = clean_text(item.get("title"))

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

                # بعضی نتایج extract_flat ممکن است thumbnail نداشته باشند
                video_id = item.get("id")

                if not image and video_id:
                    image = (
                        f"https://i.ytimg.com/vi/"
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

            except Exception as e:
                # یک نتیجه خراب نباید کل search را 500 کند
                print(f"[SEARCH] Skipping invalid result: {e}")
                continue

        return jsonify({
            "success": True,
            "query": q,
            "count": len(results),
            "results": results
        })

    except Exception as e:
        print(f"[SEARCH ERROR] {e}")

        return jsonify({
            "success": False,
            "error": str(e),
            "query": q,
            "count": 0,
            "results": []
        }), 500


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

    try:
        data = get_yt_info(url)

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

    try:
        # -------------------------------------------------
        # Get direct audio URL
        # -------------------------------------------------

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,

            # فقط بهترین صدای قابل دسترس
            "format": "bestaudio/best",
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
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
        # Forward Range
        # -------------------------------------------------

        range_header = request.headers.get("Range")

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

        if range_header:
            headers["Range"] = range_header

        # -------------------------------------------------
        # Connect to upstream
        # -------------------------------------------------

        upstream = requests.get(
            direct_url,
            headers=headers,
            stream=True,
            timeout=(15, 60)
        )

        status_code = upstream.status_code

        # -------------------------------------------------
        # Handle errors
        # -------------------------------------------------

        if status_code >= 400:
            error_text = ""

            try:
                error_text = upstream.text[:500]
            except Exception:
                pass

            upstream.close()

            return jsonify({
                "success": False,
                "error": "Upstream stream request failed",
                "status": status_code,
                "details": error_text
            }), status_code

        # -------------------------------------------------
        # Headers
        # -------------------------------------------------

        content_type = (
            upstream.headers.get("Content-Type")
            or "audio/mpeg"
        )

        content_length = upstream.headers.get(
            "Content-Length"
        )

        content_range = upstream.headers.get(
            "Content-Range"
        )

        accept_ranges = upstream.headers.get(
            "Accept-Ranges"
        )

        etag = upstream.headers.get("ETag")

        last_modified = upstream.headers.get(
            "Last-Modified"
        )

        # -------------------------------------------------
        # Generator
        # -------------------------------------------------

        def generate():
            try:
                for chunk in upstream.iter_content(
                    chunk_size=256 * 1024
                ):
                    if chunk:
                        yield chunk

            finally:
                upstream.close()

        # -------------------------------------------------
        # Response
        # -------------------------------------------------

        response = Response(
            generate(),
            status=status_code,
            content_type=content_type
        )

        # CORS
        response.headers["Access-Control-Allow-Origin"] = "*"

        response.headers["Access-Control-Expose-Headers"] = (
            "Content-Length, Content-Range, Accept-Ranges, "
            "Content-Type, ETag, Last-Modified"
        )

        response.headers["Access-Control-Allow-Headers"] = (
            "Range, Content-Type, Accept, Origin, User-Agent"
        )

        # Range / Seeking
        response.headers["Accept-Ranges"] = (
            accept_ranges or "bytes"
        )

        if content_length:
            response.headers["Content-Length"] = content_length

        if content_range:
            response.headers["Content-Range"] = content_range

        if etag:
            response.headers["ETag"] = etag

        if last_modified:
            response.headers["Last-Modified"] = last_modified

        return response

    except Exception as e:
        print(f"[STREAM ERROR] {e}")

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# =========================================================
# 404
# =========================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "success": False,
        "error": "Endpoint not found"
    }), 404


# =========================================================
# 500
# =========================================================

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "success": False,
        "error": "Internal server error"
    }), 500


# =========================================================
# Local run
# =========================================================

if __name__ == "__main__":
    import os

    port = int(
        os.environ.get("PORT", 8080)
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
