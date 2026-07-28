from __future__ import annotations

import sys

from hermes_cli import managed_scope
from hermes_cli import plugin_config_state


def test_plugin_lists_honor_managed_leaf_precedence(monkeypatch):
    monkeypatch.delitem(sys.modules, "hermes_cli.config", raising=False)
    monkeypatch.setattr(
        plugin_config_state,
        "_read_user_config",
        lambda: {
            "plugins": {
                "enabled": ["user-enabled"],
                "disabled": ["user-disabled"],
            }
        },
    )
    monkeypatch.setattr(
        managed_scope,
        "load_managed_config",
        lambda: {"plugins": {"disabled": ["managed-disabled"]}},
    )

    assert plugin_config_state.get_enabled_plugins() == {"user-enabled"}
    assert plugin_config_state.get_disabled_plugins() == {"managed-disabled"}


def test_invalid_enabled_value_keeps_allow_list_unconfigured(monkeypatch):
    monkeypatch.delitem(sys.modules, "hermes_cli.config", raising=False)
    monkeypatch.setattr(
        plugin_config_state,
        "_read_user_config",
        lambda: {"plugins": {"enabled": "not-a-list"}},
    )
    monkeypatch.setattr(managed_scope, "load_managed_config", lambda: {})

    assert plugin_config_state.get_enabled_plugins() is None


def test_malformed_list_entries_do_not_abort_discovery(monkeypatch):
    monkeypatch.delitem(sys.modules, "hermes_cli.config", raising=False)
    monkeypatch.setattr(
        plugin_config_state,
        "_read_user_config",
        lambda: {
            "plugins": {
                "enabled": ["valid-enabled", {"bad": "entry"}],
                "disabled": [["bad"], "valid-disabled"],
            }
        },
    )
    monkeypatch.setattr(managed_scope, "load_managed_config", lambda: {})

    assert plugin_config_state.get_enabled_plugins() == {"valid-enabled"}
    assert plugin_config_state.get_disabled_plugins() == {"valid-disabled"}


def test_import_safe_reader_expands_user_and_managed_env_refs(monkeypatch):
    monkeypatch.delitem(sys.modules, "hermes_cli.config", raising=False)
    monkeypatch.setenv("USER_PLUGIN", "user-enabled")
    monkeypatch.setenv("MANAGED_PLUGIN", "managed-disabled")
    monkeypatch.setattr(
        plugin_config_state,
        "_read_user_config",
        lambda: {"plugins": {"enabled": ["${USER_PLUGIN}"]}},
    )
    monkeypatch.setattr(
        managed_scope,
        "load_managed_config",
        lambda: {"plugins": {"disabled": ["${env:MANAGED_PLUGIN}"]}},
    )

    assert plugin_config_state.get_enabled_plugins() == {"user-enabled"}
    assert plugin_config_state.get_disabled_plugins() == {"managed-disabled"}


def test_user_dotenv_overrides_stale_shell_for_activation_refs(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delitem(sys.modules, "hermes_cli.config", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text(
        "PLUGIN_DENY=model-providers/dotenv-provider\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("PLUGIN_DENY", "model-providers/stale-shell-provider")
    monkeypatch.setattr(
        plugin_config_state,
        "_read_user_config",
        lambda: {"plugins": {"disabled": ["${PLUGIN_DENY}"]}},
    )
    monkeypatch.setattr(managed_scope, "load_managed_config", lambda: {})
    monkeypatch.setattr(managed_scope, "get_managed_dir", lambda: None)

    assert plugin_config_state.get_disabled_plugins() == {
        "model-providers/dotenv-provider",
    }


def test_managed_dotenv_resolves_managed_activation_refs(monkeypatch, tmp_path):
    monkeypatch.delitem(sys.modules, "hermes_cli.config", raising=False)
    home = tmp_path / "home"
    managed_dir = tmp_path / "managed"
    home.mkdir()
    managed_dir.mkdir()
    (managed_dir / ".env").write_text(
        "MANAGED_PLUGIN_DENY=model-providers/managed-provider\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(plugin_config_state, "_read_user_config", lambda: {})
    monkeypatch.setattr(
        managed_scope,
        "load_managed_config",
        lambda: {
            "plugins": {
                "disabled": ["${env:MANAGED_PLUGIN_DENY}"],
            }
        },
    )
    monkeypatch.setattr(
        managed_scope,
        "get_managed_dir",
        lambda: managed_dir,
    )

    assert plugin_config_state.get_disabled_plugins() == {
        "model-providers/managed-provider",
    }


def test_ignore_user_config_keeps_managed_plugin_policy(monkeypatch):
    monkeypatch.delitem(sys.modules, "hermes_cli.config", raising=False)
    monkeypatch.setenv("HERMES_IGNORE_USER_CONFIG", "1")
    monkeypatch.setattr(
        plugin_config_state,
        "_read_user_config",
        lambda: {
            "plugins": {
                "enabled": ["user-enabled"],
                "disabled": ["user-disabled"],
            }
        },
    )
    monkeypatch.setattr(
        managed_scope,
        "load_managed_config",
        lambda: {
            "plugins": {
                "disabled": ["managed-disabled"],
            }
        },
    )

    assert plugin_config_state.get_enabled_plugins() is None
    assert plugin_config_state.get_disabled_plugins() == {
        "managed-disabled",
    }


def test_policy_reader_keeps_last_known_good_after_parse_failure(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(plugin_config_state, "get_config_path", lambda: config_path)
    monkeypatch.setattr(plugin_config_state, "_LAST_USER_CONFIG_BY_PATH", {})
    config_path.write_text(
        "plugins:\n  disabled:\n    - model-providers/private\n",
        encoding="utf-8",
    )

    assert plugin_config_state._read_user_config()["plugins"]["disabled"] == [
        "model-providers/private"
    ]

    config_path.write_text("plugins: [", encoding="utf-8")

    assert plugin_config_state._read_user_config()["plugins"]["disabled"] == [
        "model-providers/private"
    ]


def test_policy_reader_fails_closed_without_last_known_good(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("plugins: [", encoding="utf-8")
    monkeypatch.setattr(plugin_config_state, "get_config_path", lambda: config_path)
    monkeypatch.setattr(plugin_config_state, "_LAST_USER_CONFIG_BY_PATH", {})
    monkeypatch.setattr(managed_scope, "load_managed_config", lambda: {})

    disabled = plugin_config_state.get_disabled_plugins()

    assert plugin_config_state.plugin_policy_failed_closed(disabled)
