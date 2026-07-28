import argparse
import json
from types import SimpleNamespace

from hermes_cli import plugins_cmd


def _args(**kwargs):
    defaults = {
        "enabled": False,
        "user": False,
        "no_bundled": False,
        "plain": False,
        "json": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_filter_plugin_entries_enabled_only():
    entries = [
        (
            "disk-cleanup",
            "2.0.0",
            "Bundled",
            "bundled",
            None,
            "disk-cleanup",
            "standalone",
        ),
        (
            "web-search-plus",
            "2.2.0",
            "Search",
            "git",
            None,
            "web-search-plus",
            "standalone",
        ),
        (
            "old-plugin",
            "1.0.0",
            "Old",
            "user",
            None,
            "old-plugin",
            "standalone",
        ),
    ]

    filtered = plugins_cmd._filter_plugin_entries(
        entries,
        _args(enabled=True),
        enabled={"disk-cleanup", "web-search-plus"},
        disabled={"old-plugin"},
    )

    assert [entry[0] for entry in filtered] == ["disk-cleanup", "web-search-plus"]


def test_filter_plugin_entries_includes_default_enabled_bundled_backend():
    entries = [
        (
            "web-tavily",
            "1.0.0",
            "Search",
            "bundled",
            None,
            "web/tavily",
            "backend",
        ),
    ]

    filtered = plugins_cmd._filter_plugin_entries(
        entries,
        _args(enabled=True),
        enabled=set(),
        disabled=set(),
    )

    assert filtered == entries


def test_bundled_default_enabled_status_respects_explicit_disable():
    entry = (
        "telegram-platform",
        "1.0.0",
        "Telegram",
        "bundled",
        None,
        "telegram-platform",
        "platform",
    )

    assert (
        plugins_cmd._plugin_status_for_entry(entry, set(), set())
        == "enabled"
    )
    assert (
        plugins_cmd._plugin_status_for_entry(
            entry,
            set(),
            {"telegram-platform"},
        )
        == "disabled"
    )


def test_user_backend_remains_opt_in():
    entry = (
        "web-custom",
        "1.0.0",
        "Custom search",
        "user",
        None,
        "web/custom",
        "backend",
    )

    assert (
        plugins_cmd._plugin_status_for_entry(entry, set(), set())
        == "not enabled"
    )


def test_filter_plugin_entries_no_bundled():
    entries = [
        (
            "disk-cleanup",
            "2.0.0",
            "Bundled",
            "bundled",
            None,
            "disk-cleanup",
            "standalone",
        ),
        (
            "drawthings-grpc",
            "0.3.0",
            "Draw Things",
            "user",
            None,
            "drawthings-grpc",
            "standalone",
        ),
        (
            "web-search-plus",
            "2.2.0",
            "Search",
            "git",
            None,
            "web-search-plus",
            "standalone",
        ),
    ]

    filtered = plugins_cmd._filter_plugin_entries(
        entries,
        _args(no_bundled=True),
        enabled=set(),
        disabled=set(),
    )

    assert [entry[0] for entry in filtered] == ["drawthings-grpc", "web-search-plus"]


def test_cmd_list_plain_compact_output(monkeypatch, capsys):
    entries = [
        (
            "disk-cleanup",
            "2.0.0",
            "Bundled",
            "bundled",
            None,
            "disk-cleanup",
            "standalone",
        ),
        (
            "web-search-plus",
            "2.2.0",
            "Search",
            "git",
            None,
            "web-search-plus",
            "standalone",
        ),
    ]
    monkeypatch.setattr(plugins_cmd, "_discover_all_plugins", lambda: entries)
    monkeypatch.setattr(plugins_cmd, "_get_enabled_set", lambda: {"web-search-plus"})
    monkeypatch.setattr(plugins_cmd, "_get_disabled_set", lambda: set())

    plugins_cmd.cmd_list(_args(plain=True, no_bundled=True))

    out = capsys.readouterr().out
    assert "web-search-plus" in out
    assert "(web-search-plus)" in out
    assert "enabled" in out
    assert "disk-cleanup" not in out
    assert "Search" not in out  # plain mode stays compact, no descriptions


def test_cmd_list_json_output(monkeypatch, capsys):
    entries = [
        (
            "web-search-plus",
            "2.2.0",
            "Search",
            "git",
            None,
            "web-search-plus",
            "standalone",
        )
    ]
    monkeypatch.setattr(plugins_cmd, "_discover_all_plugins", lambda: entries)
    monkeypatch.setattr(plugins_cmd, "_get_enabled_set", lambda: {"web-search-plus"})
    monkeypatch.setattr(plugins_cmd, "_get_disabled_set", lambda: set())

    plugins_cmd.cmd_list(_args(json=True))

    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "name": "web-search-plus",
            "key": "web-search-plus",
            "status": "enabled",
            "version": "2.2.0",
            "description": "Search",
            "source": "git",
        }
    ]


def test_discover_all_plugins_includes_entrypoint_plugins(monkeypatch, tmp_path):
    bundled_dir = tmp_path / "bundled"
    user_dir = tmp_path / "user"
    bundled_dir.mkdir()
    user_dir.mkdir()

    dist = SimpleNamespace(
        version="0.1.0",
        metadata={"Summary": "Karpathy-style LLM Wikis for Hermes"},
    )
    entry_point = SimpleNamespace(
        name="wiki",
        value="adapters.hermes.cli_plugin",
        group="hermes_agent.plugins",
        dist=dist,
    )

    monkeypatch.setattr(plugins_cmd, "_plugins_dir", lambda: user_dir)
    monkeypatch.setattr(
        "hermes_cli.plugins.get_bundled_plugins_dir",
        lambda: bundled_dir,
    )
    monkeypatch.setattr(
        plugins_cmd.importlib.metadata,
        "entry_points",
        lambda: [entry_point],
    )

    entries = plugins_cmd._discover_all_plugins()

    assert entries == [
        (
            "wiki",
            "0.1.0",
            "Karpathy-style LLM Wikis for Hermes",
            "entrypoint",
            "adapters.hermes.cli_plugin",
            "wiki",
            "standalone",
        )
    ]


def test_cmd_list_json_output_includes_entrypoint_source(monkeypatch, capsys):
    entries = [
        (
            "wiki",
            "0.1.0",
            "Karpathy-style LLM Wikis for Hermes",
            "entrypoint",
            "adapters.hermes.cli_plugin",
            "wiki",
            "standalone",
        )
    ]
    monkeypatch.setattr(plugins_cmd, "_discover_all_plugins", lambda: entries)
    monkeypatch.setattr(plugins_cmd, "_get_enabled_set", lambda: {"wiki"})
    monkeypatch.setattr(plugins_cmd, "_get_disabled_set", lambda: set())

    plugins_cmd.cmd_list(_args(json=True))

    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "name": "wiki",
            "key": "wiki",
            "status": "enabled",
            "version": "0.1.0",
            "description": "Karpathy-style LLM Wikis for Hermes",
            "source": "entrypoint",
        }
    ]


def test_user_model_provider_is_default_enabled():
    entry = (
        "custom-provider",
        "1.0.0",
        "Provider",
        "user",
        None,
        "model-providers/custom",
        "model-provider",
    )

    assert plugins_cmd._plugin_status_for_entry(entry, set(), set()) == "enabled"
    assert (
        plugins_cmd._plugin_status_for_entry(
            entry,
            set(),
            {"model-providers/custom"},
        )
        == "disabled"
    )


def test_safe_mode_does_not_report_user_model_provider_enabled(monkeypatch):
    entry = (
        "custom-provider",
        "1.0.0",
        "Provider",
        "user",
        None,
        "model-providers/custom",
        "model-provider",
    )
    monkeypatch.setenv("HERMES_SAFE_MODE", "1")

    assert plugins_cmd._plugin_status_for_entry(entry, set(), set()) == "not enabled"
    assert not plugins_cmd._plugin_default_enabled(entry)


def test_safe_mode_only_keeps_bundled_model_providers_enabled(monkeypatch):
    monkeypatch.setenv("HERMES_SAFE_MODE", "1")
    bundled_backend = (
        "web-tavily",
        "1.0.0",
        "Backend",
        "bundled",
        None,
        "web/tavily",
        "backend",
    )
    bundled_provider = (
        "anthropic-provider",
        "1.0.0",
        "Provider",
        "bundled",
        None,
        "model-providers/anthropic",
        "model-provider",
    )
    entrypoint = (
        "third-party",
        "1.0.0",
        "Entry point",
        "entrypoint",
        None,
        "third-party",
        "standalone",
    )

    assert (
        plugins_cmd._plugin_status_for_entry(bundled_backend, set(), set())
        == "not enabled"
    )
    assert (
        plugins_cmd._plugin_status_for_entry(bundled_provider, set(), set())
        == "enabled"
    )
    assert (
        plugins_cmd._plugin_status_for_entry(entrypoint, {"third-party"}, set())
        == "not enabled"
    )


def test_policy_read_failure_blocks_entrypoint_status(monkeypatch):
    from hermes_cli import plugin_config_state

    monkeypatch.delenv("HERMES_SAFE_MODE", raising=False)
    entry = (
        "third-party",
        "1.0.0",
        "Entry point",
        "entrypoint",
        None,
        "third-party",
        "standalone",
    )
    disabled = {plugin_config_state._POLICY_FAIL_CLOSED_SENTINEL}

    assert (
        plugins_cmd._plugin_status_for_entry(entry, {"third-party"}, disabled)
        == "not enabled"
    )
    assert not plugins_cmd._plugin_default_enabled(entry, disabled)


def test_user_override_of_bundled_backend_remains_opt_in():
    entry = (
        "web-tavily",
        "2.0.0",
        "User override",
        "user",
        None,
        "web/tavily",
        "backend",
    )

    assert (
        plugins_cmd._plugin_status_for_entry(entry, set(), set())
        == "not enabled"
    )


def test_apply_plugin_selection_noop_preserves_config():
    entry = (
        "telegram-platform",
        "1.0.0",
        "Telegram",
        "bundled",
        None,
        "telegram-platform",
        "platform",
    )
    enabled = {"legacy-enabled"}
    disabled = {"legacy-disabled"}

    new_enabled, new_disabled = plugins_cmd._apply_plugin_selection_changes(
        [entry],
        {0},
        {0},
        enabled,
        disabled,
    )

    assert new_enabled == enabled
    assert new_disabled == disabled


def test_apply_plugin_selection_default_reenable_clears_all_aliases():
    entry = (
        "telegram-platform",
        "1.0.0",
        "Telegram",
        "bundled",
        None,
        "telegram-platform",
        "platform",
    )

    new_enabled, new_disabled = plugins_cmd._apply_plugin_selection_changes(
        [entry],
        set(),
        {0},
        {"telegram-platform", "other"},
        {"telegram-platform", "disabled-other"},
    )

    assert new_enabled == {"other"}
    assert new_disabled == {"disabled-other"}


def test_apply_plugin_selection_opt_in_uses_canonical_key():
    entry = (
        "custom-search",
        "1.0.0",
        "Search",
        "user",
        None,
        "web/custom",
        "backend",
    )

    new_enabled, new_disabled = plugins_cmd._apply_plugin_selection_changes(
        [entry],
        set(),
        {0},
        {"custom-search"},
        {"web/custom", "custom"},
    )

    assert new_enabled == {"web/custom"}
    assert new_disabled == {"custom"}


def test_apply_plugin_selection_normalizes_redundant_default_allowlist():
    entry = (
        "web-firecrawl",
        "1.0.0",
        "Search",
        "bundled",
        None,
        "web/firecrawl",
        "backend",
    )

    new_enabled, new_disabled = plugins_cmd._apply_plugin_selection_changes(
        [entry],
        {0},
        {0},
        {"web-firecrawl"},
        set(),
        normalize={0},
    )

    assert new_enabled == set()
    assert new_disabled == set()


def test_apply_plugin_selection_preserves_sibling_with_shared_manifest_name():
    entries = [
        (
            "fal",
            "1.0.0",
            "Image generation",
            "bundled",
            None,
            "image_gen/fal",
            "backend",
        ),
        (
            "fal",
            "1.0.0",
            "Video generation",
            "bundled",
            None,
            "video_gen/fal",
            "backend",
        ),
    ]

    new_enabled, new_disabled = plugins_cmd._apply_plugin_selection_changes(
        entries,
        set(),
        {0},
        set(),
        {"fal"},
    )

    assert new_enabled == set()
    assert new_disabled == {"video_gen/fal"}


def test_dashboard_default_plugin_reenable_uses_runtime_defaults(monkeypatch):
    entry = (
        "telegram-platform",
        "1.0.0",
        "Telegram",
        "bundled",
        None,
        "telegram-platform",
        "platform",
    )
    saved = {}
    toolset_calls = []

    monkeypatch.setattr(plugins_cmd, "_discover_all_plugins", lambda: [entry])
    monkeypatch.setattr(
        plugins_cmd,
        "_resolve_plugin_entry",
        lambda _name, _entries=None: entry,
    )
    monkeypatch.setattr(plugins_cmd, "_get_enabled_set", lambda: {"telegram-platform"})
    monkeypatch.setattr(
        plugins_cmd,
        "_get_disabled_set",
        lambda: {"telegram-platform"},
    )
    monkeypatch.setattr(
        plugins_cmd,
        "_save_enabled_set",
        lambda value: saved.setdefault("enabled", value),
    )
    monkeypatch.setattr(
        plugins_cmd,
        "_save_disabled_set",
        lambda value: saved.setdefault("disabled", value),
    )
    monkeypatch.setattr(
        plugins_cmd,
        "_toggle_plugin_toolset",
        lambda name, *, enable: toolset_calls.append((name, enable)),
    )

    result = plugins_cmd.dashboard_set_agent_plugin_enabled(
        "telegram-platform",
        enabled=True,
    )

    assert result == {
        "ok": True,
        "name": "telegram-platform",
        "key": "telegram-platform",
        "unchanged": False,
    }
    assert saved == {"enabled": set(), "disabled": set()}
    assert toolset_calls == [("telegram-platform", True)]


def test_dashboard_default_plugin_noop_does_not_persist(monkeypatch):
    entry = (
        "telegram-platform",
        "1.0.0",
        "Telegram",
        "bundled",
        None,
        "telegram-platform",
        "platform",
    )
    writes = []

    monkeypatch.setattr(plugins_cmd, "_discover_all_plugins", lambda: [entry])
    monkeypatch.setattr(
        plugins_cmd,
        "_resolve_plugin_entry",
        lambda _name, _entries=None: entry,
    )
    monkeypatch.setattr(plugins_cmd, "_get_enabled_set", set)
    monkeypatch.setattr(plugins_cmd, "_get_disabled_set", set)
    monkeypatch.setattr(
        plugins_cmd,
        "_save_enabled_set",
        lambda value: writes.append(("enabled", value)),
    )
    monkeypatch.setattr(
        plugins_cmd,
        "_save_disabled_set",
        lambda value: writes.append(("disabled", value)),
    )
    monkeypatch.setattr(
        plugins_cmd,
        "_toggle_plugin_toolset",
        lambda name, *, enable: writes.append(("toolset", name, enable)),
    )

    result = plugins_cmd.dashboard_set_agent_plugin_enabled(
        "telegram-platform",
        enabled=True,
    )

    assert result == {
        "ok": True,
        "name": "telegram-platform",
        "key": "telegram-platform",
        "unchanged": True,
    }
    assert writes == []
