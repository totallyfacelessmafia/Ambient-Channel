#!/usr/bin/env python3
"""
generate_assets.py — Full asset pipeline for Ultra Focus Zone videos.

Two modes:
  --prompt  : generate a new AI image via fal.ai FLUX Pro, then produce assets
  --image   : use an existing image file, then produce assets

Usage
-----
    # AI-generated image (recommended)
    py generate_assets.py --prompt "cozy rainy cabin with fireplace" --title "Rainy Day"

    # Existing image
    py generate_assets.py --image "../images/light room/Cinematic_...png" --title "Deep Focus"

    # Flags
    py generate_assets.py --prompt "..." --title "..." --output-dir "../images/generated"
    py generate_assets.py --prompt "..." --title "..." --loop-duration 60

Outputs (saved to --output-dir, default: images/generated/)
------------------------------------------------------------
    <Slug>_raw.jpg         -- raw AI image from fal.ai
    <Slug>_thumbnail.jpg   -- 1280x720 YouTube thumbnail (Montserrat, no shadow)
    <Slug>_background.png  -- 1920x1080 still frame (lossless)
    <Slug>_loop.mp4        -- looping background video for build_video.py
                              AI animation (--animate) or static looped still
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT      = Path(__file__).parent
FONTS_DIR = ROOT / "fonts"

THUMB_W, THUMB_H = 1280, 720
BG_W,    BG_H    = 1920, 1080

WHITE = (255, 255, 255, 255)

# Automatically appended to every --prompt to keep the channel's visual style
PROMPT_STYLE = (
    ", ultra-modern minimalist home office with floor-to-ceiling glass walls, "
    "dramatic ocean cliffs or mountain coastline visible through windows, "
    "dark moody cinematic lighting with deep teal and blue tones, "
    "luxury architectural interior photography, photorealistic, "
    "no people, ultra-detailed, 8K"
)

DEFAULT_OUT_DIR = ROOT.parent / "images" / "generated"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_slug(text: str) -> str:
    return re.sub(r"[^\w\s-]", "", text).strip().replace(" ", "_")


def load_fal_key() -> str:
    cfg_path = ROOT / "config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        key = cfg.get("fal", {}).get("api_key", "")
        if key and not key.startswith("your-"):
            return key
    key = os.environ.get("FAL_KEY", "")
    if key:
        return key
    print("ERROR: fal API key not set.")
    print("  -> Edit config.json and set  fal.api_key")
    sys.exit(1)


def load_font(filename: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS_DIR / filename
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        print(f"  WARNING: font '{filename}' not found in fonts/")
        return ImageFont.load_default()


def smart_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w = round(src_w * scale)
    new_h = round(src_h * scale)
    img   = img.resize((new_w, new_h), Image.LANCZOS)
    left  = (new_w - target_w) // 2
    top   = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


# ---------------------------------------------------------------------------
# Step 1: AI image generation via fal.ai FLUX Pro
# ---------------------------------------------------------------------------

def generate_ai_image(prompt: str, output_path: Path) -> None:
    """Call fal.ai FLUX Pro and save the result to output_path."""
    import fal_client

    os.environ["FAL_KEY"] = load_fal_key()

    full_prompt = prompt + PROMPT_STYLE
    print(f"  Prompt     : {full_prompt[:100]}...")
    print("  Generating via fal.ai FLUX Pro...")

    result = fal_client.run(
        "fal-ai/flux-pro",
        arguments={
            "prompt":           full_prompt,
            "image_size":       "landscape_16_9",   # ~1344x768, native 16:9
            "num_images":       1,
            "safety_tolerance": "6",
            "output_format":    "jpeg",
        },
    )

    images = result.get("images") or []
    if not images:
        print("ERROR: fal.ai returned no images.")
        print(result)
        sys.exit(1)

    urllib.request.urlretrieve(images[0]["url"], str(output_path))
    print(f"  Raw image  : {output_path.name}")


def generate_ai_images(prompt: str, out_dir: Path, slug: str, count: int = 5) -> list[Path]:
    """
    Generate `count` candidate images via fal.ai FLUX Pro in a single API call.
    Saves them as <slug>_candidate_1.jpg … <slug>_candidate_N.jpg.
    Returns the list of saved Paths.
    """
    import fal_client

    os.environ["FAL_KEY"] = load_fal_key()

    full_prompt = prompt + PROMPT_STYLE
    print(f"  Prompt     : {full_prompt[:100]}...")
    print(f"  Generating {count} images via fal.ai FLUX Pro...")

    result = fal_client.run(
        "fal-ai/flux-pro",
        arguments={
            "prompt":           full_prompt,
            "image_size":       "landscape_16_9",
            "num_images":       count,
            "safety_tolerance": "6",
            "output_format":    "jpeg",
        },
    )

    images = result.get("images") or []
    if not images:
        print("ERROR: fal.ai returned no images.")
        print(result)
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, img in enumerate(images, 1):
        dest = out_dir / f"{slug}_candidate_{i}.jpg"
        urllib.request.urlretrieve(img["url"], str(dest))
        print(f"  Candidate {i}: {dest.name}")
        paths.append(dest)

    return paths


# ---------------------------------------------------------------------------
# Step 2: Background frame (1920x1080 — clean, no text)
# ---------------------------------------------------------------------------

def generate_background(source: Path, output: Path) -> None:
    img = Image.open(source).convert("RGB")
    bg  = smart_crop(img, BG_W, BG_H)
    bg.save(str(output), "PNG")
    print(f"  Background : {output.name}  ({BG_W}x{BG_H})")


# ---------------------------------------------------------------------------
# Step 3: Thumbnail (1280x720 — minimal Montserrat style)
# ---------------------------------------------------------------------------

def generate_thumbnail(source: Path, title: str, output: Path, text_position: str = "top") -> None:
    """
    Minimal thumbnail: full atmospheric image + centered Montserrat text.
    Supports text placement (top/middle/bottom) and adds subtle readability
    styling for bright backgrounds.
    """
    W, H = THUMB_W, THUMB_H

    base = smart_crop(Image.open(source).convert("RGBA"), W, H)
    font = load_font("Montserrat-Regular.ttf", 120)
    draw = ImageDraw.Draw(base)

    bbox = draw.textbbox((0, 0), title, font=font)
    tw   = bbox[2] - bbox[0]
    th   = bbox[3] - bbox[1]

    x = (W - tw) // 2 - bbox[0]   # true horizontal center
    pos_y = {
        "top": round(H * 0.18),
        "middle": round((H - th) * 0.50),
        "bottom": round(H * 0.72),
    }
    y = pos_y.get(text_position, pos_y["top"])

    # Keep original clean style: no rectangle backing, only subtle legibility aids.
    # Thin stroke + gentle drop shadow for legibility without looking heavy.
    draw.text((x + 2, y + 2), title, font=font, fill=(0, 0, 0, 110))
    draw.text((x, y), title, font=font, fill=WHITE, stroke_width=2, stroke_fill=(10, 16, 28, 190))

    base.convert("RGB").save(str(output), "JPEG", quality=97, subsampling=0)
    print(f"  Thumbnail  : {output.name}  ({W}x{H})")


# ---------------------------------------------------------------------------
# Step 4: Looping background video (completely static — no zoom, no pan, NVENC)
# ---------------------------------------------------------------------------

def generate_loop_video(source: Path, output: Path, duration: int = 30) -> None:
    """
    Create a seamlessly loopable MP4 from a still image.

    Produces a completely static background — no zoom, no pan.
    Encoded with NVENC for speed; build_video.py will loop this to fill 1 hour.
    """
    fps = 30
    vf  = "scale=1920:1080:flags=lanczos,format=yuv420p"
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(source),
        "-vf", vf,
        "-c:v", "h264_nvenc",
        "-preset", "p4",
        "-cq", "21",
        "-t", str(duration),
        str(output),
    ]
    print(f"  Rendering loop video ({duration}s)...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Fall back to CPU encoding if NVENC is unavailable
        cmd[cmd.index("h264_nvenc")] = "libx264"
        del cmd[cmd.index("-preset") : cmd.index("-preset") + 4]  # remove preset+cq
        cmd += ["-crf", "21", "-preset", "slow"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print("ERROR generating loop video:")
            print(result.stderr[:2000])
            sys.exit(1)
    print(f"  Loop video : {output.name}  ({duration}s, loops seamlessly)")


# ---------------------------------------------------------------------------
# Step 4b: Ambient Glow loop — Pillow brightness oscillation, zero camera movement
# ---------------------------------------------------------------------------

def generate_ambient_loop(source: Path, output: Path, duration: int = 30) -> None:
    """
    Create a looping ambient video using a sinusoidal brightness oscillation.

    Technique:
      - Generate `duration * FPS` JPEG frames where each frame is the source
        image with brightness adjusted by  1.0 + A * sin(2π * t / P).
      - Encode the frame sequence to MP4 with NVENC.

    Properties:
      - ZERO camera movement — the frame content never shifts position.
      - Seamlessly loops: period P = 10 s divides evenly into any duration
        that is a multiple of 5 s (30 s, 60 s, etc.) so sin(0) = sin(end).
      - No AI, no external API, no credits required.
      - Typical runtime: < 30 seconds for a 30 s loop.
    """
    import math
    import shutil
    import tempfile
    from PIL import ImageEnhance

    FPS       = 6       # low framerate — brightness changes are imperceptible at higher fps
    PERIOD    = 10.0    # oscillation cycle in seconds
    AMPLITUDE = 0.025   # ±2.5 % brightness swing (very subtle)

    n_frames = duration * FPS

    img = Image.open(source).convert("RGB")
    img = smart_crop(img, BG_W, BG_H)

    tmp_dir = Path(tempfile.mkdtemp(prefix="ambient_frames_"))
    try:
        print(f"  Generating {n_frames} frames ({FPS} fps × {duration} s)...")
        for i in range(n_frames):
            t      = i / FPS
            factor = 1.0 + AMPLITUDE * math.sin(2 * math.pi * t / PERIOD)
            frame  = ImageEnhance.Brightness(img).enhance(factor)
            frame.save(str(tmp_dir / f"frame_{i:05d}.jpg"), quality=85)

        print("  Encoding ambient loop (NVENC)...")
        base_cmd = [
            "ffmpeg", "-y",
            "-framerate", str(FPS),
            "-i", str(tmp_dir / "frame_%05d.jpg"),
            "-t", str(duration),
        ]
        result = subprocess.run(
            base_cmd + ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "21", str(output)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            result = subprocess.run(
                base_cmd + ["-c:v", "libx264", "-crf", "21", "-preset", "slow", str(output)],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print("ERROR generating ambient loop:")
                print(result.stderr[:2000])
                sys.exit(1)

        print(f"  Ambient loop: {output.name}  ({duration}s, zero camera movement)")

    finally:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)


# ---------------------------------------------------------------------------
# Step 4b: AI image-to-video animation — multi-model (Kling, Seedance, Hailuo)
# ---------------------------------------------------------------------------

# ── Per-model prompts ──────────────────────────────────────────────────────
#
# Key principle: each model needs a prompt written FOR THAT MODEL.
#
# Kling (no camera_fixed param): use cfg_scale=1.0 + long restrictive prompt + negative prompt.
# Seedance (camera_fixed=True): camera is locked by the API param — the prompt
#   should ONLY describe what to animate, not what NOT to do.  Long prohibition
#   lists confuse Seedance and cause it to invent extreme scene changes.
# Hailuo: intermediate — describe the scene, keep motion minimal.

# ── Kling prompt — Subject + Background + Movement formula ─────────────────
# Rules:
#  - Use literal rig metaphors ("locked-off frame", "fixed tripod perspective",
#    "zero lens drift") — avoid "cinematic" which triggers camera motion.
#  - Low-level atmospheric haze IS allowed with "linear wisp movement, zero
#    global wind". Racing sky clouds are not.
#  - Water motion must be magnitude-controlled: "barely perceptible, global motion 5%".
#  - cfg_scale=1.0 maximises adherence to this prompt (set in AI_VIDEO_MODELS).
KLING_MOTION_PROMPT = (
    # SUBJECT
    "Empty modern architectural interior — no people, no humans, no persons, no figures, no silhouettes. "
    "Architecture and all furniture are completely anchored and motionless. "
    # CAMERA (rig metaphors — avoid vague words like 'cinematic')
    "Static shot, locked-off frame, zero camera movement, no lens drift, fixed tripod perspective. "
    "The camera is bolted to the floor — no panning, no zooming, no tilting, no drift of any kind. "
    # MOVEMENT (magnitude-controlled; water + low-level mist only)
    "The only visible motion: subtle rhythmic ocean waves crashing gently in the distance; "
    "shimmering dark water surface with gentle natural ripples on the pool; "
    "fluid reflections moving extremely slowly. "
    "Global motion is 5% — movement is barely perceptible, unhurried, real-world speed. "
    "Soft atmospheric haze drifts linearly with zero global wind, "
    "taking the full 10 seconds to cross the frame. "
    # REAL-TIME enforcement
    "THIS IS REAL-TIME FOOTAGE — NOT a time-lapse, NOT accelerated, NOT sped up. "
    "Exactly 10 real seconds. Time of day does NOT change. "
    "Lighting, color temperature, and atmospheric density are completely frozen. "
    "No sunrise, no sunset, no golden hour, no day-to-night transition. "
    "Ultra-realistic, calm, serene ambient scene."
)

# Kling negative prompt — deliberately verbose for time-lapse suppression.
# 'cinematic motion' is blocked — the word 'cinematic' alone triggers camera movement.
KLING_NEGATIVE_PROMPT = (
    # Camera motion — specific recommended block terms
    "camera movement, moving perspective, zoom, pan, tilt, camera shake, lens drift, "
    "zoom in, zoom out, push in, pull out, dolly, tracking shot, handheld, "
    "camera rotation, orbit, pedestal move, parallax, "
    "camera slide, camera sweep, camera arc, floating camera, "
    "unstable camera, wobbly camera, drifting camera, creeping zoom, breathing zoom, "
    "cinematic motion, cinematic camera, "
    # Time-lapse — many variants required to suppress Kling's default tendency
    "time-lapse, timelapse, time lapse, time lapses, time-lapses, "
    "accelerated, sped up, fast-forward, fast forward, speed up, "
    "time compression, temporal acceleration, time warp, compressed time, "
    "sunrise, sunset, golden hour, blue hour, dawn, dusk, twilight, "
    "day to night, night to day, day-to-night, night-to-day, sky transition, "
    "lighting change, color shift, exposure change, brightness change, "
    "fast clouds, cloud timelapse, moving clouds, racing clouds, sky movement, "
    # People and other unwanted content
    "people, humans, persons, figures, silhouettes, bodies, faces, hands, "
    "fast motion, speed ramp, motion blur trails, "
    "scene change, morphing, distortion, hallucination, random objects"
)

# ── Seedance v1 prompt — [Scene] + [Motion], + [Motion], [Camera] + [Motion]
# IMPORTANT: Seedance v1 does NOT respond to negative prompts — use positive
# constraints only. Describe only the moving part of each element; do not
# describe the static surrounding context (e.g. don't describe the pool tiles,
# only the water surface itself).
# Use adverbs ("extremely slowly", "gently", "barely perceptible") to control speed.
# camera_fixed=True API param locks camera; "fixed tripod perspective" reinforces it.
# End-frame trick: image is uploaded as both first AND last frame so the AI must
# return to the original state, producing a near-perfect natural loop.
SEEDANCE_PROMPT = (
    # [Scene]
    "Modern dark architectural interior, no people, no figures, no silhouettes. "
    # [Motion] — describe only the moving element, with adverbs of degree
    "Water surface gently ripples with subtle reflections, "
    "subtle rhythmic waves crashing slowly on the distant shore, "
    "thin wisps of morning mist drifting very slowly past the cliffs. "
    # [Camera] + [Motion reinforcement]
    "Fixed tripod perspective, locked-off frame, zero camera movement. "
    # Style / lighting continuity
    "Maintain exact lighting and cool color temperature from the first frame. "
    "Real-time footage, not accelerated, not a time-lapse."
)

# Hailuo: cinematic ambient description, keep it concise.
HAILUO_PROMPT = (
    "Empty architectural scene — no people, no humans, no figures, no silhouettes. "
    "Architecture and furniture are completely anchored and motionless. "
    "Static shot, locked-off frame, zero camera movement, no lens drift, fixed tripod perspective. "
    "The only visible motion: subtle rhythmic ocean waves crashing gently; "
    "gentle natural ripples on the pool surface — barely perceptible, global motion is 5%. "
    "Soft atmospheric haze drifts linearly with zero global wind. "
    "Real-time footage — NOT a time-lapse, NOT accelerated. "
    "Lighting, color temperature, and atmospheric density frozen exactly as shown. "
    "No sunrise, sunset, golden hour, or day-to-night. Calm, serene ambient scene."
)

# ---------------------------------------------------------------------------
# Model registry — one entry per supported AI video model
# ---------------------------------------------------------------------------

AI_VIDEO_MODELS = {
    "kling_v16": {
        "label":    "Kling v1.6 Standard (~$0.25)",
        "endpoint": "fal-ai/kling-video/v1.6/standard/image-to-video",
        "duration": "10",           # string: "5" or "10"
        "prompt":   KLING_MOTION_PROMPT,
        "extra": {
            "negative_prompt": KLING_NEGATIVE_PROMPT,
            "aspect_ratio":    "16:9",
            # cfg_scale 1.0 = max prompt adherence → enforces "STATIC SHOT" instruction.
            # Kling image-to-video has no camera_fixed param; this is the strongest
            # lever available for suppressing camera drift and breathing zoom.
            "cfg_scale":       1.0,
        },
    },
    "kling_v21": {
        "label":    "Kling v2.1 Standard (~$0.28)",
        "endpoint": "fal-ai/kling-video/v2.1/standard/image-to-video",
        "duration": "10",
        "prompt":   KLING_MOTION_PROMPT,
        "extra": {
            "negative_prompt": KLING_NEGATIVE_PROMPT,
            "aspect_ratio":    "16:9",
            "cfg_scale":       1.0,
        },
    },
    "seedance_lite": {
        "label":    "Seedance v1 Lite (~$0.36, camera_fixed)",
        "endpoint": "fal-ai/bytedance/seedance/v1/lite/image-to-video",
        "duration": 10,             # integer: 2–10
        "prompt":   SEEDANCE_PROMPT,
        # Seedance v1 ignores negative_prompt — do NOT include it.
        # end_frame_loop=True → generate_animated_loop_ai sends end_image_url=image_url
        # so the AI must return to the first frame, creating a near-perfect natural loop.
        "end_frame_loop": True,
        "extra": {
            "camera_fixed": True,
        },
    },
    "seedance_pro": {
        "label":    "Seedance v1 Pro (~$1.24, camera_fixed, 1080p)",
        "endpoint": "fal-ai/bytedance/seedance/v1/pro/image-to-video",
        "duration": 10,
        "prompt":   SEEDANCE_PROMPT,
        # Seedance v1 ignores negative_prompt — do NOT include it.
        "end_frame_loop": True,
        "extra": {
            "camera_fixed": True,
            "resolution":   "1080p",
        },
    },
    "hailuo_pro": {
        "label":    "Hailuo-02 Pro (~$0.48)",
        "endpoint": "fal-ai/minimax/hailuo-02/pro/image-to-video",
        "duration": 6,              # integer: 6 or 10
        "prompt":   HAILUO_PROMPT,
        "extra": {},
    },
}


def generate_animated_loop_ai(
    source: Path,
    output: Path,
    model: str = "kling_v16",
) -> None:
    """
    Animate a still image using a fal.ai image-to-video model and bake in a
    crossfade dissolve so the clip loops seamlessly.

    Each model uses its own tailored prompt from AI_VIDEO_MODELS["prompt"].
    Supported models (``model`` key):
      kling_v16     — Kling v1.6 Standard  (~$0.25, 10 s)
      kling_v21     — Kling v2.1 Standard  (~$0.28, 10 s)
      seedance_lite — Seedance v1 Lite     (~$0.18,  5 s, camera_fixed)
      seedance_pro  — Seedance v1 Pro      (~$0.62,  5 s, camera_fixed)
      hailuo_pro    — Hailuo-02 Pro        (~$0.48,  6 s)

    Produces two files:
      <output>              — seamless loop clip (xfade dissolve at loop point)
      <output stem>_raw.mp4 — raw clip from fal.ai (kept for debugging)
    """
    import fal_client

    cfg = AI_VIDEO_MODELS.get(model)
    if cfg is None:
        raise ValueError(f"Unknown AI video model: {model!r}. "
                         f"Choose from: {list(AI_VIDEO_MODELS)}")

    os.environ["FAL_KEY"] = load_fal_key()

    # Use the per-model prompt — each model needs its own wording
    model_prompt = cfg["prompt"]

    # Upload the source image to fal.ai storage so the model can read it
    print(f"  Uploading image to fal.ai for {cfg['label']}...")
    image_url = fal_client.upload_file(str(source))

    arguments = {
        "image_url": image_url,
        "prompt":    model_prompt,
        "duration":  cfg["duration"],
    }
    arguments.update(cfg["extra"])

    # Start-to-End Frames trick: upload same image as last frame so the model
    # must return to the original state → near-perfect natural loop before crossfade.
    if cfg.get("end_frame_loop"):
        arguments["end_image_url"] = image_url
        print(f"  End-frame loop: first == last frame for seamless natural loop.")

    print(f"  Animating with {cfg['label']}...")
    try:
        result = fal_client.run(cfg["endpoint"], arguments=arguments)
    except Exception as e:
        msg = str(e)
        if "balance" in msg.lower() or "exhausted" in msg.lower():
            raise RuntimeError(
                f"fal.ai balance exhausted. Top up at: fal.ai/dashboard/billing"
            ) from e
        raise RuntimeError(f"{cfg['label']} animation failed: {msg}") from e

    # Extract video URL — fal.ai models return {"video": {"url": "..."}}
    video_info = result.get("video") or {}
    video_url  = video_info.get("url", "") if isinstance(video_info, dict) else ""
    if not video_url:
        raise RuntimeError(
            f"{cfg['label']} returned no video URL. Raw result: {result}"
        )

    # Download the raw clip
    raw_clip = output.with_name(output.stem + "_raw.mp4")
    urllib.request.urlretrieve(video_url, str(raw_clip))
    clip_duration = int(cfg["duration"])
    print(f"  Raw clip   : {raw_clip.name}  ({clip_duration}s, {model})")

    # Bake in the crossfade dissolve so the clip loops invisibly
    print(f"  Applying {CROSSFADE_DURATION}s crossfade dissolve at loop point...")
    _apply_crossfade_loop(raw_clip, output, clip_duration)
    print(f"  Loop file  : {output.name}  (seamless {clip_duration}s)")


# ---------------------------------------------------------------------------
# Beatoven music generation via fal.ai
# ---------------------------------------------------------------------------

BEATOVEN_NEGATIVE_PROMPT = (
    "vocals, singing, lyrics, speech, talking, "
    "fast tempo, upbeat, energetic, pop, rock, metal, jazz, "
    "drums, heavy percussion, distorted, harsh, abrupt"
)


def generate_music_beatoven(
    output_mp3: Path,
    prompt: str,
    duration: float = 30.0,
    seed: int | None = None,
) -> None:
    """
    Generate one ambient music track via Beatoven on fal.ai and save as MP3.

    Args:
        output_mp3: Destination .mp3 path.
        prompt:     Text description of the desired music style.
        duration:   Length in seconds (5–150). Default 30s (fast generation).
        seed:       Optional integer seed for reproducibility.
    """
    import fal_client
    import urllib.request
    import tempfile
    import subprocess
    import concurrent.futures

    os.environ["FAL_KEY"] = load_fal_key()

    arguments: dict = {
        "prompt":          prompt,
        "negative_prompt": BEATOVEN_NEGATIVE_PROMPT,
        "duration":        duration,
        "refinement":      100,
        "creativity":      16,
    }
    if seed is not None:
        arguments["seed"] = seed

    print(f"  Generating track via Beatoven ({duration}s, seed={seed})...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
        _fut = _ex.submit(fal_client.run, "beatoven/music-generation", arguments=arguments)
        try:
            result = _fut.result(timeout=300)  # 5-minute hard timeout
        except concurrent.futures.TimeoutError:
            raise RuntimeError("Beatoven API timed out after 5 minutes")

    audio_url = (result.get("audio") or {}).get("url", "")
    if not audio_url:
        raise RuntimeError(f"Beatoven returned no audio URL. Raw result: {result}")

    # Download WAV to a temp file, then convert to MP3 with FFmpeg
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_wav = Path(tmp.name)

    try:
        urllib.request.urlretrieve(audio_url, str(tmp_wav))
        output_mp3.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(tmp_wav),
             "-codec:a", "libmp3lame", "-q:a", "2",
             str(output_mp3)],
            check=True, capture_output=True,
        )
    finally:
        tmp_wav.unlink(missing_ok=True)

    print(f"  Saved: {output_mp3.name}")


def generate_music_stable_audio(
    output_mp3: Path,
    prompt: str,
    duration: float = 180.0,
    seed: int | None = None,
) -> None:
    """
    Generate one ambient music track via fal.ai Stable Audio and save as MP3.

    Args:
        output_mp3: Destination .mp3 path.
        prompt:     Text description of the desired music style.
        duration:   Length in seconds (max 190). Default 180s (3 min per track).
        seed:       Optional integer seed for reproducibility.
    """
    import fal_client
    import urllib.request
    import tempfile
    import subprocess
    import concurrent.futures

    os.environ["FAL_KEY"] = load_fal_key()

    arguments: dict = {
        "prompt":        prompt,
        "seconds_start": 0,
        "seconds_total": min(float(duration), 190.0),
        "steps":         100,
    }
    if seed is not None:
        arguments["seed"] = seed

    print(f"  Generating track via Stable Audio ({duration}s, seed={seed})...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fal_client.run, "fal-ai/stable-audio", arguments=arguments)
        try:
            result = fut.result(timeout=360)
        except concurrent.futures.TimeoutError:
            raise RuntimeError("Stable Audio API timed out after 6 minutes")

    audio_url = (result.get("audio_file") or {}).get("url", "")
    if not audio_url:
        raise RuntimeError(f"Stable Audio returned no audio URL. Raw result: {result}")

    # Download to temp file, convert to MP3 with FFmpeg
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_wav = Path(tmp.name)

    try:
        urllib.request.urlretrieve(audio_url, str(tmp_wav))
        output_mp3.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(tmp_wav),
             "-codec:a", "libmp3lame", "-q:a", "2",
             str(output_mp3)],
            check=True, capture_output=True,
        )
    finally:
        tmp_wav.unlink(missing_ok=True)

    print(f"  Saved: {output_mp3.name}")


# ---------------------------------------------------------------------------
# Crossfade helper (used by both generate_animated_loop_ai and the legacy wrapper)
# ---------------------------------------------------------------------------

# Duration of the dissolve crossfade baked into each loop clip (seconds).
# 1.5s gives the xfade enough time to hide any remaining motion discontinuity
# at the loop point without looking like an obvious fade.
CROSSFADE_DURATION = 1.5


def _apply_crossfade_loop(raw_clip: Path, output: Path, clip_duration: int) -> None:
    """
    Crossfade the end of the clip back into its own beginning so it loops
    invisibly.  The output is clip_duration seconds long; the final
    CROSSFADE_DURATION seconds are a dissolve from the clip's end frame
    into its start frame, hiding the seam when the player loops.
    """
    offset = clip_duration - CROSSFADE_DURATION
    vf = (
        f"[0:v][1:v]xfade=transition=fade:"
        f"duration={CROSSFADE_DURATION}:offset={offset:.3f}[v]"
    )
    base = [
        "ffmpeg", "-y",
        "-i", str(raw_clip), "-i", str(raw_clip),
        "-filter_complex", vf,
        "-map", "[v]",
        "-t", str(clip_duration),
    ]
    result = subprocess.run(
        base + ["-pix_fmt", "yuv420p", "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "21", str(output)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        result = subprocess.run(
            base + ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "21", "-preset", "slow", str(output)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print("ERROR applying crossfade dissolve:")
            print(result.stderr[:2000])
            sys.exit(1)


def _make_loop30(loop_clip: Path, output: Path) -> None:
    """
    Concatenate 3 copies of the seamless loop clip into a 30-second file.
    Each copy already ends with a crossfade into its own beginning, so the
    joins at the 10s and 20s marks are seamless.
    """
    base = [
        "ffmpeg", "-y",
        "-i", str(loop_clip), "-i", str(loop_clip), "-i", str(loop_clip),
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1[v]",
        "-map", "[v]",
    ]
    result = subprocess.run(
        base + ["-pix_fmt", "yuv420p", "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "21", str(output)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        result = subprocess.run(
            base + ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "21", "-preset", "slow", str(output)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print("ERROR building 30-second loop:")
            print(result.stderr[:2000])
            sys.exit(1)


def generate_animated_loop(
    source: Path,
    output: Path,
    motion_prompt: str = KLING_MOTION_PROMPT,
    duration: str = "10",         # "5" or "10" seconds (Kling supports both)
) -> None:
    """
    Animate a still image using Kling v1.6 via fal.ai.
    Only exterior elements (waves, clouds, pool) move; interior stays static.

    Produces two files:
      <output>              -- seamless loop clip (xfade dissolve at loop point)
      <output stem>30.mp4   -- 30-second version (3 x loop, seamless joins)
    """
    import fal_client

    os.environ["FAL_KEY"] = load_fal_key()

    # Upload the source image to fal.ai's storage so Kling can read it
    print("  Uploading image to fal.ai...")
    image_url = fal_client.upload_file(str(source))

    print(f"  Animating with Kling v1.6 ({duration}s)...")
    try:
        result = fal_client.run(
            "fal-ai/kling-video/v1.6/standard/image-to-video",
            arguments={
                "image_url":       image_url,
                "prompt":          motion_prompt,
                "negative_prompt": KLING_NEGATIVE_PROMPT,
                "duration":        duration,      # "5" or "10"
                "aspect_ratio":    "16:9",
            },
        )
    except Exception as e:
        msg = str(e)
        if "Exhausted balance" in msg or "balance" in msg.lower():
            print("ERROR: fal.ai balance exhausted.")
            print("  -> Top up at: fal.ai/dashboard/billing")
        else:
            print(f"ERROR: Kling animation failed: {msg}")
        sys.exit(1)

    video_url = (result.get("video") or {}).get("url", "")
    if not video_url:
        print("ERROR: Kling returned no video.")
        print(result)
        sys.exit(1)

    # Download the raw Kling clip
    raw_clip = output.with_name(output.stem + "_raw.mp4")
    urllib.request.urlretrieve(video_url, str(raw_clip))
    print(f"  Raw clip   : {raw_clip.name}  ({duration}s, Kling v1.6)")

    # Bake in the crossfade dissolve so the clip loops invisibly
    print(f"  Applying {CROSSFADE_DURATION}s crossfade dissolve at loop point...")
    _apply_crossfade_loop(raw_clip, output, int(duration))
    print(f"  Loop file  : {output.name}  (seamless {duration}s)")

    # Build the 30-second version: 3 x seamless loop
    loop30 = output.with_name(output.stem + "30.mp4")
    print("  Building 30s version (3 x loop)...")
    _make_loop30(output, loop30)
    print(f"  Loop 30s   : {loop30.name}  (seamless 30s)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate AI image + YouTube thumbnail + 1920x1080 background."
    )

    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--prompt",
        help="Scene description for fal.ai FLUX Pro  e.g. 'cozy rainy cabin'",
    )
    src.add_argument(
        "--image",
        help="Path to an existing image to use instead of generating one",
    )

    parser.add_argument(
        "--title",      required=True,
        help="Thumbnail title — 1-2 words, Title Case  e.g. 'Deep Focus'",
    )
    parser.add_argument(
        "--output-dir", default=str(DEFAULT_OUT_DIR),
        help=f"Output folder  (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--animate",         action="store_true",
        help="Use an AI model to animate the image (moving waves/clouds)",
    )
    parser.add_argument(
        "--animate-duration", choices=["5", "10"], default="10",
        help="Kling clip length in seconds when using --animate (default: 10)",
    )
    parser.add_argument(
        "--loop-duration", type=int, default=30,
        help="Ken Burns loop length in seconds when NOT using --animate (default: 30)",
    )
    parser.add_argument(
        "--no-loop",         action="store_true",
        help="Skip loop video generation entirely",
    )
    parser.add_argument(
        "--thumbnail-only",  action="store_true",
        help="Generate thumbnail only (skip background + loop)",
    )
    parser.add_argument(
        "--background-only", action="store_true",
        help="Generate background frame only (skip thumbnail + loop)",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = safe_slug(args.title)

    print(f"Generating assets for: {args.title!r}")

    # ── Resolve source image ─────────────────────────────────────────────────
    if args.prompt:
        raw_path = out_dir / f"{slug}_raw.jpg"
        generate_ai_image(args.prompt, raw_path)
        source = raw_path
    else:
        source = Path(args.image)
        if not source.exists():
            print(f"ERROR: image not found: {source}")
            sys.exit(1)

    # ── Produce assets ───────────────────────────────────────────────────────
    only_mode = args.thumbnail_only or args.background_only

    if not args.thumbnail_only:
        generate_background(source, out_dir / f"{slug}_background.png")

    if not args.background_only:
        generate_thumbnail(
            source = source,
            title  = args.title,
            output = out_dir / f"{slug}_thumbnail.jpg",
        )

    if not only_mode and not args.no_loop:
        bg_png = out_dir / f"{slug}_background.png"
        loop_out = out_dir / f"{slug}_loop.mp4"
        if args.animate:
            generate_animated_loop(
                source         = bg_png,
                output         = loop_out,
                duration       = args.animate_duration,
            )
        else:
            generate_loop_video(
                source   = bg_png,
                output   = loop_out,
                duration = args.loop_duration,
            )

    print("Done.")


if __name__ == "__main__":
    main()
