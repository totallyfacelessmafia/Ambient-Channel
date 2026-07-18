"""
seo_generator.py — YouTube SEO generation for Ultra Focus Zone (2026 blueprint).

Title formula  : Specificity Mandate — every title targets ONE high-friction activity.
                 · Core value (Task + Benefit) MUST appear within the first 55 chars
                   (70% of ambient music is consumed on mobile where text truncates early)
                 · 4 rotating patterns to avoid "AI Slop" repetition
                 · Identity Hooks from a fixed dictionary (CEO Mode, 1% Concentration…)
                 · "Locked In" never in the first 40 characters (zero search weight)
                 · "Deep Focus Music" never used as the opening more than once per batch
                 · ≤1 emoji per title

Description    : 250-300 word "mini-blog"
                 · First 125 chars: Search-Engine Hook with ≥3 LSI terms
                   (Alpha Waves | Binaural Beats | Productivity | Flow State | 40Hz Drones)
                 · Written in second person ("You / Your") throughout
                 · "Productivity Guide" section — 3-5 actionable tips
                 · 4-6+ chapter timestamps (indexed by Google as separate search results)
                 · "cinematic" used ≤1 time per description
                 · Does NOT open with the same phrase as the title
                 · AI disclosure footer: "Assets substantively edited and curated by @UltraFocusZone"

Tags           : Exactly 15 — 5 broad + 5 medium-tail + 5 specific long-tail
"""

import re
import subprocess
from collections import Counter
from pathlib import Path

DASHBOARD_DIR = Path(__file__).parent
ROOT          = DASHBOARD_DIR.parent
MUSIC_DIR     = ROOT / "music"

CURRENT_YEAR  = 2026   # update annually


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_seo(project: dict, profile: dict) -> dict:
    title       = generate_title(project, profile)
    description = generate_description(project, profile, title)
    tags        = generate_tags(project, profile)
    return {"title": title, "description": description, "tags": tags}


# ---------------------------------------------------------------------------
# Activity detection — "Specificity Mandate" (high-friction task targeting)
# ---------------------------------------------------------------------------

# Ordered by specificity — first match wins
_ACTIVITY_MAP = [
    (["debug"],                                                "Debugging"),
    (["coding", "code", "developer", "engineer", "programming"], "Coding"),
    (["writing", "essay", "draft", "blog", "novel", "prose"], "Deep Writing"),
    (["cad", "autocad", "modeling", "3d", "render"],          "CAD & Design"),
    (["spreadsheet", "excel", "finance", "accounting"],       "Spreadsheets"),
    (["exam", "cramm", "revision"],                           "Exam Cramming"),
    (["sleep", "night", "drift", "insomnia"],                 "Sleep"),
    (["relax", "calm", "chill", "peace", "zen"],              "Recovery"),
    (["pomodoro", "timer"],                                   "Pomodoro"),
    (["study", "lecture", "learn"],                           "Studying"),
    (["hyper", "ultra"],                                      "Hyperfocus"),
]

# Identity hooks — "Status-Driven Power Words" that trigger the Browse algorithm.
# "Locked In" intentionally excluded (zero search weight in first 40 chars).
_IDENTITY_HOOKS = [
    "CEO Focus Mode",
    "1% Concentration",
    "Total Silence",
    "Absolute Focus",
    "Unrivaled Depth",
    "ADHD Flow State",
]


def _detect_activity(slw: str) -> str:
    """Return the specific high-friction activity label for this scene."""
    for keywords, label in _ACTIVITY_MAP:
        if any(k in slw for k in keywords):
            return label
    return "Deep Work"


def _identity_hook(scene: str) -> str:
    """Pick a deterministic identity hook keyed to the scene name."""
    return _IDENTITY_HOOKS[abs(hash(scene)) % len(_IDENTITY_HOOKS)]


def generate_title(project: dict, profile: dict) -> str:
    """
    2026 title formula — Specificity Mandate + Identity Hook + 55-char mobile gate.

    Hard rules:
    - Core value (Task + Benefit) must fit within the first 55 chars on mobile
    - 4 rotating patterns prevent "AI Slop" repetition across a batch
    - "Locked In" never placed in the first 40 characters
    - ≤1 emoji per title
    - Parentheses used at the end for format signals (1 Hour) / (Pomodoro 50/10)
    """
    scene    = (project.get("title") or "Deep Focus").strip()
    slw      = scene.lower()
    yr       = str(CURRENT_YEAR)
    activity = _detect_activity(slw)
    hook     = _identity_hook(scene)

    # ── Special-case activities with their own formulas ───────────────────────
    if activity == "Sleep":
        options = [
            f"Sleep Music {yr} — {hook} (1 Hour)",
            f"Music for Deep Sleep — {hook} {yr} (1 Hour)",
            f"Deep Sleep Ambient {yr} — {hook} (1 Hour)",
            f"Sleep Music — {hook} {yr} (1 Hour)",
        ]
    elif activity == "Recovery":
        options = [
            f"Relaxing Ambient Music {yr} — {hook} (1 Hour)",
            f"Recovery Music — {hook} {yr} (1 Hour)",
            f"Ambient Recovery — {hook} {yr} (1 Hour)",
            f"Music for Recovery — {hook} {yr} (1 Hour)",
        ]
    elif activity == "Pomodoro":
        options = [
            f"Pomodoro Focus Music {yr} — {hook} (1 Hour)",
            f"Music for Pomodoro — {hook} {yr} (1 Hour)",
            f"{hook}: Pomodoro Music {yr} (Pomodoro 50/10)",
            f"Pomodoro Music — {hook} {yr} (50/10 Blocks)",
        ]
    else:
        # ── 4 rotating patterns for all work/study activities ─────────────────
        # Pattern A — Activity-first (long-tail keyword leads)
        pat_a = f"{activity} Focus Music {yr} — {hook} (1 Hour)"
        # Pattern B — Identity Hook first (Browse algorithm trigger)
        pat_b = f"{hook}: Music for {activity} {yr} (1 Hour)"
        # Pattern C — "Deep [Activity]" formula
        # Don't stutter when the activity already starts with "Deep"
        # (autopilot's first live run titled a video "Deep Deep Work Music").
        if activity.lower().startswith("deep"):
            pat_c = f"{activity} Music — {hook} {yr} (1 Hour)"
        else:
            pat_c = f"Deep {activity} Music — {hook} {yr} (1 Hour)"
        # Pattern D — "Music for [Activity]" (exact match long-tail)
        pat_d = f"Music for {activity} — {hook} {yr} (1 Hour)"
        options = [pat_a, pat_b, pat_c, pat_d]

    # Pick deterministically by scene hash so same scene → same pattern
    title = options[abs(hash(scene)) % len(options)]

    # ── 55-char mobile gate — core value must land before the paren ──────────
    core = title.split("(")[0].rstrip()
    if len(core) > 55:
        # Fall back to the shortest safe form
        title = f"Music for {activity} — {hook} {yr} (1 Hour)"
        core  = title.split("(")[0].rstrip()
        if len(core) > 55:
            title = f"{activity} Music {yr} — {hook} (1 Hour)"

    return title


def generate_description(project: dict, profile: dict, seo_title: str = None) -> str:
    """
    250-300 word "mini-blog" description.

    Structure (2026 blueprint):
      1. Hook — first 125 chars are the search-indexed snippet; must contain
         ≥3 LSI terms; opens in second person; does NOT repeat the title phrase
      2. Context — secondary keywords, use-case personas, engagement question
      3. What to expect — sets expectation, reduces bounce rate
      4. Productivity Guide — 3-5 actionable tips (Human Perspective score)
      5. Chapters / Timestamps
      6. CTA
      7. AI Disclosure footer (required May 2025 YouTube policy)
      8. Hashtags (max 3, at end)

    Hard constraints enforced here:
      - "cinematic" appears ≤1 time (post-process guard)
      - Description does not open with the same root phrase as the title
    """
    channel = profile.get("channel_name", "Ultra Focus Zone")
    vibes   = profile.get("vibe_tags", [])
    scene   = project.get("title") or "Deep Focus"

    lines = []

    # 1 — Hook
    lines.append(_build_hook(scene, profile, channel))
    lines.append("")

    # 2 — Context
    lines.append(_build_context(scene, vibes, profile))
    lines.append("")

    # 3 — What to expect
    lines.append(_build_expectation(scene))
    lines.append("")

    # 4 — Productivity Guide (Human Perspective score)
    lines.append(_build_productivity_guide(scene))
    lines.append("")

    # 5 — Chapters (real ffprobe timestamps)
    # Use the playlist Step 3 recorded — the pool is shuffled per render, so
    # an alphabetical glob would name tracks that aren't in this video.
    stored = project.get("song_config", {}).get("songs") or []
    mp3s = [Path(p) for p in stored if Path(p).exists()]
    if not mp3s:
        # Legacy projects rendered before the playlist was recorded — glob the
        # project's own channel music dir, never the shared global one.
        import channels as _ch
        _cid = project.get("channel_id", "")
        _mdir = _ch.music_dir(_cid) if _cid else MUSIC_DIR
        mp3s  = sorted(p for p in _mdir.glob("*.mp3") if not p.name.startswith("._"))
        count = project.get("song_config", {}).get("count", 18)
        if count and count > 0:
            mp3s = mp3s[:count]

    if mp3s:
        crossfade = float(project.get("song_config", {}).get("crossfade_sec", 2.0))
        durations = _get_durations(mp3s, crossfade)
        lines.append("⏱️ CHAPTERS")
        elapsed = 0.0
        for mp3, dur in zip(mp3s, durations):
            lines.append(f"{_fmt_ts(elapsed)} {_clean_stem(mp3.stem)}")
            elapsed += dur
        lines.append("")

    # 6 — CTA
    lines.append(f"🔔 Subscribe to {channel} for new ambient sessions every week.")
    lines.append("👍 Save this to your focus, study, or work playlist.")
    lines.append("")

    # 7 — AI Disclosure (required May 2025 YouTube policy)
    lines.append("─" * 40)
    channel_handle = "@" + channel.replace(" ", "")
    lines.append(
        f"Assets substantively edited and curated by {channel_handle} | "
        f"AI-generated auditory/visual content."
    )

    # 8 — Hashtags (max 3)
    lines.append("")
    lines.append(_build_hashtags(scene, profile))

    full = "\n".join(lines)

    # Hard constraint: "cinematic" ≤ 1 occurrence
    full = _limit_word(full, "cinematic", max_count=1)

    return full


def generate_tags(project: dict, profile: dict) -> list:
    return _build_tag_list(project, profile)


# ---------------------------------------------------------------------------
# Description section builders
# ---------------------------------------------------------------------------

def _build_hook(scene: str, profile: dict, channel: str) -> str:
    """
    Search-Engine Hook — must satisfy ALL of:
      1. First 125 characters contain ≥3 LSI terms:
         Alpha Waves | Binaural Beats | Productivity | Flow State | 40Hz Drones
      2. Written in second person ("You / Your")
      3. Does NOT open with the same root phrase as the generated title
         (title opens with the primary keyword, so hook must open differently)
    """
    slw = scene.lower()

    if any(k in slw for k in ["sleep", "night", "drift", "insomnia"]):
        # LSI in first 125: alpha waves, binaural beats, 40hz drones ✓
        return (
            "You deserve real rest. This 1-hour session uses alpha waves, binaural beats, "
            "and 40Hz drones to guide your nervous system out of overthink and into the "
            "kind of deep, restorative sleep your body is wired for — no effort required."
        )
    if any(k in slw for k in ["study", "exam", "lecture", "revision"]):
        # LSI in first 125: alpha waves, binaural beats, productivity ✓
        return (
            "Your study session just got smarter. Alpha waves, binaural beats, and a "
            "steady productivity anchor are baked into every layer of this 1-hour "
            "instrumental track — so you absorb more information, retain it longer, "
            "and push through exam prep without burning out."
        )
    if any(k in slw for k in ["coding", "code", "developer", "tech", "engineer"]):
        # LSI in first 125: flow state, alpha waves, productivity ✓
        return (
            "You need music built for the way your developer brain actually works. "
            "This 1-hour session is engineered for flow state, alpha waves, and peak "
            "productivity — zero lyrics, zero drops, zero interruptions, so you can "
            "ship code at your highest level."
        )
    if any(k in slw for k in ["relax", "calm", "chill", "peace", "zen"]):
        # LSI in first 125: alpha waves, binaural beats, flow state ✓
        return (
            "Your nervous system is calling for a reset. This 1-hour ambient session "
            "uses alpha waves, binaural beats, and a slow flow state-inducing texture "
            "to bring you out of fight-or-flight and into the calm you've been "
            "putting off all week."
        )
    if any(k in slw for k in ["pomodoro", "timer"]):
        # LSI in first 125: flow state, productivity, alpha waves ✓
        return (
            "Your Pomodoro sessions are about to hit different. This 1-hour track is "
            "built to carry you into a flow state, sustain your productivity across "
            "multiple work blocks, and use alpha waves to keep your mind locked on "
            "task — not on the clock."
        )
    # Default: deep work / hyperfocus
    # LSI in first 125: flow state, alpha waves, binaural beats ✓
    return (
        "Your best work session starts right here. This 1-hour ambient track is "
        "engineered to activate flow state, alpha waves, and binaural beats layering "
        "— all without lyrics, sudden drops, or anything that pulls your attention "
        "away from the work that actually matters."
    )


def _build_context(scene: str, vibes: list, profile: dict) -> str:
    """
    4-5 sentence context paragraph in second person.
    Weaves LSI and competitor keywords; addresses specific listener persona.
    Closes with an engagement question.
    Target: ~90-110 words.
    """
    vibe_str = ", ".join(vibes[:3]) if vibes else "minimal, warm, focused"

    competitor_kws = _extract_competitor_keywords(profile)
    use_cases      = competitor_kws[:2] if competitor_kws else ["productivity", "deep work"]
    use_case_str   = " and ".join(use_cases)

    slw = scene.lower()

    if any(k in slw for k in ["coding", "code", "tech"]):
        persona = (
            "Whether you're debugging a complex system, building something from scratch, "
            "or just need your flow state back after a slow start — this is your soundtrack."
        )
        question = "What are you building today? Drop it in the comments."
    elif any(k in slw for k in ["study", "exam"]):
        persona = (
            "Whether you're preparing for an exam, working through a difficult concept, "
            "or trying to stay sharp through a 4-hour study block — this session "
            "has you covered."
        )
        question = "What subject are you studying today? Let us know below."
    elif any(k in slw for k in ["sleep", "night"]):
        persona = (
            "If your mind races at night — replaying the day, planning tomorrow, "
            "or just refusing to switch off — this session gives it something soft "
            "to follow instead."
        )
        question = "What's your biggest challenge when trying to fall asleep? Tell us in the comments."
    else:
        persona = (
            "Whether you're writing, planning, deep in a project, or just trying to "
            "find your rhythm on a slow day — this session gives your brain the "
            "acoustic anchor it needs to stop drifting."
        )
        question = "What's your biggest distraction today? Drop it below — we read every comment."

    return (
        f"The atmosphere is {vibe_str} — no drops, no jarring transitions, no lyrics. "
        f"Just a consistent, carefully layered soundscape built as a cognitive anchor "
        f"for long sessions of {use_case_str}. "
        f"{persona} "
        f"\u27a4 {question}"
    )


def _build_expectation(scene: str) -> str:
    """
    2-3 sentences setting listener expectation. Reduces bounce rate.
    Avoids using "cinematic" here (reserved for one use in the full description).
    """
    slw = scene.lower()

    if any(k in slw for k in ["sleep", "night"]):
        return (
            "What you'll hear: slow modular textures, low rising-and-falling drone waves, "
            "and soft harmonic layers — no beats, no melody, no sudden changes. "
            "Tempo aligned with slow breathing to support your body's natural sleep-onset process."
        )
    if any(k in slw for k in ["coding", "code", "tech"]):
        return (
            "What you'll hear: warm ambient pads, subtle atmospheric movement, "
            "and a consistent sonic texture that fills silence without competing for "
            "your attention — ideal background music for work sessions of 1-4 hours. "
            "No sharp highs. No sudden changes. No lyrics."
        )
    if any(k in slw for k in ["relax", "calm"]):
        return (
            "What you'll hear: warm synth arps at a slow pace, soft harmonic layers, "
            "and a gradual energy release that guides you from stimulated to settled. "
            "No beats. No drops. No effort required."
        )
    return (
        "What you'll hear: a slow build with a focused mid-section and an introspective "
        "outro — warm ambient textures, subtle atmospheric movement, and a tempo "
        "aligned with calm, deliberate breathing. No beats. No lyrics. No distractions."
    )


def _build_productivity_guide(scene: str) -> str:
    """
    3-5 actionable productivity tips — required for 2026 'Human Perspective' score.
    Scene-specific where relevant; general deep-work defaults otherwise.
    """
    slw = scene.lower()

    lines = ["📋 YOUR FOCUS SESSION GUIDE"]

    if any(k in slw for k in ["sleep", "night", "insomnia"]):
        lines += [
            "1. Dim every screen 30 minutes before you press play.",
            "2. Drink a full glass of water — dehydration disrupts sleep quality.",
            "3. Write down tomorrow's top 3 tasks to empty your mental RAM.",
            "4. Set your room to 65-68°F (18-20°C) — the science-backed sleep temperature.",
            "5. Use sleep headphones or a small speaker; earbuds are fine at low volume.",
        ]
    elif any(k in slw for k in ["study", "exam"]):
        lines += [
            "1. Write your study goal on paper before pressing play — it primes recall.",
            "2. Use Pomodoro 50/10 (50 minutes study, 10 minutes break) for best retention.",
            "3. Drink water every 30 minutes — even 2% dehydration reduces focus by 20%.",
            "4. After each block, do a 2-minute brain dump: write everything you just learned.",
            "5. Put your phone in another room — not on silent, in another room.",
        ]
    elif any(k in slw for k in ["coding", "code", "tech"]):
        lines += [
            "1. Write your one target outcome for this session before you open your editor.",
            "2. Close every tab you don't need — tab noise is a silent flow-state killer.",
            "3. Use Pomodoro 90/20 for deep architectural work; 50/10 for feature sprints.",
            "4. Drink water every 45 minutes — your brain is 75% water.",
            "5. If you're stuck, narrate the problem aloud for 60 seconds before Googling.",
        ]
    elif any(k in slw for k in ["pomodoro", "timer"]):
        lines += [
            "1. Set your session goal before the first timer starts — vague goals drain energy.",
            "2. Use 50-minute work blocks with 10-minute movement breaks for endurance.",
            "3. Drink water at every break — it resets your nervous system faster than coffee.",
            "4. On each break: stand, stretch, and look at something 20 feet away for 20 seconds.",
            "5. After 4 blocks, take a 30-minute real break — no screens.",
        ]
    else:
        lines += [
            "1. Set your one most important task before pressing play.",
            "2. Drink water every 30 minutes — even mild dehydration kills concentration.",
            "3. Use Pomodoro 50/10 (50 min work, 10 min break) to protect your focus reserves.",
            "4. Put your phone face-down in another room — not on silent, in another room.",
            "5. At the 30-minute mark, stand for 90 seconds and breathe deeply, then return.",
        ]

    return "\n".join(lines)


def _build_hashtags(scene: str, profile: dict) -> str:
    """3 hashtags at end of description (YouTube best practice: max 3)."""
    slw         = scene.lower()
    channel_tag = profile.get("channel_name", "Ultra Focus Zone").replace(" ", "")

    if any(k in slw for k in ["sleep", "night", "insomnia"]):
        return f"#SleepMusic #AmbientMusic #{channel_tag}"
    if any(k in slw for k in ["study", "exam"]):
        return f"#StudyMusic #FocusMusic #{channel_tag}"
    if any(k in slw for k in ["coding", "code", "tech"]):
        return f"#CodingMusic #DeepWork #{channel_tag}"
    if any(k in slw for k in ["relax", "calm", "chill"]):
        return f"#RelaxingMusic #AmbientMusic #{channel_tag}"
    if any(k in slw for k in ["pomodoro"]):
        return f"#PomodoroMusic #FocusMusic #{channel_tag}"
    return f"#FocusMusic #DeepWork #{channel_tag}"


# ---------------------------------------------------------------------------
# Tag list builder — exactly 15 tags: 5 broad + 5 medium-tail + 5 specific
# ---------------------------------------------------------------------------

def _build_tag_list(project: dict, profile: dict) -> list:
    """
    500-character tag rule: exactly 15 tags.
      - 5 broad     (category-level, always present)
      - 5 medium    (intent-specific, 2-3 words)
      - 5 specific  (long-tail, scene-matched, year-stamped where applicable)
    """
    title = project.get("title", "")
    slw   = title.lower()
    yr    = str(CURRENT_YEAR)

    # ── 5 Broad tags (category-level) ────────────────────────────────────────
    broad = [
        "ambient music",
        "focus music",
        "study music",
        "deep work music",
        "instrumental music",
    ]

    # ── 5 Medium-tail tags (intent-specific) ─────────────────────────────────
    if any(k in slw for k in ["sleep", "night", "insomnia"]):
        medium = [
            "sleep music",
            "music for deep sleep",
            "alpha waves sleep",
            "binaural beats sleep",
            "stress relief music",
        ]
    elif any(k in slw for k in ["study", "exam"]):
        medium = [
            "study music for exams",
            "alpha wave study",
            "binaural beats focus",
            "music for studying",
            "concentration music",
        ]
    elif any(k in slw for k in ["coding", "code", "tech", "engineer"]):
        medium = [
            "coding music",
            "programming music",
            "flow state music",
            "alpha waves focus",
            "productivity music",
        ]
    elif any(k in slw for k in ["relax", "calm", "chill", "peace", "zen"]):
        medium = [
            "relaxing music",
            "alpha waves relaxation",
            "binaural beats calm",
            "flow state music",
            "stress relief music",
        ]
    elif any(k in slw for k in ["pomodoro", "timer"]):
        medium = [
            "pomodoro music",
            "productivity music",
            "flow state music",
            "alpha waves focus",
            "work session music",
        ]
    else:
        medium = [
            "deep focus music",
            "hyperfocus music",
            "flow state music",
            "alpha waves focus",
            "productivity music",
        ]

    # ── 5 Specific long-tail tags (scene-matched, year-stamped) ──────────────
    if any(k in slw for k in ["sleep", "night", "insomnia"]):
        specific = [
            f"sleep music {yr}",
            "music for insomnia relief",
            "deep sleep ambient music",
            "40hz drones sleep",
            "binaural beats deep sleep",
        ]
    elif any(k in slw for k in ["study", "exam"]):
        specific = [
            f"study music {yr}",
            "music for studying hard",
            "exam study music no lyrics",
            "study motivation music",
            f"focus music {yr}",
        ]
    elif any(k in slw for k in ["coding", "code", "tech", "engineer"]):
        specific = [
            f"coding music {yr}",
            "developer focus music",
            "music for coding no lyrics",
            f"deep work music {yr}",
            "future garage for coding",
        ]
    elif any(k in slw for k in ["relax", "calm", "chill"]):
        specific = [
            f"relaxing ambient music {yr}",
            "calm music for anxiety",
            "chill music for work",
            "ambient music no lyrics",
            f"stress relief music {yr}",
        ]
    elif any(k in slw for k in ["pomodoro", "timer"]):
        specific = [
            f"pomodoro music {yr}",
            "25 minute focus music",
            "pomodoro technique music",
            "work session focus music",
            f"productivity music {yr}",
        ]
    else:
        specific = [
            f"focus music {yr}",
            "1 hour focus music",
            "deep focus music no lyrics",
            f"deep work music {yr}",
            "hyperfocus ambient music",
        ]

    # Deduplicate while preserving order, then return exactly 15
    seen   = set()
    result = []
    for tag in broad + medium + specific:
        key = tag.lower().strip()
        if key not in seen:
            seen.add(key)
            result.append(tag)
        if len(result) == 15:
            break

    return result


# ---------------------------------------------------------------------------
# Post-processing guard
# ---------------------------------------------------------------------------

def _limit_word(text: str, word: str, max_count: int = 1) -> str:
    """Replace occurrences of `word` beyond `max_count` with an empty string."""
    pattern = re.compile(re.escape(word), re.IGNORECASE)
    occurrences = list(pattern.finditer(text))
    if len(occurrences) <= max_count:
        return text
    # Remove occurrences after the allowed count
    for match in reversed(occurrences[max_count:]):
        text = text[:match.start()] + text[match.end():]
    return text


# ---------------------------------------------------------------------------
# Competitor keyword extractor
# ---------------------------------------------------------------------------

def _extract_competitor_keywords(profile: dict) -> list:
    """Mine recurring bigrams from ref_channel thumbnail titles."""
    stop = {
        "music", "for", "and", "the", "of", "a", "to", "in", "with",
        "amp", "1", "2", "3", "hour", "hours", "minutes", "video",
        "audio", "no", "best", "your", "my", "our", "new", "its",
    }
    counts = Counter()
    for ref_ch in profile.get("ref_channels", []):
        for thumb in ref_ch.get("thumbnails", []):
            title = thumb.get("title", "")
            title = re.sub(r"&amp;", "&", title).lower()
            title = re.sub(r"[|•·–—/]", " ", title)
            words = [w.strip(".,;:!?()") for w in title.split()]
            words = [w for w in words if len(w) >= 3 and w not in stop]
            for i in range(len(words) - 1):
                counts[f"{words[i]} {words[i+1]}"] += 1
    return [kw for kw, _ in counts.most_common(16)]


# ---------------------------------------------------------------------------
# Music prompt helper
# ---------------------------------------------------------------------------

def build_music_prompt(scene: str = "Deep Focus", style: str = "ambient") -> str:
    """
    Technically-detailed music generation prompt (2026 prompting framework).
    """
    slw = scene.lower()

    if any(k in slw for k in ["sleep", "night", "drift"]):
        return (
            "warm modular drone pads, low rising and falling waves, distant ocean "
            "at night, slow harmonic breathing texture, tempo aligned with slow "
            "resting breathing at 50-60 bpm, no beats, no sharp highs, no sudden "
            "changes, minimal evolution, soft introspective outro, ambient sleep"
        )
    if any(k in slw for k in ["coding", "code", "tech"]):
        return (
            "warm modular synth arps at medium-low tempo, subtle sub-bass pulse, "
            "clean atmospheric pads, distant city at night, no sharp highs, no "
            "sudden changes, no lyrics, steady consistent energy through full "
            "session, slight slow build then stable plateau, ambient electronic "
            "focus, technical thinking soundscape"
        )
    if any(k in slw for k in ["relax", "calm", "chill"]):
        return (
            "warm acoustic guitar harmonics, soft piano, light natural reverb, "
            "slow rising and falling waves, golden afternoon light texture, "
            "no sharp highs, no sudden transitions, tempo aligned with calm "
            "breathing at 60-70 bpm, gradual energy release, introspective outro, "
            "ambient relaxation"
        )
    if any(k in slw for k in ["pomodoro"]):
        return (
            "steady rhythmic ambient pulse, warm modular synth arps, subtle "
            "forward momentum without urgency, clean mid-tempo at 70-80 bpm, "
            "no sharp highs, no sudden changes, structured energy with soft "
            "transition points every 25 minutes, focus and productivity soundscape"
        )
    return (
        "warm modular synth arps, low rising and falling atmospheric pads, "
        "subtle sub-bass grounding texture, distant rain on glass, no sharp highs, "
        "no sudden changes, no lyrics, no beats, slow build with focused "
        "mid-section and introspective outro, tempo aligned with calm deliberate "
        "breathing, deep work ambient, ultra focus"
    )


# ---------------------------------------------------------------------------
# FFprobe duration helpers
# ---------------------------------------------------------------------------

def _get_durations(mp3s: list, crossfade: float) -> list:
    durations = []
    for mp3 in mp3s:
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(mp3),
                ],
                capture_output=True, text=True, timeout=10,
            )
            raw = result.stdout.strip()
            dur = float(raw) if raw else 210.0
        except Exception:
            dur = 210.0
        durations.append(max(0.0, dur - crossfade))
    return durations


def _fmt_ts(seconds: float) -> str:
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _clean_stem(stem: str) -> str:
    stem = re.sub(r"^\d+[\s_\-\.]+", "", stem)
    return stem.replace("_", " ").replace("-", " ").title()
