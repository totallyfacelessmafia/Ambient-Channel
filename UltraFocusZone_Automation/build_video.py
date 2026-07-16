#!/usr/bin/env python3
"""
build_video.py — Fast 1-hour ambient YouTube video builder.

Workflow
--------
1. Reads MP3s from the music folder (sorted alphabetically).
2. Concatenates them with smooth crossfades into one master audio file.
3. Loops a short background video, upscales to 1080p via NVENC (GPU).
4. Burns in a "Now Playing" overlay (title, channel, red progress bar,
   MM:SS timestamps) — all inside a single FFmpeg pass using drawtext
   and drawbox filters, no extra libraries required.

Typical run time: 5-15 min for a 1-hour video. The audio crossfade step is
CPU-bound; the video step uses NVENC GPU encoding so it stays fast.

Usage
-----
    python build_video.py                         # uses all defaults
    python build_video.py --songs 15              # first 15 tracks only
    python build_video.py --no-overlay            # skip Now Playing panel
    python build_video.py --channel "My Channel"  # custom channel name
    python build_video.py --crossfade 3 --songs 15 --output "Mix Vol1.mp4"

Default paths (relative to this script)
----------------------------------------
    Music folder : ../music
    Background   : ../raw footage/Seamless_Video_Loop_Creation.mp4
    Output       : ../Edited Videos/final_video.mp4
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw


# ---------------------------------------------------------------------------
# GPU detection — fall back to CPU (libx264) if NVENC is not available
# ---------------------------------------------------------------------------

def _has_nvenc() -> bool:
    """Return True if FFmpeg can use h264_nvenc (NVIDIA GPU encoding)."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        return "h264_nvenc" in r.stdout
    except Exception:
        return False

HAS_NVENC = _has_nvenc()


# ---------------------------------------------------------------------------
# Now Playing overlay — layout constants (pixels, 1920×1080)
# ---------------------------------------------------------------------------

# Calibri has rounded letterforms and is installed on all Windows systems.
# We use fontfile= (direct path) instead of font= (fontconfig) to avoid the
# "Monocromatic (1bpp) fonts not supported" error that fontconfig can trigger
# on Windows when it resolves a name to a bitmap-hinted font variant.
#
# Windows paths contain a colon after the drive letter (C:) which FFmpeg's
# filter-graph parser cannot escape reliably for fontfile= option values.
# Workaround: use bare filenames (no path) and set FONT_DIR as the working
# directory for the FFmpeg video-assembly call.  FFmpeg resolves fontfile=
# relative to its CWD, while all other inputs/outputs use absolute paths.
if os.name == "nt":
    FONT_DIR         = "C:/Windows/Fonts"
    # Segoe UI — clean modern sans-serif, ships on all Windows 10/11 systems.
    # Change to e.g. "Nunito-Bold.ttf" / "Nunito-Regular.ttf" after installing
    # a dedicated rounded font (place the .ttf files inside FONT_DIR).
    FONTFILE_BOLD    = "segoeuib.ttf"    # Segoe UI Bold
    FONTFILE_REGULAR = "segoeui.ttf"     # Segoe UI Regular
else:
    # macOS/Linux: no Windows fonts dir — use the repo's bundled Montserrat
    # via the same CWD trick so there is a single fontfile= code path.
    FONT_DIR         = str(Path(__file__).parent / "fonts")
    FONTFILE_BOLD    = "Montserrat-SemiBold.ttf"
    FONTFILE_REGULAR = "Montserrat-Regular.ttf"

# Panel position and size
PX, PY = 40, 905          # panel top-left corner
PW, PH = 480, 125         # panel width × height

# Glassmorphism backdrop-blur strength (higher = more frosted)
BLUR_SIGMA = 12

# Progress bar animation resolution (segments per song).
# Each segment is one drawbox with a fixed width enabled for a narrow window.
# 60 segments ≈ a new 7-pixel step every ~3 s for a 3-min song — looks smooth.
BAR_STEPS = 60

# Inner elements (relative to panel)
TITLE_X  = PX + 20
TITLE_Y  = PY + 16
ARTIST_X = PX + 20
ARTIST_Y = PY + 56
BAR_X    = PX + 20
BAR_W    = PW - 40        # 440 px
BAR_H    = 3
BAR_Y    = PY + PH - 32  # sits near panel bottom
TIME_Y   = BAR_Y + BAR_H + 6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ffprobe_duration(filepath: Path) -> float:
    """Return the duration of a media file in seconds."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(filepath),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        print(result.stderr[-1000:])
        raise RuntimeError(f"ffprobe could not read duration of {filepath}")


def run_ffmpeg(cmd: list, label: str, cwd: str | None = None):
    """
    Run an FFmpeg command, printing label and abbreviated command.

    cwd — optional working directory for the subprocess.  Used by the video
          assembly step to set CWD=FONT_DIR so bare fontfile= filenames (no
          drive colon) resolve to the correct Windows font files.
    """
    print(f"\n  >> {label}")
    print(f"     {' '.join(str(c) for c in cmd[:6])} ...")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        print("\n  [FFmpeg error — first 4000 chars]")
        print(result.stderr[:4000])
        raise RuntimeError(f"FFmpeg failed: {label}")


def format_mmss(seconds: float) -> str:
    """Format seconds as zero-padded MM:SS  (e.g. 194.3 → '03:14')."""
    t = int(seconds)
    return f"{t // 60:02d}:{t % 60:02d}"


def ffmpeg_esc(text: str) -> str:
    """
    Sanitise a string for use inside an FFmpeg drawtext single-quoted value.
    Drops characters that would break FFmpeg's option or text parsers.
    """
    return (
        text
        .replace("\\", "")
        .replace("'",  "")   # single quote would close the surrounding ' '
        .replace("%",  "")   # % starts special expansions in drawtext
        .replace(":",  " ")  # : separates filter options
        .replace(",",  "")   # , separates filters
    )


def _font(filename: str) -> str:
    """
    Return ':fontfile=FILENAME' for a bare filename (no drive-letter colon).
    FFmpeg resolves the filename against its working directory (FONT_DIR),
    which bypasses fontconfig and avoids the "Monocromatic" bitmap-font error.
    """
    return f":fontfile={filename}"


def generate_panel_png(output_path: Path) -> None:
    """
    Write a full-frame (1920x1080) RGBA PNG containing the Now Playing panel
    as a semi-transparent rounded rectangle.  Used as an FFmpeg overlay input
    so the panel gets proper alpha compositing instead of a flat drawbox.

    Corner radius = 20% of panel height (PH).
    """
    radius = round(PH * 0.20)          # 20% of 125 px = 25 px
    img  = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    box  = (PX, PY, PX + PW, PY + PH)

    # Glassmorphism style:
    #   fill  — very subtle dark tint (blurred video shows through from underneath)
    #   outline — soft white border giving the frosted-glass edge highlight
    draw.rounded_rectangle(
        box,
        radius=radius,
        fill=(0, 0, 0, 80),            # ~31 % black tint  (alpha 80/255)
        outline=(255, 255, 255, 70),   # ~27 % white border (alpha 70/255)
        width=2,
    )
    img.save(str(output_path), "PNG")


# ---------------------------------------------------------------------------
# Step 1 — Merge audio with crossfades
# ---------------------------------------------------------------------------

def build_filter_complex(n: int, crossfade_sec: float) -> tuple[str, str]:
    """
    Build the FFmpeg filter_complex that chains n acrossfade filters.
    Returns (filter_string, output_label).
    """
    d = crossfade_sec

    if n == 1:
        return "[0:a]anull[aout]", "aout"

    if n == 2:
        return f"[0:a][1:a]acrossfade=d={d}:c1=tri:c2=tri[aout]", "aout"

    # Chain: [0:a][1:a] → [a1] → [a1][2:a] → [a2] → … → [aout]
    parts = []
    for i in range(1, n):
        in_a      = "0:a" if i == 1 else f"a{i - 1}"
        out_label = "aout" if i == n - 1 else f"a{i}"
        parts.append(f"[{in_a}][{i}:a]acrossfade=d={d}:c1=tri:c2=tri[{out_label}]")

    return ";".join(parts), "aout"


def merge_audio(mp3_files: list[Path], output: Path, crossfade_sec: float) -> float:
    """Concatenate MP3 files with crossfades, saving to an M4A container."""
    n = len(mp3_files)
    print(f"\n[1/2] Merging {n} track(s) with {crossfade_sec}s crossfade ...")
    for i, f in enumerate(mp3_files, 1):
        print(f"       {i:>3}. {f.name}")

    cmd = ["ffmpeg", "-y"]
    for f in mp3_files:
        cmd += ["-i", str(f)]

    if n == 1:
        # -vn: ignore any embedded video/image stream (e.g. MP3 album art)
        cmd += ["-vn", "-c:a", "aac", "-b:a", "320k", str(output)]
    else:
        filter_str, out_label = build_filter_complex(n, crossfade_sec)
        cmd += [
            "-filter_complex", filter_str,
            "-map", f"[{out_label}]",
            "-c:a", "aac", "-b:a", "320k",
            str(output),
        ]

    run_ffmpeg(cmd, "Building master audio (may take 1-3 min for many tracks)")

    duration = ffprobe_duration(output)
    mins, secs = divmod(int(duration), 60)
    print(f"     Master audio ready: {mins}m {secs:02d}s total")
    return duration


# ---------------------------------------------------------------------------
# Now Playing overlay
# ---------------------------------------------------------------------------

def get_song_timings(
    mp3_files: list[Path],
    crossfade_sec: float,
) -> list[tuple[str, float, float, float]]:
    """
    Return one row per song: (title, abs_start, duration, display_until).

    abs_start    — where this song begins in the master audio (seconds)
    duration     — actual song length (drives the progress bar fill)
    display_until — when this song's panel is replaced by the next one
    """
    rows: list[tuple[str, float, float]] = []
    cursor = 0.0
    for f in mp3_files:
        dur = ffprobe_duration(f)
        rows.append((f.stem, cursor, dur))
        cursor += dur - crossfade_sec

    # display_until = next song's start (or final cursor for the last song)
    result = []
    for i, (title, start, dur) in enumerate(rows):
        display_until = rows[i + 1][1] if i + 1 < len(rows) else cursor
        result.append((title, start, dur, display_until))
    return result


def build_overlay_filter(
    timings: list[tuple[str, float, float, float]],
    channel_name: str,
) -> str:
    """
    Return a comma-joined FFmpeg filter fragment for the Now Playing overlay.
    Intended to be appended after 'scale=1920:1080:flags=lanczos' in -vf.

    The rounded-corner panel background is composited via a separate PNG
    overlay (see generate_panel_png / assemble_video).  Each song contributes
    6 filter stages here:
      1. Song title         (drawtext, bold white)
      2. Channel name       (drawtext, light grey)
      3. Progress bar track (drawbox, dark grey)
      4. Progress bar fill  (drawbox, red, dynamic width via expression)
      5. Elapsed time       (drawtext, MM:SS, computed per-frame)
      6. Total time         (drawtext, MM:SS, static per song)
    """
    bold_ff = _font(FONTFILE_BOLD)
    reg_ff  = _font(FONTFILE_REGULAR)
    safe_ch = ffmpeg_esc(channel_name)

    parts: list[str] = []

    for title, start, duration, display_until in timings:
        s   = f"{start:.3f}"
        e   = f"{display_until:.3f}"

        # Use backslash-escaped commas (\,) for expressions — unambiguous
        # even without single-quote wrapping, safe for all FFmpeg versions.
        enable    = f"enable=between(t\\,{s}\\,{e})"
        # Escape the colon in MM:SS — in this FFmpeg build (:) acts as an
        # option separator even inside single-quoted values, so we must use \:
        total_str = format_mmss(duration).replace(":", "\\:")
        safe_t    = ffmpeg_esc(title)

        # Elapsed time: %{eif\:EXPR\:d\:2} produces zero-padded "04:50" per frame.
        # Use \: (escaped colon) — NOT | — because recent FFmpeg treats | as an
        # option separator even inside single-quoted values, breaking the parser.
        # The comma inside mod(floor(...),60) is safe; , is protected by ' '.
        elapsed_text = (
            f"'%{{eif\\:floor((t-{s})/60)\\:d\\:2}}"
            f"\\:%{{eif\\:mod(floor(t-{s}),60)\\:d\\:2}}'"
        )

        # ── 1. Song title (bold white) ────────────────────────────────────
        parts.append(
            f"drawtext=text='{safe_t}'"
            f":x={TITLE_X}:y={TITLE_Y}"
            f":fontsize=30:fontcolor=white{bold_ff}"
            f":shadowcolor=black@0.6:shadowx=1:shadowy=1"
            f":{enable}"
        )

        # ── 2. Channel / artist name (light grey) ────────────────────────
        parts.append(
            f"drawtext=text='{safe_ch}'"
            f":x={ARTIST_X}:y={ARTIST_Y}"
            f":fontsize=19:fontcolor=0xBBBBBB{reg_ff}"
            f":{enable}"
        )

        # ── 3. Progress bar track (translucent white on glassmorphism panel) ─
        parts.append(
            f"drawbox=x={BAR_X}:y={BAR_Y}:w={BAR_W}:h={BAR_H}"
            f":color=0xFFFFFF@0.25:t=fill:{enable}"
        )

        # ── 4. Progress bar fill (red, grows left->right) ─────────────────
        # drawbox's w= is evaluated once at init, not per-frame, so we cannot
        # use a dynamic expression.  Instead we emit BAR_STEPS drawbox filters,
        # each covering a small time window with a fixed pre-computed width.
        # 60 steps → ~7 px jump every ~3 s for a typical 3-min song (imperceptible).
        for step in range(BAR_STEPS):
            seg_s = start + step / BAR_STEPS * duration
            seg_e = min(start + (step + 1) / BAR_STEPS * duration, display_until)
            if seg_s >= display_until:
                break
            seg_w   = max(1, int(BAR_W * (step + 1) / BAR_STEPS))
            seg_en  = f"enable=between(t\\,{seg_s:.3f}\\,{seg_e:.3f})"
            parts.append(
                f"drawbox=x={BAR_X}:y={BAR_Y}:w={seg_w}:h={BAR_H}"
                f":color=0xEE2222:t=fill:{seg_en}"
            )

        # ── 5. Elapsed time (left side, MM:SS per-frame) ──────────────────
        parts.append(
            f"drawtext=text={elapsed_text}"
            f":x={BAR_X}:y={TIME_Y}"
            f":fontsize=14:fontcolor=0xBBBBBB{reg_ff}"
            f":{enable}"
        )

        # ── 6. Total duration (right side, static) ────────────────────────
        # Wrap x expression in single quotes so FFmpeg evaluates tw at render time.
        parts.append(
            f"drawtext=text='{total_str}'"
            f":x='{BAR_X + BAR_W}-tw':y={TIME_Y}"
            f":fontsize=14:fontcolor=0xBBBBBB{reg_ff}"
            f":{enable}"
        )

    return ",".join(parts)


# ---------------------------------------------------------------------------
# Step 2 — Assemble final video
# ---------------------------------------------------------------------------

def assemble_video(
    bg_video: Path,
    master_audio: Path,
    output: Path,
    overlay_filter: str | None = None,
    panel_png: Path | None = None,
    logo_path: Path | None = None,
):
    """
    Loop background video, upscale to 1080p (NVENC), optionally burn overlay.

    When panel_png is given the function switches to -filter_complex so the
    rounded-corner PNG can be alpha-composited via the overlay filter before
    the drawtext / drawbox text layers are applied.

    Input layout (panel_png mode):
      0 : bg_video    (stream_loop -1)
      1 : master_audio
      2 : panel_png   (loop 1 — static frame repeated for full duration)

    -stream_loop -1  : loop the video input indefinitely
    h264_nvenc       : GPU-accelerated H.264 encoding
    -c:a copy        : copy AAC audio bitstream unchanged
    -shortest        : stop when the audio stream ends
    """
    print(f"\n[2/2] Assembling final video ...")

    if HAS_NVENC:
        base_encode = [
            "-c:v", "h264_nvenc",
            "-preset", "p4",    # p1=fastest … p7=best quality
            "-cq", "21",        # constant quality; 18-24 is good range
            "-b:v", "0",        # let -cq drive bitrate
            "-c:a", "copy",
            "-shortest",
            str(output),
        ]
    else:
        base_encode = [
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "21",
            "-c:a", "copy",
            "-shortest",
            str(output),
        ]

    # Still images (PNG/JPG) use -loop 1; video files use -stream_loop -1.
    _is_image = Path(bg_video).suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}
    _bg_loop  = ["-loop", "1"] if _is_image else ["-stream_loop", "-1"]

    # Logo input flags: GIFs loop indefinitely; static images use -loop 1.
    _logo_is_gif  = logo_path is not None and Path(logo_path).suffix.lower() == ".gif"
    _logo_loop    = ["-stream_loop", "-1"] if _logo_is_gif else ["-loop", "1"]

    # With many songs the filter_complex string can exceed Windows' 32 767-char
    # command-line limit (WinError 206).  Writing it to a temp file and using
    # -filter_complex_script bypasses that limit entirely.
    fc_script: Path | None = None
    try:
        if overlay_filter and panel_png:
            # Glassmorphism pipeline:
            #  0 : bg_video,  1 : master_audio,  2 : panel_png,  [3 : logo]
            logo_idx = 3
            if logo_path:
                logo_tail = (
                    f"[textdone][{logo_idx}:v]scale=-1:80[logo_scaled];"
                    f"[textdone][logo_scaled]overlay=20:20,format=yuv420p[out]"
                )
                text_out = "[textdone]"
            else:
                logo_tail = ""
                text_out  = ",format=yuv420p[out]"
            fc = (
                f"[0:v]scale=1920:1080:flags=lanczos[scaled];"
                f"[scaled]split=2[sc1][sc2];"
                f"[sc1]crop={PW}:{PH}:{PX}:{PY}[panel_bg];"
                f"[panel_bg]gblur=sigma={BLUR_SIGMA}[panel_blur];"
                f"[sc2][panel_blur]overlay={PX}:{PY}[with_blur];"
                f"[with_blur][2:v]overlay=0:0[base];"
                f"[base]{overlay_filter}{text_out}"
                + (f";{logo_tail}" if logo_tail else "")
            )
            _, _tmp = tempfile.mkstemp(suffix=".txt")
            fc_script = Path(_tmp)
            fc_script.write_text(fc, encoding="utf-8")
            logo_inputs = [*_logo_loop, "-i", str(logo_path)] if logo_path else []
            cmd = [
                "ffmpeg", "-y",
                *_bg_loop,
                "-i", str(bg_video),
                "-i", str(master_audio),
                "-loop", "1",
                "-i", str(panel_png),
                *logo_inputs,
                "-filter_complex_script", str(fc_script),
                "-map", "[out]",
                "-map", "1:a",
            ] + base_encode
            label = "Encoding to 1080p with glassmorphism overlay ({'NVENC' if HAS_NVENC else 'CPU'}) ..."

        elif overlay_filter:
            # Minimal / text-only pipeline: no panel PNG.
            # 0 : bg_video,  1 : master_audio,  [2 : logo]
            logo_idx = 2
            if logo_path:
                fc = (
                    f"[0:v]scale=1920:1080:flags=lanczos,{overlay_filter}[textdone];"
                    f"[{logo_idx}:v]scale=-1:80[logo_scaled];"
                    f"[textdone][logo_scaled]overlay=20:20,format=yuv420p[out]"
                )
            else:
                fc = f"[0:v]scale=1920:1080:flags=lanczos,{overlay_filter},format=yuv420p[out]"
            _, _tmp = tempfile.mkstemp(suffix=".txt")
            fc_script = Path(_tmp)
            fc_script.write_text(fc, encoding="utf-8")
            logo_inputs = [*_logo_loop, "-i", str(logo_path)] if logo_path else []
            cmd = [
                "ffmpeg", "-y",
                *_bg_loop,
                "-i", str(bg_video),
                "-i", str(master_audio),
                *logo_inputs,
                "-filter_complex_script", str(fc_script),
                "-map", "[out]",
                "-map", "1:a",
            ] + base_encode
            label = "Encoding to 1080p with Now Playing overlay ({'NVENC' if HAS_NVENC else 'CPU'}) ..."

        else:
            # No overlay — logo only (if set), or plain encode.
            if logo_path:
                logo_idx = 2
                fc = (
                    f"[0:v]scale=1920:1080:flags=lanczos[base];"
                    f"[{logo_idx}:v]scale=-1:80[logo_scaled];"
                    f"[base][logo_scaled]overlay=20:20,format=yuv420p[out]"
                )
                _, _tmp = tempfile.mkstemp(suffix=".txt")
                fc_script = Path(_tmp)
                fc_script.write_text(fc, encoding="utf-8")
                logo_inputs = [*_logo_loop, "-i", str(logo_path)]
                cmd = [
                    "ffmpeg", "-y",
                    *_bg_loop,
                    "-i", str(bg_video),
                    "-i", str(master_audio),
                    *logo_inputs,
                    "-filter_complex_script", str(fc_script),
                    "-map", "[out]",
                    "-map", "1:a",
                ] + base_encode
                label = "Encoding to 1080p with logo overlay ({'NVENC' if HAS_NVENC else 'CPU'}) ..."
            else:
                vf = "scale=1920:1080:flags=lanczos,format=yuv420p"
                cmd = [
                    "ffmpeg", "-y",
                    *_bg_loop,
                    "-i", str(bg_video),
                    "-i", str(master_audio),
                    "-map", "0:v",
                    "-map", "1:a",
                    "-vf", vf,
                ] + base_encode
                label = "Encoding to 1080p via NVENC ..."

        # cwd=FONT_DIR: fontfile= bare filenames resolve against C:\Windows\Fonts
        run_ffmpeg(cmd, label, cwd=FONT_DIR)
    finally:
        if fc_script is not None:
            try:
                fc_script.unlink(missing_ok=True)
            except PermissionError:
                pass  # FFmpeg may briefly hold the file on Windows; temp dir cleans up on reboot

    duration = ffprobe_duration(output)
    mins, secs = divmod(int(duration), 60)
    print(f"     Final video: {mins}m {secs:02d}s  ->  {output}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    script_dir = Path(__file__).parent

    parser = argparse.ArgumentParser(
        description="Build a 1-hour ambient YouTube video from a playlist of MP3s.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--music-dir",
        default=str(script_dir / "../music"),
        help="Folder containing MP3 files  [default: ../music]",
    )
    parser.add_argument(
        "--background",
        default=str(script_dir / "../raw footage/Seamless_Video_Loop_Creation.mp4"),
        help="Short looping background video  [default: Seamless_Video_Loop_Creation.mp4]",
    )
    parser.add_argument(
        "--output",
        default=str(script_dir / "../Edited Videos/final_video.mp4"),
        help="Output file path  [default: ../Edited Videos/final_video.mp4]",
    )
    parser.add_argument(
        "--songs",
        type=int,
        default=0,
        metavar="N",
        help="Use only the first N tracks (0 = all)  [default: 0]",
    )
    parser.add_argument(
        "--crossfade",
        type=float,
        default=2.0,
        metavar="SEC",
        help="Crossfade duration in seconds  [default: 2.0]",
    )
    parser.add_argument(
        "--channel",
        default="Ultra Focus Zone",
        help="Channel name shown in the overlay  [default: 'Ultra Focus Zone']",
    )
    parser.add_argument(
        "--no-overlay",
        action="store_true",
        help="Skip the Now Playing overlay (faster, no text burned in)",
    )
    args = parser.parse_args()

    music_dir  = Path(args.music_dir).resolve()
    background = Path(args.background).resolve()
    output     = Path(args.output).resolve()

    # ---- Validate ----
    if not music_dir.is_dir():
        print(f"ERROR: music folder not found: {music_dir}")
        sys.exit(1)
    if not background.is_file():
        print(f"ERROR: background video not found: {background}")
        sys.exit(1)

    output.parent.mkdir(parents=True, exist_ok=True)

    # ---- Collect MP3s ----
    mp3_files = sorted(music_dir.glob("*.mp3"))
    if not mp3_files:
        print(f"ERROR: no .mp3 files found in {music_dir}")
        sys.exit(1)
    if args.songs > 0:
        mp3_files = mp3_files[: args.songs]

    # ---- Summary ----
    bg_dur = ffprobe_duration(background)
    print()
    print("=" * 54)
    print("  UltraFocusZone Video Builder")
    print("=" * 54)
    print(f"  Tracks     : {len(mp3_files)}")
    print(f"  Crossfade  : {args.crossfade}s")
    print(f"  Overlay    : {'off' if args.no_overlay else 'on  (Now Playing panel)'}")
    print(f"  Channel    : {args.channel}")
    print(f"  Background : {background.name}  ({bg_dur:.1f}s loop)")
    print(f"  Output     : {output}")
    print("=" * 54)

    # ---- Build ----
    with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
        master_audio = Path(tmp.name)

    panel_png: Path | None = None

    try:
        merge_audio(mp3_files, master_audio, args.crossfade)

        if args.no_overlay:
            overlay_filter = None
        else:
            print("\n  Computing Now Playing timings ...")
            timings = get_song_timings(mp3_files, args.crossfade)
            for i, (title, start, dur, until) in enumerate(timings, 1):
                print(
                    f"    {i:>2}. {format_mmss(start)} to {format_mmss(until)}"
                    f"  {title}  ({format_mmss(dur)})"
                )
            overlay_filter = build_overlay_filter(timings, args.channel)

            # Generate rounded-corner panel PNG (used as FFmpeg overlay input).
            _, _panel_tmp = tempfile.mkstemp(suffix=".png")
            panel_png = Path(_panel_tmp)
            print(f"  Generating rounded-corner panel PNG (radius={round(PH*0.20)}px) ...")
            generate_panel_png(panel_png)

        assemble_video(background, master_audio, output, overlay_filter, panel_png)
        print(f"\n  Done!  ->  {output}\n")
    finally:
        master_audio.unlink(missing_ok=True)
        if panel_png is not None:
            try:
                panel_png.unlink(missing_ok=True)
            except PermissionError:
                pass  # FFmpeg may briefly hold the file on Windows; temp dir cleans up on reboot


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
