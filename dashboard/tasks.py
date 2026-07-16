"""
tasks.py — Background task runners for each pipeline step.

Each run_stepN function is executed in its own daemon thread so the
Flask server stays responsive during long-running operations.
"""

import io
import random
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import state

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DASHBOARD_DIR    = Path(__file__).parent
ROOT             = DASHBOARD_DIR.parent
AUTOMATION_DIR   = ROOT / "UltraFocusZone_Automation"
MUSIC_DIR        = ROOT / "music"
OUTPUT_DIR       = ROOT / "images" / "generated"
VIDEOS_DIR       = ROOT / "Edited Videos"

# Inject the automation scripts into sys.path so we can import them directly
if str(AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOMATION_DIR))

# ---------------------------------------------------------------------------
# Thread registry (prevents double-starts)
# ---------------------------------------------------------------------------

_active_threads: dict[str, threading.Thread] = {}
_registry_lock  = threading.Lock()


def is_running(pid: str) -> bool:
    with _registry_lock:
        t = _active_threads.get(pid)
        return t is not None and t.is_alive()


def _register(pid: str, thread: threading.Thread) -> None:
    with _registry_lock:
        _active_threads[pid] = thread


# ---------------------------------------------------------------------------
# Log capture — redirects stdout inside a task thread to the project log
# ---------------------------------------------------------------------------

class _LogCapture(io.StringIO):
    def __init__(self, pid: str, original_stdout):
        super().__init__()
        self._pid = pid
        self._orig = original_stdout

    def write(self, text: str) -> int:
        if text.strip():
            ts = datetime.now().strftime("%H:%M:%S")
            state.append_log(self._pid, f"[{ts}] {text.rstrip()}")
        return len(text)

    def flush(self):
        pass


def _log(pid: str, msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    state.append_log(pid, f"[{ts}] {msg}")


def _record_quality(pid: str, key: str, ok: bool, warning: str | None = None) -> None:
    """
    Merge one quality flag into project state, keeping warnings from other
    stages intact (state.update_project's dict merge is shallow, so writing
    `warnings` directly would clobber the other stages' entries).
    """
    project = state.get_project(pid) or {}
    q = dict(project.get("quality") or {})
    warns = [w for w in q.get("warnings", []) if not w.startswith(f"[{key}]")]
    if not ok and warning:
        warns.append(f"[{key}] {warning}")
    q[key] = ok
    q["warnings"] = warns
    state.update_project(pid, quality=q)


# ---------------------------------------------------------------------------
# Step 1a — Generate 4 candidate images + title suggestions
# ---------------------------------------------------------------------------

def _run_step1(pid: str) -> None:
    orig_stdout = sys.stdout
    sys.stdout = _LogCapture(pid, orig_stdout)
    try:
        import generate_assets as ga
        import channel_profile as cp
        import channels as ch

        project = state.get_project(pid)
        if project is None:
            return

        slug = project["slug"]
        cid  = project.get("channel_id", "")
        generation = project.get("generation", {})
        manual_prompt = str(generation.get("prompt", "")).strip()
        use_channel_style = generation.get("use_channel_style", True)

        # Respect manual prompt first; otherwise auto-build based on style toggle.
        prompt = manual_prompt
        if not prompt and use_channel_style:
            try:
                import youtube_api as ya
                style = ya.build_style_prompt()
                prompt = style.get("prompt", "")
            except Exception:
                prompt = ""

        if not prompt:
            if use_channel_style:
                prefix = ch.build_prompt_prefix(cid) if cid else cp.build_prompt_prefix()
                prompt = (prefix + ", " if prefix else "") + \
                         "warm ambient lighting, cinematic architectural photography, ultra realistic"
            else:
                prompt = "ambient cinematic focus workspace, atmospheric lighting, ultra realistic"

        state.update_project(pid, prompt=prompt)
        _log(pid, f"Scene prompt: {prompt[:120]}...")
        state.set_progress(pid, 5)

        # ── Generate 4 candidate images (model limit) ───────────────────
        _log(pid, "Generating 4 candidate images via fal.ai FLUX Pro…")
        paths = ga.generate_ai_images(prompt, OUTPUT_DIR, slug, count=4)
        state.set_progress(pid, 85)

        # Detect the FLUX Ultra→Pro fallback — reduced-resolution candidates
        # are the same class of silent degradation as an upscale failure.
        try:
            from PIL import Image as _PILImage
            with _PILImage.open(paths[0]) as _im:
                _w = _im.width
            if _w < 2000:
                _log(pid, f"WARNING: FLUX Ultra fallback — candidates at reduced resolution ({_w}px wide).")
            _record_quality(pid, "flux_ultra", _w >= 2000,
                            f"FLUX Ultra fallback — candidates at reduced resolution ({_w}px wide)")
        except Exception:
            pass

        # Save all candidates to the Image Library so nothing is wasted
        try:
            import image_library as il
            il.add_batch(prompt, paths)
        except Exception as _ile:
            _log(pid, f"(Image Library save skipped: {_ile})")

        # ── Generate title suggestions from channel profile ─────────────
        _log(pid, "Building title suggestions from channel profile…")
        suggestions  = ch.suggest_titles(cid, n=12) if cid else cp.suggest_titles(cp.load(), n=12)
        _log(pid, f"Suggestions: {', '.join(suggestions)}")

        state.set_progress(pid, 100)
        state.update_project(
            pid,
            candidate_images=[str(p) for p in paths],
            title_suggestions=suggestions,
            status="idle",
            task={"running": False, "step_running": None, "error": None},
        )
        _log(pid, f"Generated {len(paths)} images. Pick one to continue.")

    except Exception as exc:
        _log(pid, f"ERROR: {exc}")
        state.update_project(
            pid,
            status="error",
            task={"running": False, "step_running": None, "error": str(exc)},
        )
    finally:
        sys.stdout = orig_stdout


def start_step1(pid: str) -> bool:
    if is_running(pid):
        return False
    state.update_project(
        pid,
        status="running",
        candidate_images=[],
        title_suggestions=[],
        task={"running": True, "step_running": 1, "progress_pct": 0, "log": [], "error": None},
    )
    t = threading.Thread(target=_run_step1, args=(pid,), daemon=True)
    _register(pid, t)
    t.start()
    return True


# ---------------------------------------------------------------------------
# Step 1b — User selected an image; generate background only (no thumbnail yet)
# ---------------------------------------------------------------------------

def _run_step1_select(pid: str, chosen_path: str) -> None:
    orig_stdout = sys.stdout
    sys.stdout = _LogCapture(pid, orig_stdout)
    try:
        import generate_assets as ga
        import channel_profile as cp
        import channels as ch
        import shutil

        project = state.get_project(pid)
        if project is None:
            return

        slug = project["slug"]
        cid  = project.get("channel_id", "")
        source = Path(chosen_path)

        raw_path = OUTPUT_DIR / f"{slug}_raw.jpg"
        bg_path  = OUTPUT_DIR / f"{slug}_background.png"

        shutil.copy2(source, raw_path)

        state.set_progress(pid, 15)
        _log(pid, "Upscaling selected image to a 4K master (AI detail pass)...")
        try:
            ga.upscale_image(raw_path)
            _record_quality(pid, "upscaled_4k", True)
        except Exception as ue:
            # Recorded in project state — the task log is transient, and
            # autopilot's veto notification must flag reduced-quality videos.
            warn = f"4K upscale failed — assets use original resolution ({ue})"
            _log(pid, f"WARNING: {warn}")
            _record_quality(pid, "upscaled_4k", False, warn)

        state.set_progress(pid, 40)
        _log(pid, "Creating 1920x1080 background from the 4K master...")
        ga.generate_background(raw_path, bg_path)

        state.set_progress(pid, 80)
        _log(pid, "Building title suggestions from channel profile...")
        suggestions = ch.suggest_titles(cid, n=12) if cid else cp.suggest_titles(cp.load(), n=12)
        _log(pid, f"Suggestions: {', '.join(suggestions)}")

        state.set_progress(pid, 100)
        state.update_project(
            pid,
            status="idle",
            candidate_images=[str(raw_path)],
            title_suggestions=suggestions,
            files={
                "raw_image":  str(raw_path),
                "background": str(bg_path),
                "thumbnail":  None,   # generated after title is chosen
            },
            task={"running": False, "step_running": None, "error": None},
        )
        _log(pid, "Background ready. Pick a title to continue.")

    except Exception as exc:
        _log(pid, f"ERROR: {exc}")
        state.update_project(
            pid,
            status="error",
            task={"running": False, "step_running": None, "error": str(exc)},
        )
    finally:
        sys.stdout = orig_stdout


def start_step1_select(pid: str, chosen_path: str) -> bool:
    if is_running(pid):
        return False
    state.update_project(
        pid,
        status="running",
        task={"running": True, "step_running": 1, "progress_pct": 0, "log": [], "error": None},
    )
    t = threading.Thread(target=_run_step1_select, args=(pid, chosen_path), daemon=True)
    _register(pid, t)
    t.start()
    return True


# ---------------------------------------------------------------------------
# Step 1c — User picked a title; generate thumbnail with that title
# ---------------------------------------------------------------------------

def _run_step1_set_title(pid: str, title: str, text_position: str = "top") -> None:
    orig_stdout = sys.stdout
    sys.stdout = _LogCapture(pid, orig_stdout)
    try:
        import generate_assets as ga

        project = state.get_project(pid)
        if project is None:
            return

        slug     = project["slug"]
        raw_path = Path(project["files"]["raw_image"])
        th_path  = OUTPUT_DIR / f"{slug}_thumbnail.jpg"

        state.set_progress(pid, 30)
        _log(pid, f"Generating thumbnail with title: {title} (position: {text_position})")
        ga.generate_thumbnail(raw_path, title, th_path, text_position=text_position)

        state.set_progress(pid, 100)
        state.update_project(
            pid,
            title=title,
            status="idle",
            files={"thumbnail": str(th_path)},
            thumbnail_config={"text_position": text_position},
            task={"running": False, "step_running": None, "error": None},
        )
        _log(pid, "Thumbnail ready. Approve to continue to Step 2.")

    except Exception as exc:
        _log(pid, f"ERROR: {exc}")
        state.update_project(
            pid,
            status="error",
            task={"running": False, "step_running": None, "error": str(exc)},
        )
    finally:
        sys.stdout = orig_stdout


def start_step1_set_title(pid: str, title: str, text_position: str = "top") -> bool:
    if is_running(pid):
        return False
    state.update_project(
        pid,
        status="running",
        task={"running": True, "step_running": 1, "progress_pct": 0, "log": [], "error": None},
    )
    t = threading.Thread(target=_run_step1_set_title, args=(pid, title, text_position), daemon=True)
    _register(pid, t)
    t.start()
    return True


# ---------------------------------------------------------------------------
# Step 2 — AI image-to-video animation (Kling, Seedance, Hailuo, …)
# ---------------------------------------------------------------------------

# Valid model keys — must match ga.AI_VIDEO_MODELS
_AI_VIDEO_MODELS = {"kling_v16", "kling_v21", "seedance_lite", "seedance_pro", "hailuo_pro"}

# Default models for the two comparison slots.
# Kling (v1.6 / v2.1) has a strong learned prior toward time-lapse on architectural/
# ocean scenes that cannot be suppressed reliably through prompting alone — it ignores
# even cfg_scale=1.0 + exhaustive negative prompts. Both Seedance models have a hard
# camera_fixed=True API param and the end_image_url loop trick, making them far more
# consistent. Kling is still selectable from the dashboard dropdown if desired.
_SLOT_DEFAULTS = {"a": "seedance_lite", "b": "seedance_pro"}


def _gen_slot(pid: str, slot: str, model: str, bg_path: Path, slug: str) -> bool:
    """
    Generate one loop slot in-place.  Returns True on success.
    Logs errors but does NOT update overall task state — caller decides.
    """
    import generate_assets as ga

    cfg   = ga.AI_VIDEO_MODELS.get(model, {})
    label = cfg.get("label", model)
    loop_file = OUTPUT_DIR / f"{slug}_loop_{slot}.mp4"

    _log(pid, f"Slot {slot.upper()} — {label}…")
    try:
        ga.generate_animated_loop_ai(source=bg_path, output=loop_file, model=model)
    except Exception as e:
        _log(pid, f"Slot {slot.upper()} ERROR: {e}")
        state.update_project(pid, files={
            f"loop_{slot}":       None,
            f"loop_{slot}_model": model,
        })
        return False

    files_update = {
        f"loop_{slot}":       str(loop_file) if loop_file.exists() else None,
        f"loop_{slot}_model": model,
    }
    if slot == "a":   # keep legacy loop/loop30 pointing at slot A
        files_update["loop"]  = files_update["loop_a"]
        files_update["loop30"] = files_update["loop_a"]
    state.update_project(pid, files=files_update)
    _log(pid, f"Slot {slot.upper()} — {label} done ✓")
    return True


def _run_step2_dual(pid: str) -> None:
    """Generate Slot A (Kling v1.6) then Slot B (Seedance Pro) sequentially."""
    orig_stdout = sys.stdout
    sys.stdout = _LogCapture(pid, orig_stdout)
    try:
        project = state.get_project(pid)
        if project is None:
            return

        slug    = project["slug"]
        bg_path = Path(project["files"]["background"])

        state.set_progress(pid, 5)
        ok_a = _gen_slot(pid, "a", "kling_v16",    bg_path, slug)
        state.set_progress(pid, 55)
        ok_b = _gen_slot(pid, "b", "seedance_pro", bg_path, slug)
        state.set_progress(pid, 100)

        if not ok_a and not ok_b:
            state.update_project(
                pid, status="error",
                task={"running": False, "step_running": None,
                      "error": "Both slots failed — check logs above."},
            )
        else:
            state.update_project(
                pid, status="idle",
                task={"running": False, "step_running": None, "error": None},
            )
            _log(pid, "Both slots complete. Preview and approve to continue.")

    except Exception as exc:
        _log(pid, f"ERROR: {exc}")
        state.update_project(
            pid, status="error",
            task={"running": False, "step_running": None, "error": str(exc)},
        )
    finally:
        sys.stdout = orig_stdout


def _run_step2_slot(pid: str, slot: str, model: str) -> None:
    """Regenerate a single comparison slot with the chosen model."""
    orig_stdout = sys.stdout
    sys.stdout = _LogCapture(pid, orig_stdout)
    try:
        project = state.get_project(pid)
        if project is None:
            return

        slug    = project["slug"]
        bg_path = Path(project["files"]["background"])

        state.set_progress(pid, 5)
        ok = _gen_slot(pid, slot, model, bg_path, slug)
        state.set_progress(pid, 100)

        if ok:
            state.update_project(
                pid, status="idle",
                task={"running": False, "step_running": None, "error": None},
            )
        else:
            state.update_project(
                pid, status="error",
                task={"running": False, "step_running": None,
                      "error": f"Slot {slot.upper()} generation failed — check logs."},
            )

    except Exception as exc:
        _log(pid, f"ERROR: {exc}")
        state.update_project(
            pid, status="error",
            task={"running": False, "step_running": None, "error": str(exc)},
        )
    finally:
        sys.stdout = orig_stdout


def start_step2(pid: str) -> bool:
    """Generate both comparison loops (Kling v1.6 + Seedance Pro) sequentially."""
    if is_running(pid):
        return False
    state.update_project(
        pid, status="running",
        task={"running": True, "step_running": 2, "progress_pct": 0, "log": [], "error": None},
    )
    t = threading.Thread(target=_run_step2_dual, args=(pid,), daemon=True)
    _register(pid, t)
    t.start()
    return True


def start_step2_slot(pid: str, slot: str, model: str) -> bool:
    """Regenerate a single slot with the chosen model."""
    if slot not in ("a", "b"):
        return False
    if is_running(pid):
        return False
    state.update_project(
        pid, status="running",
        task={"running": True, "step_running": f"2_slot_{slot}", "progress_pct": 0, "log": [], "error": None},
    )
    t = threading.Thread(target=_run_step2_slot, args=(pid, slot, model), daemon=True)
    _register(pid, t)
    t.start()
    return True


# ---------------------------------------------------------------------------
# Step 2b — Suno music generation (optional, between Step 2 approval and Step 3)
# ---------------------------------------------------------------------------

def _run_step2b(pid: str, suno_prompt: str) -> None:
    """
    Launch Suno Chrome bot to generate music into the music/ folder.
    Chrome opens visibly — Suno detects headless browsers.
    """
    orig_stdout = sys.stdout
    sys.stdout = _LogCapture(pid, orig_stdout)
    try:
        from suno_automation import SunoAutomation

        _log(pid, f"Starting Suno music generation: '{suno_prompt}'")
        _log(pid, "Chrome will open on your desktop. Do not close it.")
        _log(pid, "This takes 10–30 minutes depending on track count.")
        state.set_progress(pid, 5)

        MUSIC_DIR.mkdir(parents=True, exist_ok=True)

        sa = SunoAutomation(headless=False, download_dir=str(MUSIC_DIR))
        outputs = []
        try:
            if not sa.setup_driver():
                raise RuntimeError("Suno Chrome driver setup failed.")
            sa.login()
            state.set_progress(pid, 15)
            _log(pid, "Generating tracks (9 clicks × 2 tracks = ~18 songs)...")
            outputs = sa.generate(suno_prompt, repeats=9, per_click=2)
            if not outputs:
                raise RuntimeError("Suno returned no tracks. Check the Chrome window.")
        finally:
            sa.close()

        state.set_progress(pid, 100)
        state.update_project(
            pid,
            status="idle",
            task={
                "running":      False,
                "step_running": None,
                "error":        None,
                "suno_tracks":  len(outputs),
            },
        )
        _log(pid, f"Suno done. {len(outputs)} tracks saved to music/ folder.")

    except Exception as exc:
        _log(pid, f"ERROR: {exc}")
        state.update_project(
            pid,
            status="error",
            task={"running": False, "step_running": None, "error": str(exc)},
        )
    finally:
        sys.stdout = orig_stdout


def _run_step2b_beatoven(pid: str, prompt: str, count: int) -> None:
    """Generate `count` ambient tracks via Beatoven (fal.ai) and save as MP3s."""
    import random
    import generate_assets as ga

    orig_stdout = sys.stdout
    sys.stdout = _LogCapture(pid, orig_stdout)
    try:
        _log(pid, f"Beatoven: generating {count} tracks (30s each)...")
        _log(pid, f"Style: {prompt}")
        MUSIC_DIR.mkdir(parents=True, exist_ok=True)

        saved = 0
        for i in range(1, count + 1):
            seed = random.randint(1, 999999)
            out_mp3 = MUSIC_DIR / f"beatoven_{i:02d}_{seed}.mp3"
            _log(pid, f"Track {i}/{count}…")
            try:
                ga.generate_music_beatoven(
                    output_mp3=out_mp3,
                    prompt=prompt,
                    duration=30.0,
                    seed=seed,
                )
                saved += 1
            except Exception as e:
                _log(pid, f"  Track {i} failed: {e}")
            state.set_progress(pid, int(i / count * 100))

        if saved == 0:
            raise RuntimeError("No tracks were generated. Check fal.ai balance and logs.")

        state.update_project(
            pid, status="idle",
            task={"running": False, "step_running": None, "error": None,
                  "beatoven_tracks": saved},
        )
        _log(pid, f"Beatoven done. {saved}/{count} tracks saved to music/ folder.")

    except Exception as exc:
        _log(pid, f"ERROR: {exc}")
        state.update_project(
            pid, status="error",
            task={"running": False, "step_running": None, "error": str(exc)},
        )
    finally:
        sys.stdout = orig_stdout


def start_step2b(pid: str, suno_prompt: str) -> bool:
    if is_running(pid):
        return False
    state.update_project(
        pid,
        status="running",
        task={"running": True, "step_running": "2b", "progress_pct": 0, "log": [], "error": None},
    )
    t = threading.Thread(target=_run_step2b, args=(pid, suno_prompt), daemon=True)
    _register(pid, t)
    t.start()
    return True


def start_step2b_beatoven(pid: str, prompt: str, count: int = 18) -> bool:
    if is_running(pid):
        return False
    state.update_project(
        pid, status="running",
        task={"running": True, "step_running": "2b_beatoven", "progress_pct": 0, "log": [], "error": None},
    )
    t = threading.Thread(target=_run_step2b_beatoven, args=(pid, prompt, count), daemon=True)
    _register(pid, t)
    t.start()
    return True


def _run_step2b_stable_audio(pid: str, prompt: str, count: int) -> None:
    """Generate `count` ambient tracks via fal.ai Stable Audio (3 min each) and save as MP3s."""
    import random
    import generate_assets as ga

    orig_stdout = sys.stdout
    sys.stdout = _LogCapture(pid, orig_stdout)
    try:
        _log(pid, f"Stable Audio: generating {count} tracks (~3 min each)...")
        _log(pid, f"Style: {prompt}")
        MUSIC_DIR.mkdir(parents=True, exist_ok=True)

        saved = 0
        for i in range(1, count + 1):
            seed = random.randint(1, 999999)
            out_mp3 = MUSIC_DIR / f"stable_{i:02d}_{seed}.mp3"
            _log(pid, f"Track {i}/{count}…")
            try:
                ga.generate_music_stable_audio(
                    output_mp3=out_mp3,
                    prompt=prompt,
                    duration=180.0,
                    seed=seed,
                )
                saved += 1
            except Exception as e:
                _log(pid, f"  Track {i} failed: {e}")
            state.set_progress(pid, int(i / count * 100))

        if saved == 0:
            raise RuntimeError("No tracks were generated. Check fal.ai balance and logs.")

        state.update_project(
            pid, status="idle",
            task={"running": False, "step_running": None, "error": None,
                  "stable_audio_tracks": saved},
        )
        _log(pid, f"Stable Audio done. {saved}/{count} tracks (~3 min each) saved to music/ folder.")

    except Exception as exc:
        _log(pid, f"ERROR: {exc}")
        state.update_project(
            pid, status="error",
            task={"running": False, "step_running": None, "error": str(exc)},
        )
    finally:
        sys.stdout = orig_stdout


def start_step2b_stable_audio(pid: str, prompt: str, count: int = 18) -> bool:
    if is_running(pid):
        return False
    state.update_project(
        pid, status="running",
        task={"running": True, "step_running": "2b_stable_audio", "progress_pct": 0, "log": [], "error": None},
    )
    t = threading.Thread(target=_run_step2b_stable_audio, args=(pid, prompt, count), daemon=True)
    _register(pid, t)
    t.start()
    return True


# ---------------------------------------------------------------------------
# FFmpeg progress helper — parses stderr for real-time progress bar updates
# ---------------------------------------------------------------------------

_TIME_PAT = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")


def _ffmpeg_with_progress(
    cmd: list,
    pid: str,
    total_seconds: float,
    base_pct: int = 50,
    end_pct: int = 95,
    cwd: str = None,
) -> None:
    """
    Run an FFmpeg command and update project progress from stderr time= output.
    base_pct → end_pct defines the percentage range this call occupies.
    Raises RuntimeError on non-zero exit code.
    """
    proc = subprocess.Popen(
        cmd,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
        cwd=cwd,
        encoding="utf-8",
        errors="replace",
    )
    for line in proc.stderr:
        line = line.strip()
        if not line:
            continue
        if not line.startswith("frame=") and not line.startswith("size="):
            _log(pid, line[:300])
        m = _TIME_PAT.search(line)
        if m and total_seconds > 0:
            h  = float(m.group(1))
            mn = float(m.group(2))
            s  = float(m.group(3))
            elapsed  = h * 3600 + mn * 60 + s
            fraction = min(elapsed / total_seconds, 1.0)
            pct = int(base_pct + fraction * (end_pct - base_pct))
            state.set_progress(pid, pct)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg exited with code {proc.returncode}")


# ---------------------------------------------------------------------------
# Step 3 — Build 1-hour video
# ---------------------------------------------------------------------------

def _run_step3(pid: str) -> None:
    import tempfile

    orig_stdout = sys.stdout
    sys.stdout = _LogCapture(pid, orig_stdout)
    try:
        import build_video as bv

        project     = state.get_project(pid)
        if project is None:
            return

        slug        = project["slug"]
        # Prefer the approved AI-animated loop (chosen in Step 2) as the background.
        # build_assemble_cmd auto-detects image vs video and applies -stream_loop -1
        # for video files so the clip loops seamlessly for the full hour.
        # Fall back to the static background image if no loop was generated.
        _loop_video = project["files"].get("loop")
        _bg_frame = (project["files"].get("bg_frame") or
                     project["files"].get("background") or
                     project["files"].get("raw_image"))
        _bg_source = _loop_video or _bg_frame
        if not _bg_source:
            raise RuntimeError("No background found. Complete Step 1 (and optionally Step 2) before running Step 3.")
        if _loop_video:
            _log(pid, f"Using approved animated loop: {Path(_loop_video).name}")
        else:
            _log(pid, f"No animated loop found — using static background image.")
        loop_path   = Path(_bg_source)
        song_cfg    = project["song_config"]
        song_count     = int(song_cfg.get("count", 18))
        crossfade      = float(song_cfg.get("crossfade_sec", 2.0))
        channel        = song_cfg.get("channel_name", "Ultra Focus Zone")
        overlay_style  = song_cfg.get("overlay_style", "default")   # "default"|"minimal"|"none"
        logo_path_str  = song_cfg.get("logo_path", None)
        logo_path_val  = Path(logo_path_str) if logo_path_str and Path(logo_path_str).exists() else None
        no_overlay     = (overlay_style == "none")

        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        final_out = VIDEOS_DIR / f"{slug}_1hr.mp4"

        mp3_files = list(MUSIC_DIR.glob("*.mp3"))
        random.shuffle(mp3_files)
        if song_count > 0:
            mp3_files = mp3_files[:song_count]

        # Record the actual playlist — SEO chapters must reflect THIS video's
        # shuffle, not an alphabetical guess at the shared pool.
        state.update_project(pid, song_config={"songs": [str(p) for p in mp3_files]})

        _log(pid, f"Using {len(mp3_files)} tracks from {MUSIC_DIR.name}/")
        _log(pid, f"Output: {final_out.name}")

        state.set_progress(pid, 5)

        _log(pid, "Merging audio tracks...")
        with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
            master_audio = Path(tmp.name)

        panel_png_path = None
        try:
            bv.merge_audio(mp3_files, master_audio, crossfade)
            state.set_progress(pid, 40)

            overlay_filter = None
            if not no_overlay:
                _log(pid, "Computing Now Playing timings...")
                timings = bv.get_song_timings(mp3_files, crossfade)
                overlay_filter = bv.build_overlay_filter(timings, channel)

                if overlay_style != "minimal":
                    _, _ptmp = tempfile.mkstemp(suffix=".png")
                    panel_png_path = Path(_ptmp)
                    bv.generate_panel_png(panel_png_path)

            state.set_progress(pid, 50)
            _log(pid, "Encoding final video (this takes ~10–15 minutes)...")

            total_seconds = bv.ffprobe_duration(str(master_audio))

            if hasattr(bv, "build_assemble_cmd"):
                ffmpeg_cmd = bv.build_assemble_cmd(
                    loop_path, master_audio, final_out, overlay_filter, panel_png_path
                )
                _ffmpeg_with_progress(
                    ffmpeg_cmd, pid, total_seconds,
                    base_pct=50, end_pct=95,
                    cwd=str(AUTOMATION_DIR),
                )
            else:
                bv.assemble_video(loop_path, master_audio, final_out, overlay_filter, panel_png_path, logo_path_val)

            state.set_progress(pid, 100)
            state.update_project(
                pid,
                status="done",
                files={"final_video": str(final_out)},
                task={"running": False, "step_running": None, "error": None},
            )
            _log(pid, f"Done! Final video: {final_out.name}")

        finally:
            master_audio.unlink(missing_ok=True)
            if panel_png_path is not None:
                try:
                    panel_png_path.unlink(missing_ok=True)
                except PermissionError:
                    pass

    except Exception as exc:
        _log(pid, f"ERROR: {exc}")
        state.update_project(
            pid,
            status="error",
            task={"running": False, "step_running": None, "error": str(exc)},
        )
    finally:
        sys.stdout = orig_stdout


def start_step3(pid: str, song_count: int, crossfade_sec: float,
                channel_name: str, overlay_style: str = "default",
                logo_path: str | None = None) -> bool:
    if is_running(pid):
        return False
    state.update_project(
        pid,
        song_config={
            "count": song_count,
            "crossfade_sec": crossfade_sec,
            "channel_name": channel_name,
            "overlay_style": overlay_style,
            "logo_path": logo_path,
        },
        status="running",
        task={"running": True, "step_running": 3, "progress_pct": 0, "log": [], "error": None},
    )
    t = threading.Thread(target=_run_step3, args=(pid,), daemon=True)
    _register(pid, t)
    t.start()
    return True


# ---------------------------------------------------------------------------
# Step 4a — Generate SEO metadata (runs in background thread)
# ---------------------------------------------------------------------------

def _run_step4_seo(pid: str) -> None:
    orig_stdout = sys.stdout
    sys.stdout = _LogCapture(pid, orig_stdout)
    try:
        import seo_generator as sg
        import channel_profile as cp
        import channels as ch

        project = state.get_project(pid)
        if project is None:
            return

        _log(pid, "Generating SEO metadata from channel profile…")
        cid     = project.get("channel_id", "")
        profile = ch.get_channel(cid) if cid else cp.load()
        seo     = sg.generate_seo(project, profile)

        state.set_progress(pid, 100)
        state.update_project(
            pid,
            status="idle",
            seo={
                "title":       seo["title"],
                "description": seo["description"],
                "tags":        seo["tags"],
                "generated":   True,
            },
            task={"running": False, "step_running": None, "error": None},
        )
        _log(pid, f"SEO ready: {seo['title'][:60]}…")

    except Exception as exc:
        _log(pid, f"ERROR: {exc}")
        state.update_project(
            pid,
            status="error",
            task={"running": False, "step_running": None, "error": str(exc)},
        )
    finally:
        sys.stdout = orig_stdout


def start_step4_seo(pid: str) -> bool:
    if is_running(pid):
        return False
    state.update_project(
        pid,
        status="running",
        task={"running": True, "step_running": "4seo", "progress_pct": 0, "log": [], "error": None},
    )
    t = threading.Thread(target=_run_step4_seo, args=(pid,), daemon=True)
    _register(pid, t)
    t.start()
    return True


# ---------------------------------------------------------------------------
# Step 4b — Upload to YouTube
# ---------------------------------------------------------------------------

def _run_step4_upload(pid: str) -> None:
    orig_stdout = sys.stdout
    sys.stdout = _LogCapture(pid, orig_stdout)
    try:
        import youtube_upload as yu

        project = state.get_project(pid)
        if project is None:
            return

        final_video = project["files"].get("final_video")
        if not final_video or not Path(final_video).exists():
            raise RuntimeError("Final video file not found. Complete Step 3 first.")

        seo         = project.get("seo", {})
        yt          = project.get("youtube", {})
        cid         = project.get("channel_id", "") or ""
        title       = seo.get("title") or project.get("title") or "Deep Focus Music"
        description = seo.get("description", "")
        tags        = seo.get("tags", [])
        publish_at  = yt.get("scheduled_publish_at") or None
        thumbnail   = project["files"].get("thumbnail")

        _log(pid, f"Uploading to YouTube: {title[:60]}…")
        _log(pid, f"Requested channel credentials: {cid or 'default'}")
        if publish_at:
            _log(pid, f"Scheduled for: {publish_at}")
        else:
            _log(pid, "No publish time set — video will go public immediately.")
        if thumbnail and Path(thumbnail).exists():
            _log(pid, f"Thumbnail: {Path(thumbnail).name}")
        else:
            _log(pid, "No thumbnail file found — skipping thumbnail upload.")
        state.set_progress(pid, 5)

        # Update upload status before starting
        state.update_project(pid, youtube={"upload_status": "uploading", "upload_error": None})

        def _progress_cb(sent, total):
            if total and total > 0:
                pct = int(5 + 90 * sent / total)
                state.set_progress(pid, min(pct, 95))

        for _i, _t in enumerate(tags):
            _bad = [c for c in str(_t) if c in '<>"&' or ord(c) > 127]
            _log(pid, f"  tag[{_i}] {repr(_t)}" + (f" BAD:{_bad}" if _bad else ""))
        _log(pid, f"Tags total chars: {sum(len(str(t)) for t in tags)}")
        result = yu.upload_video(
            video_path=final_video,
            title=title,
            description=description,
            tags=tags,
            publish_at=publish_at,
            thumbnail_path=thumbnail,
            progress_callback=_progress_cb,
            cid=cid,
        )

        if result["ok"]:
            if result.get("warning"):
                _log(pid, f"WARNING: {result['warning']}")
            state.set_progress(pid, 100)

            # Sync calendar date from YouTube's confirmed publishAt
            # (takes priority over whatever was stored locally)
            confirmed_pub = result.get("publish_at") or ""
            if confirmed_pub:
                cal_date = utc_to_local_date(confirmed_pub)
            else:
                project = state.get_project(pid)
                pub_at = (project or {}).get("youtube", {}).get("scheduled_publish_at") or ""
                cal_date = utc_to_local_date(pub_at) if pub_at else datetime.now().strftime("%Y-%m-%d")

            update_kwargs = dict(
                step=4,
                status="done",
                scheduled_date=cal_date,
                youtube={
                    "upload_status": "done",
                    "video_id":      result["video_id"],
                    "video_url":     result["video_url"],
                    "scheduled_publish_at": confirmed_pub or None,
                    "upload_error":  None,
                    "upload_progress_pct": 100,
                },
                task={"running": False, "step_running": None, "error": None},
            )

            state.update_project(pid, **update_kwargs)
            _log(pid, f"Uploaded! {result['video_url']}")
        else:
            raise RuntimeError(result["error"] or "Upload failed.")

    except Exception as exc:
        _log(pid, f"ERROR: {exc}")
        state.update_project(
            pid,
            status="error",
            youtube={"upload_status": "error", "upload_error": str(exc)},
            task={"running": False, "step_running": None, "error": str(exc)},
        )
    finally:
        sys.stdout = orig_stdout


def start_step4_upload(pid: str) -> bool:
    if is_running(pid):
        return False
    state.update_project(
        pid,
        status="running",
        task={"running": True, "step_running": "4upload", "progress_pct": 0, "log": [], "error": None},
    )
    t = threading.Thread(target=_run_step4_upload, args=(pid,), daemon=True)
    _register(pid, t)
    t.start()
    return True


# ---------------------------------------------------------------------------
# Utility — suggest next available publish slot
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Image Library — standalone image generation (not tied to a project)
# ---------------------------------------------------------------------------

def _run_library_generate(prompt: str, count: int) -> None:
    import time as _time
    import image_library as il
    try:
        import generate_assets as ga
        import channel_profile as cp
        import channels as ch

        if not prompt:
            try:
                import youtube_api as ya
                style = ya.build_style_prompt()
                prompt = style.get("prompt", "")
            except Exception:
                prompt = ""

        if not prompt:
            # Image library generation is global (not per-channel), use first channel's prefix
            all_chs = ch.all_channels()
            prefix = ch.build_prompt_prefix(all_chs[0]["id"]) if all_chs else cp.build_prompt_prefix()
            prompt = (prefix + ", " if prefix else "") + \
                     "warm ambient lighting, cinematic architectural photography, ultra realistic"

        slug = f"lib_{int(_time.time())}"
        paths = ga.generate_ai_images(prompt, OUTPUT_DIR, slug, count=count)
        il.add_batch(prompt, paths)

    except Exception as exc:
        import image_library as il
        il.set_generating(False, error=str(exc))


def start_library_generate(prompt: str, count: int = 4) -> None:
    """Generate images for the image library in a background thread."""
    t = threading.Thread(target=_run_library_generate, args=(prompt, count), daemon=True)
    t.start()


# ---------------------------------------------------------------------------
def utc_to_local_date(utc_iso: str) -> str:
    """
    Convert a genuine-UTC ISO string to the local calendar day (YYYY-MM-DD).

    The dashboard runs on the user's own machine, so the server-local day is
    the day they see in the browser — truncating the UTC string instead puts
    evening schedules on the wrong calendar day.
    """
    from datetime import timezone

    try:
        dt = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().date().isoformat()
    except ValueError:
        return utc_iso[:10]


def suggest_next_slot() -> str:
    """
    Return an ISO 8601 UTC datetime string for the next sensible publish slot:
      - At least 7 days from the most recently scheduled video
      - Falls on a weekday (Mon–Fri)
      - At 14:00 UTC
    """
    from datetime import date, timedelta, timezone
    import state as _state

    # Find the latest scheduled date across all projects
    latest = date.today()
    for p in _state.all_projects():
        ds = p.get("scheduled_date")
        if ds:
            try:
                d = date.fromisoformat(ds)
                if d > latest:
                    latest = d
            except ValueError:
                pass

    # Start 7 days after the latest scheduled date
    candidate = latest + timedelta(days=7)

    # Push forward to a weekday (Mon=0 … Fri=4)
    while candidate.weekday() > 4:
        candidate += timedelta(days=1)

    return candidate.isoformat() + "T14:00:00Z"

