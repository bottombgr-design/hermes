"""Tests for `hermes chat --safe-mode` isolation."""

from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path

import pytest


_VARS = ("HERMES_SAFE_MODE", "HERMES_IGNORE_USER_CONFIG", "HERMES_IGNORE_RULES")
_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _VARS:
        monkeypatch.delenv(var, raising=False)
    yield
    for var in _VARS:
        os.environ.pop(var, None)


def test_cmd_chat_safe_mode_sets_env_before_startup(monkeypatch):
    import hermes_cli.main as main_mod
    from hermes_cli._parser import build_top_level_parser

    parser, _subparsers, chat_parser = build_top_level_parser()
    chat_parser.set_defaults(func=main_mod.cmd_chat)
    args = parser.parse_args(["chat", "--safe-mode"])
    captured: dict[str, object] = {}
    fake_cli = types.ModuleType("cli")

    def fake_has_provider() -> bool:
        assert os.environ["HERMES_SAFE_MODE"] == "1"
        assert os.environ["HERMES_IGNORE_USER_CONFIG"] == "1"
        assert os.environ["HERMES_IGNORE_RULES"] == "1"
        return True

    def fake_main(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(main_mod, "_has_any_provider_configured", fake_has_provider)
    monkeypatch.setattr(main_mod, "_pin_kanban_board_env", lambda: None)
    monkeypatch.setattr(main_mod, "_sync_bundled_skills_for_startup", lambda: None)
    monkeypatch.setattr(main_mod, "_termux_should_prefetch_update_check", lambda: False)
    setattr(fake_cli, "main", fake_main)
    monkeypatch.setitem(sys.modules, "cli", fake_cli)

    main_mod.cmd_chat(args)

    assert captured["ignore_user_config"] is True
    assert captured["ignore_rules"] is True


def test_safe_mode_is_applied_before_provider_discovery(tmp_path):
    hermes_home = tmp_path / "home"
    plugin_dir = (
        hermes_home
        / "plugins"
        / "model-providers"
        / "unsafe-provider"
    )
    plugin_dir.mkdir(parents=True)
    marker = tmp_path / "provider-imported"
    (plugin_dir / "plugin.yaml").write_text(
        "name: unsafe-provider\n"
        "kind: model-provider\n"
        "version: 0.0.1\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported')\n",
        encoding="utf-8",
    )
    (hermes_home / ".env").write_text(
        "HERMES_SAFE_MODE=0\n"
        "HERMES_IGNORE_USER_CONFIG=0\n"
        "HERMES_IGNORE_RULES=0\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    for var in _VARS:
        env.pop(var, None)
    script = (
        "import os, sys\n"
        "sys.argv = ['hermes', 'chat', '--safe-mode']\n"
        "import hermes_cli.main\n"
        "assert os.environ['HERMES_SAFE_MODE'] == '1'\n"
        "assert os.environ['HERMES_IGNORE_USER_CONFIG'] == '1'\n"
        "assert os.environ['HERMES_IGNORE_RULES'] == '1'\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_dotenv_cannot_clear_truthy_safe_mode(monkeypatch, tmp_path):
    from hermes_cli.env_loader import load_hermes_dotenv

    hermes_home = tmp_path / "home"
    hermes_home.mkdir()
    (hermes_home / ".env").write_text(
        "HERMES_SAFE_MODE=0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_SAFE_MODE", "true")

    load_hermes_dotenv(hermes_home=hermes_home)

    assert os.environ["HERMES_SAFE_MODE"] == "true"


def test_dotenv_plugin_denylist_is_loaded_before_provider_discovery(tmp_path):
    hermes_home = tmp_path / "home"
    plugin_dir = (
        hermes_home
        / "plugins"
        / "model-providers"
        / "dotenv-disabled-provider"
    )
    plugin_dir.mkdir(parents=True)
    marker = tmp_path / "provider-imported"
    (hermes_home / "config.yaml").write_text(
        "plugins:\n"
        "  disabled:\n"
        "    - ${DISABLED_PROVIDER}\n",
        encoding="utf-8",
    )
    (hermes_home / ".env").write_text(
        "DISABLED_PROVIDER=model-providers/dotenv-disabled-provider\n",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.yaml").write_text(
        "name: dotenv-disabled-provider\n"
        "kind: model-provider\n"
        "version: 0.0.1\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    env.pop("DISABLED_PROVIDER", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.argv = ['hermes', '--version']; import hermes_cli.main",
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_early_safe_mode_detection_ignores_mcp_child_args():
    import hermes_cli.main as main_mod

    assert main_mod._safe_mode_requested_early(
        ["chat", "--safe-mode"]
    )
    assert not main_mod._safe_mode_requested_early(
        [
            "mcp",
            "add",
            "child",
            "--args",
            "child-command",
            "--safe-mode",
        ]
    )


def test_prepare_agent_startup_applies_safe_mode_before_plugin_discovery(monkeypatch):
    import hermes_cli.main as main_mod

    args = types.SimpleNamespace(command="chat", safe_mode=True, tui=False)
    plugins = types.ModuleType("hermes_cli.plugins")

    def discover_plugins() -> None:
        assert os.environ["HERMES_SAFE_MODE"] == "1"
        assert os.environ["HERMES_IGNORE_USER_CONFIG"] == "1"
        assert os.environ["HERMES_IGNORE_RULES"] == "1"

    setattr(plugins, "discover_plugins", discover_plugins)
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", plugins)
    monkeypatch.setattr(main_mod, "_should_background_mcp_startup", lambda _args: False)
    monkeypatch.setattr(main_mod, "_command_has_dedicated_mcp_startup", lambda _args: True)

    main_mod._prepare_agent_startup(args)


def test_plugin_discovery_skipped(monkeypatch):
    monkeypatch.setenv("HERMES_SAFE_MODE", "1")
    from hermes_cli.plugins import PluginManager

    mgr = PluginManager()
    called = []
    monkeypatch.setattr(mgr, "_discover_and_load_inner", lambda: called.append(True))

    mgr.discover_and_load()

    assert called == []
    assert mgr._discovered is True
    assert mgr._plugins == {}


def test_plugin_discovery_runs_without_safe_mode(monkeypatch):
    from hermes_cli.plugins import PluginManager

    mgr = PluginManager()
    called = []
    monkeypatch.setattr(mgr, "_discover_and_load_inner", lambda: called.append(True))

    mgr.discover_and_load()

    assert called == [True]


def test_mcp_servers_empty(monkeypatch):
    monkeypatch.setenv("HERMES_SAFE_MODE", "1")
    from tools.mcp_tool import _load_mcp_config

    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"mcp_servers": {"github": {"url": "https://example.com/mcp"}}},
    )

    assert _load_mcp_config() == {}


def test_mcp_servers_load_without_safe_mode(monkeypatch):
    from tools.mcp_tool import _load_mcp_config

    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"mcp_servers": {"github": {"url": "https://example.com/mcp"}}},
    )

    assert "github" in _load_mcp_config()


def test_parser_accepts_safe_mode_on_root_and_chat():
    from hermes_cli._parser import build_top_level_parser

    parser, _subparsers, _chat = build_top_level_parser()

    assert parser.parse_args(["--safe-mode"]).safe_mode is True
    assert parser.parse_args(["chat", "--safe-mode"]).safe_mode is True
    assert parser.parse_args(["chat"]).safe_mode is False


def test_shell_hooks_skipped(monkeypatch):
    monkeypatch.setenv("HERMES_SAFE_MODE", "1")
    from agent.shell_hooks import register_from_config

    cfg = {
        "hooks": {
            "pre_tool_call": [{"command": "echo hooked"}],
        },
        "hooks_auto_accept": True,
    }

    assert register_from_config(cfg, accept_hooks=True) == []


def test_shell_hooks_register_without_safe_mode(monkeypatch):
    import agent.shell_hooks as sh

    cfg = {
        "hooks": {
            "pre_tool_call": [{"command": "echo hooked"}],
        },
        "hooks_auto_accept": True,
    }

    manager = types.SimpleNamespace(_hooks={})
    plugins = types.ModuleType("hermes_cli.plugins")
    setattr(plugins, "get_plugin_manager", lambda: manager)
    setattr(plugins, "VALID_HOOKS", {"pre_tool_call"})
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", plugins)
    monkeypatch.setattr(sh, "_registered", set())

    registered = sh.register_from_config(cfg, accept_hooks=True)

    assert len(registered) == 1
    assert "pre_tool_call" in manager._hooks
