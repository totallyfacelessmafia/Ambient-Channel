#!/usr/bin/env python3
"""
Behavior tests for the _fal_call retry/timeout helper in generate_assets.py.

Run directly (no pytest needed):
    py tests/test_fal_retry.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_real_sleep = time.sleep
time.sleep = lambda s: None   # skip backoff waits during tests

import generate_assets as ga  # noqa: E402


def test_transient_error_is_retried():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("connection reset by peer")
        return "ok"

    assert ga._fal_call(flaky, "flaky", timeout_sec=10) == "ok"
    assert calls["n"] == 3


def test_attempts_exhausted_raises():
    calls = {"n": 0}

    def dead():
        calls["n"] += 1
        raise ConnectionError("503 service unavailable")

    try:
        ga._fal_call(dead, "dead", timeout_sec=10, attempts=2)
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "after 2 attempts" in str(e)
    assert calls["n"] == 2


def test_billing_error_never_retried():
    calls = {"n": 0}

    def broke():
        calls["n"] += 1
        raise RuntimeError("Exhausted balance. Top up your account.")

    try:
        ga._fal_call(broke, "broke", timeout_sec=10)
        raise AssertionError("should have raised")
    except RuntimeError:
        pass
    assert calls["n"] == 1, f"billing error was retried ({calls['n']} calls)"


def test_auth_error_never_retried():
    calls = {"n": 0}

    def denied():
        calls["n"] += 1
        raise RuntimeError("401 Unauthorized")

    try:
        ga._fal_call(denied, "denied", timeout_sec=10)
        raise AssertionError("should have raised")
    except RuntimeError:
        pass
    assert calls["n"] == 1


def test_transient_messages_containing_billing_words_are_retried():
    # Regression: bare substring matching classified these as non-retryable
    # ("balance" in "load balancer", "exhausted" in "resource exhausted").
    for message in ("502 Bad Gateway from load balancer",
                    "429 resource exhausted, retry later"):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError(message)
            return "ok"

        assert ga._fal_call(flaky, "transient", timeout_sec=10) == "ok"
        assert calls["n"] == 2, f"not retried: {message!r}"


def test_timeout_is_retryable():
    calls = {"n": 0}

    def hangs_then_works():
        calls["n"] += 1
        if calls["n"] == 1:
            _real_sleep(5)
        return "recovered"

    assert ga._fal_call(hangs_then_works, "hang", timeout_sec=1) == "recovered"
    assert calls["n"] == 2


def test_billing_classifier():
    assert ga._is_billing_error("Exhausted balance. Top up.")
    assert ga._is_billing_error("insufficient credits")
    assert not ga._is_billing_error("502 Bad Gateway from load balancer")
    assert not ga._is_billing_error("429 resource exhausted")


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                failures += 1
                print(f"  FAIL  {name}: {e}")
    if failures:
        sys.exit(1)
    print("All retry tests passed.")
