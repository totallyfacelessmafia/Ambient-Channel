"""
state.py — Thread-safe project state backed by projects.json.
"""

import json
import os
import time
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

_PROJECTS_FILE = Path(__file__).parent / "projects.json"
_lock = threading.Lock()


def _empty_store() -> dict:
    return {"projects": {}}


def _load_raw() -> dict:
    if not _PROJECTS_FILE.exists():
        return _empty_store()
    try:
        return json.loads(_PROJECTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_store()


def _save_raw(data: dict) -> None:
    tmp = _PROJECTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    # On Windows, antivirus can briefly lock the file causing WinError 5 (Access Denied).
    # Retry up to 5 times with a short sleep before giving up.
    for attempt in range(5):
        try:
            os.replace(str(tmp), str(_PROJECTS_FILE))
            return
        except OSError:
            if attempt < 4:
                time.sleep(0.05)
            else:
                raise


def _new_project() -> dict:
    pid = uuid4().hex[:8]
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "id":                pid,
        "channel_id":        "",     # set at creation time from session["channel_id"]
        "title":             "",     # set after user selects a title suggestion
        "slug":              pid,    # stable UUID-based prefix for all output files
        "prompt":            "",     # auto-generated from channel profile during step 1
        "generation": {
            "prompt": "",
            "use_channel_style": True,
            "quantity": 5,
        },
        "title_suggestions": [],     # populated at end of step 1 image generation
        "scheduled_date":    None,   # YYYY-MM-DD string; None = unscheduled (backlog)
        "step":              1,
        "status":            "idle",
        "created_at":        now,
        "updated_at":        now,
        "candidate_images":  [],
        "files": {
            "raw_image":   None,
            "background":  None,
            "thumbnail":   None,
            "loop_raw":    None,
            "loop":        None,   # points to loop_a (for backward compat)
            "loop30":      None,   # same
            "loop_a":      None,   # Slot A file (default: Kling v1.6)
            "loop_a_model": None,  # model key that produced slot A
            "loop_b":      None,   # Slot B file (default: Seedance v1 Pro)
            "loop_b_model": None,  # model key that produced slot B
            "final_video": None,
        },
        "thumbnail_config": {
            "text_position": "top",  # top | middle | bottom
        },
        "song_config": {
            "count":         18,
            "crossfade_sec": 2.0,
            "channel_name":  "Ultra Focus Zone",
            "overlay":       True,
        },
        "task": {
            "running":      False,
            "step_running": None,
            "progress_pct": 0,
            "log":          [],
            "error":        None,
        },
        "seo": {
            "title":       "",
            "description": "",
            "tags":        [],
            "generated":   False,
        },
        "youtube": {
            "scheduled_publish_at": None,   # ISO 8601 UTC string, e.g. "2026-03-01T14:00:00Z"
            "upload_status":        "idle", # idle | uploading | done | error
            "video_id":             None,
            "video_url":            None,
            "upload_error":         None,
            "upload_progress_pct":  0,
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def all_projects() -> list[dict]:
    with _lock:
        data = _load_raw()
    projects = list(data["projects"].values())
    projects.sort(key=lambda p: p["created_at"], reverse=True)
    return projects


def projects_for_channel(cid: str) -> list[dict]:
    """Return all projects belonging to the given channel, sorted by created_at desc."""
    with _lock:
        data = _load_raw()
    projects = [p for p in data["projects"].values() if p.get("channel_id") == cid]
    projects.sort(key=lambda p: p["created_at"], reverse=True)
    return projects


def get_project(pid: str) -> dict | None:
    with _lock:
        data = _load_raw()
    return data["projects"].get(pid)


def create_project() -> dict:
    """Create a blank project — title and prompt are set automatically during Step 1."""
    project = _new_project()
    with _lock:
        data = _load_raw()
        data["projects"][project["id"]] = project
        _save_raw(data)
    return project


def update_project(pid: str, **kwargs) -> dict | None:
    """Update top-level fields on a project. Nested dicts are merged."""
    with _lock:
        data = _load_raw()
        project = data["projects"].get(pid)
        if project is None:
            return None
        for key, val in kwargs.items():
            if isinstance(val, dict) and isinstance(project.get(key), dict):
                project[key].update(val)
            else:
                project[key] = val
        project["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_raw(data)
    return project


def append_log(pid: str, line: str) -> None:
    with _lock:
        data = _load_raw()
        project = data["projects"].get(pid)
        if project is None:
            return
        log = project["task"]["log"]
        log.append(line)
        if len(log) > 200:
            project["task"]["log"] = log[-200:]
        project["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_raw(data)


def set_progress(pid: str, pct: int) -> None:
    with _lock:
        data = _load_raw()
        project = data["projects"].get(pid)
        if project is None:
            return
        project["task"]["progress_pct"] = pct
        _save_raw(data)


def delete_project(pid: str) -> bool:
    with _lock:
        data = _load_raw()
        if pid not in data["projects"]:
            return False
        del data["projects"][pid]
        _save_raw(data)
    return True


def recover_crashed_tasks() -> None:
    """On server start, mark any tasks still flagged running as errored."""
    with _lock:
        data = _load_raw()
        changed = False
        for project in data["projects"].values():
            if project["task"].get("running"):
                project["task"]["running"] = False
                project["task"]["step_running"] = None
                project["status"] = "error"
                project["task"]["error"] = "Server restarted during task — please retry."
                project["task"]["log"].append("[--:--:--] Server restarted. Task was interrupted.")
                changed = True
        if changed:
            _save_raw(data)
