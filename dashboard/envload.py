"""
envload.py — Load .env / .env.local into the process environment.

Precedence (highest first): real OS environment  >  .env.local  >  .env.
Called once at app boot so os.environ carries whatever the operator put in an
env file, matching how the other apps (and cloud hosts) inject secrets. Config
readers use env vars first and fall back to config.json, so both work.
"""

import os
from pathlib import Path

_ROOT = Path(__file__).parent.parent   # repo root (holds .env / .env.local)


def load_env() -> None:
    merged = {}
    for name in (".env", ".env.local"):   # .local overrides .env
        f = _ROOT / name
        if not f.exists():
            continue
        for raw in f.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            merged[key.strip()] = val.strip().strip('"').strip("'")
    for key, val in merged.items():
        os.environ.setdefault(key, val)   # a real OS env var always wins
