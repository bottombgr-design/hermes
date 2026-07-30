import asyncio
from unittest.mock import MagicMock, patch

import pytest

import gateway.run as gateway_run
from gateway.restart import DEFAULT_GATEWAY_SIGNAL_INTERRUPT_GRACE_TIMEOUT
from tests.gateway.restart_test_helpers import make_restart_runner


@pytest.mark.asyncio
async def test_unexpected_signal_starts_teardown_after_bounded_interrupt_grace():
    runner, adapter = make_restart_runner()
    runner._restart_drain_timeout = 0.0
    runner._signal_initiated_shutdown = True
    runner._signal_interrupt_grace_timeout = 0.01
    runner._running_agents = {"session": MagicMock()}

    disconnect_started = asyncio.Event()

    async def disconnect():
        disconnect_started.set()

    adapter.disconnect = disconnect

    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ):
        stop_task = asyncio.create_task(runner.stop())
        await asyncio.wait_for(disconnect_started.wait(), timeout=0.75)
        await stop_task

    assert runner._shutdown_event.is_set() is True


@pytest.mark.parametrize(
    ("signal_initiated", "restart_requested", "expected"),
    [
        (True, False, 0.25),
        (False, False, 5.0),
        (True, True, 5.0),
    ],
)
def test_post_interrupt_grace_only_shortens_unexpected_signal_shutdown(
    signal_initiated, restart_requested, expected
):
    runner, _adapter = make_restart_runner()
    runner._signal_initiated_shutdown = signal_initiated
    runner._restart_requested = restart_requested
    runner._signal_interrupt_grace_timeout = 0.25

    assert runner._post_interrupt_grace_timeout() == expected


def test_post_interrupt_grace_tolerates_duck_typed_runner():
    runner = MagicMock(spec=[])

    assert (
        gateway_run.GatewayRunner._post_interrupt_grace_timeout(runner)
        == gateway_run.DEFAULT_GATEWAY_POST_INTERRUPT_GRACE_TIMEOUT
    )


def test_load_signal_interrupt_grace_timeout_from_typed_config(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)

    assert (
        gateway_run.GatewayRunner._load_signal_interrupt_grace_timeout()
        == DEFAULT_GATEWAY_SIGNAL_INTERRUPT_GRACE_TIMEOUT
    )

    (tmp_path / "config.yaml").write_text(
        "gateway:\n  signal_interrupt_grace_timeout: 0.25\n",
        encoding="utf-8",
    )
    assert gateway_run.GatewayRunner._load_signal_interrupt_grace_timeout() == 0.25

    (tmp_path / "config.yaml").write_text(
        "gateway:\n  signal_interrupt_grace_timeout: 0\n",
        encoding="utf-8",
    )
    assert gateway_run.GatewayRunner._load_signal_interrupt_grace_timeout() == 0.0

    (tmp_path / "config.yaml").write_text(
        "gateway:\n  signal_interrupt_grace_timeout: .inf\n",
        encoding="utf-8",
    )
    assert (
        gateway_run.GatewayRunner._load_signal_interrupt_grace_timeout()
        == DEFAULT_GATEWAY_SIGNAL_INTERRUPT_GRACE_TIMEOUT
    )

    (tmp_path / "config.yaml").write_text(
        "gateway:\n  signal_interrupt_grace_timeout: invalid\n",
        encoding="utf-8",
    )
    assert (
        gateway_run.GatewayRunner._load_signal_interrupt_grace_timeout()
        == DEFAULT_GATEWAY_SIGNAL_INTERRUPT_GRACE_TIMEOUT
    )
    assert "Invalid signal_interrupt_grace_timeout" in caplog.text
