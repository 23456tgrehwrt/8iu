from flask import Flask, request, jsonify, Response
import yt_dlp
import requests
import re
import os
import urllib.parse

app = Flask(__name__)

REQUEST_TIMEOUT = 30
CHUNK_SIZE = 64 * 1024


# =========================================================
# Helpers
# =========================================================

def json_error(message, status=400, details=None):
    data = {
        "success": False,
        "error": message
    }

    if details:
        data["details"] = details

    return jsonify(data), status


def format_duration(seconds):
    if seconds is None:
        return None

    try:
        seconds = int(seconds)
    except (ValueError, TypeError):
        return None

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60

    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    return f"{minutes:02d}:{seconds:02d}"


def clean_filename(value):
    if not value:
        return "audio"

    value = re.sub(r'[\\/*?:"<>|]', "", value)
    value = re.sub(r"\s+", " ", value).strip()

    return value[:200]


def get_yt_info(url):
    """
    Extract metadata + direct media URL.

    No media file is downloaded to disk.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "format": "bestaudio/best",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def get_direct_audio_url(url):
    info = get_yt_info(url)

    direct_url = info.get("url")

    if not direct_url:
        raise RuntimeError("No direct audio URL was found")

    return info, direct_url


def copy_request_headers():
    """
    Copy useful browser headers to the upstream request.
    """
    allowed = [
        "User-Agent",
        "Accept",
        "Accept-Language",
        "Referer",
        "Origin",
    ]

    headers = {}

    for header in allowed:
        value = request.headers.get(header)

        if value:
            headers[header] = value

    if "User-Agent" not in headers:
        headers["User-Agent"] = (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )

    if "Accept" not in headers:
        headers["Accept"] = "*/*"

    return headers


def build_upstream_headers(direct_url):
    """
    Prepare headers for the upstream media request.
    """
    headers = copy_request_headers()

    client_range = request.headers.get("Range")

    if client_range:
        headers["Range"] = client_range

    return headers


def get_content_type(upstream, info):
    """
    Determine the audio MIME type.
    """

    content_type = upstream.headers.get("Content-Type")

    if content_type:
        return content_type.split(";")[0].strip()

    # yt-dlp may provide MIME related metadata.
    formats = info.get("formats") or []

    for fmt in formats:
        if fmt.get("url") == upstream.url:
            mime = fmt.get("mime")

            if mime:
                return mime.split(";")[0]

    ext = (info.get("ext") or "").lower()

    mime_by_ext = {
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
        "mp4": "audio/mp4",
        "webm": "audio/webm",
        "opus": "audio/ogg",
        "ogg": "audio/ogg",
        "aac": "audio/aac",
        "wav": "audio/wav",
    }

    return mime_by_ext.get(ext, "application/octet-stream")


# =========================================================
# Home / Health
# =========================================================

@app.get("/")
def home():
    return jsonify({
        "success": True,
        "service": "Music Streaming API",
        "version": "1.0.0",
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
        return json_error("Missing q")

    try:
        try:
            limit = int(request.args.get("limit", "10"))
        except ValueError:
            limit = 10

        limit = max(1, min(limit, 20))

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
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

            result_url = (
                item.get("webpage_url")
                or f"https://www.youtube.com/watch?v={video_id}"
            )

            thumbnail = (
                item.get("thumbnail")
                or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            )

            results.append({
                "id": video_id,
                "title": item.get("title"),
                "artist": (
                    item.get("channel")
                    or item.get("uploader")
                    or "Unknown"
                ),
                "duration": format_duration(
                    item.get("duration")
                ),
                "duration_seconds": item.get("duration"),
                "thumbnail": thumbnail,
                "url": result_url,
                "source": "youtube"
            })

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
        info_data = get_yt_info(url)

        return jsonify({
            "success": True,
            "id": info_data.get("id"),
            "title": info_data.get("title"),
            "artist": (
                info_data.get("channel")
                or info_data.get("uploader")
                or "Unknown"
            ),
            "duration": format_duration(
                info_data.get("duration")
            ),
            "duration_seconds": info_data.get("duration"),
            "thumbnail": info_data.get("thumbnail"),
            "url": info_data.get("webpage_url") or url,
            "source": "youtube"
        })

    except Exception as e:
        return json_error(
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
        return json_error("Missing url")

    upstream = None

    try:

        # -------------------------------------------------
        # 1. Extract direct audio URL
        # -------------------------------------------------

        info_data, direct_url = get_direct_audio_url(url)

        # -------------------------------------------------
        # 2. Forward Range header to upstream
        # -------------------------------------------------

        upstream_headers = build_upstream_headers(
            direct_url
        )

        upstream = requests.get(
            direct_url,
            headers=upstream_headers,
            stream=True,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        # -------------------------------------------------
        # 3. Handle upstream errors
        # -------------------------------------------------

        if upstream.status_code >= 400:

            body = ""

            try:
                body = upstream.text[:1000]
            except Exception:
                pass

            status = upstream.status_code

            upstream.close()
            upstream = None

            return json_error(
                "Upstream audio request failed",
                status,
                body
            )

        # -------------------------------------------------
        # 4. Build response headers
        # -------------------------------------------------

        response_headers = {}

        upstream_content_type = get_content_type(
            upstream,
            info_data
        )

        response_headers["Content-Type"] = (
            upstream_content_type
        )

        # Important for seeking
        response_headers["Accept-Ranges"] = "bytes"

        # Content-Length
        content_length = upstream.headers.get(
            "Content-Length"
        )

        if content_length:
            response_headers["Content-Length"] = (
                content_length
            )

        # Partial content
        content_range = upstream.headers.get(
            "Content-Range"
        )

        if content_range:
            response_headers["Content-Range"] = (
                content_range
            )

        # ETag / Last-Modified when available
        etag = upstream.headers.get("ETag")

        if etag:
            response_headers["ETag"] = etag

        last_modified = upstream.headers.get(
            "Last-Modified"
        )

        if last_modified:
            response_headers["Last-Modified"] = (
                last_modified
            )

        # Tell reverse proxies not to buffer the stream
        response_headers["X-Accel-Buffering"] = "no"

        response_headers["Cache-Control"] = (
            "public, max-age=30"
        )

        # -------------------------------------------------
        # 5. Stream chunks to browser
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

                if upstream is not None:
                    try:
                        upstream.close()
                    except Exception:
                        pass

        status_code = upstream.status_code

        return Response(
            generate(),
            status=status_code,
            headers=response_headers,
            direct_passthrough=True
        )

    except requests.RequestException as e:

        if upstream is not None:
            try:
                upstream.close()
            except Exception:
                pass

        return json_error(
            "Network error while streaming",
            502,
            str(e)
        )

    except Exception as e:

        if upstream is not None:
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
# Run
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", "8080")
    )

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )
