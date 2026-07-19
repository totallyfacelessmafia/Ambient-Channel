#!/usr/bin/env python3
"""
stripe_setup.py — Autonomous Stripe product+price creation for AmbiHub.

Same proven pattern as the personal-ai-avatar operational-audit-stripe-setup
script: create (or find) one product per subscription tier, then a recurring
monthly Price for each. Idempotent — matches existing objects by
metadata.plan_key, so re-running is safe. Dry-run by default.

Reads the Stripe secret key from config.json → "stripe": { "secret_key": ... }
(the same file billing.py reads). Nothing is created without --apply.

Usage:
    pip install stripe
    python dashboard/stripe_setup.py            # dry-run preview
    python dashboard/stripe_setup.py --apply    # create in Stripe (test or live per key)
    python dashboard/stripe_setup.py --apply --write   # also write price IDs into config.json

The key's mode (sk_test_… vs sk_live_…) decides test vs live — use a test key first.
"""

import argparse
import json
import sys
from pathlib import Path

CONFIG_FILE = Path(__file__).parent.parent / "UltraFocusZone_Automation" / "config.json"

# AmbiHub tiers (prices in cents). Pro = 25 videos (the locked margin-safe cap).
SPECS = [
    {"plan": "starter", "name": "AmbiHub Starter",
     "description": "6 videos/month, 1 channel, Kling loops, publishes to YouTube.",
     "amount": 2900, "videos": 6},
    {"plan": "growth", "name": "AmbiHub Growth",
     "description": "15 videos/month, 3 channels, more video models, scheduling.",
     "amount": 6900, "videos": 15},
    {"plan": "pro", "name": "AmbiHub Pro",
     "description": "25 videos/month, unlimited channels, all models incl. Seedance Pro 1080p.",
     "amount": 12900, "videos": 25},
]


def _secret_key() -> str:
    import os
    try:
        import envload
        envload.load_env()          # pick up .env / .env.local
    except Exception:
        pass
    # Prefer a test key so setup runs against the test account by default.
    key = os.environ.get("STRIPE_TEST_SECRET_KEY") or os.environ.get("STRIPE_SECRET_KEY", "")
    if not key.startswith("sk_"):
        try:
            key = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) \
                .get("stripe", {}).get("secret_key", "")
        except (OSError, json.JSONDecodeError):
            key = ""
    if not key.startswith("sk_"):
        sys.exit("No Stripe secret key found. Put STRIPE_SECRET_KEY in .env.local "
                 "(or stripe.secret_key in config.json). Expected sk_test_… / sk_live_….")
    return key


def _find_product(stripe, plan: str):
    # Search by our metadata key so re-runs reuse the same product.
    res = stripe.Product.search(query=f"metadata['plan_key']:'{plan}'")
    return res.data[0] if res.data else None


def _find_price(stripe, product_id: str, amount: int):
    for p in stripe.Price.list(product=product_id, active=True, limit=100).data:
        if (p.unit_amount == amount and p.recurring
                and p.recurring.get("interval") == "month"):
            return p
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="make live API calls")
    ap.add_argument("--write", action="store_true", help="write price IDs into config.json")
    args = ap.parse_args()

    try:
        import stripe
    except ImportError:
        sys.exit("The stripe library isn't installed. Run: pip install stripe")
    stripe.api_key = _secret_key()
    mode = "LIVE" if stripe.api_key.startswith("sk_live_") else "TEST"

    print(f"AmbiHub Stripe setup — {mode} mode — {'APPLY' if args.apply else 'DRY-RUN'}\n")
    prices = {}
    for spec in SPECS:
        plan, amount = spec["plan"], spec["amount"]
        print(f"[{plan}] {spec['name']} — ${amount/100:.0f}/mo")
        if not args.apply:
            print("   (dry-run) would find-or-create product + monthly price\n")
            continue

        product = _find_product(stripe, plan)
        if product is None:
            product = stripe.Product.create(
                name=spec["name"], description=spec["description"],
                metadata={"plan_key": plan, "videos_per_month": spec["videos"]})
            print(f"   product created: {product.id}")
        else:
            print(f"   product exists:  {product.id}")

        price = _find_price(stripe, product.id, amount)
        if price is None:
            price = stripe.Price.create(
                product=product.id, unit_amount=amount, currency="usd",
                recurring={"interval": "month"}, metadata={"plan_key": plan})
            print(f"   price created:   {price.id}")
        else:
            print(f"   price exists:    {price.id}")
        prices[plan] = price.id
        print()

    if args.apply and prices:
        import re
        env_names = {"starter": "STRIPE_PRICE_STARTER",
                     "growth": "STRIPE_PRICE_GROWTH", "pro": "STRIPE_PRICE_PRO"}
        lines = [f"{env_names[plan]}={pid}" for plan, pid in prices.items()]
        print("Price IDs (for .env.local):")
        for line in lines:
            print("   " + line)
        if args.write:
            env_file = Path(__file__).parent.parent / ".env.local"
            text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
            for line in lines:
                k = line.split("=", 1)[0]
                if re.search(rf"(?m)^{k}=", text):
                    text = re.sub(rf"(?m)^{k}=.*$", line, text)
                else:
                    text = (text.rstrip() + "\n" + line + "\n") if text else line + "\n"
            env_file.write_text(text, encoding="utf-8")
            print("\n✓ Wrote the price IDs into .env.local")
        else:
            print("\nPaste those into .env.local (or re-run with --write).")


if __name__ == "__main__":
    main()
