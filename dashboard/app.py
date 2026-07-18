"""
app.py — Flask dashboard for the UltraFocusZone video pipeline.

Run:
    cd "d:\\TFC\\Ultra Focus\\dashboard"
    py app.py

Then open http://localhost:5000
"""

import calendar as _calendar
import json
import mimetypes
from datetime import date, timedelta
from pathlib import Path

from flask import (Flask, abort, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for)

import auth
import autopilot
import billing
import channel_profile
import channels as ch
import state
import tasks
import tiers

import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or auth.get_secret_key()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024   # 20 MB max upload
_is_dev = os.environ.get("FLASK_ENV") != "production"
# Always serve static uncached from Flask itself: hour-long browser caching
# made CSS/JS updates invisibly stale (cost us repeated ghost-bug rounds).
# Long-lived caching belongs to a CDN or front proxy at real deployment.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# Jinja2 helper: extract filename from an absolute path string
app.jinja_env.filters["basename"] = lambda p: Path(p).name if p else ""


def _format_views(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


app.jinja_env.filters["format_views"] = _format_views

_CONFIG_PATH = Path(__file__).parent.parent / "UltraFocusZone_Automation" / "config.json"
_CONFIG_DEFAULT = _CONFIG_PATH.with_name("config.default.json")

# First-run: copy config.default.json → config.json if it doesn't exist
if not _CONFIG_PATH.exists() and _CONFIG_DEFAULT.exists():
    import shutil
    shutil.copy2(str(_CONFIG_DEFAULT), str(_CONFIG_PATH))
    print("[setup] Created config.json from defaults.")


def _is_operator() -> bool:
    """Return True if this instance has Suno enabled (operator account only)."""
    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return bool(cfg.get("suno", {}).get("enabled", False))
    except Exception:
        return False

# Allowed asset directories (security: only serve files from these)
ASSET_DIRS = [
    Path(__file__).parent.parent / "images" / "generated",
    Path(__file__).parent.parent / "Edited Videos",
]

MUSIC_DIR = Path(__file__).parent.parent / "music"


# ---------------------------------------------------------------------------
# On startup — recover any tasks that were running when server last died
# ---------------------------------------------------------------------------

state.recover_crashed_tasks()
ch.migrate_from_singleton()   # idempotent; runs once to create channels.json


def _migrate_ownership():
    """One-time: assign any ownerless channel to the sole existing user.
    Only runs when there is exactly one account (the pre-multi-tenant case);
    with multiple users an ownerless channel can't be attributed safely, so it
    is left ownerless (invisible), which is the safe default."""
    emails = list(auth._load().get("users", {}).keys())
    if len(emails) != 1:
        return
    owner = emails[0]
    for c in ch.all_channels():
        if not c.get("owner"):
            ch.backfill_owner(c["id"], owner)


def _migrate_media():
    """One-time: move the legacy GLOBAL music library + image-library file into
    the sole channel's private, per-tenant location. Only runs with exactly one
    user AND one channel (unambiguous); idempotent (skips if already moved)."""
    import shutil as _sh
    emails = list(auth._load().get("users", {}).keys())
    if len(emails) != 1:
        return
    chans = ch.channels_for_user(emails[0])
    if len(chans) != 1:
        return
    cid = chans[0]["id"]

    # Root-level music/*.mp3 → music/<cid>/
    dest = ch.music_dir(cid)
    moved = 0
    for f in MUSIC_DIR.glob("*.mp3"):
        target = dest / f.name
        if not target.exists():
            _sh.move(str(f), str(target))
            moved += 1
    for junk in MUSIC_DIR.glob("._*"):          # macOS AppleDouble sidecars
        if junk.is_file():
            try:
                junk.unlink()
            except OSError:
                pass
    if moved:
        print(f"[migrate] moved {moved} songs into music/{cid}/")

    # Legacy global image_library.json → image_library_<cid>.json
    legacy_lib = Path(__file__).parent / "image_library.json"
    per_channel_lib = ch.image_library_file(cid)
    if legacy_lib.exists() and not per_channel_lib.exists():
        _sh.move(str(legacy_lib), str(per_channel_lib))
        print(f"[migrate] image library → {per_channel_lib.name}")


_migrate_ownership()
_migrate_media()
auth.migrate_plans()   # stamp the pre-billing operator account as 'owner'


# ---------------------------------------------------------------------------
# Channel helpers
# ---------------------------------------------------------------------------

def _require_channel():
    """Return (cid, channel_dict) for the session's active channel, or (None, None).
    Only returns a channel the current user owns — otherwise clears it."""
    cid = session.get("channel_id")
    user = session.get("user", "")
    channel = ch.get_channel(cid) if cid else None
    if channel is not None and channel.get("owner") != user:
        # Session points at someone else's / an ownerless channel — drop it.
        session.pop("channel_id", None)
        return None, None
    return cid, channel


def _looks_like_pid(v: str) -> bool:
    return len(v) == 8 and all(c in "0123456789abcdef" for c in v)


@app.before_request
def enforce_ownership():
    """Multi-tenancy guard: a user may only touch resources they own.

    Name-INDEPENDENT: it inspects the ID *values* in the URL (any project id
    or ch_ channel id, whatever the route names the param), so a future route
    like /project/<id> can't silently escape the guard. Aborts 403 only when
    the referenced resource exists and the user doesn't own it."""
    user = session.get("user")
    if not user:
        return
    for val in (request.view_args or {}).values():
        if not isinstance(val, str):
            continue
        if _looks_like_pid(val):
            proj = state.get_project(val)
            if proj is not None and not ch.user_owns_channel(proj.get("channel_id", ""), user):
                abort(403)
        elif val.startswith("ch_"):
            if ch.get_channel(val) is not None and not ch.user_owns_channel(val, user):
                abort(403)


@app.before_request
def inject_channel_context():
    """Inject g.active_channel, g.active_cid, g.all_channels into every request."""
    user = session.get("user")
    if not user:
        return
    all_chs = ch.channels_for_user(user)     # only this user's channels
    cid = session.get("channel_id")
    # Drop a stale/foreign active channel; auto-select the sole owned one.
    if cid and not any(c["id"] == cid for c in all_chs):
        session.pop("channel_id", None)
        cid = None
    if not cid and len(all_chs) == 1:
        cid = all_chs[0]["id"]
        session["channel_id"] = cid
    g.active_channel = ch.get_channel(cid) if cid else None
    g.active_cid     = cid or ""
    g.all_channels   = all_chs
    # Research outlier badge — quick check (only reads cache file if it exists)
    try:
        import research as rs
        g.research_unseen = rs.get_unseen_outlier_count(cid) if cid else 0
    except Exception:
        g.research_unseen = 0
    g.is_operator = _is_operator()


# ---------------------------------------------------------------------------
# Channel routes
# ---------------------------------------------------------------------------

@app.route("/channels/switch/<cid>", methods=["POST"])
@auth.login_required
def channel_switch(cid):
    if not ch.user_owns_channel(cid, session.get("user", "")):
        return jsonify(ok=False, error="Unknown channel"), 404
    session["channel_id"] = cid
    return jsonify(ok=True)


@app.route("/channels/new", methods=["POST"])
@auth.login_required
def channel_new():
    allowed, why = tiers.can_add_channel(session.get("user", ""))
    if not allowed:
        return redirect(url_for("index", err=why))
    new_ch = ch.create_channel(owner=session.get("user", ""))
    session["channel_id"] = new_ch["id"]
    return redirect(url_for("onboarding"))


@app.route("/api/channels")
@auth.login_required
def api_channels():
    return jsonify(channels=[
        {"id": c["id"], "channel_name": c.get("channel_name", ""), "completed": c.get("completed", False)}
        for c in ch.channels_for_user(session.get("user", ""))
    ])


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if session.get("user"):
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if auth.verify_password(email, password):
            session["user"] = email
            session.pop("channel_id", None)   # never inherit a prior user's channel
            # Auto-select single channel; redirect to onboarding if none complete
            all_chs = ch.channels_for_user(email)
            if not all_chs:
                return redirect(url_for("onboarding"))
            if len(all_chs) == 1:
                session["channel_id"] = all_chs[0]["id"]
            cid = session.get("channel_id", "")
            if not ch.is_complete(cid):
                return redirect(url_for("onboarding"))
            return redirect(url_for("index"))
        else:
            error = "Invalid email or password."

    return render_template("login.html", error=error)


@app.route("/register", methods=["GET", "POST"])
def register_page():
    if session.get("user"):
        return redirect(url_for("index"))

    error = None
    email = ""
    if request.method == "POST":
        email     = request.form.get("email", "").strip().lower()
        password  = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        if not email or not password:
            error = "Email and password are required."
        elif password != password2:
            error = "Passwords do not match."
        else:
            try:
                auth.create_user(email, password)
                session["user"] = email
                # Auto-create their first channel and go to onboarding
                new_ch = ch.create_channel(owner=email)
                session["channel_id"] = new_ch["id"]
                return redirect(url_for("onboarding"))
            except ValueError as e:
                error = str(e)

    return render_template("register.html", error=error, email=email)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password_page():
    error = None
    success = None
    reset_link = None

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            error = "Please enter your email address."
        elif not auth.user_exists(email):
            # Don't reveal whether the email exists — just show success
            success = "If an account exists with that email, a reset link has been generated."
        else:
            token = auth.generate_reset_token(email)
            if token:
                reset_url = url_for("reset_password_page", token=token, _external=True)
                success = "Reset link generated. Valid for 1 hour."
                reset_link = reset_url
            else:
                error = "Could not generate reset token. Please try again."

    return render_template("forgot_password.html", error=error, success=success, reset_link=reset_link)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password_page(token):
    email = auth.find_email_by_reset_token(token)
    if not email:
        return render_template("reset_password.html", expired=True, token=token, email=None, error=None)

    error = None
    if request.method == "POST":
        password  = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        if not password or len(password) < 6:
            error = "Password must be at least 6 characters."
        elif password != password2:
            error = "Passwords do not match."
        else:
            if auth.reset_with_token(token, password):
                session["user"] = email
                return redirect(url_for("index"))
            else:
                return render_template("reset_password.html", expired=True, token=token, email=None, error=None)

    return render_template("reset_password.html", expired=False, token=token, email=email, error=error)


@app.route("/terms")
def terms_page():
    return render_template("terms.html")


@app.route("/privacy")
def privacy_page():
    return render_template("privacy.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------

@app.route("/onboarding", methods=["GET"])
@auth.login_required
def onboarding():
    cid, profile = _require_channel()
    if not cid:
        # No channel yet — create a blank one to onboard into
        new_ch = ch.create_channel(owner=session.get("user", ""))
        session["channel_id"] = new_ch["id"]
        cid, profile = new_ch["id"], new_ch
    import youtube_upload as yu
    yt_status = yu.get_auth_status(cid or "")
    return render_template(
        "onboarding.html",
        profile=profile,
        cid=cid,
        vibe_options=ch.VIBE_OPTIONS,
        is_new_channel=not profile.get("completed"),
        yt_connected=yt_status.get("connected", False),
        yt_configured=yt_status.get("configured", False),
    )


@app.route("/onboarding/save", methods=["POST"])
@auth.login_required
def onboarding_save():
    """Save the channel profile form."""
    cid, profile = _require_channel()
    if not cid:
        return redirect(url_for("onboarding"))

    profile["channel_name"]  = request.form.get("channel_name", "").strip()
    profile["subtitle"]      = request.form.get("subtitle", "").strip()
    profile["channel_url"]   = request.form.get("channel_url", "").strip()
    profile["niche"]         = request.form.get("niche", "").strip()
    profile["color_notes"]   = request.form.get("color_notes", "").strip()
    profile["scene_notes"]   = request.form.get("scene_notes", "").strip()
    profile["vibe_tags"]     = request.form.getlist("vibe_tags")
    profile["music_style"]   = request.form.get("music_style", "").strip() or profile.get("music_style", "")
    profile["overlay_style"] = request.form.get("overlay_style", "default")
    profile["completed"]     = True

    ch.save_channel(cid, profile)
    return redirect(url_for("index"))


@app.route("/onboarding/fetch-channel", methods=["POST"])
@auth.login_required
def onboarding_fetch_channel():
    """Resolve a channel URL → channel ID + info + recent thumbnails."""
    import youtube_api as ya

    url = request.form.get("channel_url", "").strip()
    if not url:
        return jsonify(ok=False, error="No URL provided.")

    try:
        channel_id = ya.resolve_channel_id(url)
        info       = ya.fetch_channel_info(channel_id)
        thumbnails = ya.fetch_channel_thumbnails(channel_id, n=8)
    except RuntimeError as e:
        return jsonify(ok=False, error=str(e))

    cid, profile = _require_channel()
    if profile is None:
        profile = {}
    profile["channel_id"]         = channel_id
    profile["channel_thumbnails"] = thumbnails
    if info.get("name") and not profile.get("channel_name"):
        profile["channel_name"] = info["name"]
    if cid:
        ch.save_channel(cid, profile)

    return jsonify(
        ok=True,
        channel_id=channel_id,
        name=info.get("name", ""),
        description=info.get("description", ""),
        thumbnails=thumbnails,
    )


@app.route("/onboarding/fetch-ref-channel", methods=["POST"])
@auth.login_required
def onboarding_fetch_ref_channel():
    """Fetch thumbnails from a reference channel (for style inspiration)."""
    import youtube_api as ya

    url = request.form.get("channel_url", "").strip()
    if not url:
        return jsonify(ok=False, error="No URL provided.")

    try:
        channel_id = ya.resolve_channel_id(url)
        info       = ya.fetch_channel_info(channel_id)
        thumbnails = ya.fetch_channel_thumbnails(channel_id, n=6)
    except RuntimeError as e:
        return jsonify(ok=False, error=str(e))

    cid, profile = _require_channel()
    if profile is None:
        profile = {}
    ref_channels = profile.get("ref_channels", [])
    ref_channels = [r for r in ref_channels if r.get("channel_id") != channel_id]
    ref_channels.append({
        "name":       info.get("name", url),
        "channel_id": channel_id,
        "thumbnails": thumbnails,
    })
    profile["ref_channels"] = ref_channels
    if cid:
        ch.save_channel(cid, profile)

    return jsonify(
        ok=True,
        name=info.get("name", url),
        channel_id=channel_id,
        thumbnails=thumbnails,
    )


@app.route("/onboarding/upload-ref", methods=["POST"])
@auth.login_required
def onboarding_upload_ref():
    """Upload a thumbnail image as a style reference."""
    files = request.files.getlist("images")
    if not files:
        return jsonify(ok=False, error="No files uploaded.")

    cid, profile = _require_channel()
    if not cid:
        return jsonify(ok=False, error="No active channel.")
    if profile is None:
        profile = {}

    saved = []
    for f in files:
        if f and f.filename:
            fname = ch.save_style_ref(cid, f)
            saved.append(fname)
            if fname not in profile.get("style_refs", []):
                profile.setdefault("style_refs", []).append(fname)
    ch.save_channel(cid, profile)

    return jsonify(ok=True, files=saved)


@app.route("/onboarding/delete-ref", methods=["POST"])
@auth.login_required
def onboarding_delete_ref():
    """Remove a style reference image."""
    filename = request.form.get("filename", "")
    if not filename:
        return jsonify(ok=False, error="No filename.")
    cid, profile = _require_channel()
    if not cid:
        return jsonify(ok=False, error="No active channel.")
    ch.delete_style_ref(cid, filename)
    if profile is None:
        profile = {}
    profile["style_refs"] = [r for r in profile.get("style_refs", []) if r != filename]
    ch.save_channel(cid, profile)
    return jsonify(ok=True)


@app.route("/onboarding/delete-ref-channel", methods=["POST"])
@auth.login_required
def onboarding_delete_ref_channel():
    """Remove a reference channel from the profile."""
    channel_id = request.form.get("channel_id", "")
    cid, profile = _require_channel()
    if not cid or profile is None:
        return jsonify(ok=False, error="No active channel.")
    profile["ref_channels"] = [
        r for r in profile.get("ref_channels", [])
        if r.get("channel_id") != channel_id
    ]
    ch.save_channel(cid, profile)
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Channel logo upload / delete / serve
# ---------------------------------------------------------------------------

@app.route("/onboarding/upload-logo", methods=["POST"])
@auth.login_required
def onboarding_upload_logo():
    """Upload a PNG or GIF logo for the channel."""
    cid, profile = _require_channel()
    if not cid:
        return jsonify(ok=False, error="No active channel.")
    f = request.files.get("logo")
    if not f or not f.filename:
        return jsonify(ok=False, error="No file received.")
    ext = Path(f.filename).suffix.lower()
    if ext not in {".png", ".gif"}:
        return jsonify(ok=False, error="Only PNG or GIF logos are supported.")
    # Delete old logo file if any
    old = profile.get("logo_filename", "")
    if old:
        ch.delete_logo(cid, old)
    filename = ch.save_logo(cid, f)
    profile["logo_filename"] = filename
    ch.save_channel(cid, profile)
    return jsonify(ok=True, filename=filename)


@app.route("/onboarding/delete-logo", methods=["POST"])
@auth.login_required
def onboarding_delete_logo():
    """Remove the channel logo."""
    cid, profile = _require_channel()
    if not cid or profile is None:
        return jsonify(ok=False, error="No active channel.")
    filename = profile.get("logo_filename", "")
    if filename:
        ch.delete_logo(cid, filename)
        profile["logo_filename"] = ""
        ch.save_channel(cid, profile)
    return jsonify(ok=True)


@app.route("/channel-logo/<path:filename>")
@auth.login_required
def serve_channel_logo(filename):
    from flask import send_file
    cid, _ = _require_channel()
    if not cid:
        return ("", 404)
    target = ch.logos_dir(cid) / Path(filename).name
    if not target.exists() or not target.is_file():
        return ("", 404)
    return send_file(str(target))


# ---------------------------------------------------------------------------
# Style reference file serving
# ---------------------------------------------------------------------------

@app.route("/style-refs/<path:filename>")
@auth.login_required
def serve_style_ref(filename):
    fname = Path(filename).name
    cid, _ = _require_channel()
    # Try channel-scoped dir first, then fall back to the legacy flat dir
    if cid:
        target = ch.style_refs_dir(cid) / fname
    else:
        target = channel_profile.STYLE_REFS_DIR / fname
    if not target.exists() or not target.is_file():
        # Legacy fallback
        target = channel_profile.STYLE_REFS_DIR / fname
    if not target.exists() or not target.is_file():
        abort(404)
    mime, _ = mimetypes.guess_type(str(target))
    return send_file(str(target), mimetype=mime or "image/jpeg")


# ---------------------------------------------------------------------------
# Public homepage
# ---------------------------------------------------------------------------

@app.route("/home")
def homepage():
    if session.get("user"):
        return redirect(url_for("index"))
    return render_template("home.html")


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if not session.get("user"):
        return render_template("home.html")
    cid, profile = _require_channel()
    if not cid or not ch.is_complete(cid):
        return redirect(url_for("onboarding"))

    # Parse requested month from query params (default = this month)
    today = date.today()
    try:
        year  = int(request.args.get("y", today.year))
        month = int(request.args.get("m", today.month))
        # Clamp to valid range
        if month < 1:  month = 12; year -= 1
        if month > 12: month = 1;  year += 1
    except ValueError:
        year, month = today.year, today.month

    # Prev / next month links
    prev = date(year, month, 1) - timedelta(days=1)
    nxt  = date(year, month, 28) + timedelta(days=4)
    nxt  = nxt.replace(day=1)

    # Build calendar grid (list of 6 weeks, each 7 days; 0 = padding)
    cal_weeks = _calendar.monthcalendar(year, month)

    # Bucket projects by scheduled_date (scoped to active channel)
    all_proj    = state.projects_for_channel(cid)
    scheduled   = {}   # "YYYY-MM-DD" → [project, ...]
    unscheduled = []
    for p in all_proj:
        d = p.get("scheduled_date")
        is_uploaded = p.get("youtube", {}).get("upload_status") == "done"
        if d:
            scheduled.setdefault(d, []).append(p)
        elif not is_uploaded:
            # Not yet uploaded and no date → show in the pipeline sidebar
            unscheduled.append(p)
        # Already uploaded with no date → omit from calendar (they're done)

    month_name = _calendar.month_name[month]
    prompt_suggestions = []
    if profile.get("niche"):
        prompt_suggestions.append(profile["niche"])
    prompt_suggestions.extend(profile.get("vibe_tags", []))
    if profile.get("scene_notes"):
        prompt_suggestions.extend([s.strip() for s in profile["scene_notes"].split(",") if s.strip()])
    # preserve order while deduplicating
    seen = set()
    prompt_suggestions = [s for s in prompt_suggestions if not (s in seen or seen.add(s))]

    # Serialise all projects for the list-view JS variable (basenames only)
    projects_data = []
    for p in all_proj:
        files_out = {k: Path(v).name if v else None for k, v in p["files"].items()}
        projects_data.append({
            "id":             p["id"],
            "title":          p.get("title", ""),
            "step":           p["step"],
            "status":         p["status"],
            "scheduled_date": p.get("scheduled_date"),
            "files":          files_out,
            "seo":            p.get("seo", {}),
            "youtube":        p.get("youtube", {}),
        })

    # Quick stats for the right sidebar
    stats = {
        "total":     len(all_proj),
        "active":    sum(1 for p in all_proj if p.get("status") == "running"),
        "published": sum(1 for p in all_proj if p.get("youtube", {}).get("upload_status") == "done"),
        "scheduled": sum(1 for p in all_proj if p.get("scheduled_date")),
    }

    # ── Concept-shell overview data ─────────────────────────────────────────
    from datetime import datetime as _dt, timezone as _tz
    now_utc = _dt.now(_tz.utc)
    week_end = today + timedelta(days=7)

    def _basename(v):
        return Path(v).name if v else None

    awaiting, this_week = [], []
    for p in all_proj:
        yt = p.get("youtube", {})
        pub = yt.get("scheduled_publish_at")
        # Awaiting your OK: uploaded private, publish time still in the future
        if yt.get("upload_status") == "done" and pub:
            try:
                pub_dt = _dt.fromisoformat(pub.replace("Z", "+00:00"))
                if pub_dt > now_utc:
                    awaiting.append({
                        "id": p["id"],
                        "title": (p.get("seo", {}).get("title") or p.get("title") or "Untitled"),
                        "publish_at": pub,
                        "thumbnail": _basename(p["files"].get("thumbnail")),
                    })
            except ValueError:
                pass
        # This week: scheduled within the next 7 days
        sd = p.get("scheduled_date")
        if sd:
            try:
                d = date.fromisoformat(sd)
                if today <= d <= week_end:
                    if yt.get("upload_status") == "done":
                        st = ("live" if not p.get("youtube", {}).get("scheduled_publish_at") else "scheduled")
                    elif p.get("status") == "running":
                        st = "rendering"
                    elif p.get("status") == "error":
                        st = "error"
                    else:
                        st = "queued"
                    this_week.append({
                        "id": p["id"], "date": sd,
                        "title": p.get("title") or "Untitled", "state": st,
                    })
            except ValueError:
                pass
    awaiting.sort(key=lambda a: a["publish_at"])
    this_week.sort(key=lambda w: w["date"])

    # Recent / active projects strip (most recently touched, unfinished first)
    def _proj_status_label(p):
        yt = p.get("youtube", {})
        if yt.get("upload_status") == "done":
            pub = yt.get("scheduled_publish_at")
            return f"Scheduled · {tasks.utc_to_local_date(pub)}" if pub else "Published"
        if p.get("status") == "running":
            step = p.get("task", {}).get("step_running")
            return f"Running · step {step}" if step else "Running"
        if p.get("status") == "error":
            return "Needs attention"
        return f"Draft · step {p.get('step', 1)} of 4"

    recent = sorted(all_proj, key=lambda p: p.get("updated_at", ""), reverse=True)[:3]
    recent_projects = [{
        "id": p["id"],
        "title": p.get("title") or "Untitled",
        "status_label": _proj_status_label(p),
        "thumbnail": _basename(p["files"].get("thumbnail") or p["files"].get("raw_image")),
    } for p in recent]

    # Usage this month (real ledger where present)
    month_prefix = f"{year:04d}-{month:02d}"
    month_projs = [p for p in all_proj if (p.get("scheduled_date") or "").startswith(month_prefix)]
    costs = [p.get("ledger_total") for p in all_proj if p.get("ledger_total")]
    qs = tiers.quota_status(session.get("user", ""))
    usage = {
        "count": qs["used"],
        "target": qs["limit"] if qs["limit"] is not None else max(qs["used"], 1),
        "unlimited": qs["limit"] is None,
        "plan_label": qs["label"],
        "avg_cost": round(sum(costs) / len(costs), 2) if costs else None,
        "published": stats["published"],
    }

    return render_template(
        "index.html",
        year=year, month=month,
        month_name=month_name,
        cal_weeks=cal_weeks,
        scheduled=scheduled,
        unscheduled=unscheduled,
        prev_y=prev.year, prev_m=prev.month,
        next_y=nxt.year,  next_m=nxt.month,
        today=today.isoformat(),
        prompt_suggestions=prompt_suggestions[:12],
        projects_data=projects_data,
        stats=stats,
        channel_name=(profile.get("channel_name") or "Your Channel"),
        awaiting=awaiting,
        this_week=this_week,
        recent_projects=recent_projects,
        usage=usage,
    )


@app.route("/autopilot/new", methods=["POST"])
@auth.login_required
def autopilot_new():
    """Create a fresh project on the active channel and run autopilot on it."""
    cid, channel = _require_channel()
    if not cid or not ch.is_complete(cid):
        return jsonify(ok=False, error="Complete channel setup first",
                       redirect=url_for("onboarding"))
    # Pre-flight: autopilot spends real money on generation before it ever
    # reaches the upload step. Refuse to start if YouTube isn't connected.
    import youtube_upload as yu
    if not yu.is_connected(cid):
        return jsonify(ok=False,
                       error="Connect your YouTube channel before running Autopilot — "
                             "otherwise it would spend AI credits and then fail at upload.",
                       connect_url=url_for("oauth_start"))
    allowed, why = tiers.can_create_video(session.get("user", ""))
    if not allowed:
        return jsonify(ok=False, error=why)
    project = state.create_project()
    channel_name = (channel or {}).get("channel_name", "")
    state.update_project(
        project["id"], channel_id=cid,
        song_config={"channel_name": channel_name},
        generation={"prompt": "", "use_channel_style": True, "quantity": 4},
    )
    if not autopilot.start_run_async(project["id"]):
        return jsonify(ok=False, error="Autopilot is already running a video. "
                       "Let it finish, then try again.")
    return jsonify(ok=True, pid=project["id"])


def _video_state(p: dict) -> dict:
    """One unified state descriptor for a project, for the My Videos list."""
    yt = p.get("youtube", {})
    from datetime import datetime as _dt, timezone as _tz
    if yt.get("upload_status") == "done":
        pub = yt.get("scheduled_publish_at")
        if pub:
            future = True
            try:
                future = _dt.fromisoformat(pub.replace("Z", "+00:00")) > _dt.now(_tz.utc)
            except ValueError:
                pass
            return {"key": "scheduled", "label": "Scheduled", "sort": 2} if future \
                else {"key": "live", "label": "Live", "sort": 1}
        if yt.get("privacy") == "public":
            return {"key": "live", "label": "Live", "sort": 1}
        return {"key": "private", "label": "Private draft", "sort": 3}
    if p.get("status") == "running":
        return {"key": "working", "label": "Working…", "sort": 0}
    if p.get("status") == "error":
        return {"key": "error", "label": "Needs attention", "sort": 0}
    return {"key": "draft", "label": f"Draft · step {p.get('step', 1)} of 4", "sort": 4}


@app.route("/videos")
@auth.login_required
def my_videos():
    """One place to see every video and its state."""
    cid, _ = _require_channel()
    if not cid or not ch.is_complete(cid):
        return redirect(url_for("onboarding"))
    rows = []
    for p in state.projects_for_channel(cid):
        st = _video_state(p)
        yt = p.get("youtube", {})
        rows.append({
            "id": p["id"],
            "title": p.get("title") or "Untitled",
            "thumbnail": (Path(p["files"].get("thumbnail")).name if p["files"].get("thumbnail")
                          else (Path(p["files"].get("raw_image")).name if p["files"].get("raw_image") else None)),
            "state": st,
            "scheduled_date": p.get("scheduled_date"),
            "publish_at": yt.get("scheduled_publish_at"),
            "video_id": yt.get("video_id"),
            "cost": p.get("ledger_total"),
            "updated_at": p.get("updated_at", ""),
        })
    # Working/error first, then live, scheduled, private, drafts; newest within each.
    # Two stable passes: newest-first, then group — the group sort preserves
    # the newest-first order inside each group.
    rows.sort(key=lambda r: r["updated_at"], reverse=True)
    rows.sort(key=lambda r: r["state"]["sort"])
    return render_template("my_videos.html", rows=rows)


@app.route("/api/calendar")
@auth.login_required
def api_calendar():
    """Return scheduled project chips for a given month as JSON."""
    today = date.today()
    try:
        year  = int(request.args.get("y", today.year))
        month = int(request.args.get("m", today.month))
        if month < 1:  month = 12; year -= 1
        if month > 12: month = 1;  year += 1
    except ValueError:
        year, month = today.year, today.month

    cid = session.get("channel_id", "")
    all_proj  = state.projects_for_channel(cid) if cid else []
    scheduled = {}
    for p in all_proj:
        d = p.get("scheduled_date")
        if d:
            scheduled.setdefault(d, []).append(p)
        # Already uploaded with no date → omit from calendar (they're done)

    chips = {}
    for ds, projs in scheduled.items():
        chips[ds] = [
            {
                "id":        p["id"],
                "title":     p.get("title") or "",
                "status":    p["status"],
                "thumbnail": Path(p["files"]["thumbnail"]).name if p["files"].get("thumbnail") else None,
            }
            for p in projs
        ]

    stats = {
        "total":     len(all_proj),
        "active":    sum(1 for p in all_proj if p.get("status") == "running"),
        "published": sum(1 for p in all_proj if p.get("youtube", {}).get("upload_status") == "done"),
        "scheduled": sum(1 for p in all_proj if p.get("scheduled_date")),
    }

    return jsonify(ok=True, chips=chips, stats=stats, year=year, month=month)


@app.route("/project/new", methods=["POST"])
@auth.login_required
def new_project_form():
    """AJAX: create a new project, optionally scheduled for a date. Returns JSON."""
    cid, channel = _require_channel()
    if not cid or not ch.is_complete(cid):
        return jsonify(ok=False, error="Complete channel setup first", redirect=url_for("onboarding"))
    allowed, why = tiers.can_create_video(session.get("user", ""))
    if not allowed:
        return jsonify(ok=False, error=why)
    project = state.create_project()
    scheduled_date = request.form.get("scheduled_date", "").strip()
    prompt = request.form.get("prompt", "").strip()
    use_channel_style = request.form.get("use_channel_style", "true").strip().lower() != "false"
    quantity = request.form.get("quantity", "").strip()
    try:
        quantity_num = max(1, min(20, int(quantity))) if quantity else 5
    except ValueError:
        quantity_num = 5
    if scheduled_date:
        state.update_project(project["id"], scheduled_date=scheduled_date)
    # Stamp channel_id and channel_name onto the new project
    channel_name = (channel or {}).get("channel_name", "Ultra Focus Zone")
    state.update_project(
        project["id"],
        channel_id=cid,
        song_config={"channel_name": channel_name},
        generation={
            "prompt": prompt,
            "use_channel_style": use_channel_style,
            "quantity": quantity_num,
        },
    )
    return jsonify(ok=True, pid=project["id"])


@app.route("/project/<pid>")
@auth.login_required
def project_detail(pid):
    project = state.get_project(pid)
    if project is None:
        abort(404)
    mdir = ch.music_dir(project.get("channel_id", "")) if project.get("channel_id") else MUSIC_DIR
    song_total = len([p for p in mdir.glob("*.mp3") if not p.name.startswith("._")])
    return render_template("project.html", project=project, song_total=song_total)


# ---------------------------------------------------------------------------
# Action routes — return JSON
# ---------------------------------------------------------------------------

@app.route("/project/<pid>/delete", methods=["POST"])
@auth.login_required
def delete_project(pid):
    state.delete_project(pid)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(ok=True)
    return redirect(url_for("index"))


# ── Step 1 ──────────────────────────────────────────────────────────────────

@app.route("/project/<pid>/step1/start", methods=["POST"])
@auth.login_required
def step1_start(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if tasks.is_running(pid):
        return jsonify(ok=False, error="Task already running")
    tasks.start_step1(pid)
    return jsonify(ok=True)


@app.route("/project/<pid>/step1/select", methods=["POST"])
@auth.login_required
def step1_select(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if tasks.is_running(pid):
        return jsonify(ok=False, error="Task already running")
    # Accept full path OR bare basename — resolve to full path
    raw = request.form.get("image_path", "").strip()
    if not raw:
        return jsonify(ok=False, error="No image path provided")

    # If it's just a basename, search candidate_images for the full path
    chosen = raw
    if not Path(raw).is_absolute():
        basename = Path(raw).name
        for cp in project.get("candidate_images", []):
            if Path(cp).name == basename:
                chosen = cp
                break
        else:
            # Fall back: look in OUTPUT_DIR
            candidate = tasks.OUTPUT_DIR / basename
            if candidate.exists():
                chosen = str(candidate)
            else:
                return jsonify(ok=False, error=f"Image not found: {basename}")

    tasks.start_step1_select(pid, chosen)
    return jsonify(ok=True, project_ids=[pid])


@app.route("/project/<pid>/step1/set-title", methods=["POST"])
@auth.login_required
def step1_set_title(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if tasks.is_running(pid):
        return jsonify(ok=False, error="Task already running")
    if not project["files"].get("raw_image"):
        return jsonify(ok=False, error="Select an image first")
    title = request.form.get("title", "").strip()
    text_position = request.form.get("text_position", "top").strip().lower()
    if not title:
        return jsonify(ok=False, error="No title provided")
    # Allow custom user-entered thumbnail text/title (not only suggestions).
    if len(title) > 90:
        return jsonify(ok=False, error="Title is too long (max 90 characters)")
    if text_position not in {"top", "middle", "bottom"}:
        return jsonify(ok=False, error="Invalid text position")
    tasks.start_step1_set_title(pid, title, text_position)
    return jsonify(ok=True)


@app.route("/project/<pid>/step1/approve", methods=["POST"])
@auth.login_required
def step1_approve(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    state.update_project(pid, step=2, status="idle")
    return jsonify(ok=True)


@app.route("/project/<pid>/step1/regenerate", methods=["POST"])
@auth.login_required
def step1_regenerate(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if tasks.is_running(pid):
        return jsonify(ok=False, error="Task already running")
    tasks.start_step1(pid)
    return jsonify(ok=True)


@app.route("/project/<pid>/step1/refresh-suggestions", methods=["POST"])
@auth.login_required
def step1_refresh_suggestions(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if tasks.is_running(pid):
        return jsonify(ok=False, error="Task already running")

    project_cid = project.get("channel_id", "")
    profile = ch.get_channel(project_cid) if project_cid else channel_profile.load()
    if profile is None:
        profile = channel_profile.load()

    # Re-pull latest channel thumbnails when possible to keep vocab current.
    try:
        import youtube_api as ya
        channel_id = profile.get("channel_id", "").strip()
        channel_url = profile.get("channel_url", "").strip()
        if not channel_id and channel_url:
            channel_id = ya.resolve_channel_id(channel_url)
        if channel_id:
            profile["channel_id"] = channel_id
            profile["channel_thumbnails"] = ya.fetch_channel_thumbnails(channel_id, n=16)
            if project_cid:
                ch.save_channel(project_cid, profile)
            else:
                channel_profile.save(profile)
    except Exception:
        pass

    suggestions = ch.suggest_titles(project_cid, n=12) if project_cid else channel_profile.suggest_titles(profile, n=12)
    state.update_project(pid, title_suggestions=suggestions)
    return jsonify(ok=True, suggestions=suggestions)


# ── Step 2 ──────────────────────────────────────────────────────────────────

@app.route("/project/<pid>/step2/start", methods=["POST"])
@auth.login_required
def step2_start(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if tasks.is_running(pid):
        return jsonify(ok=False, error="Task already running")
    tasks.start_step2(pid)
    return jsonify(ok=True)


@app.route("/project/<pid>/step2/approve", methods=["POST"])
@auth.login_required
def step2_approve(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    slot = request.form.get("slot", "a")
    if slot not in ("a", "b"):
        slot = "a"
    files = project.get("files", {})
    chosen = files.get(f"loop_{slot}")
    if not chosen:
        return jsonify(ok=False, error=f"Slot {slot.upper()} has no video yet"), 400
    # Point the canonical loop fields at the chosen slot so Step 3 picks it up
    state.update_project(pid,
        step=3, status="idle",
        files={"loop": chosen, "loop30": chosen, "loop_chosen_slot": slot},
    )
    return jsonify(ok=True)


@app.route("/project/<pid>/step2/regenerate", methods=["POST"])
@auth.login_required
def step2_regenerate(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if tasks.is_running(pid):
        return jsonify(ok=False, error="Task already running")
    tasks.start_step2(pid)
    return jsonify(ok=True)


@app.route("/project/<pid>/step2/slot/<slot>", methods=["POST"])
@auth.login_required
def step2_slot_start(pid, slot):
    if slot not in ("a", "b"):
        return jsonify(ok=False, error="Invalid slot"), 400
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if tasks.is_running(pid):
        return jsonify(ok=False, error="Task already running")
    model = request.form.get("model", "kling_v16" if slot == "a" else "seedance_pro")
    if not tiers.model_allowed(session.get("user", ""), model):
        t = tiers.tier(session.get("user", ""))
        return jsonify(ok=False, error=f"The {t['label']} plan doesn't include that "
                       f"video model. Upgrade to use it, or pick an included one.")
    tasks.start_step2_slot(pid, slot, model)
    return jsonify(ok=True)


# ── Step 2b — Music generation ────────────────────────────────────────────────
# Operator account (suno.enabled=true in config.json): Suno or Stable Audio
# Subscriber accounts: Stable Audio only

@app.route("/project/<pid>/step2b/start", methods=["POST"])
@auth.login_required
def step2b_start(pid):
    import seo_generator as sg
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if tasks.is_running(pid):
        return jsonify(ok=False, error="Task already running")

    scene          = project.get("title", "Deep Focus")
    default_prompt = sg.build_music_prompt(scene)
    prompt         = request.form.get("suno_prompt", "").strip() or default_prompt
    count          = max(1, min(30, int(request.form.get("track_count", 18) or 18)))
    provider       = request.form.get("provider", "stable_audio")

    if provider == "suno" and _is_operator():
        tasks.start_step2b(pid, prompt)
    else:
        # Default for all subscribers (and operator fallback)
        tasks.start_step2b_stable_audio(pid, prompt, count)

    return jsonify(ok=True, provider=provider if (provider == "suno" and _is_operator()) else "stable_audio")


# ── Step 3 ──────────────────────────────────────────────────────────────────

@app.route("/project/<pid>/step3/start", methods=["POST"])
@auth.login_required
def step3_start(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if tasks.is_running(pid):
        return jsonify(ok=False, error="Task already running")

    song_count   = int(request.form.get("song_count", 18))
    crossfade    = float(request.form.get("crossfade_sec", 2.0))

    # Pull channel-level overlay settings; allow per-render override from the panel
    cid, profile = _require_channel()
    channel_name   = (profile or {}).get("channel_name", "") or "Ultra Focus Zone"
    overlay_style  = request.form.get("overlay_style") or (profile or {}).get("overlay_style", "default")
    logo_filename  = (profile or {}).get("logo_filename", "")
    logo_path      = str(ch.logos_dir(cid) / logo_filename) if cid and logo_filename else None

    tasks.start_step3(pid, song_count, crossfade, channel_name, overlay_style, logo_path)
    return jsonify(ok=True)


@app.route("/project/<pid>/step3/approve", methods=["POST"])
@auth.login_required
def step3_approve(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    state.update_project(pid, step=4, status="idle")
    # Auto-generate SEO so Step 4 arrives pre-filled
    if not tasks.is_running(pid):
        tasks.start_step4_seo(pid)
    return jsonify(ok=True)


@app.route("/project/<pid>/step3/rerender", methods=["POST"])
@auth.login_required
def step3_rerender(pid):
    """Reset Step 3 to idle so the user can re-run the 1-hour render."""
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if tasks.is_running(pid):
        return jsonify(ok=False, error="A task is still running — wait for it to finish first.")
    state.update_project(pid, step=3, status="idle",
                         task={"running": False, "step_running": None, "error": None})
    return jsonify(ok=True)


# ── Step 4 — SEO & YouTube upload ─────────────────────────────────────────

@app.route("/project/<pid>/step4/generate-seo", methods=["POST"])
@auth.login_required
def step4_generate_seo(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if tasks.is_running(pid):
        return jsonify(ok=False, error="Task already running")
    tasks.start_step4_seo(pid)
    return jsonify(ok=True)


@app.route("/project/<pid>/step4/save-seo", methods=["POST"])
@auth.login_required
def step4_save_seo(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    seo_title = request.form.get("seo_title", "").strip()
    seo_desc  = request.form.get("seo_description", "").strip()
    seo_tags  = request.form.get("seo_tags", "").strip()
    tags_list = [t.strip() for t in seo_tags.split(",") if t.strip()]
    state.update_project(
        pid,
        seo={
            "title":       seo_title,
            "description": seo_desc,
            "tags":        tags_list,
            "generated":   True,
        },
    )
    return jsonify(ok=True)


@app.route("/project/<pid>/veto", methods=["POST"])
@auth.login_required
def project_veto(pid):
    """
    Veto a scheduled video: force it back to private on YouTube and clear the
    scheduled publish time, so it will NOT go live. The video is kept (private),
    not deleted. This is the real mechanism behind "nothing publishes without
    your OK."
    """
    import youtube_upload as yu
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    yt = project.get("youtube", {})
    video_id = yt.get("video_id")
    if not video_id:
        return jsonify(ok=False, error="This video hasn't been uploaded yet — "
                       "nothing to veto.")
    cid = project.get("channel_id", "") or ""
    result = yu.cancel_scheduled_publish(video_id, cid)
    if not result.get("ok"):
        return jsonify(ok=False, error=result.get("error") or "Could not cancel on YouTube.")
    state.update_project(pid, youtube={"scheduled_publish_at": None, "vetoed": True,
                                       "privacy": "private"})
    return jsonify(ok=True)


@app.route("/project/<pid>/publish-now", methods=["POST"])
@auth.login_required
def project_publish_now(pid):
    """Make an already-uploaded video public immediately."""
    import youtube_upload as yu
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    video_id = project.get("youtube", {}).get("video_id")
    if not video_id:
        return jsonify(ok=False, error="This video hasn't been uploaded yet.")
    result = yu.publish_now(video_id, project.get("channel_id", "") or "")
    if not result.get("ok"):
        return jsonify(ok=False, error=result.get("error") or "Could not publish on YouTube.")
    state.update_project(pid, youtube={"scheduled_publish_at": None, "vetoed": False,
                                       "privacy": "public"})
    return jsonify(ok=True)


@app.route("/project/<pid>/reschedule", methods=["POST"])
@auth.login_required
def project_reschedule(pid):
    """Reschedule an already-uploaded video to a new future publish time."""
    import youtube_upload as yu
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    video_id = project.get("youtube", {}).get("video_id")
    if not video_id:
        return jsonify(ok=False, error="This video hasn't been uploaded yet.")
    publish_at = request.form.get("publish_at", "").strip()
    local_date = request.form.get("local_date", "").strip()
    if not publish_at:
        return jsonify(ok=False, error="Pick a date and time to schedule for.")
    result = yu.reschedule(video_id, publish_at, project.get("channel_id", "") or "")
    if not result.get("ok"):
        return jsonify(ok=False, error=result.get("error") or "Could not reschedule on YouTube.")
    state.update_project(pid, youtube={"scheduled_publish_at": publish_at, "vetoed": False,
                                       "privacy": "private"},
                         scheduled_date=(local_date or tasks.utc_to_local_date(publish_at)))
    return jsonify(ok=True)


@app.route("/project/<pid>/step4/set-publish-time", methods=["POST"])
@auth.login_required
def step4_set_publish_time(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    publish_at = request.form.get("publish_at", "").strip() or None
    local_date = request.form.get("local_date", "").strip()
    state.update_project(pid, youtube={"scheduled_publish_at": publish_at})
    # Keep calendar scheduled_date in sync with the YouTube publish date.
    # Prefer the client's local calendar day — truncating the UTC string puts
    # evening schedules on the wrong day.
    if publish_at:
        cal_date = local_date or tasks.utc_to_local_date(publish_at)
        state.update_project(pid, scheduled_date=cal_date)
    return jsonify(ok=True)


@app.route("/project/<pid>/sync-yt-date", methods=["POST"])
@auth.login_required
def sync_yt_date(pid):
    """Fetch the actual scheduled publish date from YouTube and update scheduled_date."""
    import youtube_upload as yu
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    video_id = project.get("youtube", {}).get("video_id")
    if not video_id:
        return jsonify(ok=False, error="No YouTube video ID for this project.")
    try:
        project_cid = project.get("channel_id", "") or session.get("channel_id", "")
        creds = yu._get_valid_credentials(project_cid)
        if creds is None:
            return jsonify(ok=False, error="YouTube not connected.")
        from googleapiclient.discovery import build as yt_build
        youtube = yt_build("youtube", "v3", credentials=creds)
        resp = youtube.videos().list(part="status", id=video_id).execute()
        items = resp.get("items", [])
        if not items:
            return jsonify(ok=False, error="Video not found on YouTube.")
        pub_at = items[0].get("status", {}).get("publishAt", "")
        if pub_at:
            cal_date = tasks.utc_to_local_date(pub_at)
            state.update_project(pid, scheduled_date=cal_date,
                                 youtube={"scheduled_publish_at": pub_at})
            return jsonify(ok=True, scheduled_date=cal_date, publish_at=pub_at)
        else:
            return jsonify(ok=False, error="No scheduled publish date on YouTube (may already be public).")
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@app.route("/project/<pid>/step4/upload", methods=["POST"])
@auth.login_required
def step4_upload(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if tasks.is_running(pid):
        return jsonify(ok=False, error="Task already running")
    if not project["files"].get("final_video"):
        return jsonify(ok=False, error="Complete Step 3 first")
    if not tiers.can_publish(session.get("user", "")):
        return jsonify(ok=False, error="Your plan doesn't include publishing to "
                       "YouTube. Upgrade to publish, or keep building drafts.")
    tasks.start_step4_upload(pid)
    return jsonify(ok=True)


@app.route("/project/<pid>/go-back/<int:target_step>", methods=["POST"])
@auth.login_required
def go_back(pid, target_step):
    """Step a project back to any earlier step so work can be redone."""
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if tasks.is_running(pid):
        return jsonify(ok=False, error="A task is still running — wait for it to finish first.")
    if target_step < 1 or target_step >= project.get("step", 1):
        return jsonify(ok=False, error=f"Cannot go back to step {target_step}.")
    state.update_project(
        pid,
        step=target_step,
        status="idle",
        task={"running": False, "step_running": None, "error": None},
    )
    return jsonify(ok=True, step=target_step)


@app.route("/project/<pid>/reset-task", methods=["POST"])
@auth.login_required
def reset_task(pid):
    """Unstick a project that is showing as running but has no active thread."""
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if tasks.is_running(pid):
        return jsonify(ok=False, error="Task is still actively running — wait for it to finish")
    state.update_project(
        pid,
        status="idle",
        task={"running": False, "step_running": None, "error": None},
    )
    return jsonify(ok=True)


# ── OAuth routes ──────────────────────────────────────────────────────────

@app.route("/oauth/start")
@auth.login_required
def oauth_start():
    import youtube_upload as yu
    cid, _ = _require_channel()
    url = yu.get_auth_url(cid or "")
    if not url:
        return "YouTube OAuth not configured. Add youtube_oauth.client_id/secret to config.json.", 400
    return redirect(url)


@app.route("/oauth/callback")
@auth.login_required
def oauth_callback():
    import youtube_upload as yu
    cid   = session.get("channel_id", "")
    code  = request.args.get("code", "")
    error = request.args.get("error", "")
    if error or not code:
        return redirect(url_for("index") + "?oauth=error")
    ok = yu.exchange_code_for_token(code, cid)
    return redirect(url_for("index") + ("?oauth=ok" if ok else "?oauth=error"))


@app.route("/api/oauth/status")
@auth.login_required
def api_oauth_status():
    import youtube_upload as yu
    cid, _ = _require_channel()
    return jsonify(yu.get_auth_status(cid or ""))


@app.route("/oauth/revoke", methods=["POST"])
@auth.login_required
def oauth_revoke():
    import youtube_upload as yu
    cid, _ = _require_channel()
    yu.revoke_token(cid or "")
    return jsonify(ok=True)


# ── Suggest next slot ──────────────────────────────────────────────────────

@app.route("/project/<pid>/autopilot/run", methods=["POST"])
@auth.login_required
def autopilot_run(pid):
    """Run the full hands-free pipeline on one project."""
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Project not found"), 404
    if not autopilot.start_run_async(pid):
        return jsonify(ok=False, error="Autopilot is already running a project.")
    return jsonify(ok=True)


@app.route("/api/autopilot/status")
@auth.login_required
def autopilot_status():
    return jsonify(ok=True, **autopilot.current_run())


@app.route("/api/channel/<cid>/autopilot", methods=["POST"])
@auth.login_required
def channel_autopilot_config(cid):
    """Save a channel's cadence config (enabled, days, models, lead time)."""
    chan = ch.get_channel(cid)
    if chan is None:
        return jsonify(ok=False, error="Channel not found"), 404
    cfg = dict(chan.get("autopilot") or {})
    f = request.form
    if "enabled" in f:
        cfg["enabled"] = f.get("enabled", "").lower() in ("1", "true", "on", "yes")
    if "days" in f:
        cfg["days"] = [d.strip().lower() for d in f.get("days", "").split(",")
                       if d.strip().lower() in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")]
        cfg["videos_per_week"] = len(cfg["days"])
    for key, cast in (("publish_hour_utc", int), ("fresh_tracks", int),
                      ("song_count", int), ("lead_hours", float)):
        if key in f:
            try:
                cfg[key] = cast(f.get(key))
            except (TypeError, ValueError):
                pass
    if "loop_model" in f and f.get("loop_model") in (
            "kling_v16", "kling_v21", "seedance_lite", "hailuo_pro", "seedance_pro"):
        want = f.get("loop_model")
        if not tiers.model_allowed(session.get("user", ""), want):
            t = tiers.tier(session.get("user", ""))
            return jsonify(ok=False, error=f"The {t['label']} plan doesn't include "
                           f"the {want} model. Upgrade to use it.")
        cfg["loop_model"] = want
    chan["autopilot"] = cfg
    ch.save_channel(cid, chan)
    return jsonify(ok=True, autopilot=cfg)


@app.route("/api/suggest-slot")
@auth.login_required
def api_suggest_slot():
    slot = tasks.suggest_next_slot()
    return jsonify(ok=True, slot=slot)


@app.route("/api/batch-schedule", methods=["POST"])
@auth.login_required
def api_batch_schedule():
    """Create project shells on selected weekdays over the next N months."""
    days_req = request.form.getlist("days")
    try:
        months = max(1, min(6, int(request.form.get("months", 1))))
    except ValueError:
        months = 1

    day_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    selected = {day_map[d] for d in days_req if d in day_map}
    if not selected:
        return jsonify(ok=False, error="No valid days selected.")

    # Compute end date (N calendar months from today)
    today     = date.today()
    end_month = today.month + months
    end_year  = today.year + (end_month - 1) // 12
    end_month = ((end_month - 1) % 12) + 1
    last_day  = _calendar.monthrange(end_year, end_month)[1]
    end_date  = date(end_year, end_month, min(today.day, last_day))

    # Already-occupied dates for this channel (skip rather than double-book)
    cid = session.get("channel_id", "")
    channel_projects = state.projects_for_channel(cid) if cid else []
    occupied = {p.get("scheduled_date") for p in channel_projects if p.get("scheduled_date")}

    # Get channel name for stamping on new projects
    channel = ch.get_channel(cid) if cid else None
    channel_name = (channel or {}).get("channel_name", "Ultra Focus Zone")

    created_dates = []
    skipped = 0
    cur = today + timedelta(days=1)
    while cur <= end_date:
        if cur.weekday() in selected:
            ds = cur.isoformat()
            if ds in occupied:
                skipped += 1
            else:
                proj = state.create_project()
                state.update_project(proj["id"],
                    scheduled_date=ds,
                    channel_id=cid,
                    song_config={"channel_name": channel_name},
                )
                created_dates.append(ds)
                occupied.add(ds)
        cur += timedelta(days=1)

    return jsonify(ok=True, created=len(created_dates), skipped=skipped, dates=created_dates)


# ── Schedule update ───────────────────────────────────────────────────────

@app.route("/project/<pid>/schedule", methods=["POST"])
@auth.login_required
def project_schedule(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Not found"), 404
    d = request.form.get("scheduled_date", "").strip() or None
    state.update_project(pid, scheduled_date=d)
    return jsonify(ok=True)


# ── Status polling (legacy — project.html) ────────────────────────────────

@app.route("/project/<pid>/status")
@auth.login_required
def project_status(pid):
    project = state.get_project(pid)
    if project is None:
        return jsonify(ok=False, error="Not found"), 404
    return jsonify(
        ok=True,
        step=project["step"],
        status=project["status"],
        task=project["task"],
        files={k: (v is not None) for k, v in project["files"].items()},
    )


# ── Full project data for the panel ──────────────────────────────────────

@app.route("/api/project/<pid>")
@auth.login_required
def api_project(pid):
    """Return full project JSON consumed by the calendar panel."""
    p = state.get_project(pid)
    if p is None:
        return jsonify(ok=False, error="Not found"), 404

    # Convert absolute file paths to basenames (used as /files/<basename> URLs)
    files_out = {}
    for k, v in p["files"].items():
        files_out[k] = Path(v).name if v else None

    # Also return basenames for candidate images
    candidates_out = [Path(c).name for c in p.get("candidate_images", [])]

    return jsonify(
        ok=True,
        id=p["id"],
        title=p.get("title", ""),
        step=p["step"],
        status=p["status"],
        scheduled_date=p.get("scheduled_date"),
        prompt=p.get("prompt", ""),
        title_suggestions=p.get("title_suggestions", []),
        candidate_images=candidates_out,
        files=files_out,
        task=p["task"],
        song_config=p.get("song_config", {}),
        thumbnail_config=p.get("thumbnail_config", {"text_position": "top"}),
        generation=p.get("generation", {"prompt": "", "use_channel_style": True, "quantity": 5}),
        seo=p.get("seo", {"title": "", "description": "", "tags": [], "generated": False}),
        youtube=p.get("youtube", {
            "scheduled_publish_at": None,
            "upload_status": "idle",
            "video_id": None,
            "video_url": None,
            "upload_error": None,
            "upload_progress_pct": 0,
        }),
    )


# ---------------------------------------------------------------------------
# File serving
# ---------------------------------------------------------------------------

@app.route("/files/<path:fp>")
@auth.login_required
def serve_file(fp):
    # Normalize backslashes too: a legacy library entry may carry a full
    # Windows path, and Path(...).name on POSIX would not split it.
    filename = os.path.basename(str(fp).replace("\\", "/"))
    user = session.get("user", "")
    # Project assets are named "<pid>_..." (pid = 8 hex chars). Verify the
    # requester owns the project that produced the file before serving it.
    prefix = filename[:8]
    if len(filename) > 8 and filename[8] == "_" \
            and all(c in "0123456789abcdef" for c in prefix):
        proj = state.get_project(prefix)
        if proj is not None and not ch.user_owns_channel(proj.get("channel_id", ""), user):
            abort(403)
    for asset_dir in ASSET_DIRS:
        candidate = asset_dir / filename
        if candidate.exists() and candidate.is_file():
            mime, _ = mimetypes.guess_type(str(candidate))
            return send_file(str(candidate), mimetype=mime)
    # Music: only from the active channel's own library (never cross-channel).
    cid = g.get("active_cid", "")
    if cid:
        cand = ch.music_dir(cid) / filename
        if cand.exists() and cand.is_file():
            return send_file(str(cand), mimetype="audio/mpeg", conditional=True)
    abort(404)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Image Library
# ---------------------------------------------------------------------------

def _library_has_image(cid: str, safe_name: str) -> bool:
    """True if this channel's library contains the image (blocks acting on
    another tenant's image by basename)."""
    import image_library as il
    data = il.load(cid)
    for b in data.get("batches", []):
        if any(Path(p).name == safe_name for p in b["images"]):
            return True
    return safe_name in data.get("used_images", [])


@app.route("/images")
@auth.login_required
def image_library_page():
    import image_library as il
    cid, _ = _require_channel()
    if not cid:
        return redirect(url_for("onboarding"))
    data = il.load(cid)
    used_set = set(data.get("used_images", []))
    batches_out = []
    for b in data["batches"]:
        imgs = [Path(p).name for p in b["images"]]
        if imgs:
            batches_out.append({
                "id":         b["id"],
                "prompt":     b["prompt"],
                "created_at": b["created_at"],
                "images":     imgs,
            })
    return render_template(
        "image_library.html",
        batches=batches_out,
        used_images=sorted(used_set),
        generating=data["generating"],
        generate_error=data.get("generate_error"),
    )


@app.route("/images/generate", methods=["POST"])
@auth.login_required
def images_generate():
    import image_library as il
    cid, _ = _require_channel()
    if not cid:
        return jsonify(ok=False, error="Complete channel setup first.")
    data = il.load(cid)
    if data["generating"]:
        return jsonify(ok=False, error="Already generating images.")
    prompt = request.form.get("prompt", "").strip()
    try:
        count = max(1, min(4, int(request.form.get("count", 4))))
    except ValueError:
        count = 4
    il.set_generating(cid, True)
    tasks.start_library_generate(cid, prompt, count)
    return jsonify(ok=True)


@app.route("/api/images/status")
@auth.login_required
def api_images_status():
    import image_library as il
    cid, _ = _require_channel()
    data = il.load(cid) if cid else il._default()
    batches_out = []
    for b in data["batches"]:
        batches_out.append({
            "id":         b["id"],
            "prompt":     b["prompt"],
            "created_at": b["created_at"],
            "images":     [Path(p).name for p in b["images"]],
        })
    return jsonify(
        ok=True,
        generating=data["generating"],
        generate_error=data.get("generate_error"),
        batches=batches_out,
    )


@app.route("/images/start-project", methods=["POST"])
@auth.login_required
def images_start_project():
    import image_library as il
    filename = request.form.get("filename", "").strip()
    if not filename:
        return jsonify(ok=False, error="No image specified.")
    safe_name = Path(filename).name
    cid, channel = _require_channel()
    if not cid:
        return jsonify(ok=False, error="Complete channel setup first.")
    # Only act on an image that belongs to THIS channel's library.
    if not _library_has_image(cid, safe_name):
        return jsonify(ok=False, error="Image not found."), 404
    target = tasks.OUTPUT_DIR / safe_name
    if not target.exists() or not target.is_file():
        return jsonify(ok=False, error="Image not found."), 404
    project = state.create_project()
    channel_name = (channel or {}).get("channel_name", "Ultra Focus Zone")
    state.update_project(project["id"],
        channel_id=cid,
        song_config={"channel_name": channel_name},
    )
    tasks.start_step1_select(project["id"], str(target))
    il.mark_image_used(cid, safe_name)
    return jsonify(
        ok=True,
        pid=project["id"],
        redirect=url_for("project_detail", pid=project["id"]),
    )


@app.route("/images/delete", methods=["POST"])
@auth.login_required
def images_delete():
    import image_library as il
    cid, _ = _require_channel()
    filename = request.form.get("filename", "").strip()
    if not cid or not filename:
        return jsonify(ok=False, error="No filename.")
    # Only delete an image that belongs to THIS channel's library.
    if not _library_has_image(cid, Path(filename).name):
        return jsonify(ok=False, error="Image not found."), 404
    il.delete_image(cid, filename, tasks.OUTPUT_DIR)
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
def _active_music_dir():
    """The active channel's private music library, or None if no channel."""
    cid = g.get("active_cid", "")
    return ch.music_dir(cid) if cid else None


@app.route("/api/songs")
@auth.login_required
def list_songs():
    mdir = _active_music_dir()
    files = sorted(p for p in mdir.glob("*.mp3") if not p.name.startswith("._")) if mdir else []
    return jsonify(total=len(files), files=[f.name for f in files])


@app.route("/api/songs/count")
@auth.login_required
def songs_count():
    mdir = _active_music_dir()
    n = len([p for p in mdir.glob("*.mp3") if not p.name.startswith("._")]) if mdir else 0
    return jsonify(total=n)


@app.route("/api/songs/<filename>")
@auth.login_required
def serve_song(filename):
    """Stream an MP3 from the active channel's own music library."""
    mdir = _active_music_dir()
    path = (mdir / Path(filename).name) if mdir else None
    if path is None or not path.exists() or path.suffix.lower() != ".mp3":
        abort(404)
    return send_file(path, mimetype="audio/mpeg", conditional=True)


@app.route("/api/songs/<filename>/delete", methods=["POST"])
@auth.login_required
def delete_song(filename):
    """Delete an MP3 from the active channel's own music library."""
    mdir = _active_music_dir()
    path = (mdir / Path(filename).name) if mdir else None
    if path is None or not path.exists() or path.suffix.lower() != ".mp3":
        return jsonify(ok=False, error="File not found"), 404
    path.unlink()
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Billing (Stripe) — scaffolding; inert until config.json has keys + price IDs
# ---------------------------------------------------------------------------

@app.route("/billing")
@auth.login_required
def billing_page():
    user = session.get("user", "")
    qs = tiers.quota_status(user)
    return render_template(
        "billing.html",
        quota=qs,
        tiers=tiers.TIERS,
        configured=billing.is_configured(),
        plans=billing.SUBSCRIPTION_PLANS,
    )


@app.route("/billing/checkout", methods=["POST"])
@auth.login_required
def billing_checkout():
    plan = request.form.get("plan", "")
    result = billing.create_checkout_session(
        session.get("user", ""), plan, request.host_url)
    if result.get("ok"):
        return jsonify(ok=True, url=result["url"])
    return jsonify(ok=False, error=result.get("error"))


@app.route("/billing/portal", methods=["POST"])
@auth.login_required
def billing_portal():
    result = billing.create_billing_portal(session.get("user", ""), request.host_url)
    if result.get("ok"):
        return jsonify(ok=True, url=result["url"])
    return jsonify(ok=False, error=result.get("error"))


@app.route("/billing/webhook", methods=["POST"])
def billing_webhook():
    """Stripe posts here. No login/CSRF — authenticity is the signature."""
    result = billing.handle_webhook(
        request.get_data(), request.headers.get("Stripe-Signature", ""))
    if result.get("ok"):
        return "", 200
    return jsonify(error=result.get("error")), 400


@app.route("/api/youtube-style")
@auth.login_required
def youtube_style():
    import youtube_api
    result = youtube_api.build_style_prompt()
    if result.get("error"):
        return jsonify(ok=False, error=result["error"])
    return jsonify(ok=True, prompt=result["prompt"], thumbnails=result["thumbnails"])


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------

@app.route("/research")
@auth.login_required
def research_page():
    import research as rs
    cid, channel = _require_channel()
    if not cid:
        return redirect(url_for("onboarding"))
    data      = rs.get_research(cid)
    age_hours = rs.cache_age_hours(cid)
    unseen    = rs.get_unseen_outlier_count(cid)
    rs.mark_research_seen(cid)
    return render_template(
        "research.html",
        research=data,
        age_hours=age_hours,
        unseen_count=unseen,
        channel=channel,
    )


@app.route("/research/refresh", methods=["POST"])
@auth.login_required
def research_refresh():
    import research as rs
    cid, channel = _require_channel()
    if not cid or not channel:
        return jsonify(ok=False, error="No channel configured"), 400
    try:
        data = rs.refresh_research(cid, channel)
    except RuntimeError as e:
        return jsonify(ok=False, error=str(e))
    return jsonify(ok=True, fetched_at=data["fetched_at"])


@app.route("/research/add-channel", methods=["POST"])
@auth.login_required
def research_add_channel():
    """Fetch a channel for browsing without saving it to the profile."""
    import youtube_api as yt
    url = request.form.get("channel_url", "").strip()
    if not url:
        return jsonify(ok=False, error="No channel URL provided")
    try:
        channel_id = yt.resolve_channel_id(url)
        info       = yt.fetch_channel_info(channel_id)
        videos     = yt.fetch_channel_videos_with_stats(channel_id, n=20)
    except RuntimeError as e:
        return jsonify(ok=False, error=str(e))
    return jsonify(ok=True, channel_id=channel_id, name=info.get("name", ""), videos=videos)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _port = int(os.environ.get("PORT", 5000))
    _debug = os.environ.get("FLASK_ENV") != "production"
    if os.environ.get("AUTOPILOT_SCHEDULER", "1") != "0":
        autopilot.start_scheduler()
    app.run(debug=_debug, port=_port, host="0.0.0.0", use_reloader=False)
