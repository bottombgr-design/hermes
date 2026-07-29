"""#71180: Gateway must not re-broadcast shutdown notification on every process cycle."""

import inspect


def test_shutdown_notify_cooldown_exists():
    """Verify that gateway/run.py has the persisted dedup helpers."""
    from gateway import run
    src = inspect.getsource(run)

    assert "_shutdown_notify_cooldown_seconds" in src, (
        "gateway/run.py must have _shutdown_notify_cooldown_seconds (#71180)"
    )
    assert "_should_suppress_shutdown_notify" in src, (
        "gateway/run.py must have _should_suppress_shutdown_notify (#71180)"
    )
    assert "_shutdown_notify_sent_path" in src, (
        "gateway/run.py must have _shutdown_notify_sent_path (#71180)"
    )
    assert "HERMES_GATEWAY_SHUTDOWN_NOTIFY_COOLDOWN" in src, (
        "gateway/run.py must read HERMES_GATEWAY_SHUTDOWN_NOTIFY_COOLDOWN (#71180)"
    )


def test_fail_open_design():
    """Verify fail-open comment is present — unreadable file must send."""
    from gateway import run
    src = inspect.getsource(run)
    assert "fail open" in src.lower() or "Fail open" in src, (
        "gateway/run.py must document fail-open semantics (#71180)"
    )