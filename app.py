from flask import Flask, request, jsonify, Response
import yt_dlp
import requests
import os
from urllib.parse import quote

app = Flask(__name__)

CHUNK_SIZE = 64 * 1024
REQUEST_TIMEOUT = 30


# =========================================================
# Helpers
# =========================================================

def format_duration(seconds):
    try:
        seconds = int(seconds or 0)
    except (ValueError, TypeError):
        return "00:00"

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60

    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    return f"{minutes:02d}:{seconds:02d}"


def get_api_base():
    """
    Build public Railway URL.

    Railway can provide RAILWAY_PUBLIC_DOMAIN.
    Otherwise use request host.
    """

    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")

    if railway_domain:
        if not railway_domain.startswith("http"):
            return f"https://{railway_domain}"

        return railway_domain.rstrip("/")

    return request.host_url.rstrip("/")


def get_yt_info(url):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "format": "bestaudio/best"
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def make_stream_url(source_url):
    base = get_api_base()

    return (
        f"{base}/api/stream?url="
        f"{quote(source_url, safe='')}"
    )


def json_error(message, status=400, details=None):
    data = {
        "success": False,
        "error": message
    }

    if details:
        data["details"] = details

    return jsonify(data), status


# =========================================================
# Home
# =========================================================

@app.get("/")
def home():
    return jsonify({
        "success": True,
        "service": "Music API",
        "status": "online"
    })


# =========================================================
# Health
# =========================================================

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
        return json_error("Missing q")

    try:

        try:
            limit = int(request.args.get("limit", "10"))
        except ValueError:
            limit = 10

        limit = max(1, min(limit, 20))

        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "skip_download": True
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

            if not video_id:
                continue

            source_url = (
                item.get("webpage_url")
                or f"https://www.youtube.com/watch?v={video_id}"
            )

            artist = (
                item.get("channel")
                or item.get("uploader")
                or "Unknown"
            )

            artist_url = (
                item.get("channel_url")
                or item.get("uploader_url")
            )

            image = (
                item.get("thumbnail")
                or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            )

            result = {
                "artist": artist,
                "artist_url": artist_url,
                "duration": format_duration(
                    item.get("duration")
                ),
                "image": image,
                "stream": make_stream_url(source_url),
                "title": item.get("title"),
                "url": source_url
            }

            results.append(result)

        return jsonify({
            "success": True,
            "query": query,
            "count": len(results),
            "results": results
        })

    except Exception as e:

        return json_error(
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
        return json_error("Missing url")

    try:

        data = get_yt_info(url)

        source_url = (
            data.get("webpage_url")
            or url
        )

        return jsonify({
            "artist": (
                data.get("channel")
                or data.get("uploader")
                or "Unknown"
            ),
            "artist_url": (
                data.get("channel_url")
                or data.get("uploader_url")
            ),
            "duration": format_duration(
                data.get("duration")
            ),
            "image": data.get("thumbnail"),
            "stream": make_stream_url(source_url),
            "title": data.get("title"),
            "url": source_url
        })

    except Exception as e:

        return json_error(
            "Could not get information",
            500,
            str(e)
        )


# =========================================================
# Stream Proxy
# =========================================================

@app.get("/api/stream")
def stream():

    url = request.args.get("url", "").strip()

    if not url:
        return json_error("Missing url")

    upstream = None

    try:

        # -------------------------------------------------
        # Extract current direct audio URL
        # -------------------------------------------------

        info = get_yt_info(url)

        direct_url = info.get("url")

        if not direct_url:
            return json_error(
                "Audio stream not found",
                404
            )

        # -------------------------------------------------
        # Forward Range
        # -------------------------------------------------

        headers = {
            "User-Agent": request.headers.get(
                "User-Agent",
                "Mozilla/5.0"
            ),
            "Accept": "*/*"
        }

        range_header = request.headers.get("Range")

        if range_header:
            headers["Range"] = range_header

        # -------------------------------------------------
        # Request upstream audio
        # -------------------------------------------------

        upstream = requests.get(
            direct_url,
            headers=headers,
            stream=True,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        if upstream.status_code >= 400:

            status = upstream.status_code

            try:
                details = upstream.text[:1000]
            except Exception:
                details = None

            upstream.close()

            return json_error(
                "Upstream request failed",
                status,
                details
            )

        # -------------------------------------------------
        # Copy important headers
        # -------------------------------------------------

        response_headers = {}

        for header in [
            "Content-Type",
            "Content-Length",
            "Content-Range",
            "Accept-Ranges",
            "ETag",
            "Last-Modified"
        ]:

            value = upstream.headers.get(header)

            if value:
                response_headers[header] = value

        # Make browser aware that seeking is supported
        response_headers["Accept-Ranges"] = "bytes"

        response_headers["Cache-Control"] = (
            "no-cache, no-store, must-revalidate"
        )

        response_headers["X-Accel-Buffering"] = "no"

        # -------------------------------------------------
        # Stream
        # -------------------------------------------------

        def generate():

            nonlocal upstream

            try:

                for chunk in upstream.iter_content(
                    chunk_size=CHUNK_SIZE
                ):

                    if chunk:
                        yield chunk

            finally:

                try:
                    upstream.close()
                except Exception:
                    pass

        return Response(
            generate(),
            status=upstream.status_code,
            headers=response_headers,
            direct_passthrough=True
        )

    except requests.RequestException as e:

        if upstream:
            try:
                upstream.close()
            except Exception:
                pass

        return json_error(
            "Network error",
            502,
            str(e)
        )

    except Exception as e:

        if upstream:
            try:
                upstream.close()
            except Exception:
                pass

        return json_error(
            "Streaming failed",
            500,
            str(e)
        )


# =========================================================
# Railway
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 8080)
    )

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )
