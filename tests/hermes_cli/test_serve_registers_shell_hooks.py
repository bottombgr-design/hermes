"""Regression: ``hermes serve`` / ``dashboard`` must register shell hooks.

``_prepare_agent_startup()`` discovers plugins and registers shell hooks, and
returns early for commands that cannot run an agent turn.  The comment at its
call site states the intent:

    Discover Python plugins and register shell hooks once, before any
    command that can fire lifecycle hooks.  Both are idempotent; gated
    so introspection/management commands (hermes hooks list, cron
    list, gateway status, mcp add, ...) don't pay discovery cost or
    trigger consent prompts for hooks the user is still inspecting.

So the gate exists to exclude *introspection* commands.  ``serve`` and
``dashboard`` are not introspection: they are the backend the Electron desktop
app spawns, and they run full agent turns over JSON-RPC.  They were simply
missing from ``_AGENT_COMMANDS``, so on the desktop surface **no** shell hook
was ever registered -- not ``pre_tool_call``, not ``pre_verify``, not
``on_session_start``.  A user's ``config.yaml`` hooks silently did nothing
there while ``hermes hooks doctor`` (a separate short-lived process that
re-reads the config) reported them healthy.

This is the same class of gap already fixed once for the cron scheduler, whose
tick loop had to be started explicitly inside the dashboard backend because
"the desktop app spawns a ``hermes dashboard`` backend, not a gateway"
(``hermes_cli/web_server.py::_start_desktop_cron_ticker``).

The tests below assert the *behaviour* -- whether registration runs for a given
invocation -- rather than the membership of ``_AGENT_COMMANDS``, so a future
refactor that moves the gate somewhere else keeps them meaningful.
"""

from __future__ import annotations

import argparse

import pytest

from hermes_cli.main import _AGENT_SUBCOMMANDS, _prepare_agent_startup


def _registers_hooks(monkeypatch, command, **arg_overrides) -> bool:
    """True when ``_prepare_agent_startup`` reaches shell-hook registration.

    Everything the function would import is stubbed, so this exercises the real
    gate without paying for plugin/MCP discovery.
    """
    called: dict[str, bool] = {"registered": False}

    def _fake_register_from_config(cfg, *, accept_hooks=False):
        called["registered"] = True
        return []

    monkeypatch.setattr(
        "agent.shell_hooks.register_from_config", _fake_register_from_config
    )
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {})
    # Keep MCP discovery (sync and background) out of the way; neither is what
    # this test is about and both are slow.
    monkeypatch.setattr(
        "tools.mcp_tool.discover_mcp_tools", lambda *a, **k: None, raising=False
    )
    monkeypatch.setattr(
        "hermes_cli.main.start_background_mcp_discovery",
        lambda *a, **k: None,
        raising=False,
    )

    args = argparse.Namespace(
        command=command,
        yolo=False,
        safe_mode=False,
        accept_hooks=False,
        **arg_overrides,
    )
    # Subcommand attributes the gate may consult (e.g. gateway_command).
    for attr, _values in _AGENT_SUBCOMMANDS.values():
        if not hasattr(args, attr):
            setattr(args, attr, None)

    _prepare_agent_startup(args)
    return called["registered"]


# ── the regression ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("command", ["serve", "dashboard"])
def test_serve_and_dashboard_register_shell_hooks(monkeypatch, command):
    """The desktop backend must load the user's hooks.

    Before the fix both returned early, so every hook in ``config.yaml`` was
    dead in the Electron app while ``hermes hooks doctor`` reported success.
    """
    assert _registers_hooks(monkeypatch, command) is True, (
        f"`hermes {command}` did not register shell hooks. This is the backend "
        f"the desktop app spawns; without registration every hook in the "
        f"user's config.yaml silently never fires on that surface."
    )


# ── the behaviour that must not regress ────────────────────────────────────


@pytest.mark.parametrize(
    "command,overrides",
    [
        ("chat", {}),
        ("acp", {}),
        (None, {}),
        ("gateway", {"gateway_command": "run"}),
        ("cron", {"cron_command": "run"}),
    ],
)
def test_agent_surfaces_still_register(monkeypatch, command, overrides):
    """Surfaces that already registered hooks must keep doing so."""
    assert _registers_hooks(monkeypatch, command, **overrides) is True


@pytest.mark.parametrize(
    "command,overrides",
    [
        ("logs", {}),
        ("version", {}),
        ("tools", {}),
        ("gateway", {"gateway_command": "status"}),
        ("cron", {"cron_command": "list"}),
    ],
)
def test_introspection_commands_still_skip(monkeypatch, command, overrides):
    """Management/introspection commands must stay on the cheap path.

    This is the reason the gate exists: they should not pay discovery cost, and
    must never trigger a consent prompt for a hook the user is inspecting.
    ``hermes hooks list`` prompting for approval of the hook being listed would
    be a nasty regression.
    """
    assert _registers_hooks(monkeypatch, command, **overrides) is False
