"""#71188: /new must read fresh config, not stale CLI_CONFIG snapshot."""

from __future__ import annotations

import inspect


def test_new_session_uses_load_cli_config_not_CLI_CONFIG():
    """Verify that new_session() reads from load_cli_config() instead of
    the module-level CLI_CONFIG snapshot."""
    from cli import HermesCLI, load_cli_config

    src = inspect.getsource(HermesCLI.new_session)
    # The fix should call load_cli_config() and assign to _fresh_config
    assert "load_cli_config" in src, (
        "new_session() must call load_cli_config() for fresh config (#71188)"
    )
    assert "_fresh_config" in src, (
        "new_session() must use a fresh config variable (#71188)"
    )
    # Should NOT read from CLI_CONFIG directly for model config
    assert "CLI_CONFIG.get(\"model\"" not in src, (
        "new_session() must not read model config from stale CLI_CONFIG (#71188)"
    )
    assert "CLI_CONFIG[\"agent\"]" not in src, (
        "new_session() must not read agent config from stale CLI_CONFIG (#71188)"
    )