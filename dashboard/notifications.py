"""
notifications.py — Email notifications (SMTP), inert until configured.

The veto window is worthless to a remote client if nothing tells them a video
is scheduled. This sends that signal (and failure alerts) to the channel
owner's email. Like billing, it does nothing until config.json provides SMTP
settings — send() returns a clear 'not configured' instead of erroring.

Configure in UltraFocusZone_Automation/config.json:
    "email": {
        "smtp_host": "smtp.resend.com",     # or any SMTP provider
        "smtp_port": 587,
        "smtp_user": "resend",
        "smtp_pass": "re_...",
        "from":      "AmbiHub <notify@ambihub.ai>",
        "base_url":  "https://app.ambihub.ai"   # for links in the email
    }
"""

import json
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

import channels as ch
import state

_CONFIG_FILE = Path(__file__).parent.parent / "UltraFocusZone_Automation" / "config.json"


def _config() -> dict:
    """Email config, env vars first (SMTP_* / EMAIL_FROM), config.json email.* fallback."""
    import os
    try:
        j = json.loads(_CONFIG_FILE.read_text(encoding="utf-8")).get("email", {})
    except (OSError, json.JSONDecodeError):
        j = {}
    return {
        "smtp_host": os.environ.get("SMTP_HOST") or j.get("smtp_host", ""),
        "smtp_port": os.environ.get("SMTP_PORT") or j.get("smtp_port", 587),
        "smtp_user": os.environ.get("SMTP_USER") or j.get("smtp_user", ""),
        "smtp_pass": os.environ.get("SMTP_PASS") or j.get("smtp_pass", ""),
        "from":      os.environ.get("EMAIL_FROM") or j.get("from", ""),
        "base_url":  os.environ.get("APP_BASE_URL") or j.get("base_url", ""),
    }


def is_configured() -> bool:
    c = _config()
    return bool(c.get("smtp_host") and c.get("from"))


def _base_url() -> str:
    return (_config().get("base_url") or "http://localhost:5000").rstrip("/")


def send(to: str, subject: str, html: str, text: str = "") -> dict:
    """Send one email. Returns {ok, error}. No-op (ok=False) if not configured."""
    if not to:
        return {"ok": False, "error": "No recipient."}
    if not is_configured():
        return {"ok": False, "error": "Email not configured."}
    c = _config()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = c["from"]
    msg["To"] = to
    msg.set_content(text or "Open AmbiHub to see this update.")
    msg.add_alternative(html, subtype="html")
    try:
        port = int(c.get("smtp_port", 587))
        with smtplib.SMTP(c["smtp_host"], port, timeout=20) as s:
            s.starttls(context=ssl.create_default_context())
            if c.get("smtp_user"):
                s.login(c["smtp_user"], c.get("smtp_pass", ""))
            s.send_message(msg)
        return {"ok": True, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# High-level triggers (called from autopilot / upload). Each resolves the
# channel owner from the project and is a safe no-op if unconfigured.
# ---------------------------------------------------------------------------

def _owner_and_title(pid: str):
    p = state.get_project(pid) or {}
    owner = (ch.get_channel(p.get("channel_id", "")) or {}).get("owner", "")
    title = (p.get("seo", {}) or {}).get("title") or p.get("title") or "your video"
    return owner, title, p


def _shell(title: str, body_html: str) -> str:
    return (f'<div style="font-family:-apple-system,Segoe UI,sans-serif;'
            f'background:#0b1226;color:#e6ecfa;padding:28px;border-radius:12px;'
            f'max-width:520px">'
            f'<div style="font-weight:800;font-size:18px;margin-bottom:14px">'
            f'&#9889; AmbiHub</div>{body_html}</div>')


def notify_scheduled(pid: str) -> dict:
    """A video was uploaded private and scheduled — tell the owner to review
    it before it goes live (the veto window)."""
    owner, title, p = _owner_and_title(pid)
    if not owner:
        return {"ok": False, "error": "No owner."}
    pub = (p.get("youtube") or {}).get("scheduled_publish_at", "")
    link = f"{_base_url()}/project/{pid}"
    body = _shell(title, (
        f'<p style="font-size:15px"><b>{title}</b> is ready and scheduled to go '
        f'live{(" on " + pub) if pub else ""}.</p>'
        f'<p style="color:#93a5ce;font-size:14px">It is uploaded as <b>private</b> '
        f'and will publish automatically at that time. Review it first, or keep it '
        f'private if you would rather not.</p>'
        f'<p style="margin-top:18px"><a href="{link}" style="background:#4d82ff;'
        f'color:#fff;text-decoration:none;padding:11px 22px;border-radius:8px;'
        f'font-weight:600">Review &amp; preview</a></p>'))
    return send(owner, f"Review before it goes live: {title}", body,
                text=f"{title} is scheduled to go live{(' on ' + pub) if pub else ''}. "
                     f"Review it: {link}")


def notify_failed(pid: str, reason: str) -> dict:
    """An autopilot run failed — alert the owner so a scheduled slot doesn't
    silently pass with no video."""
    owner, title, p = _owner_and_title(pid)
    if not owner:
        return {"ok": False, "error": "No owner."}
    link = f"{_base_url()}/project/{pid}"
    body = _shell(title, (
        f'<p style="font-size:15px"><b>{title}</b> couldn\'t be completed.</p>'
        f'<p style="color:#e8a85c;font-size:14px">{reason}</p>'
        f'<p style="margin-top:18px"><a href="{link}" style="background:#4d82ff;'
        f'color:#fff;text-decoration:none;padding:11px 22px;border-radius:8px;'
        f'font-weight:600">Open the project</a></p>'))
    return send(owner, f"AmbiHub couldn't finish: {title}", body,
                text=f"{title} failed: {reason}. Open: {link}")
