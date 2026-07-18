"""
tiers.py — Subscription tiers, per-account plan storage, and quota/capability
enforcement.

Metering is by videos created per calendar month, counted across all of an
account's channels. The plan lives on the user record (auth.get_plan/set_plan);
billing (Stripe) will call set_plan() when a subscription/credit event lands —
this module is the enforcement layer it plugs into, with no payment rails yet.

Tier definitions come from the decided pricing plan:
  Starter $29 / 6 videos / 1 channel / Kling loops
  Growth  $69 / 15 videos / 3 channels / + mid models
  Pro     $129 / 25 videos / unlimited channels / all models incl. Seedance Pro
plus a watermarked Free trial tier and an internal Owner (operator) tier with
no limits. `None` means unlimited.

Pro is 25 (not 30) so the margin holds even when every video uses the most
expensive config (Seedance Pro + fresh music). Extra-video credit packs have
a $4.25 gross per-credit floor so they survive Stripe fees (credits TBD).
"""

from datetime import datetime

import auth
import channels as ch
import state

# Loop-model ids mirror generate_assets' AI_VIDEO_MODELS / LOOP_MODEL_COST.
_KLING = ["kling_v16", "kling_v21"]
_MID   = _KLING + ["seedance_lite", "hailuo_pro"]
_ALL   = _MID + ["seedance_pro"]

TIERS = {
    "free":    {"label": "Free",    "videos_per_month": 1,    "max_channels": 1,    "loop_models": ["kling_v16"], "can_publish": False, "watermark": True},
    "starter": {"label": "Starter", "videos_per_month": 6,    "max_channels": 1,    "loop_models": _KLING,        "can_publish": True,  "watermark": False},
    "growth":  {"label": "Growth",  "videos_per_month": 15,   "max_channels": 3,    "loop_models": _MID,          "can_publish": True,  "watermark": False},
    "pro":     {"label": "Pro",     "videos_per_month": 25,   "max_channels": None, "loop_models": _ALL,          "can_publish": True,  "watermark": False},
    "owner":   {"label": "Owner",   "videos_per_month": None, "max_channels": None, "loop_models": _ALL,          "can_publish": True,  "watermark": False},
}
DEFAULT_PLAN = "free"


def plan_of(email: str) -> str:
    return auth.get_plan(email) or DEFAULT_PLAN


def tier(email: str) -> dict:
    return TIERS.get(plan_of(email), TIERS[DEFAULT_PLAN])


def videos_used_this_month(email: str) -> int:
    """Video projects created this calendar month across the account's channels."""
    month = datetime.now().strftime("%Y-%m")
    cids = {c["id"] for c in ch.channels_for_user(email)}
    return sum(
        1 for p in state.all_projects()
        if p.get("channel_id") in cids and (p.get("created_at") or "")[:7] == month
    )


def quota_status(email: str) -> dict:
    """Usage snapshot for display: plan label, used, limit, remaining."""
    t = tier(email)
    used = videos_used_this_month(email)
    limit = t["videos_per_month"]
    return {
        "plan": plan_of(email),
        "label": t["label"],
        "used": used,
        "limit": limit,
        "remaining": None if limit is None else max(0, limit - used),
        "watermark": t["watermark"],
        "can_publish": t["can_publish"],
    }


def can_create_video(email: str) -> tuple[bool, str]:
    t = tier(email)
    limit = t["videos_per_month"]
    if limit is None:
        return True, ""
    used = videos_used_this_month(email)
    if used >= limit:
        return False, (f"You've used all {limit} videos on the {t['label']} plan "
                       f"this month ({used}/{limit}). Upgrade for more, or wait "
                       f"for next month's reset.")
    return True, ""


def can_add_channel(email: str) -> tuple[bool, str]:
    t = tier(email)
    maxc = t["max_channels"]
    if maxc is None:
        return True, ""
    have = len(ch.channels_for_user(email))
    if have >= maxc:
        plural = "s" if maxc != 1 else ""
        return False, (f"The {t['label']} plan includes {maxc} channel{plural}. "
                       f"Upgrade to add more.")
    return True, ""


def model_allowed(email: str, model: str) -> bool:
    return model in tier(email)["loop_models"]


def can_publish(email: str) -> bool:
    return tier(email)["can_publish"]


def watermark(email: str) -> bool:
    return tier(email)["watermark"]
