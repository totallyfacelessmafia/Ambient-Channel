"""
youtube_api.py — YouTube Data API v3 helpers for the UFZ dashboard.

Capabilities:
  - Resolve a YouTube channel URL → channel ID
  - Fetch recent video thumbnails for any channel
  - Build a style-aware fal.ai prompt using the channel profile

Setup:
  1. console.cloud.google.com → create project → enable "YouTube Data API v3"
  2. Credentials → Create API Key
  3. Add to UltraFocusZone_Automation/config.json:
       "youtube": { "api_key": "AIzaSy...", "channel_id": "UCxxx..." }
"""

import json
import os
import re
import urllib.parse
import urllib.request
import urllib.error
from collections import Counter
from pathlib import Path

_CONFIG = (
    Path(__file__).parent.parent
    / "UltraFocusZone_Automation"
    / "config.json"
)

_SAMPLE_COUNT = 8


def _load_config() -> dict:
    if not _CONFIG.exists():
        return {}
    return json.loads(_CONFIG.read_text(encoding="utf-8"))


def _api_key() -> str:
    env_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if env_key and not env_key.startswith("YOUR_"):
        return env_key
    cfg = _load_config()
    key = cfg.get("youtube", {}).get("api_key", "")
    if not key or key.startswith("YOUR_"):
        return ""
    return key


def _channel_id() -> str:
    env_id = os.getenv("YOUTUBE_CHANNEL_ID", "").strip()
    if env_id and not env_id.startswith("YOUR_"):
        return env_id
    cfg = _load_config()
    ch_id = cfg.get("youtube", {}).get("channel_id", "")
    if not ch_id or ch_id.startswith("YOUR_"):
        return ""
    return ch_id


def _yt_get(url: str) -> dict:
    """Perform a YouTube API GET and return the parsed JSON."""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            msg = json.loads(body)["error"]["message"]
        except Exception:
            msg = body[:200]
        raise RuntimeError(f"YouTube API error {e.code}: {msg}")
    except Exception as e:
        raise RuntimeError(f"YouTube API request failed: {e}")
    if "error" in data:
        raise RuntimeError(data["error"].get("message", "Unknown YouTube API error"))
    return data


# ---------------------------------------------------------------------------
# Channel URL → Channel ID resolution
# ---------------------------------------------------------------------------

def parse_channel_url(url: str) -> tuple[str, str]:
    """
    Parse a YouTube channel URL and return (type, value) where type is one of:
      "id"       — URL contains /channel/UCxxx
      "handle"   — URL contains /@Handle
      "user"     — URL contains /user/Name
      "search"   — no URL structure, treat as channel name search query
    """
    url = url.strip()
    # /channel/UCxxx
    m = re.search(r"youtube\.com/channel/(UC[\w-]+)", url)
    if m:
        return "id", m.group(1)
    # /@Handle or @Handle bare
    m = re.search(r"(?:youtube\.com/)?@([\w.-]+)", url)
    if m:
        return "handle", m.group(1)
    # /user/Name
    m = re.search(r"youtube\.com/user/([\w.-]+)", url)
    if m:
        return "user", m.group(1)
    # Bare channel name / search string
    clean = re.sub(r"https?://[^\s]*", "", url).strip()
    return "search", clean or url


def resolve_channel_id(url_or_name: str) -> str:
    """
    Given a YouTube channel URL or name, return the channel ID (UCxxx...).
    Requires a YouTube API key to be configured.
    Raises RuntimeError on failure.
    """
    api_key = _api_key()
    if not api_key:
        raise RuntimeError(
            "YouTube API key not configured. "
            "Set YOUTUBE_API_KEY env var or UltraFocusZone_Automation/config.json -> youtube.api_key."
        )

    kind, value = parse_channel_url(url_or_name)

    if kind == "id":
        return value

    if kind == "handle":
        data = _yt_get(
            "https://www.googleapis.com/youtube/v3/channels"
            f"?key={api_key}&forHandle={urllib.parse.quote(value)}&part=id"
        )
        items = data.get("items", [])
        if items:
            return items[0]["id"]
        raise RuntimeError(f"No channel found for @{value}")

    if kind == "user":
        data = _yt_get(
            "https://www.googleapis.com/youtube/v3/channels"
            f"?key={api_key}&forUsername={urllib.parse.quote(value)}&part=id"
        )
        items = data.get("items", [])
        if items:
            return items[0]["id"]
        raise RuntimeError(f"No channel found for user '{value}'")

    # Search fallback
    data = _yt_get(
        "https://www.googleapis.com/youtube/v3/search"
        f"?key={api_key}&q={urllib.parse.quote(value)}&type=channel&part=snippet&maxResults=1"
    )
    items = data.get("items", [])
    if items:
        return items[0]["id"]["channelId"]
    raise RuntimeError(f"No channel found matching '{value}'")


def fetch_channel_info(channel_id: str) -> dict:
    """Return {"name": str, "description": str, "thumbnail": str} for a channel."""
    api_key = _api_key()
    if not api_key:
        return {}
    data = _yt_get(
        "https://www.googleapis.com/youtube/v3/channels"
        f"?key={api_key}&id={channel_id}&part=snippet"
    )
    items = data.get("items", [])
    if not items:
        return {}
    snippet = items[0].get("snippet", {})
    thumbs  = snippet.get("thumbnails", {})
    thumb   = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {})
    return {
        "name":        snippet.get("title", ""),
        "description": snippet.get("description", "")[:300],
        "thumbnail":   thumb.get("url", ""),
    }


def fetch_channel_videos_with_stats(channel_id: str, n: int = 30) -> list:
    """
    Return recent videos with view/like counts for outlier detection.
    [{"video_id", "title", "thumbnail_url", "view_count", "like_count", "published_at"}, ...]
    Uses 2 API calls: search.list (100 quota units) + videos.list (1 unit).
    """
    api_key = _api_key()
    if not api_key:
        raise RuntimeError(
            "YouTube API key not configured. "
            "Add youtube.api_key to UltraFocusZone_Automation/config.json."
        )

    # Step 1: recent video IDs
    data = _yt_get(
        "https://www.googleapis.com/youtube/v3/search"
        f"?key={api_key}&channelId={channel_id}"
        "&part=id&order=date"
        f"&maxResults={min(n, 50)}&type=video"
    )
    video_ids = [
        item["id"]["videoId"]
        for item in data.get("items", [])
        if item.get("id", {}).get("videoId")
    ]
    if not video_ids:
        return []

    # Step 2: stats + snippets
    ids_param = ",".join(video_ids)
    data2 = _yt_get(
        "https://www.googleapis.com/youtube/v3/videos"
        f"?key={api_key}&id={ids_param}"
        "&part=snippet,statistics"
    )

    results = []
    for item in data2.get("items", []):
        snippet = item.get("snippet", {})
        stats   = item.get("statistics", {})
        thumbs  = snippet.get("thumbnails", {})
        thumb   = (
            thumbs.get("maxres") or thumbs.get("high")
            or thumbs.get("medium") or thumbs.get("default") or {}
        )
        results.append({
            "video_id":      item["id"],
            "title":         snippet.get("title", ""),
            "thumbnail_url": thumb.get("url", ""),
            "view_count":    int(stats.get("viewCount", 0) or 0),
            "like_count":    int(stats.get("likeCount", 0) or 0),
            "published_at":  snippet.get("publishedAt", ""),
        })
    return results


def fetch_channel_thumbnails(channel_id: str, n: int = _SAMPLE_COUNT) -> list:
    """
    Return [{"title": str, "thumbnail_url": str}, ...] for the n most recent videos.
    """
    api_key = _api_key()
    if not api_key:
        return []
    data = _yt_get(
        "https://www.googleapis.com/youtube/v3/search"
        f"?key={api_key}&channelId={channel_id}"
        "&part=snippet&order=date"
        f"&maxResults={n}&type=video"
    )
    results = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        thumbs  = snippet.get("thumbnails", {})
        thumb = (
            thumbs.get("maxres") or thumbs.get("high")
            or thumbs.get("medium") or thumbs.get("default") or {}
        )
        results.append({
            "title":         snippet.get("title", ""),
            "thumbnail_url": thumb.get("url", ""),
        })
    return results


# ---------------------------------------------------------------------------
# Style prompt builder (used by /api/youtube-style and onboarding)
# ---------------------------------------------------------------------------

def _keywords_from_titles(titles: list) -> str:
    skip = {
        "hour","1","music","focus","beats","lofi","lo-fi",
        "deep","work","study","hours","ambient","session",
    }
    words = []
    for title in titles:
        scene = title.split(" - ")[0].lower()
        words.extend(
            w.strip(".,;:!?\"'") for w in scene.split()
            if w not in skip and len(w) > 3
        )
    top = [w for w, _ in Counter(words).most_common(6)]
    return ", ".join(top) if top else "cozy interior"


def build_style_prompt() -> dict:
    """
    Build a style-aware fal.ai prompt.

    Priority:
      1. Channel profile (if completed) → richest context
      2. YouTube API thumbnails via config.json channel_id → title-based keywords
      3. Fallback generic prompt

    Returns {"prompt": str, "thumbnails": list} or {"error": str}.
    """
    import channel_profile as cp

    profile    = cp.load()
    thumbnails = []
    prompt_parts = []

    # ── Use profile data if available ──────────────────────────────────────
    if profile.get("completed"):
        prefix = cp.build_prompt_prefix()
        if prefix:
            prompt_parts.append(prefix)
        thumbnails = profile.get("channel_thumbnails", [])

        # Add thumbnails from reference channels
        for ref_ch in profile.get("ref_channels", []):
            thumbnails += ref_ch.get("thumbnails", [])

    # ── Fallback: YouTube API via config.json ──────────────────────────────
    if not thumbnails:
        api_key_ok = bool(_api_key())
        channel_id = _channel_id()
        ch_id_ok = bool(channel_id)

        if api_key_ok and ch_id_ok:
            try:
                thumbnails = fetch_channel_thumbnails(channel_id)
            except RuntimeError as e:
                # Non-fatal — just proceed without thumbnails
                pass
        elif not profile.get("completed"):
            return {"error": "Channel profile not set up. Complete onboarding first, or add youtube keys to config.json."}

    # ── Build final prompt ────────────────────────────────────────────────
    if thumbnails:
        titles   = [t.get("title", "") for t in thumbnails if t.get("title")]
        keywords = _keywords_from_titles(titles)
        if keywords and keywords not in " ".join(prompt_parts):
            prompt_parts.append(keywords)

    prompt_parts.append("warm ambient lighting, cinematic architectural photography, ultra realistic")

    prompt = ", ".join(p.strip() for p in prompt_parts if p.strip())

    return {"prompt": prompt, "thumbnails": thumbnails[:8]}
