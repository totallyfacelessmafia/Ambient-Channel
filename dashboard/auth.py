"""
auth.py — Session-cookie authentication for the AmbiHub dashboard.

Supports self-service registration, password reset tokens, and email
verification stubs (enforcement can be toggled later).

User records in users.json:
  "email@example.com": {
      "password_hash": "$2b$12$...",
      "created_at":    "2026-03-14T12:00:00",
      "verified":      false,
      "reset_token":   null,
      "reset_expires": null
  }

Legacy format (plain hash string) is auto-migrated on first read.
"""

import json
import re
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

import bcrypt
from flask import redirect, session, url_for

_USERS_FILE = Path(__file__).parent / "users.json"

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load() -> dict:
    if not _USERS_FILE.exists():
        return {"secret_key": secrets.token_hex(32), "users": {}}
    return json.loads(_USERS_FILE.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    _USERS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _user_record(data: dict, email: str) -> dict | None:
    """Return the user record for *email*, auto-migrating legacy format."""
    raw = data.get("users", {}).get(email)
    if raw is None:
        return None
    if isinstance(raw, str):
        # Legacy: plain bcrypt hash string → upgrade to dict
        rec = {
            "password_hash": raw,
            "created_at":    "",
            "verified":      True,  # existing users are implicitly verified
            "reset_token":   None,
            "reset_expires": None,
        }
        data["users"][email] = rec
        _save(data)
        return rec
    return raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_secret_key() -> str:
    """Return the app secret key, creating users.json if it doesn't exist."""
    data = _load()
    if not _USERS_FILE.exists():
        _save(data)
    return data["secret_key"]


def has_users() -> bool:
    """Return True if at least one user account exists."""
    return bool(_load().get("users"))


def user_exists(email: str) -> bool:
    return email in _load().get("users", {})


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


# ── Registration ──────────────────────────────────────────────────────────────

def create_user(email: str, password: str) -> None:
    """Register a new user. Raises ValueError on invalid input."""
    email = email.strip().lower()
    if not is_valid_email(email):
        raise ValueError("Invalid email address.")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")

    data = _load()
    if email in data.get("users", {}):
        raise ValueError("An account with that email already exists.")

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    data.setdefault("users", {})[email] = {
        "password_hash": hashed,
        "created_at":    datetime.now().isoformat(timespec="seconds"),
        "verified":      False,
        "reset_token":   None,
        "reset_expires": None,
        "plan":          "free",   # new signups start on the free trial tier
    }
    _save(data)


def verify_password(email: str, password: str) -> bool:
    email = email.strip().lower()
    data = _load()
    rec = _user_record(data, email)
    if rec is None:
        return False
    return bcrypt.checkpw(password.encode(), rec["password_hash"].encode())


# ── Password reset ────────────────────────────────────────────────────────────

def generate_reset_token(email: str) -> str | None:
    """Create a one-time reset token valid for 1 hour. Returns token or None."""
    email = email.strip().lower()
    data = _load()
    rec = _user_record(data, email)
    if rec is None:
        return None
    token = secrets.token_urlsafe(32)
    rec["reset_token"] = token
    rec["reset_expires"] = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
    data["users"][email] = rec
    _save(data)
    return token


def reset_with_token(token: str, new_password: str) -> bool:
    """Consume a reset token and set a new password. Returns success."""
    if not token or len(new_password) < 6:
        return False
    data = _load()
    for email, raw in data.get("users", {}).items():
        rec = _user_record(data, email)
        if rec and rec.get("reset_token") == token:
            expires = rec.get("reset_expires", "")
            if expires and datetime.fromisoformat(expires) < datetime.now():
                # Token expired — clear it
                rec["reset_token"] = None
                rec["reset_expires"] = None
                _save(data)
                return False
            hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
            rec["password_hash"] = hashed
            rec["reset_token"] = None
            rec["reset_expires"] = None
            data["users"][email] = rec
            _save(data)
            return True
    return False


def find_email_by_reset_token(token: str) -> str | None:
    """Look up the email associated with a reset token (if still valid)."""
    if not token:
        return None
    data = _load()
    for email, raw in data.get("users", {}).items():
        rec = _user_record(data, email)
        if rec and rec.get("reset_token") == token:
            expires = rec.get("reset_expires", "")
            if expires and datetime.fromisoformat(expires) < datetime.now():
                return None
            return email
    return None


# ── Email verification (stub — enforced=False for friends launch) ─────────────

def is_verified(email: str) -> bool:
    data = _load()
    rec = _user_record(data, email)
    return bool(rec and rec.get("verified"))


def mark_verified(email: str) -> None:
    data = _load()
    rec = _user_record(data, email)
    if rec:
        rec["verified"] = True
        data["users"][email] = rec
        _save(data)


# ── Subscription plan (used by tiers.py for quota/capability enforcement) ─────

def get_plan(email: str) -> str | None:
    data = _load()
    rec = _user_record(data, email)
    return (rec or {}).get("plan")


def set_plan(email: str, plan: str) -> bool:
    """Set an account's plan (called by billing when a subscription changes)."""
    data = _load()
    rec = _user_record(data, email)
    if rec is None:
        return False
    rec["plan"] = plan
    data["users"][email] = rec
    _save(data)
    return True


def set_stripe_customer(email: str, customer_id: str) -> bool:
    """Remember the Stripe customer id for an account, so later subscription
    webhooks (which carry only the customer id) can map back to the user."""
    data = _load()
    rec = _user_record(data, email)
    if rec is None:
        return False
    rec["stripe_customer_id"] = customer_id
    data["users"][email] = rec
    _save(data)
    return True


def email_by_stripe_customer(customer_id: str) -> str | None:
    if not customer_id:
        return None
    for email, rec in _load().get("users", {}).items():
        if rec.get("stripe_customer_id") == customer_id:
            return email
    return None


def migrate_plans() -> None:
    """Stamp any user missing a plan as 'owner' (the pre-billing operator).
    New signups get an explicit plan via create_user, so only legacy/operator
    accounts — created before plans existed — hit this. Idempotent."""
    data = _load()
    changed = False
    for email in list(data.get("users", {}).keys()):
        rec = _user_record(data, email)
        if rec is not None and not rec.get("plan"):
            rec["plan"] = "owner"
            data["users"][email] = rec
            changed = True
    if changed:
        _save(data)


# ── Flask decorator ───────────────────────────────────────────────────────────

def login_required(f):
    """Route decorator — redirects to /login if not authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated
