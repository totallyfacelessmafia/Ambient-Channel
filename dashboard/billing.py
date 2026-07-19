"""
billing.py — Stripe billing scaffolding (test-mode ready, placeholders).

The enforcement layer already exists (tiers.py + auth.get_plan/set_plan); this
module is the payment side that flips an account's plan when Stripe reports a
subscription change. Nothing here charges anyone until real keys + price IDs
are configured.

Configure in UltraFocusZone_Automation/config.json:
    "stripe": {
        "secret_key":     "sk_test_...",
        "webhook_secret": "whsec_...",
        "publishable_key":"pk_test_...",
        "prices": {                # Stripe Price IDs (create in the dashboard)
            "starter": "price_...",
            "growth":  "price_...",
            "pro":     "price_..."
        }
    }

Until then, is_configured() is False and the routes return a clear message
instead of erroring.
"""

import json
from pathlib import Path

_CONFIG_FILE = Path(__file__).parent.parent / "UltraFocusZone_Automation" / "config.json"

# Plans that are purchasable subscriptions (Free is the default, Owner internal).
SUBSCRIPTION_PLANS = ("starter", "growth", "pro")


def _config() -> dict:
    """Stripe config, env vars first (STRIPE_*), config.json stripe.* as fallback."""
    import os
    try:
        j = json.loads(_CONFIG_FILE.read_text(encoding="utf-8")).get("stripe", {})
    except (OSError, json.JSONDecodeError):
        j = {}
    jp = j.get("prices", {}) or {}
    # A STRIPE_TEST_* value wins when present, so dev/staging runs in test mode
    # while the live keys sit parked for launch. This also fails SAFE: a stray
    # test key in prod means "no real charges", never the reverse.
    return {
        "secret_key":      os.environ.get("STRIPE_TEST_SECRET_KEY") or os.environ.get("STRIPE_SECRET_KEY") or j.get("secret_key", ""),
        "webhook_secret":  os.environ.get("STRIPE_TEST_WEBHOOK_SECRET") or os.environ.get("STRIPE_WEBHOOK_SECRET") or j.get("webhook_secret", ""),
        "publishable_key": os.environ.get("STRIPE_TEST_PUBLISHABLE_KEY") or os.environ.get("STRIPE_PUBLISHABLE_KEY") or j.get("publishable_key", ""),
        "prices": {
            "starter": os.environ.get("STRIPE_PRICE_STARTER") or jp.get("starter", ""),
            "growth":  os.environ.get("STRIPE_PRICE_GROWTH")  or jp.get("growth", ""),
            "pro":     os.environ.get("STRIPE_PRICE_PRO")     or jp.get("pro", ""),
        },
    }


def _prices() -> dict:
    return _config().get("prices", {}) or {}


def is_configured() -> bool:
    """True only when a real secret key AND at least one price ID are present."""
    cfg = _config()
    key = cfg.get("secret_key", "")
    has_price = any(str(v).startswith("price_") for v in _prices().values())
    return bool(key.startswith("sk_")) and has_price


def publishable_key() -> str:
    return _config().get("publishable_key", "")


def price_for_plan(plan: str) -> str | None:
    pid = _prices().get(plan, "")
    return pid if str(pid).startswith("price_") else None


def plan_for_price(price_id: str) -> str | None:
    for plan, pid in _prices().items():
        if pid == price_id:
            return plan
    return None


def _stripe():
    """Lazy import so the app boots without the stripe library installed."""
    try:
        import stripe  # noqa: PLC0415
    except ImportError:
        return None
    stripe.api_key = _config().get("secret_key", "")
    return stripe


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------

def create_checkout_session(email: str, plan: str, base_url: str) -> dict:
    """Start a subscription Checkout for `plan`. Returns {ok, url|error}.

    client_reference_id carries the app user email so the completion webhook
    can attribute the subscription without a pre-existing customer mapping.
    """
    if plan not in SUBSCRIPTION_PLANS:
        return {"ok": False, "error": "Unknown plan."}
    if not is_configured():
        return {"ok": False, "error": "Billing isn't configured yet "
                "(no Stripe keys / price IDs)."}
    price = price_for_plan(plan)
    if not price:
        return {"ok": False, "error": f"No Stripe price configured for {plan}."}
    stripe = _stripe()
    if stripe is None:
        return {"ok": False, "error": "The stripe library isn't installed "
                "(pip install stripe)."}
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price, "quantity": 1}],
            client_reference_id=email,
            customer_email=email,
            success_url=base_url.rstrip("/") + "/billing?ok=1",
            cancel_url=base_url.rstrip("/") + "/billing?canceled=1",
            allow_promotion_codes=True,
        )
        return {"ok": True, "url": session.url}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def create_billing_portal(email: str, base_url: str) -> dict:
    """Open the Stripe customer portal (manage/cancel) for an existing customer."""
    import auth
    if not is_configured():
        return {"ok": False, "error": "Billing isn't configured yet."}
    stripe = _stripe()
    if stripe is None:
        return {"ok": False, "error": "The stripe library isn't installed."}
    data = auth._load()
    rec = auth._user_record(data, email) or {}
    cust = rec.get("stripe_customer_id")
    if not cust:
        return {"ok": False, "error": "No subscription on file yet."}
    try:
        portal = stripe.billing_portal.Session.create(
            customer=cust, return_url=base_url.rstrip("/") + "/billing")
        return {"ok": True, "url": portal.url}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Webhook — the single place that flips a plan
# ---------------------------------------------------------------------------

def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """Verify the Stripe signature and apply plan changes. Returns {ok, error}.

    Handles:
      checkout.session.completed        → set plan from the purchased price,
                                          remember the customer id
      customer.subscription.updated     → set plan from the active price
      customer.subscription.deleted     → downgrade to free
    """
    import auth
    cfg = _config()
    secret = cfg.get("webhook_secret", "")
    stripe = _stripe()
    if stripe is None or not secret:
        return {"ok": False, "error": "Billing webhook not configured."}
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception as exc:  # noqa: BLE001 (bad signature / parse)
        return {"ok": False, "error": f"Signature verification failed: {exc}"}

    etype = event["type"]
    obj = event["data"]["object"]

    if etype == "checkout.session.completed":
        email = obj.get("client_reference_id") or obj.get("customer_email")
        customer = obj.get("customer")
        if email and customer:
            auth.set_stripe_customer(email, customer)
        # Resolve the purchased plan from the subscription's price.
        plan = _plan_from_subscription(stripe, obj.get("subscription"))
        if email and plan:
            auth.set_plan(email, plan)
        return {"ok": True, "error": None}

    if etype == "customer.subscription.updated":
        email = auth.email_by_stripe_customer(obj.get("customer"))
        price = _first_price_id(obj)
        plan = plan_for_price(price) if price else None
        if email and plan and obj.get("status") in ("active", "trialing"):
            auth.set_plan(email, plan)
        return {"ok": True, "error": None}

    if etype == "customer.subscription.deleted":
        email = auth.email_by_stripe_customer(obj.get("customer"))
        if email:
            auth.set_plan(email, "free")
        return {"ok": True, "error": None}

    return {"ok": True, "error": None}   # ignore unrelated events


def _first_price_id(subscription_obj: dict) -> str | None:
    try:
        return subscription_obj["items"]["data"][0]["price"]["id"]
    except (KeyError, IndexError, TypeError):
        return None


def _plan_from_subscription(stripe, sub_id: str) -> str | None:
    if not sub_id:
        return None
    try:
        sub = stripe.Subscription.retrieve(sub_id)
        return plan_for_price(_first_price_id(sub))
    except Exception:  # noqa: BLE001
        return None
