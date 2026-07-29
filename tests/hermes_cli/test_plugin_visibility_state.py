from __future__ import annotations

import asyncio

from hermes_cli import plugins_cmd
from hermes_cli import web_server
from hermes_cli.web_server import _set_hidden_plugin_state


def _entry(name: str, key: str) -> tuple:
    return (name, "1.0.0", "", "bundled", None, key, "backend")


def test_visibility_normalization_preserves_shared_alias_sibling():
    entries = [
        _entry("fal", "image_gen/fal"),
        _entry("fal", "video_gen/fal"),
    ]

    hidden = _set_hidden_plugin_state(
        entries,
        0,
        {"fal"},
        should_hide=False,
    )

    assert hidden == {"video_gen/fal"}


def test_visibility_normalization_preserves_sibling_for_dashboard_alias():
    entries = [
        _entry("image-fal", "image_gen/fal"),
        _entry("fal", "video_gen/fal"),
    ]

    hidden = _set_hidden_plugin_state(
        entries,
        0,
        {"fal"},
        should_hide=False,
        extra_target_aliases={"fal"},
    )

    assert hidden == {"video_gen/fal"}


def test_visibility_normalization_writes_canonical_key():
    entries = [_entry("web-firecrawl", "web/firecrawl")]

    hidden = _set_hidden_plugin_state(
        entries,
        0,
        set(),
        should_hide=True,
    )

    assert hidden == {"web/firecrawl"}


def test_dashboard_plugin_filter_accepts_yaml_hidden_list(monkeypatch, tmp_path):
    entry = _entry("web-firecrawl", "web/firecrawl")
    plugin_dir = tmp_path / "plugins" / "web" / "firecrawl"
    plugin_dir.mkdir(parents=True)
    entry = (*entry[:4], plugin_dir, *entry[5:])
    dashboard = {
        "name": "web-firecrawl",
        "source": "bundled",
        "_plugin_dir": str(plugin_dir),
    }
    monkeypatch.setattr(
        web_server,
        "_get_dashboard_plugins",
        lambda force_rescan=False: [dashboard],
    )
    monkeypatch.setattr(
        web_server,
        "load_config",
        lambda: {"dashboard": {"hidden_plugins": []}},
    )
    monkeypatch.setattr(plugins_cmd, "_discover_all_plugins", lambda: [entry])
    monkeypatch.setattr(plugins_cmd, "_get_enabled_set", lambda: set())
    monkeypatch.setattr(plugins_cmd, "_get_disabled_set", lambda: set())

    result = asyncio.run(web_server.get_dashboard_plugins())

    assert result == [{"name": "web-firecrawl", "source": "bundled"}]


def test_dashboard_plugin_filter_honors_dashboard_manifest_name(
    monkeypatch,
    tmp_path,
):
    entry = _entry("runtime-name", "web/runtime-key")
    plugin_dir = tmp_path / "plugins" / "web" / "runtime-key"
    plugin_dir.mkdir(parents=True)
    entry = (*entry[:4], plugin_dir, *entry[5:])
    dashboard = {
        "name": "dashboard-label",
        "source": "bundled",
        "_plugin_dir": str(plugin_dir),
    }
    monkeypatch.setattr(
        web_server,
        "_get_dashboard_plugins",
        lambda force_rescan=False: [dashboard],
    )
    monkeypatch.setattr(
        web_server,
        "load_config",
        lambda: {
            "dashboard": {
                "hidden_plugins": ["dashboard-label"],
            }
        },
    )
    monkeypatch.setattr(plugins_cmd, "_discover_all_plugins", lambda: [entry])
    monkeypatch.setattr(plugins_cmd, "_get_enabled_set", lambda: set())
    monkeypatch.setattr(plugins_cmd, "_get_disabled_set", lambda: set())

    result = asyncio.run(web_server.get_dashboard_plugins())

    assert result == []


def test_dashboard_hub_accepts_yaml_hidden_list(monkeypatch, tmp_path):
    entry = _entry("web-firecrawl", "web/firecrawl")
    plugin_dir = tmp_path / "plugins" / "web" / "firecrawl"
    plugin_dir.mkdir(parents=True)
    entry = (*entry[:4], plugin_dir, *entry[5:])
    monkeypatch.setattr(
        web_server,
        "_get_dashboard_plugins",
        lambda force_rescan=False: [],
    )
    monkeypatch.setattr(
        web_server,
        "load_config",
        lambda: {"dashboard": {"hidden_plugins": []}},
    )
    monkeypatch.setattr(web_server, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(
        web_server,
        "_discover_memory_provider_statuses",
        lambda: [],
    )
    monkeypatch.setattr(plugins_cmd, "_discover_all_plugins", lambda: [entry])
    monkeypatch.setattr(plugins_cmd, "_get_enabled_set", lambda: set())
    monkeypatch.setattr(plugins_cmd, "_get_disabled_set", lambda: set())
    monkeypatch.setattr(
        plugins_cmd,
        "_read_manifest",
        lambda _path: {"provides_tools": ["missing-test-tool"]},
    )
    monkeypatch.setattr(plugins_cmd, "_discover_context_engines", lambda: [])
    monkeypatch.setattr(plugins_cmd, "_get_current_context_engine", lambda: "")
    monkeypatch.setattr(plugins_cmd, "_get_current_memory_provider", lambda: "")

    result = web_server._merged_plugins_hub()

    assert result["plugins"][0]["user_hidden"] is False


def test_dashboard_hub_does_not_attach_same_name_manifest_from_other_path(
    monkeypatch,
    tmp_path,
):
    project_dir = tmp_path / "project" / ".hermes" / "plugins" / "shared"
    user_dir = tmp_path / ".hermes" / "plugins" / "shared"
    project_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)
    entry = (
        "shared-name",
        "2.0.0",
        "project override",
        "project",
        project_dir,
        "shared-name",
        "standalone",
    )
    dashboard = {
        "name": "shared-name",
        "source": "user",
        "entry": "dashboard/index.html",
        "_plugin_dir": str(user_dir),
    }
    monkeypatch.setattr(
        web_server,
        "_get_dashboard_plugins",
        lambda force_rescan=False: [dashboard],
    )
    monkeypatch.setattr(web_server, "load_config", lambda: {})
    monkeypatch.setattr(web_server, "get_hermes_home", lambda: tmp_path / ".hermes")
    monkeypatch.setattr(
        web_server,
        "_discover_memory_provider_statuses",
        lambda: [],
    )
    monkeypatch.setattr(plugins_cmd, "_discover_all_plugins", lambda: [entry])
    monkeypatch.setattr(plugins_cmd, "_get_enabled_set", lambda: set())
    monkeypatch.setattr(plugins_cmd, "_get_disabled_set", lambda: set())
    monkeypatch.setattr(plugins_cmd, "_read_manifest", lambda _path: {})
    monkeypatch.setattr(plugins_cmd, "_discover_context_engines", lambda: [])
    monkeypatch.setattr(plugins_cmd, "_get_current_context_engine", lambda: "")
    monkeypatch.setattr(plugins_cmd, "_get_current_memory_provider", lambda: "")

    result = web_server._merged_plugins_hub()

    row = result["plugins"][0]
    assert row["path"] == project_dir
    assert row["dashboard_manifest"] is None
    assert row["has_dashboard_manifest"] is False
    assert result["orphan_dashboard_plugins"] == [
        {
            "name": "shared-name",
            "source": "user",
            "entry": "dashboard/index.html",
        }
    ]


def test_visibility_write_filters_malformed_hidden_entries(monkeypatch):
    config = {
        "dashboard": {
            "hidden_plugins": [
                "keep-hidden",
                {"bad": "mapping"},
                42,
            ]
        }
    }
    saved = {}
    monkeypatch.setattr(web_server, "_require_token", lambda _request: None)
    monkeypatch.setattr(web_server, "load_config", lambda: config)
    monkeypatch.setattr(
        web_server,
        "save_config",
        lambda value: saved.update(value),
    )
    monkeypatch.setattr(plugins_cmd, "_discover_all_plugins", lambda: [])

    body = web_server._PluginVisibilityBody(hidden=True)
    result = asyncio.run(
        web_server.post_plugin_visibility(
            object(),
            "new-hidden",
            body,
        )
    )

    assert result == {
        "ok": True,
        "name": "new-hidden",
        "hidden": True,
    }
    assert saved["dashboard"]["hidden_plugins"] == [
        "keep-hidden",
        "new-hidden",
    ]


def test_visibility_write_resolves_dashboard_name_to_canonical_key(
    monkeypatch,
    tmp_path,
):
    plugin_dir = tmp_path / "plugins" / "web" / "runtime-key"
    plugin_dir.mkdir(parents=True)
    entry = (
        "runtime-name",
        "1.0.0",
        "",
        "user",
        plugin_dir,
        "web/runtime-key",
        "standalone",
    )
    dashboard = {
        "name": "dashboard-label",
        "source": "user",
        "_plugin_dir": str(plugin_dir),
    }
    config = {
        "dashboard": {
            "hidden_plugins": ["dashboard-label"],
        }
    }
    saved = {}
    monkeypatch.setattr(web_server, "_require_token", lambda _request: None)
    monkeypatch.setattr(web_server, "load_config", lambda: config)
    monkeypatch.setattr(
        web_server,
        "save_config",
        lambda value: saved.update(value),
    )
    monkeypatch.setattr(
        web_server,
        "_get_dashboard_plugins",
        lambda force_rescan=False: [dashboard],
    )
    monkeypatch.setattr(plugins_cmd, "_discover_all_plugins", lambda: [entry])

    hidden_result = asyncio.run(
        web_server.post_plugin_visibility(
            object(),
            "dashboard-label",
            web_server._PluginVisibilityBody(hidden=True),
        )
    )

    assert hidden_result == {
        "ok": True,
        "name": "web/runtime-key",
        "hidden": True,
    }
    assert saved["dashboard"]["hidden_plugins"] == ["web/runtime-key"]

    config["dashboard"]["hidden_plugins"] = [
        "dashboard-label",
        "web/runtime-key",
    ]
    visible_result = asyncio.run(
        web_server.post_plugin_visibility(
            object(),
            "web/runtime-key",
            web_server._PluginVisibilityBody(hidden=False),
        )
    )

    assert visible_result == {
        "ok": True,
        "name": "web/runtime-key",
        "hidden": False,
    }
    assert saved["dashboard"]["hidden_plugins"] == []
