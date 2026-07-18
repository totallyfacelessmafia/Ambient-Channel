"""
channels.py — Multi-channel profile store.

Replaces the singleton channel_profile.py with a per-channel keyed store.
Data lives in dashboard/channels.json; style refs in dashboard/style_refs/<cid>/.

Migration from the old singleton is automatic and idempotent — call
migrate_from_singleton() once at app startup.
"""

import json
import shutil
import os
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

_CHANNELS_FILE = Path(__file__).parent / "channels.json"
_STYLE_REFS_BASE = Path(__file__).parent / "style_refs"

# Pre-defined vibe tags (copied verbatim from channel_profile.py)
VIBE_OPTIONS = [
    "cozy", "cinematic", "dark", "bright", "warm", "cool",
    "minimal", "luxurious", "moody", "calm", "dramatic",
    "nature", "urban", "rainy", "foggy", "golden hour",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _channel_default() -> dict:
    return {
        "owner":              "",      # user email that owns this channel (multi-tenancy)
        "completed":          False,
        "channel_name":       "",
        "channel_url":        "",
        "channel_id":         "",      # YouTube channel ID (resolved from URL)
        "niche":              "",
        "vibe_tags":          [],
        "color_notes":        "",
        "scene_notes":        "",
        "style_refs":         [],      # filenames in style_refs/<cid>/
        "ref_channels":       [],
        "channel_thumbnails": [],
        "subtitle":           "",
        "music_style":        "electronic, ambient, deep, slow, warm, instrumental",
        "overlay_style":      "default",  # "default" | "minimal" | "none"
        "logo_filename":      "",          # PNG or GIF filename in logos/<cid>/
        "autopilot": {                     # hands-free cadence (autopilot.py)
            "enabled":          False,
            "videos_per_week":  3,
            "days":             ["mon", "wed", "fri"],
            "publish_hour_utc": 14,
            "loop_model":       "kling_v16",   # tier capability map hook
            "fresh_tracks":     5,             # Stable Audio tracks per video
            "song_count":       18,
            "lead_hours":       36,            # veto window before publishAt
        },
        "updated_at":         "",
    }


def _load_raw() -> dict:
    if not _CHANNELS_FILE.exists():
        return {"channels": {}}
    try:
        return json.loads(_CHANNELS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"channels": {}}


def _save_raw(data: dict) -> None:
    tmp = _CHANNELS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(str(tmp), str(_CHANNELS_FILE))
            return
        except OSError:
            if attempt < 4:
                time.sleep(0.05)
            else:
                raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def all_channels() -> list:
    """Return ALL channel dicts (every user) sorted by created_at. Use only for
    server-wide jobs (e.g. the autopilot scheduler); user-facing code must use
    channels_for_user()."""
    data = _load_raw()
    channels = list(data.get("channels", {}).values())
    channels.sort(key=lambda c: c.get("created_at", ""))
    return channels


def channels_for_user(email: str) -> list:
    """Channels owned by this user (the multi-tenant, user-facing list)."""
    if not email:
        return []
    return [c for c in all_channels() if c.get("owner") == email]


def user_owns_channel(cid: str, email: str) -> bool:
    if not cid or not email:
        return False
    c = get_channel(cid)
    return bool(c and c.get("owner") == email)


def backfill_owner(cid: str, email: str) -> None:
    """One-time migration helper: assign an ownerless channel to a user."""
    data = _load_raw()
    c = data.get("channels", {}).get(cid)
    if c is not None and not c.get("owner"):
        c["owner"] = email
        _save_raw(data)


def get_channel(cid: str) -> dict | None:
    """Return a single channel dict, or None if not found."""
    if not cid:
        return None
    data = _load_raw()
    ch = data.get("channels", {}).get(cid)
    if ch is None:
        return None
    # Backfill any keys added since channel was last saved
    defaults = _channel_default()
    for k, v in defaults.items():
        ch.setdefault(k, v)
    return ch


def create_channel(owner: str = "") -> dict:
    """Mint a new blank channel and persist it. Returns the new channel dict."""
    cid = "ch_" + uuid4().hex[:8]
    now = datetime.now().isoformat(timespec="seconds")
    ch = {"id": cid, "created_at": now}
    ch.update(_channel_default())
    ch["owner"] = owner
    data = _load_raw()
    data.setdefault("channels", {})[cid] = ch
    _save_raw(data)
    return ch


def save_channel(cid: str, channel_data: dict) -> None:
    """Persist one channel's data back to channels.json (atomic write)."""
    data = _load_raw()
    data.setdefault("channels", {})
    channel_data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    # Preserve id and created_at
    existing = data["channels"].get(cid, {})
    channel_data["id"] = cid
    channel_data.setdefault("created_at", existing.get("created_at", channel_data["updated_at"]))
    data["channels"][cid] = channel_data
    _save_raw(data)


def is_complete(cid: str) -> bool:
    ch = get_channel(cid)
    return bool(ch and ch.get("completed"))


# ---------------------------------------------------------------------------
# Style refs — scoped per channel
# ---------------------------------------------------------------------------

def style_refs_dir(cid: str) -> Path:
    return _STYLE_REFS_BASE / cid


def save_style_ref(cid: str, file_storage) -> str:
    """
    Save an uploaded image to style_refs/<cid>/ and return the filename.
    file_storage is a Werkzeug FileStorage object.
    """
    from werkzeug.utils import secure_filename
    dest_dir = style_refs_dir(cid)
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = secure_filename(file_storage.filename) or "ref.jpg"
    dest = dest_dir / filename
    counter = 1
    stem = dest.stem
    while dest.exists():
        dest = dest_dir / f"{stem}_{counter}{dest.suffix}"
        counter += 1
    file_storage.save(str(dest))
    return dest.name


def delete_style_ref(cid: str, filename: str) -> None:
    target = style_refs_dir(cid) / Path(filename).name
    if target.exists() and target.is_file():
        target.unlink()


# ---------------------------------------------------------------------------
# Channel logos — scoped per channel
# ---------------------------------------------------------------------------

def logos_dir(cid: str) -> Path:
    return Path(__file__).parent / "logos" / cid


def save_logo(cid: str, file_storage) -> str:
    """Save an uploaded PNG/GIF logo and return the filename."""
    from werkzeug.utils import secure_filename
    dest_dir = logos_dir(cid)
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = secure_filename(file_storage.filename) or "logo.png"
    dest = dest_dir / filename
    file_storage.save(str(dest))
    return dest.name


def delete_logo(cid: str, filename: str) -> None:
    target = logos_dir(cid) / Path(filename).name
    if target.exists() and target.is_file():
        target.unlink()


# ---------------------------------------------------------------------------
# Prompt helpers (ported from channel_profile.py)
# ---------------------------------------------------------------------------

def suggest_titles(cid: str, n: int = 8) -> list:
    """Generate short thumbnail-text suggestions that mirror the channel's language."""
    import re
    ch = get_channel(cid)
    if ch is None:
        return []

    canonical = [
        "Locked In", "Focus Zone", "Deep Focus", "Work Music", "Deep Work",
        "Zero Distractions", "Ultra Focus", "Flow State", "Hyper Focus",
        "Workflow Music", "Focus Mode", "Pure Focus", "Cosmic Chill",
    ]

    titles = [t.get("title", "") for t in ch.get("channel_thumbnails", []) if t.get("title")]
    lowered = " | ".join(titles).lower()

    ranked = [term for term in canonical if term.lower() in lowered]

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


def build_prompt_prefix(cid: str) -> str:
    """
    Build a style-context string to prepend to every fal.ai image prompt.
    Returns empty string if channel is not yet complete.
    """
    from collections import Counter
    import re

    ch = get_channel(cid)
    if not ch or not ch.get("completed"):
        return ""

    parts = []
    if ch.get("scene_notes"):
        parts.append(ch["scene_notes"])
    if ch.get("vibe_tags"):
        parts.append(", ".join(ch["vibe_tags"]))
    if ch.get("color_notes"):
        parts.append(ch["color_notes"])

    if ch.get("channel_thumbnails"):
        skip = {"hour","1","music","focus","beats","lofi","deep","work","study","hours"}
        words = []
        for t in ch["channel_thumbnails"][:8]:
            title = t.get("title", "")
            scene = title.split(" - ")[0].lower()
            words.extend(
                w.strip(".,;:") for w in scene.split()
                if w not in skip and len(w) > 3
            )
        top = [w for w, _ in Counter(words).most_common(4)]
        if top:
            parts.append(", ".join(top))

    return ", ".join(p.strip() for p in parts if p.strip())


# ---------------------------------------------------------------------------
# Migration — channel_profile.json → channels.json (runs once on startup)
# ---------------------------------------------------------------------------

def migrate_from_singleton() -> str | None:
    """
    If channels.json does not yet exist but channel_profile.json does,
    migrate the singleton profile to channels.json as the first channel.

    Also:
      - Copies style_refs/* → style_refs/<cid>/
      - Renames yt_token.json → yt_token_<cid>.json
      - Stamps channel_id on all projects in projects.json

    Returns the new cid on success, or None if migration was already done
    (channels.json already exists).
    """
    if _CHANNELS_FILE.exists():
        return None   # already migrated

    dashboard_dir = Path(__file__).parent
    profile_file  = dashboard_dir / "channel_profile.json"

    # Load existing singleton profile (or use blank defaults)
    if profile_file.exists():
        try:
            profile = json.loads(profile_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            profile = {}
    else:
        profile = {}

    cid = "ch_" + uuid4().hex[:8]
    now = datetime.now().isoformat(timespec="seconds")

    ch = {"id": cid, "created_at": profile.get("updated_at", now)}
    ch.update(_channel_default())
    # Copy all fields from the singleton profile
    for k in ("completed", "channel_name", "channel_url", "channel_id",
               "niche", "vibe_tags", "color_notes", "scene_notes",
               "style_refs", "ref_channels", "channel_thumbnails", "updated_at"):
        if k in profile:
            ch[k] = profile[k]

    # Write channels.json
    _save_raw({"channels": {cid: ch}})
    print(f"[channels] Migrated singleton -> {cid} ({ch.get('channel_name','(unnamed)')})")

    # Copy style_refs/* → style_refs/<cid>/
    old_refs = dashboard_dir / "style_refs"
    if old_refs.is_dir():
        new_refs = style_refs_dir(cid)
        new_refs.mkdir(parents=True, exist_ok=True)
        for f in old_refs.iterdir():
            if f.is_file():
                shutil.copy2(str(f), str(new_refs / f.name))
        print(f"[channels] Copied style refs → style_refs/{cid}/")

    # Rename yt_token.json → yt_token_<cid>.json
    old_token = dashboard_dir / "yt_token.json"
    new_token = dashboard_dir / f"yt_token_{cid}.json"
    if old_token.exists() and not new_token.exists():
        old_token.rename(new_token)
        print(f"[channels] Renamed yt_token.json → yt_token_{cid}.json")

    # Stamp channel_id on all existing projects
    projects_file = dashboard_dir / "projects.json"
    if projects_file.exists():
        try:
            pdata = json.loads(projects_file.read_text(encoding="utf-8"))
            changed = False
            for pid, proj in pdata.get("projects", {}).items():
                if not proj.get("channel_id"):
                    proj["channel_id"] = cid
                    changed = True
            if changed:
                tmp = projects_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(pdata, indent=2, ensure_ascii=False), encoding="utf-8")
                os.replace(str(tmp), str(projects_file))
                print(f"[channels] Stamped channel_id={cid} on existing projects")
        except Exception as e:
            print(f"[channels] Warning: could not stamp projects.json: {e}")

    return cid
