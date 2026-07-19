"""wsgi.py — gunicorn entry point for hosted deploys (Railway).

Start command:

    gunicorn --workers 1 --timeout 120 wsgi:app

--workers 1 is REQUIRED for Phase A: state is JSON on a single volume and the
autopilot scheduler + in-process locks assume exactly one process. (Phase B
moves state to Postgres before scaling to more workers/instances.)

The autopilot scheduler start lives in app.py only under `if __name__ ==
"__main__"`, i.e. it fires for local `python app.py` but NOT when gunicorn
imports the module — so we start it here, gated by the same AUTOPILOT_SCHEDULER
flag (set it to 0 on any instance that must not own the loop).
"""

import os

from app import app, autopilot  # noqa: F401  (app is the gunicorn target)

if os.environ.get("AUTOPILOT_SCHEDULER", "1") != "0":
    autopilot.start_scheduler()
