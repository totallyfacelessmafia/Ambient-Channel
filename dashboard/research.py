"""
research.py — Competitor channel research, outlier detection & title analysis.

Caches YouTube channel stats in dashboard/research_cache.json.
Call refresh_research(cid, channel_data) to pull fresh data from the YouTube API.
Call get_research(cid) to read cached data without hitting the API.
"""

import json
import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median as _median

_CACHE_FILE = Path(__file__).parent / "research_cache.json"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _load_cache() -> dict:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(data: dict) -> None:
    tmp = _CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(str(tmp), str(_CACHE_FILE))
            return
        except OSError:
            if attempt < 4:
                time.sleep(0.05)
            else:
                raise


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def _detect_outliers(videos: list, multiplier: float = 3.0) -> list[str]:
    """Return video_ids with view_count > multiplier * median."""
    counts = [v["view_count"] for v in videos if v.get("view_count", 0) > 0]
    if len(counts) < 3:
        return []
    med = _median(counts)
    if med == 0:
        return []
    return [v["video_id"] for v in videos if v.get("view_count", 0) > multiplier * med]


def _analyze_titles(all_videos: list) -> dict:
    """Extract top keywords, bigram phrases, and title templates."""
    titles = [v["title"] for v in all_videos if v.get("title")]

    stop = {
        "for", "and", "the", "a", "an", "in", "of", "to", "with", "your",
        "you", "by", "from", "at", "on", "be", "is", "are", "this", "that",
        "it", "as", "1", "2", "3", "4", "5", "6", "hour", "hours", "min",
        "minutes", "music", "study", "work", "zone", "deep", "focus",
        "ambient", "beats", "lofi", "lo", "fi", "amp",
    }

    word_freq: Counter = Counter()
    bigram_freq: Counter = Counter()

    for title in titles:
        main = re.split(r"\s*[\|\-]\s*", title)[0]
        words = re.findall(r"[a-zA-Z]+", main.lower())
        filtered = [w for w in words if w not in stop and len(w) > 2]
        word_freq.update(filtered)
        for i in range(len(filtered) - 1):
            bigram_freq[(filtered[i], filtered[i + 1])] += 1

    top_keywords = [w for w, _ in word_freq.most_common(20)]
    top_phrases  = [f"{a} {b}" for (a, b), _ in bigram_freq.most_common(15)]

    # Identify common suffix patterns after | or -
    suffix_freq: Counter = Counter()
    for title in titles:
        for sep in [" | ", " - "]:
            if sep in title:
                parts = title.split(sep)
                if len(parts) >= 2:
                    raw = parts[-1].strip()
                    normalized = re.sub(
                        r"\b\d+\s*(hour|hr|h|min|minute)\w*\b",
                        "[Duration]", raw, flags=re.I,
                    ).strip()
                    if normalized and len(normalized) < 60:
                        suffix_freq[normalized] += 1

    common_suffixes = [s for s, c in suffix_freq.most_common(8) if c >= 2]
    templates = [f"[Your Title] | {s}" for s in common_suffixes[:6]]

    return {
        "top_keywords": top_keywords,
        "top_phrases":  top_phrases,
        "templates":    templates,
        "total_videos": len(titles),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_research(cid: str) -> dict | None:
    """Return cached research data for this channel, or None."""
    return _load_cache().get(cid)


def cache_age_hours(cid: str) -> float:
    data = get_research(cid)
    if not data:
        return 9999.0
    try:
        dt = datetime.fromisoformat(data["fetched_at"])
        return (datetime.now() - dt).total_seconds() / 3600
    except Exception:
        return 9999.0


def get_unseen_outlier_count(cid: str) -> int:
    """Return the number of outlier videos not yet seen on the research page."""
    data = get_research(cid)
    if not data:
        return 0
    all_ids  = {v["video_id"] for v in data.get("all_outliers", [])}
    seen_ids = set(data.get("seen_outlier_ids", []))
    return len(all_ids - seen_ids)


def mark_research_seen(cid: str) -> None:
    """Mark all current outliers as seen (clears the nav badge)."""
    cache = _load_cache()
    if cid not in cache:
        return
    all_ids = [v["video_id"] for v in cache[cid].get("all_outliers", [])]
    cache[cid]["seen_outlier_ids"] = all_ids
    _save_cache(cache)


def refresh_research(cid: str, channel_data: dict) -> dict:
    """
    Fetch fresh YouTube stats for all channels in the channel profile.
    channel_data: the channel dict from channels.py.
    Returns the updated research dict, or raises RuntimeError on API failure.
    """
    import youtube_api as yt

    to_fetch = []
    own_yt_id = channel_data.get("channel_id", "")
    own_name  = channel_data.get("channel_name", "Your Channel")
    if own_yt_id:
        to_fetch.append({"channel_id": own_yt_id, "name": own_name, "is_own": True})
    for ref in channel_data.get("ref_channels", []):
        if ref.get("channel_id"):
            to_fetch.append({
                "channel_id": ref["channel_id"],
                "name":       ref.get("name", ""),
                "is_own":     False,
            })

    if not to_fetch:
        raise RuntimeError(
            "No channels configured. Add your YouTube channel ID in Channel Setup."
        )

    results    = []
    all_videos = []

    for ch_info in to_fetch:
        try:
            videos = yt.fetch_channel_videos_with_stats(ch_info["channel_id"], n=30)
            error  = None
        except Exception as e:
            videos = []
            error  = str(e)

        counts   = [v["view_count"] for v in videos if v.get("view_count", 0) > 0]
        med      = int(_median(counts)) if len(counts) >= 3 else 0
        outliers = _detect_outliers(videos)

        results.append({
            "channel_id":   ch_info["channel_id"],
            "name":         ch_info["name"],
            "is_own":       ch_info["is_own"],
            "videos":       videos,
            "median_views": med,
            "outliers":     outliers,   # list of video_ids
            "error":        error,
        })
        all_videos.extend(videos)

    # Build flat list of all outlier video dicts with channel attribution
    outlier_map: dict = {}
    for ch_res in results:
        vid_by_id = {v["video_id"]: v for v in ch_res["videos"]}
        for vid_id in ch_res["outliers"]:
            if vid_id in vid_by_id:
                outlier_map[vid_id] = {**vid_by_id[vid_id], "channel_name": ch_res["name"]}
    all_outliers = sorted(outlier_map.values(), key=lambda x: x["view_count"], reverse=True)

    title_analysis = _analyze_titles(all_videos)

    cache    = _load_cache()
    seen_ids = list(set(cache.get(cid, {}).get("seen_outlier_ids", [])))

    research = {
        "fetched_at":      datetime.now().isoformat(timespec="seconds"),
        "channels":        results,
        "title_analysis":  title_analysis,
        "all_outliers":    all_outliers,
        "seen_outlier_ids": seen_ids,
    }

    cache[cid] = research
    _save_cache(cache)
    return research
