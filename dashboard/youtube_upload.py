"""
youtube_upload.py — YouTube Data API v3 upload + OAuth 2.0 flow.

Requires (install once):
    pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib

OAuth client credentials go in config.json under "youtube_oauth":
    {
      "youtube_oauth": {
        "client_id":     "YOUR_CLIENT_ID.apps.googleusercontent.com",
        "client_secret": "YOUR_CLIENT_SECRET"
      }
    }

Tokens are stored in dashboard/yt_token.json (auto-refreshed on use).
"""

import json
import re
from pathlib import Path

DASHBOARD_DIR = Path(__file__).parent


def _token_file(cid: str = "") -> Path:
    """Return the OAuth token file path for the given channel ID."""
    if cid:
        return DASHBOARD_DIR / f"yt_token_{cid}.json"
    return DASHBOARD_DIR / "yt_token.json"   # backward-compat fallback
CONFIG_FILE   = DASHBOARD_DIR.parent / "UltraFocusZone_Automation" / "config.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

REDIRECT_URI = "http://localhost:5000/oauth/callback"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _get_client_config() -> dict | None:
    """Return the OAuth client_secrets dict, or None if not configured."""
    cfg     = _load_config()
    yt_oauth = cfg.get("youtube_oauth", {})
    cid     = yt_oauth.get("client_id", "")
    cse     = yt_oauth.get("client_secret", "")
    if not cid or not cse or cid.startswith("YOUR_"):
        return None
    return {
        "installed": {
            "client_id":     cid,
            "client_secret": cse,
            "redirect_uris": [REDIRECT_URI],
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
        }
    }


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------

def get_auth_url(cid: str = "") -> str | None:
    """
    Return the Google OAuth consent-screen URL, or None if:
      - google-auth-oauthlib is not installed
      - youtube_oauth not configured in config.json
    """
    client_config = _get_client_config()
    if not client_config:
        return None
    try:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_config(client_config, scopes=SCOPES)
        flow.redirect_uri = REDIRECT_URI
        auth_url, _ = flow.authorization_url(
            prompt="consent",
            access_type="offline",
            include_granted_scopes="true",
        )
        return auth_url
    except ImportError:
        return None


def exchange_code_for_token(code: str, cid: str = "") -> bool:
    """
    Exchange the OAuth callback code for access + refresh tokens.
    Saves tokens to yt_token_<cid>.json. Returns True on success.
    """
    client_config = _get_client_config()
    if not client_config:
        return False
    try:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_config(client_config, scopes=SCOPES)
        flow.redirect_uri = REDIRECT_URI
        flow.fetch_token(code=code)
        creds = flow.credentials
        _token_file(cid).write_text(json.dumps({
            "token":         creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri":     creds.token_uri,
            "client_id":     creds.client_id,
            "client_secret": creds.client_secret,
            "scopes":        list(creds.scopes or SCOPES),
        }, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def revoke_token(cid: str = "") -> None:
    """Delete the stored token for the given channel (disconnect YouTube account)."""
    tf = _token_file(cid)
    if tf.exists():
        tf.unlink()


def get_auth_status(cid: str = "") -> dict:
    """Return {"connected": bool, "configured": bool}"""
    configured = _get_client_config() is not None
    tf = _token_file(cid)
    if not tf.exists():
        return {"connected": False, "configured": configured}
    try:
        creds = _get_valid_credentials(cid)
        return {"connected": creds is not None, "configured": configured}
    except Exception:
        return {"connected": False, "configured": configured}


def _get_valid_credentials(cid: str = ""):
    """Load and auto-refresh credentials from the channel token file. Returns None if unavailable."""
    tf = _token_file(cid)
    if cid and not tf.exists() and _token_file("").exists():
        # Channels connected before per-channel tokens existed only have the
        # legacy file — fall back so single-channel setups keep working.
        # Loudly: uploading channel B's video with the legacy (channel A)
        # token is exactly the failure this warning exists to expose.
        print(f"WARNING: no token file for channel {cid!r} — falling back to "
              f"the legacy yt_token.json account.")
        tf = _token_file("")
    if not tf.exists():
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        data  = json.loads(tf.read_text(encoding="utf-8"))
        creds = Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scopes=data.get("scopes", SCOPES),
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            data["token"] = creds.token
            tf.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return creds
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Connection check + veto (cancel a scheduled publish)
# ---------------------------------------------------------------------------

def is_connected(cid: str = "") -> bool:
    """True if we hold valid, refreshable YouTube credentials for this channel.

    Used as a pre-flight gate before autopilot spends money on generation:
    without this, an unconnected client burns a full render and only fails at
    the upload step.
    """
    return _get_valid_credentials(cid) is not None


def cancel_scheduled_publish(video_id: str, cid: str = "") -> dict:
    """
    Veto a scheduled video: force it back to private and clear its publishAt,
    so it will NOT go live at the scheduled time. The video stays on the
    channel as a private draft (not deleted), so nothing is lost.

    Returns {"ok": bool, "error": str|None}.
    """
    creds = _get_valid_credentials(cid)
    if creds is None:
        return {"ok": False, "error": "YouTube is not connected for this channel."}
    try:
        from googleapiclient.discovery import build
        youtube = build("youtube", "v3", credentials=creds)
        # publishAt can only be cleared by rewriting the status resource.
        youtube.videos().update(
            part="status",
            body={"id": video_id, "status": {"privacyStatus": "private"}},
        ).execute()
        return {"ok": True, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Tag sanitizer
# ---------------------------------------------------------------------------

def _sanitize_tags(tags: list) -> list:
    """
    Clean tags before sending to the YouTube API.

    YouTube rejects tags that contain < > " characters, are empty,
    or exceed the 500-character combined total.
    """
    result = []
    total = 0
    for raw in tags:
        # Normalise whitespace and strip invalid characters
        t = re.sub(r'\s+', ' ', str(raw)).strip()
        t = re.sub(r'[<>"&]', '', t).strip()
        if not t:
            continue
        # Per-tag safety limit
        t = t[:100]
        # YouTube's combined-tags limit is 500 characters
        if total + len(t) > 500:
            break
        result.append(t)
        total += len(t)
    return result


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list,
    publish_at: str | None,
    thumbnail_path: str | None = None,
    progress_callback=None,
    cid: str = "",
) -> dict:
    """
    Upload a video to YouTube and optionally set its thumbnail.

    Args:
        video_path:        Full path to the .mp4 file.
        title:             YouTube title (truncated to 100 chars).
        description:       YouTube description text.
        tags:              List of tag strings.
        publish_at:        ISO 8601 UTC string ("2026-03-01T14:00:00Z") for
                           scheduled publish, or None to publish immediately.
        thumbnail_path:    Path to a .jpg thumbnail image (1280×720 recommended).
                           Uploaded after the video via thumbnails().set().
        progress_callback: Optional callable(bytes_sent, bytes_total).

    Returns:
        {"ok": bool, "video_id": str|None, "video_url": str|None, "error": str|None}
    """
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        return {
            "ok": False, "video_id": None, "video_url": None,
            "error": (
                "google-api-python-client not installed. "
                "Run: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
            ),
        }

    try:
        creds = _get_valid_credentials(cid)
        if creds is None:
            return {
                "ok": False, "video_id": None, "video_url": None,
                "error": "Not authenticated. Connect your YouTube account in Step 4.",
            }

        youtube = build("youtube", "v3", credentials=creds)

        # YouTube title limit = 100 chars
        title = title[:100]

        # Sanitize tags — remove invalid characters, enforce 500-char combined limit
        tags = _sanitize_tags(tags)
        if not isinstance(tags, list):
            tags = []

        # Scheduled publish: video must be private with a publishAt time.
        # Immediate publish: video is public right away.
        status_body = {
            "privacyStatus": "private" if publish_at else "public",
        }
        if publish_at:
            status_body["publishAt"] = publish_at

        def _do_insert(tag_list):
            body = {
                "snippet": {
                    "title":       title,
                    "description": description,
                    "tags":        tag_list,
                    "categoryId":  "10",   # Music
                },
                "status": status_body,
            }
            media = MediaFileUpload(
                video_path,
                chunksize=1024 * 1024,
                resumable=True,
                mimetype="video/mp4",
            )
            req = youtube.videos().insert(
                part=",".join(body.keys()),
                body=body,
                media_body=media,
            )
            resp = None
            while resp is None:
                st, resp = req.next_chunk()
                if st and progress_callback:
                    progress_callback(st.resumable_progress, st.total_size)
            return resp

        # First attempt: with sanitized tags
        _tags_warning = None
        try:
            response = _do_insert(tags)
        except Exception as first_exc:
            if "invalidTags" in str(first_exc):
                # Retry with no tags — isolates whether tags are the true cause
                _tags_warning = (
                    f"Tags rejected by YouTube (invalidTags) — uploaded without tags. "
                    f"Original error: {first_exc}"
                )
                response = _do_insert([])
            else:
                raise

        video_id = response["id"]

        # ── Upload thumbnail (separate API call) ─────────────────────────────
        if thumbnail_path and Path(thumbnail_path).exists():
            try:
                thumb_media = MediaFileUpload(
                    thumbnail_path,
                    mimetype="image/jpeg",
                    resumable=False,
                )
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=thumb_media,
                ).execute()
            except Exception as thumb_exc:
                # Thumbnail failure is non-fatal — video is still uploaded
                pass

        # Capture the confirmed publishAt that YouTube accepted
        confirmed_publish_at = response.get("status", {}).get("publishAt")

        return {
            "ok":               True,
            "video_id":         video_id,
            "video_url":        f"https://www.youtube.com/watch?v={video_id}",
            "publish_at":       confirmed_publish_at,   # UTC ISO string or None
            "error":            None,
            "warning":          _tags_warning,
        }

    except Exception as exc:
        return {
            "ok": False, "video_id": None, "video_url": None,
            "error": str(exc),
            "warning": None,
        }
