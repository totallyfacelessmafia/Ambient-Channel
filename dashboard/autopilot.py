"""
autopilot.py — Hands-free pipeline runner + cadence scheduler.

The auto-runner drives a project through every step with default decisions
(the clicks a human would make), a wall-time watchdog per stage, and a
usage/cost ledger recorded in project state. The scheduler creates project
shells to match each channel's cadence ("N videos per week on these days")
and runs the auto-runner with enough lead time that videos upload as
*private* with a publishAt — giving the user a veto window before anything
goes live.

Stage failure policies (decided 2026-07-17):
  images   -> retry once, then abort
  select   -> abort on hard failure (4K upscale inside already degrades)
  title    -> retry once, then abort
  loop     -> retry once, then degrade to the zero-AI ambient loop
  music    -> continue with the shared pool (recorded as quality warning)
  build    -> abort (local FFmpeg failures aren't transient)
  seo      -> abort
  upload   -> retry once, then abort
Aborts and degradations are recorded via tasks._record_quality and the
project lands in status="error" with the reason — never wedged.
"""

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import channels as ch
import state
import tasks

# ---------------------------------------------------------------------------
# Stage wall-time caps (seconds)
# ---------------------------------------------------------------------------

STAGE_CAPS = {
    "images": 900,
    "select": 900,       # includes the 4K upscale pass
    "title":  300,
    "loop":   1800,
    "music":  3600,      # N tracks at up to ~6 min each
    "build":  5400,      # 1-hour render, CPU worst case
    "seo":    300,
    "upload": 3600,
}

# Estimated cost per stage (USD) for the usage ledger.
LOOP_MODEL_COST = {
    "kling_v16":     0.25,
    "kling_v21":     0.28,
    "seedance_lite": 0.36,
    "hailuo_pro":    0.48,
    "seedance_pro":  1.24,
}
IMAGES_COST      = 0.24   # 4 FLUX Ultra candidates
UPSCALE_COST     = 0.05
MUSIC_TRACK_COST = 0.20   # Stable Audio 2.5 per track

# Only one autopilot run at a time — serializes GPU/CPU load and avoids the
# known stdout-capture race between concurrent project threads.
_runner_lock = threading.Lock()
_current_run: dict = {"pid": None, "stage": None, "started_at": None}


def current_run() -> dict:
    return dict(_current_run)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _mark_running(pid: str, step_label) -> None:
    """Mimic the start_* wrappers so the dashboard shows live progress."""
    state.update_project(
        pid, status="running",
        task={"running": True, "step_running": step_label,
              "progress_pct": 0, "error": None},
    )


def _stage(pid: str, name: str, fn, args: tuple, step_label) -> str:
    """
    Run one pipeline stage under a wall-time cap.
    Returns "ok", "timeout", or "error".
    The _run_* functions manage project status themselves; a timeout leaves
    the stage thread orphaned (daemon) and we take over the state.
    """
    _current_run.update(pid=pid, stage=name)
    _mark_running(pid, step_label)

    err: list = []

    def _target():
        try:
            fn(*args)
        except BaseException as exc:
            err.append(exc)

    t = threading.Thread(target=_target, daemon=True, name=f"autopilot-{name}")
    t.start()
    t.join(STAGE_CAPS[name])

    if t.is_alive():
        state.update_project(
            pid, status="error",
            task={"running": False, "step_running": None,
                  "error": f"Autopilot: stage '{name}' exceeded its "
                           f"{STAGE_CAPS[name]}s time cap."},
        )
        return "timeout"
    if err:
        # _run_* normally catch their own errors; this is a raise from a
        # direct helper call (e.g. ambient fallback).
        state.update_project(
            pid, status="error",
            task={"running": False, "step_running": None, "error": str(err[0])},
        )
        return "error"

    project = state.get_project(pid) or {}
    return "error" if project.get("status") == "error" else "ok"


def _ledger_add(pid: str, stage: str, detail: str, cost: float) -> None:
    project = state.get_project(pid) or {}
    ledger = list(project.get("ledger") or [])
    ledger.append({
        "stage":    stage,
        "detail":   detail,
        "est_cost": round(cost, 2),
        "at":       datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    total = round(sum(e.get("est_cost", 0) for e in ledger), 2)
    state.update_project(pid, ledger=ledger, ledger_total=total)


def _fail(pid: str, msg: str) -> None:
    tasks._log(pid, f"AUTOPILOT ABORT: {msg}")
    state.update_project(
        pid, status="error", autopilot_state="failed",
        task={"running": False, "step_running": None, "error": f"Autopilot: {msg}"},
    )
    try:
        import notifications
        notifications.notify_failed(pid, msg)
    except Exception:
        pass


def _ambient_fallback(pid: str) -> bool:
    """Loop degrade path: zero-AI ambient loop from the background still."""
    import generate_assets as ga
    project = state.get_project(pid) or {}
    bg = project.get("files", {}).get("background")
    if not bg or not Path(bg).exists():
        return False
    slug = project["slug"]
    out = tasks.OUTPUT_DIR / f"{slug}_loop_a.mp4"
    try:
        ga.generate_ambient_loop(Path(bg), out, duration=30)
    except Exception as exc:
        tasks._log(pid, f"Ambient fallback failed too: {exc}")
        return False
    state.update_project(pid, files={"loop_a": str(out), "loop_a_model": "ambient"})
    tasks._record_quality(pid, "ai_loop", False,
                          "AI loop failed — degraded to zero-motion ambient loop")
    return True


# ---------------------------------------------------------------------------
# The auto-runner
# ---------------------------------------------------------------------------

def run_project(pid: str) -> bool:
    """
    Drive one project from wherever it is to uploaded-private-with-publishAt.
    Returns True on full success. Serialized: one autopilot run at a time.
    """
    with _runner_lock:
        try:
            return _run_project_inner(pid)
        finally:
            _current_run.update(pid=None, stage=None, started_at=None)


def _run_project_inner(pid: str) -> bool:
    project = state.get_project(pid)
    if project is None:
        return False
    # "Already running" must not match ourselves: start_run_async registers
    # the autopilot thread under this pid before it starts.
    with tasks._registry_lock:
        existing = tasks._active_threads.get(pid)
    if (existing is not None and existing.is_alive()
            and existing is not threading.current_thread()):
        tasks._log(pid, "AUTOPILOT: project already has a running task — skipping.")
        return False

    _current_run.update(pid=pid, started_at=datetime.now(timezone.utc).isoformat())
    cid  = project.get("channel_id", "") or ""
    chan = ch.get_channel(cid) or {}
    cfg  = dict(chan.get("autopilot") or {})

    # Pre-flight: don't spend money on generation if the video can't be
    # uploaded at the end. This also protects the cadence scheduler.
    import youtube_upload as yu
    if not yu.is_connected(cid):
        _fail(pid, "YouTube is not connected for this channel — connect it "
                   "before autopilot runs (no credits were spent).")
        return False

    state.update_project(pid, autopilot_state="running")
    tasks._log(pid, "AUTOPILOT: starting hands-free run.")

    # ── Stage 1: candidate images (retry once) ──────────────────────────
    project = state.get_project(pid)
    if not project.get("candidate_images") or project.get("step", 1) < 2:
        for attempt in (1, 2):
            if _stage(pid, "images", tasks._run_step1, (pid,), 1) == "ok":
                break
            if attempt == 2:
                _fail(pid, "image generation failed twice")
                return False
            tasks._log(pid, "AUTOPILOT: image generation failed — retrying once.")
        _ledger_add(pid, "images", "4x FLUX Ultra candidates", IMAGES_COST)

        # ── Auto-pick image #1 → 4K master + background ─────────────────
        project = state.get_project(pid)
        candidates = project.get("candidate_images") or []
        if not candidates:
            _fail(pid, "no candidate images produced")
            return False
        if _stage(pid, "select", tasks._run_step1_select, (pid, candidates[0]), 1) != "ok":
            _fail(pid, "image selection / 4K upscale stage failed")
            return False
        _ledger_add(pid, "upscale", "Clarity 4K master", UPSCALE_COST)

        # ── Auto-pick title (rotate through suggestions for variety) ────
        project = state.get_project(pid)
        suggestions = project.get("title_suggestions") or ["Deep Focus"]
        n_existing = len([p for p in state.all_projects()
                          if p.get("channel_id") == cid])
        title = suggestions[n_existing % len(suggestions)]
        if _stage(pid, "title", tasks._run_step1_set_title, (pid, title, "top"), 1) != "ok":
            _fail(pid, "thumbnail generation failed")
            return False
        state.update_project(pid, step=2)

    # ── Stage 2: video loop (retry once, then ambient degrade) ──────────
    project = state.get_project(pid)
    if not project.get("files", {}).get("loop"):
        model = cfg.get("loop_model", "kling_v16")
        import tiers
        owner = chan.get("owner", "")
        if owner and not tiers.model_allowed(owner, model):
            allowed = tiers.tier(owner)["loop_models"]
            model = allowed[-1] if allowed else "kling_v16"
            tasks._log(pid, f"AUTOPILOT: plan doesn't include the configured model — using {model}.")
        ok = False
        for attempt in (1, 2):
            if _stage(pid, "loop", tasks._run_step2_slot, (pid, "a", model), 2) == "ok":
                ok = True
                _ledger_add(pid, "loop", model, LOOP_MODEL_COST.get(model, 0.5))
                break
            tasks._log(pid, f"AUTOPILOT: loop attempt {attempt} failed.")
        if not ok:
            tasks._log(pid, "AUTOPILOT: degrading to ambient loop (zero AI).")
            if not _ambient_fallback(pid):
                _fail(pid, "loop generation and ambient fallback both failed")
                return False
        project = state.get_project(pid)
        chosen = project["files"].get("loop_a")
        state.update_project(pid, step=3, status="idle",
                             files={"loop": chosen, "loop30": chosen,
                                    "loop_chosen_slot": "a"})

    # ── Stage 2b: fresh music (optional; degrade to pool) ───────────────
    # Skipped when the video is already built — new tracks can't get into a
    # finished render, they'd just be spend into the pool.
    fresh = int(cfg.get("fresh_tracks", 5) or 0)
    project = state.get_project(pid)
    if (fresh > 0 and not project.get("autopilot_music_done")
            and not project.get("files", {}).get("final_video")):
        import seo_generator as sg
        prompt = chan.get("music_style") or "ambient, deep, slow, instrumental"
        try:
            prompt = sg.build_music_prompt(state.get_project(pid).get("title") or "Deep Focus")
        except Exception:
            pass
        if _stage(pid, "music", tasks._run_step2b_stable_audio,
                  (pid, prompt, fresh), "2b_stable_audio") == "ok":
            _ledger_add(pid, "music", f"{fresh} fresh Stable Audio tracks",
                        MUSIC_TRACK_COST * fresh)
        else:
            tasks._record_quality(pid, "fresh_music", False,
                                  "fresh music generation failed — using pool tracks only")
            tasks._log(pid, "AUTOPILOT: continuing with pool music.")
        state.update_project(pid, autopilot_music_done=True, status="idle")

    # ── Stage 3: build the video ────────────────────────────────────────
    project = state.get_project(pid)
    if not project.get("files", {}).get("final_video"):
        song_count = int(cfg.get("song_count", 18) or 18)
        logo = chan.get("logo_filename", "")
        logo_path = str(ch.logos_dir(cid) / logo) if (cid and logo) else None
        state.update_project(pid, song_config={
            "count": song_count, "crossfade_sec": 2.0,
            "channel_name": chan.get("channel_name") or "Ultra Focus Zone",
            "overlay_style": chan.get("overlay_style", "default"),
            "logo_path": logo_path,
        })
        if _stage(pid, "build", tasks._run_step3, (pid,), 3) != "ok":
            _fail(pid, "video build failed")
            return False
        _ledger_add(pid, "build", f"{song_count}-track render", 0.0)
    state.update_project(pid, step=4, status="idle")

    # ── Stage 4a: SEO ───────────────────────────────────────────────────
    if _stage(pid, "seo", tasks._run_step4_seo, (pid,), "4seo") != "ok":
        _fail(pid, "SEO generation failed")
        return False

    # ── Publish time: use the shell's slot, else the next sensible one ──
    # Free/no-publish plans: build the video but stop before upload.
    import tiers
    owner = chan.get("owner", "")
    if owner and not tiers.can_publish(owner):
        tasks._log(pid, "AUTOPILOT: your plan doesn't allow publishing — the video "
                        "is built and saved as a draft. Upgrade to publish it.")
        state.update_project(pid, autopilot_state="done")
        return True

    project = state.get_project(pid)
    pub_at = (project.get("youtube") or {}).get("scheduled_publish_at")
    if not pub_at:
        pub_at = _slot_for_project(project, cfg)
        state.update_project(pid, youtube={"scheduled_publish_at": pub_at},
                             scheduled_date=tasks.utc_to_local_date(pub_at))
    tasks._log(pid, f"AUTOPILOT: will publish at {pub_at} (uploads private — "
                    "delete or edit before then to veto).")

    # ── Stage 4b: upload (retry once) ───────────────────────────────────
    for attempt in (1, 2):
        if _stage(pid, "upload", tasks._run_step4_upload, (pid,), "4upload") == "ok":
            break
        if attempt == 2:
            _fail(pid, "upload failed twice")
            return False
        tasks._log(pid, "AUTOPILOT: upload failed — retrying once.")

    state.update_project(pid, autopilot_state="done")
    q = (state.get_project(pid) or {}).get("quality") or {}
    warn_note = f" ({len(q.get('warnings', []))} quality warning(s))" if q.get("warnings") else ""
    tasks._log(pid, f"AUTOPILOT: complete{warn_note}. Video is scheduled.")
    # Tell the owner to review it before it goes live (the veto window).
    try:
        import notifications
        notifications.notify_scheduled(pid)
    except Exception as _nexc:  # never let a notify failure fail the run
        tasks._log(pid, f"(notification skipped: {_nexc})")
    return True


def _slot_for_project(project: dict, cfg: dict) -> str:
    """Publish instant for a shell: its scheduled_date at the cadence hour."""
    hour = int(cfg.get("publish_hour_utc", 14) or 14)
    ds = project.get("scheduled_date")
    if ds:
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").replace(
                hour=hour, minute=0, second=0, tzinfo=timezone.utc)
            if d > datetime.now(timezone.utc):
                return d.isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return tasks.suggest_next_slot()


# ---------------------------------------------------------------------------
# Cadence scheduler
# ---------------------------------------------------------------------------

_DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_scheduler_thread: threading.Thread | None = None


def _upcoming_slots(cfg: dict, horizon_days: int = 8) -> list:
    """Next publish dates (YYYY-MM-DD) implied by the channel's cadence."""
    days = [d for d in (cfg.get("days") or []) if d in _DAY_INDEX]
    if not days:
        days = ["mon", "wed", "fri"]
    wanted = {_DAY_INDEX[d] for d in days}
    out = []
    today = datetime.now(timezone.utc).date()
    for i in range(1, horizon_days + 1):
        d = today + timedelta(days=i)
        if d.weekday() in wanted:
            out.append(d.isoformat())
    return out


def _ensure_shells(cid: str, chan: dict, cfg: dict) -> None:
    """Create scheduled project shells so the cadence always has work queued."""
    existing_dates = {
        p.get("scheduled_date")
        for p in state.all_projects()
        if p.get("channel_id") == cid and p.get("scheduled_date")
    }
    for ds in _upcoming_slots(cfg):
        if ds in existing_dates:
            continue
        project = state.create_project()
        state.update_project(
            project["id"],
            channel_id=cid,
            scheduled_date=ds,
            autopilot_state="queued",
            song_config={"channel_name": chan.get("channel_name") or ""},
            generation={"prompt": "", "use_channel_style": True, "quantity": 4},
        )


def _due_projects(cid: str, cfg: dict) -> list:
    """Shells whose publish slot is within lead_hours and not yet produced."""
    lead = float(cfg.get("lead_hours", 36) or 36)
    now = datetime.now(timezone.utc)
    due = []
    for p in state.all_projects():
        if p.get("channel_id") != cid:
            continue
        if p.get("autopilot_state") in ("running", "done", "failed"):
            continue
        if (p.get("youtube") or {}).get("upload_status") == "done":
            continue
        ds = p.get("scheduled_date")
        if not ds:
            continue
        try:
            slot = datetime.strptime(ds, "%Y-%m-%d").replace(
                hour=int(cfg.get("publish_hour_utc", 14)), tzinfo=timezone.utc)
        except ValueError:
            continue
        if now <= slot <= now + timedelta(hours=lead):
            due.append((slot, p["id"]))
    due.sort()
    return [pid for _, pid in due]


def _scheduler_loop(poll_seconds: int = 600) -> None:
    while True:
        try:
            for chan in ch.all_channels():
                cfg = chan.get("autopilot") or {}
                if not cfg.get("enabled"):
                    continue
                cid = chan["id"]
                _ensure_shells(cid, chan, cfg)
                for pid in _due_projects(cid, cfg):
                    run_project(pid)   # serialized by _runner_lock
        except Exception as exc:
            print(f"[autopilot scheduler] error: {exc}")
        time.sleep(poll_seconds)


def start_scheduler() -> None:
    """Idempotent background scheduler start (called from app boot)."""
    global _scheduler_thread
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        return
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, daemon=True, name="autopilot-scheduler")
    _scheduler_thread.start()


def start_run_async(pid: str) -> bool:
    """Manual 'run this project hands-free now' trigger from the UI."""
    if tasks.is_running(pid) or _current_run.get("pid"):
        return False
    t = threading.Thread(target=run_project, args=(pid,), daemon=True,
                         name=f"autopilot-{pid}")
    tasks._register(pid, t)
    t.start()
    return True
