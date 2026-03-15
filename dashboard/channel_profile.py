"""
channel_profile.py — Persistent channel style profile.

Stores everything the system knows about the channel so that image
generation prompts are consistent with the channel's visual identity.

Files:
  dashboard/channel_profile.json  — text/tag data
  dashboard/style_refs/            — uploaded thumbnail images
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

_PROFILE_FILE  = Path(__file__).parent / "channel_profile.json"
STYLE_REFS_DIR = Path(__file__).parent / "style_refs"

# Pre-defined vibe tags users can choose from
VIBE_OPTIONS = [
    "cozy", "cinematic", "dark", "bright", "warm", "cool",
    "minimal", "luxurious", "moody", "calm", "dramatic",
    "nature", "urban", "rainy", "foggy", "golden hour",
]


def _default() -> dict:
    return {
        "completed":         False,
        "channel_name":      "",
        "channel_url":       "",
        "channel_id":        "",      # YouTube channel ID (resolved from URL)
        "niche":             "",      # e.g. "ambient lofi music for deep work"
        "vibe_tags":         [],      # subset of VIBE_OPTIONS
        "color_notes":       "",      # e.g. "dark warm tones, amber lighting"
        "scene_notes":       "",      # e.g. "cozy cabin interiors, rain, fireplace"
        "style_refs":        [],      # filenames in style_refs/ (uploaded images)
        "ref_channels":      [],      # [{name, channel_id, thumbnails:[{title,url}]}]
        "channel_thumbnails": [],     # [{title, thumbnail_url}] from own channel
        "updated_at":        "",
    }


def load() -> dict:
    if not _PROFILE_FILE.exists():
        return _default()
    try:
        data = json.loads(_PROFILE_FILE.read_text(encoding="utf-8"))
        # Backfill any keys added since profile was last saved
        defaults = _default()
        for k, v in defaults.items():
            data.setdefault(k, v)
        return data
    except (json.JSONDecodeError, OSError):
        return _default()


def save(data: dict) -> None:
    STYLE_REFS_DIR.mkdir(exist_ok=True)
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    tmp = _PROFILE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_PROFILE_FILE)


def is_complete() -> bool:
    return load().get("completed", False)


def save_style_ref(file_storage) -> str:
    """
    Save an uploaded image file to style_refs/ and return the filename.
    file_storage is a Werkzeug FileStorage object from request.files.
    """
    STYLE_REFS_DIR.mkdir(exist_ok=True)
    # Sanitize filename
    from werkzeug.utils import secure_filename
    filename = secure_filename(file_storage.filename)
    if not filename:
        filename = "ref.jpg"
    dest = STYLE_REFS_DIR / filename
    # Avoid overwriting — add counter suffix if needed
    counter = 1
    stem = dest.stem
    while dest.exists():
        dest = STYLE_REFS_DIR / f"{stem}_{counter}{dest.suffix}"
        counter += 1
    file_storage.save(str(dest))
    return dest.name


def delete_style_ref(filename: str) -> None:
    target = STYLE_REFS_DIR / Path(filename).name
    if target.exists() and target.is_file():
        target.unlink()


def suggest_titles(profile: dict = None, n: int = 8) -> list:
    """
    Generate short thumbnail-text suggestions that mirror channel language.
    """
    import re

    if profile is None:
        profile = load()

    canonical = [
        "Locked In",
        "Focus Zone",
        "Deep Focus",
        "Work Music",
        "Deep Work",
        "Zero Distractions",
        "Ultra Focus",
        "Flow State",
        "Hyper Focus",
        "Workflow Music",
        "Focus Mode",
        "Pure Focus",
        "Cosmic Chill",
    ]

    titles = [t.get("title", "") for t in profile.get("channel_thumbnails", []) if t.get("title")]
    lowered = " | ".join(titles).lower()

    # Keep terms that visibly appear in the channel's existing title corpus first.
    ranked = []
    for term in canonical:
        if term.lower() in lowered:
            ranked.append(term)

    # Mine additional short phrases from channel titles.
    mined = []
    phrase_re = re.compile(r"[a-z0-9]+(?:\s+[a-z0-9]+){0,2}", re.IGNORECASE)
    block = {
        "music", "for", "and", "the", "zone", "hour", "hours", "minutes",
        "ambient", "study", "studying", "productivity", "concentration",
    }
    for raw_title in titles:
        for m in phrase_re.findall(raw_title):
            words = [w for w in m.strip().split() if w]
            if not words or len(words) > 3:
                continue
            if all(w.lower() in block for w in words):
                continue
            phrase = " ".join(w.capitalize() for w in words)
            if len(phrase) < 4 or len(phrase) > 22:
                continue
            mined.append(phrase)

    # Deduplicate preserving order.
    seen = set()
    out = []
    for term in ranked + canonical + mined:
        key = term.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(term)
        if len(out) >= n:
            break

    return out[:n]

def build_prompt_prefix() -> str:
    """
    Build a style-context string to prepend to every fal.ai image prompt.
    Returns empty string if profile is not yet complete.
    """
    profile = load()
    if not profile.get("completed"):
        return ""

    parts = []

    # Scene type from notes
    if profile.get("scene_notes"):
        parts.append(profile["scene_notes"])

    # Vibe tags
    if profile.get("vibe_tags"):
        parts.append(", ".join(profile["vibe_tags"]))

    # Color notes
    if profile.get("color_notes"):
        parts.append(profile["color_notes"])

    # Channel title hint from own thumbnails
    if profile.get("channel_thumbnails"):
        from collections import Counter
        import re
        skip = {"hour","1","music","focus","beats","lofi","deep","work","study","hours"}
        words = []
        for t in profile["channel_thumbnails"][:8]:
            title = t.get("title", "")
            scene = title.split(" - ")[0].lower()
            words.extend(
                w.strip(".,;:") for w in scene.split()
                if w not in skip and len(w) > 3
            )
        top = [w for w, _ in Counter(words).most_common(4)]
        if top:
            parts.append(", ".join(top))

    prefix = ", ".join(p.strip() for p in parts if p.strip())
    return prefix
