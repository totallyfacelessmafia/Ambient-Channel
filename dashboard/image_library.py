"""
image_library.py — Standalone image library state.

Tracks batches of AI-generated images that are not yet tied to a project.
Stored in dashboard/image_library.json.
"""

import json
import os
import time
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

_LIBRARY_FILE = Path(__file__).parent / "image_library.json"
_lock = threading.Lock()


def _default() -> dict:
    return {"batches": [], "used_images": [], "generating": False, "generate_error": None}


def load() -> dict:
    if not _LIBRARY_FILE.exists():
        return _default()
    try:
        return json.loads(_LIBRARY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default()


def _save(data: dict) -> None:
    tmp = _LIBRARY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(str(tmp), str(_LIBRARY_FILE))
            return
        except OSError:
            if attempt < 4:
                time.sleep(0.05)
            else:
                raise


def set_generating(generating: bool, error: str = None) -> None:
    with _lock:
        data = load()
        data["generating"] = generating
        data["generate_error"] = error
        _save(data)


def add_batch(prompt: str, image_paths: list) -> dict:
    """Add a completed batch of images and clear the generating flag."""
    entry = {
        "id": uuid4().hex[:8],
        "prompt": prompt,
        "images": [str(p) for p in image_paths],
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _lock:
        data = load()
        data["batches"].insert(0, entry)  # newest first
        data["generating"] = False
        data["generate_error"] = None
        _save(data)
    return entry


def mark_image_used(filename: str) -> None:
    """Move an image from the available pool into the used list."""
    safe_name = Path(filename).name
    with _lock:
        data = load()
        if "used_images" not in data:
            data["used_images"] = []
        if safe_name not in data["used_images"]:
            data["used_images"].append(safe_name)
        # Remove it from active batches
        for batch in data["batches"]:
            batch["images"] = [p for p in batch["images"] if Path(p).name != safe_name]
        data["batches"] = [b for b in data["batches"] if b["images"]]
        _save(data)


def delete_image(filename: str, output_dir: Path) -> None:
    """Remove an image from all batches (and used list) and delete the file from disk."""
    safe_name = Path(filename).name  # strip any path traversal
    with _lock:
        data = load()
        for batch in data["batches"]:
            batch["images"] = [p for p in batch["images"] if Path(p).name != safe_name]
        data["batches"] = [b for b in data["batches"] if b["images"]]
        if "used_images" in data:
            data["used_images"] = [p for p in data["used_images"] if Path(p).name != safe_name]
        _save(data)
    target = output_dir / safe_name
    if target.exists() and target.is_file():
        target.unlink()
