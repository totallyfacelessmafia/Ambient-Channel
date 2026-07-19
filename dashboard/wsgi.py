"""wsgi.py — gunicorn entry point for hosted deploys (Railway).

Start command (working directory must be dashboard/, where this file lives):

    gunicorn --workers 1 --timeout 120 wsgi:app

--workers 1 is REQUIRED for Phase A and is NOT optional: state is JSON on a
single volume and the in-process threading.Lock guards do not span processes,
so >1 worker would corrupt state regardless of anything here. (Phase B moves
state to Postgres before scaling workers/instances.)

The autopilot scheduler start lives in app.py only under `if __name__ ==
"__main__"`, i.e. it fires for local `python app.py` but NOT when gunicorn
imports the module — so we start it here, gated by AUTOPILOT_SCHEDULER.

Defense-in-depth: an exclusive file lock ensures only ONE process starts the
scheduler even if the start command is misconfigured with >1 worker (which would
otherwise mean duplicate cadence runs = double spend + double uploads). This
guards the scheduler ONLY; it does not make multi-worker safe for state — see
the --workers 1 rule above.
"""

import os

from app import app, autopilot  # noqa: F401  (app is the gunicorn target)

_scheduler_lock = None  # module-global keeps the fd (and the lock) alive


def _acquire_scheduler_lock():
    """Non-blocking exclusive lock; returns the held file handle or None.
    Falls back to no-lock (flag-gated start only) on platforms without fcntl,
    e.g. Windows — where this file isn't used anyway (that box runs app.py)."""
    try:
        import fcntl
    except ImportError:
        return True  # no fcntl (Windows): rely on the flag + --workers 1
    path = os.environ.get("SCHEDULER_LOCK", "/tmp/ambihub_scheduler.lock")
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None  # another process already owns the scheduler
    return fh


if os.environ.get("AUTOPILOT_SCHEDULER", "1") != "0":
    _scheduler_lock = _acquire_scheduler_lock()
    if _scheduler_lock is not None:
        autopilot.start_scheduler()
