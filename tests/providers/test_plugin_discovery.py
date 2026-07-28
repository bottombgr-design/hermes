"""Tests for the model-providers plugin discovery system.

Verifies that:
 1. All bundled providers at plugins/model-providers/<name>/ are discovered
 2. User plugins at $HERMES_HOME/plugins/model-providers/<name>/ override bundled
 3. plugin.yaml manifests with kind=model-provider are correctly categorized
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _clear_provider_caches():
    """Force providers/__init__.py to re-discover on next list_providers()."""
    import providers as _pkg
    _pkg._REGISTRY.clear()
    _pkg._ALIASES.clear()
    _pkg._discovered = False
    _pkg._discovering = False
    _pkg._IMPORTED_PROVIDER_MODULES.clear()
    _pkg._PLUGIN_MANAGED_PROVIDER_IDS.clear()
    # Evict any cached plugin modules so the next import re-executes.
    for mod in list(sys.modules.keys()):
        if (
            mod.startswith("plugins.model_providers")
            or mod.startswith("_hermes_user_provider")
            or mod.startswith("_hermes_project_provider")
        ):
            del sys.modules[mod]


def test_bundled_plugins_discovered():
    """Every plugins/model-providers/<name>/ should contain a plugin.yaml + __init__.py."""
    plugins_dir = REPO_ROOT / "plugins" / "model-providers"
    assert plugins_dir.is_dir(), f"Missing {plugins_dir}"

    child_dirs = [c for c in plugins_dir.iterdir() if c.is_dir()]
    assert len(child_dirs) >= 28, f"Expected at least 28 provider plugins, found {len(child_dirs)}"

    for child in child_dirs:
        assert (child / "__init__.py").exists(), f"{child.name} missing __init__.py"
        assert (child / "plugin.yaml").exists(), f"{child.name} missing plugin.yaml"


def test_all_profiles_register():
    """After discovery, the registry must contain every bundled provider directory.

    This is an invariant — the number of profiles matches the number of plugin
    directories, not a hardcoded count. Counts shift when providers are
    added/removed; that's expected and shouldn't break CI.
    """
    _clear_provider_caches()
    from providers import list_providers

    plugins_dir = REPO_ROOT / "plugins" / "model-providers"
    plugin_dir_count = sum(1 for c in plugins_dir.iterdir() if c.is_dir())

    profiles = list_providers()
    names = sorted(p.name for p in profiles)
    # Some plugin __init__.py files register multiple profiles, so the registry
    # count is >= the directory count (never less).
    assert len(names) >= plugin_dir_count, (
        f"Expected at least {plugin_dir_count} profiles (one per plugin dir), got {len(names)}: {names}"
    )

    # Spot-check representative providers from different categories
    for required in (
        "openrouter", "anthropic", "custom", "bedrock", "openai-codex",
        "minimax-oauth", "gmi", "xiaomi", "alibaba-coding-plan", "fireworks",
    ):
        assert required in names, f"Missing profile: {required}"


def test_user_plugin_overrides_bundled(tmp_path, monkeypatch):
    """A user plugin with the same name must override the bundled profile."""
    # Point HERMES_HOME at a fresh temp dir
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    # get_hermes_home() may be module-cached depending on codebase; ensure the
    # env var is the source of truth. Most code paths re-read it each call.

    # Drop a user plugin that replaces 'gmi'
    user_gmi = hermes_home / "plugins" / "model-providers" / "gmi"
    user_gmi.mkdir(parents=True)
    (user_gmi / "__init__.py").write_text(
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "\n"
        "custom_gmi = ProviderProfile(\n"
        '    name="gmi",\n'
        '    aliases=("gmi-user-override-test",),\n'
        '    env_vars=("GMI_API_KEY",),\n'
        '    base_url="https://user-override.example.com/v1",\n'
        '    auth_type="api_key",\n'
        ")\n"
        "register_provider(custom_gmi)\n"
    )
    (user_gmi / "plugin.yaml").write_text(
        "name: gmi-user-override\n"
        "kind: model-provider\n"
        "version: 0.0.1\n"
        "description: Test user override\n"
    )

    _clear_provider_caches()
    from providers import get_provider_profile

    gmi = get_provider_profile("gmi")
    assert gmi is not None
    assert gmi.base_url == "https://user-override.example.com/v1", (
        f"User override not applied; got base_url={gmi.base_url!r}"
    )
    assert "gmi-user-override-test" in gmi.aliases

    # Clean up: reset discovery state so other tests see the bundled version
    _clear_provider_caches()


def test_user_override_keeps_later_source_registration_precedence(
    tmp_path,
    monkeypatch,
):
    import providers as provider_mod

    bundled_root = tmp_path / "bundled"
    user_root = tmp_path / "user"

    def write_provider(root, directory, base_url):
        plugin_dir = root / directory
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.yaml").write_text(
            f"name: {directory}-provider\n"
            "kind: model-provider\n"
            "version: 0.0.1\n",
            encoding="utf-8",
        )
        (plugin_dir / "__init__.py").write_text(
            "from providers import register_provider\n"
            "from providers.base import ProviderProfile\n"
            "register_provider(ProviderProfile(\n"
            "    name='shared-runtime-name',\n"
            "    env_vars=('SHARED_RUNTIME_KEY',),\n"
            f"    base_url='{base_url}',\n"
            "    auth_type='api_key',\n"
            "))\n",
            encoding="utf-8",
        )

    write_provider(
        bundled_root,
        "alpha",
        "https://bundled-alpha.example/v1",
    )
    write_provider(
        bundled_root,
        "zulu",
        "https://bundled-zulu.example/v1",
    )
    write_provider(
        user_root,
        "alpha",
        "https://user-alpha.example/v1",
    )

    monkeypatch.setattr(provider_mod, "_BUNDLED_PLUGINS_DIR", bundled_root)
    monkeypatch.setattr(provider_mod, "_user_plugins_dir", lambda: user_root)
    monkeypatch.setattr(provider_mod, "_project_plugins_dir", lambda: None)
    monkeypatch.setattr(provider_mod, "get_disabled_plugins", lambda: set())
    _clear_provider_caches()

    profile = provider_mod.get_provider_profile("shared-runtime-name")
    assert profile is not None
    assert profile.base_url == "https://user-alpha.example/v1"

    _clear_provider_caches()


def test_user_override_preserves_other_bundled_profiles(
    tmp_path,
    monkeypatch,
):
    import providers as provider_mod

    bundled_root = tmp_path / "bundled"
    user_root = tmp_path / "user"
    bundled_dir = bundled_root / "multi"
    user_dir = user_root / "multi"
    bundled_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)
    for plugin_dir, display_name in (
        (bundled_dir, "bundled-multi"),
        (user_dir, "user-multi"),
    ):
        (plugin_dir / "plugin.yaml").write_text(
            f"name: {display_name}\n"
            "kind: model-provider\n"
            "version: 0.0.1\n",
            encoding="utf-8",
        )
    (bundled_dir / "__init__.py").write_text(
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "register_provider(ProviderProfile(\n"
        "    name='multi-primary',\n"
        "    base_url='https://bundled-primary.example/v1',\n"
        "))\n"
        "register_provider(ProviderProfile(\n"
        "    name='multi-extra',\n"
        "    base_url='https://bundled-extra.example/v1',\n"
        "))\n",
        encoding="utf-8",
    )
    (user_dir / "__init__.py").write_text(
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "register_provider(ProviderProfile(\n"
        "    name='multi-primary',\n"
        "    base_url='https://user-primary.example/v1',\n"
        "))\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(provider_mod, "_BUNDLED_PLUGINS_DIR", bundled_root)
    monkeypatch.setattr(provider_mod, "_user_plugins_dir", lambda: user_root)
    monkeypatch.setattr(provider_mod, "_project_plugins_dir", lambda: None)
    monkeypatch.setattr(provider_mod, "get_disabled_plugins", lambda: set())
    _clear_provider_caches()

    primary = provider_mod.get_provider_profile("multi-primary")
    extra = provider_mod.get_provider_profile("multi-extra")

    assert primary is not None
    assert primary.base_url == "https://user-primary.example/v1"
    assert extra is not None
    assert extra.base_url == "https://bundled-extra.example/v1"
    _clear_provider_caches()


def test_provider_discovery_invalidation_applies_activation_changes(
    tmp_path,
    monkeypatch,
):
    import providers as provider_mod

    bundled_root = tmp_path / "bundled"
    plugin_dir = bundled_root / "toggle"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "name: toggle-provider\n"
        "kind: model-provider\n"
        "version: 0.0.1\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "register_provider(ProviderProfile(name='toggle-runtime'))\n",
        encoding="utf-8",
    )
    disabled: set[str] = set()
    monkeypatch.setattr(provider_mod, "_BUNDLED_PLUGINS_DIR", bundled_root)
    monkeypatch.setattr(provider_mod, "_user_plugins_dir", lambda: None)
    monkeypatch.setattr(provider_mod, "_project_plugins_dir", lambda: None)
    monkeypatch.setattr(
        provider_mod,
        "get_disabled_plugins",
        lambda: set(disabled),
    )
    _clear_provider_caches()

    assert provider_mod.get_provider_profile("toggle-runtime") is not None

    disabled.add("model-providers/toggle")
    provider_mod.invalidate_provider_discovery()
    assert provider_mod.get_provider_profile("toggle-runtime") is None

    disabled.clear()
    provider_mod.invalidate_provider_discovery()
    assert provider_mod.get_provider_profile("toggle-runtime") is not None
    _clear_provider_caches()


def test_provider_invalidation_refreshes_loaded_derived_indexes(
    tmp_path,
    monkeypatch,
):
    import hermes_cli.auth as auth_mod
    import hermes_cli.config as config_mod
    import hermes_cli.models as models_mod
    import providers as provider_mod

    bundled_root = tmp_path / "bundled"
    plugin_dir = bundled_root / "derived-refresh"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "name: derived-refresh-provider\n"
        "kind: model-provider\n"
        "version: 0.0.1\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "register_provider(ProviderProfile(\n"
        "    name='derived-refresh-runtime',\n"
        "    display_name='Derived Refresh',\n"
        "    env_vars=('HERMES_TEST_DERIVED_REFRESH_KEY',),\n"
        "    base_url='https://derived-refresh.example/v1',\n"
        "))\n",
        encoding="utf-8",
    )
    disabled: set[str] = set()

    try:
        with monkeypatch.context() as context:
            context.setattr(
                provider_mod,
                "_BUNDLED_PLUGINS_DIR",
                bundled_root,
            )
            context.setattr(
                provider_mod,
                "_user_plugins_dir",
                lambda: None,
            )
            context.setattr(
                provider_mod,
                "_project_plugins_dir",
                lambda: None,
            )
            context.setattr(
                provider_mod,
                "get_disabled_plugins",
                lambda: set(disabled),
            )
            _clear_provider_caches()
            provider_mod.invalidate_provider_discovery()

            assert "derived-refresh-runtime" in auth_mod.PROVIDER_REGISTRY
            assert (
                "HERMES_TEST_DERIVED_REFRESH_KEY"
                in config_mod.OPTIONAL_ENV_VARS
            )
            assert any(
                entry.slug == "derived-refresh-runtime"
                for entry in models_mod.CANONICAL_PROVIDERS
            )

            disabled.add("model-providers/derived-refresh")
            provider_mod.invalidate_provider_discovery()

            assert "derived-refresh-runtime" not in auth_mod.PROVIDER_REGISTRY
            assert (
                "HERMES_TEST_DERIVED_REFRESH_KEY"
                not in config_mod.OPTIONAL_ENV_VARS
            )
            assert all(
                entry.slug != "derived-refresh-runtime"
                for entry in models_mod.CANONICAL_PROVIDERS
            )
    finally:
        _clear_provider_caches()
        provider_mod.invalidate_provider_discovery()


def test_disabling_bundled_provider_hides_static_runtime_indexes(monkeypatch):
    import hermes_cli.auth as auth_mod
    import hermes_cli.models as models_mod
    import hermes_cli.runtime_provider as runtime_mod
    import providers as provider_mod

    disabled: set[str] = set()
    monkeypatch.setattr(
        provider_mod,
        "get_disabled_plugins",
        lambda: set(disabled),
    )

    try:
        _clear_provider_caches()
        provider_mod.invalidate_provider_discovery()

        assert provider_mod.is_plugin_managed_provider_id("anthropic")
        assert provider_mod.is_plugin_managed_provider_id("minimax-oauth")
        assert "anthropic" in auth_mod.PROVIDER_REGISTRY
        assert any(
            entry.slug == "anthropic"
            for entry in models_mod.CANONICAL_PROVIDERS
        )

        disabled.add("model-providers/anthropic")
        provider_mod.invalidate_provider_discovery()

        assert provider_mod.get_provider_profile("anthropic") is None
        assert "anthropic" not in auth_mod.PROVIDER_REGISTRY
        assert all(
            entry.slug != "anthropic"
            for entry in models_mod.CANONICAL_PROVIDERS
        )
        assert "claude" not in models_mod._KNOWN_PROVIDER_NAMES

        disabled.clear()
        provider_mod.invalidate_provider_discovery()

        assert provider_mod.get_provider_profile("anthropic") is not None
        assert "anthropic" in auth_mod.PROVIDER_REGISTRY
        assert any(
            entry.slug == "anthropic"
            for entry in models_mod.CANONICAL_PROVIDERS
        )
        assert "claude" in models_mod._KNOWN_PROVIDER_NAMES

        disabled.add("model-providers/nous")
        provider_mod.invalidate_provider_discovery()

        assert provider_mod.get_provider_profile("nous") is None
        assert "nous" not in auth_mod.PROVIDER_REGISTRY
        assert all(
            entry.slug != "nous"
            for entry in models_mod.CANONICAL_PROVIDERS
        )
        assert auth_mod.get_nous_service_config().id == "nous"
        assert auth_mod.get_nous_service_config().auth_type == "oauth_device_code"

        disabled.clear()
        provider_mod.invalidate_provider_discovery()

        disabled.add("model-providers/minimax")
        provider_mod.invalidate_provider_discovery()

        for provider_id in ("minimax", "minimax-cn", "minimax-oauth"):
            assert provider_mod.get_provider_profile(provider_id) is None
            assert provider_id not in auth_mod.PROVIDER_REGISTRY
            assert all(
                entry.slug != provider_id
                for entry in models_mod.CANONICAL_PROVIDERS
            )

        for provider_id in ("openrouter", "custom", "vertex", "azure-foundry"):
            disabled.clear()
            disabled.add(f"model-providers/{provider_id}")
            provider_mod.invalidate_provider_discovery()

            assert provider_mod.get_provider_profile(provider_id) is None
            assert not provider_mod.is_provider_plugin_active(provider_id)
            assert provider_id not in models_mod._KNOWN_PROVIDER_NAMES
            assert provider_id not in models_mod._PROVIDER_LABELS
            with pytest.raises(auth_mod.AuthError, match="disabled"):
                auth_mod.resolve_provider(provider_id)
            with pytest.raises(auth_mod.AuthError, match="disabled"):
                runtime_mod.resolve_runtime_provider(requested=provider_id)
    finally:
        disabled.clear()
        _clear_provider_caches()
        provider_mod.invalidate_provider_discovery()


def test_derived_provider_indexes_fall_back_when_discovery_fails(monkeypatch):
    import hermes_cli.auth as auth_mod
    import hermes_cli.models as models_mod
    import providers as provider_mod

    try:
        dynamic = auth_mod.ProviderConfig(
            id="dynamic-only",
            name="Dynamic only",
            auth_type="api_key",
        )
        auth_mod.PROVIDER_REGISTRY.replace({"dynamic-only": dynamic})
        models_mod.CANONICAL_PROVIDERS[:] = [
            models_mod.ProviderEntry("dynamic-only", "Dynamic only", "test")
        ]
        models_mod._canonical_slugs.clear()
        models_mod._canonical_slugs.add("dynamic-only")
        monkeypatch.setattr(
            provider_mod,
            "list_providers",
            lambda: (_ for _ in ()).throw(
                PermissionError("unreadable plugin root")
            ),
        )

        auth_mod._refresh_provider_registry_from_plugins()
        models_mod._refresh_canonical_providers_from_plugins()

        assert "dynamic-only" not in auth_mod.PROVIDER_REGISTRY
        assert "nous" in auth_mod.PROVIDER_REGISTRY
        assert all(
            entry.slug != "dynamic-only"
            for entry in models_mod.CANONICAL_PROVIDERS
        )
        assert any(
            entry.slug == "openrouter"
            for entry in models_mod.CANONICAL_PROVIDERS
        )
        assert "openrouter" in models_mod._PROVIDER_LABELS
    finally:
        monkeypatch.undo()
        _clear_provider_caches()
        provider_mod.invalidate_provider_discovery()


def test_provider_registry_replacement_keeps_reader_snapshots_stable():
    import hermes_cli.auth as auth_mod

    first = auth_mod.ProviderConfig(
        id="first",
        name="First",
        auth_type="api_key",
    )
    second = auth_mod.ProviderConfig(
        id="second",
        name="Second",
        auth_type="api_key",
    )
    registry = auth_mod._AtomicProviderRegistry({"first": first})
    errors = []
    start = threading.Event()

    def read_snapshots():
        start.wait()
        try:
            for _ in range(2_000):
                snapshot = registry.items()
                assert snapshot in (
                    (("first", first),),
                    (("second", second),),
                )
        except BaseException as exc:
            errors.append(exc)

    reader = threading.Thread(target=read_snapshots)
    reader.start()
    start.set()
    for index in range(2_000):
        registry.replace(
            {"first": first} if index % 2 == 0 else {"second": second}
        )
    reader.join(timeout=5)

    assert not reader.is_alive()
    assert errors == []


def test_bundled_provider_manifests_declare_every_runtime_profile():
    import providers as provider_mod

    _clear_provider_caches()
    try:
        profiles = provider_mod.list_providers()
        undeclared = sorted(
            profile.name
            for profile in profiles
            if not provider_mod.is_plugin_managed_provider_id(profile.name)
        )
        assert undeclared == []
    finally:
        _clear_provider_caches()


def test_safe_mode_skips_legacy_provider_modules(
    tmp_path,
    monkeypatch,
):
    import pkgutil

    import providers as provider_mod

    bundled_root = tmp_path / "bundled"
    bundled_root.mkdir()

    def _unexpected_legacy_scan(_paths):
        raise AssertionError("safe mode must not scan legacy provider modules")

    monkeypatch.setenv("HERMES_SAFE_MODE", "1")
    monkeypatch.setattr(provider_mod, "_BUNDLED_PLUGINS_DIR", bundled_root)
    monkeypatch.setattr(provider_mod, "_user_plugins_dir", lambda: None)
    monkeypatch.setattr(provider_mod, "_project_plugins_dir", lambda: None)
    monkeypatch.setattr(provider_mod, "get_disabled_plugins", lambda: set())
    monkeypatch.setattr(pkgutil, "iter_modules", _unexpected_legacy_scan)
    _clear_provider_caches()

    assert provider_mod.list_providers() == []
    _clear_provider_caches()


def test_general_plugin_manager_skips_model_provider_kind(tmp_path, monkeypatch):
    """The general PluginManager must NOT import model-provider plugins
    (providers/__init__.py handles them). It records the manifest only."""
    from hermes_cli import plugins as plugin_mod

    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    # Create a user-installed plugin with an explicit kind: model-provider.
    user_plugin = hermes_home / "plugins" / "test-model-provider"
    user_plugin.mkdir(parents=True)
    (user_plugin / "plugin.yaml").write_text(
        "name: test-model-provider\n"
        "kind: model-provider\n"
        "version: 0.0.1\n"
    )
    (user_plugin / "__init__.py").write_text(
        # Intentionally broken import — if the general loader tries to
        # import this module, the test will fail with ImportError.
        "raise AssertionError('model-provider plugins must not be imported by PluginManager')\n"
    )

    # Fresh manager
    manager = plugin_mod.PluginManager()
    manager.discover_and_load(force=True)

    # The manifest should be recorded but not loaded
    loaded = manager._plugins.get("test-model-provider")
    assert loaded is not None
    assert loaded.manifest.kind == "model-provider"
    # No import means the module must NOT be in the plugins list as a loaded one.
    # We check that the general loader didn't crash and didn't raise from the
    # broken __init__.py.


@pytest.mark.parametrize(
    "disabled_alias",
    [
        "disabled-provider-display",
        "model-providers/disabled-provider",
    ],
)
def test_disabled_provider_plugin_is_not_registered(
    tmp_path,
    monkeypatch,
    disabled_alias,
):
    import providers as provider_mod

    bundled_dir = tmp_path / "model-providers"
    plugin_dir = bundled_dir / "disabled-provider"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "name: disabled-provider-display\n"
        "kind: model-provider\n"
        "version: 0.0.1\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "register_provider(ProviderProfile(\n"
        "    name='disabled-provider-runtime',\n"
        "    env_vars=('DISABLED_PROVIDER_KEY',),\n"
        "    base_url='https://disabled.example/v1',\n"
        "    auth_type='api_key',\n"
        "))\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(provider_mod, "_BUNDLED_PLUGINS_DIR", bundled_dir)
    monkeypatch.setattr(provider_mod, "_user_plugins_dir", lambda: None)
    monkeypatch.setattr(
        provider_mod,
        "get_disabled_plugins",
        lambda: {disabled_alias},
    )
    _clear_provider_caches()

    assert provider_mod.get_provider_profile("disabled-provider-runtime") is None

    _clear_provider_caches()


def test_project_provider_plugin_is_loaded_when_opted_in(tmp_path, monkeypatch):
    import providers as provider_mod

    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    project = tmp_path / "project"
    plugin_dir = (
        project / ".hermes" / "plugins" / "model-providers" / "project-provider"
    )
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "name: project-provider-display\n"
        "kind: model-provider\n"
        "version: 0.0.1\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "register_provider(ProviderProfile(\n"
        "    name='project-provider-runtime',\n"
        "    env_vars=('PROJECT_PROVIDER_KEY',),\n"
        "    base_url='https://project.example/v1',\n"
        "    auth_type='api_key',\n"
        "))\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(project)
    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "1")
    monkeypatch.setattr(provider_mod, "_BUNDLED_PLUGINS_DIR", bundled_dir)
    monkeypatch.setattr(provider_mod, "_user_plugins_dir", lambda: None)
    monkeypatch.setattr(provider_mod, "get_disabled_plugins", lambda: set())
    _clear_provider_caches()

    profile = provider_mod.get_provider_profile("project-provider-runtime")
    assert profile is not None
    assert profile.base_url == "https://project.example/v1"

    _clear_provider_caches()


@pytest.mark.parametrize("source", ["user", "project"])
def test_safe_mode_skips_custom_provider_plugins(
    source,
    tmp_path,
    monkeypatch,
):
    import providers as provider_mod

    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    hermes_home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    if source == "user":
        plugin_dir = (
            hermes_home
            / "plugins"
            / "model-providers"
            / "unsafe-provider"
        )
    else:
        plugin_dir = (
            project
            / ".hermes"
            / "plugins"
            / "model-providers"
            / "unsafe-provider"
        )
    plugin_dir.mkdir(parents=True)
    marker = tmp_path / f"{source}-provider-imported"
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

    monkeypatch.chdir(project)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "1")
    monkeypatch.setenv("HERMES_SAFE_MODE", "1")
    monkeypatch.setattr(provider_mod, "_BUNDLED_PLUGINS_DIR", bundled_dir)
    monkeypatch.setattr(provider_mod, "get_disabled_plugins", lambda: set())
    _clear_provider_caches()

    provider_mod.list_providers()

    assert not marker.exists()
    _clear_provider_caches()


def test_policy_read_failure_skips_user_provider_plugins(
    tmp_path,
    monkeypatch,
):
    import providers as provider_mod
    from hermes_cli import plugin_config_state

    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    user_dir = tmp_path / "user"
    plugin_dir = user_dir / "unsafe-provider"
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

    monkeypatch.setattr(provider_mod, "_BUNDLED_PLUGINS_DIR", bundled_dir)
    monkeypatch.setattr(provider_mod, "_user_plugins_dir", lambda: user_dir)
    monkeypatch.setattr(provider_mod, "_project_plugins_dir", lambda: None)
    monkeypatch.setattr(
        provider_mod,
        "get_disabled_plugins",
        lambda: {plugin_config_state._POLICY_FAIL_CLOSED_SENTINEL},
    )
    _clear_provider_caches()

    provider_mod.list_providers()

    assert not marker.exists()
    _clear_provider_caches()


def test_disabled_user_override_does_not_fall_back_to_bundled(
    tmp_path,
    monkeypatch,
):
    import providers as provider_mod

    bundled_root = tmp_path / "bundled"
    user_root = tmp_path / "user"
    for root, source, base_url, display_name in (
        (
            bundled_root,
            "bundled",
            "https://bundled.example/v1",
            "bundled-provider-display",
        ),
        (
            user_root,
            "user",
            "https://user.example/v1",
            "user-provider-display",
        ),
    ):
        plugin_dir = root / "shared-provider"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.yaml").write_text(
            f"name: {display_name}\n"
            "kind: model-provider\n"
            "version: 0.0.1\n",
            encoding="utf-8",
        )
        (plugin_dir / "__init__.py").write_text(
            "from providers import register_provider\n"
            "from providers.base import ProviderProfile\n"
            "register_provider(ProviderProfile(\n"
            "    name='shared-provider-runtime',\n"
            "    env_vars=('SHARED_PROVIDER_KEY',),\n"
            f"    base_url='{base_url}',\n"
            "    auth_type='api_key',\n"
            "))\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(provider_mod, "_BUNDLED_PLUGINS_DIR", bundled_root)
    monkeypatch.setattr(provider_mod, "_user_plugins_dir", lambda: user_root)
    monkeypatch.setattr(provider_mod, "_project_plugins_dir", lambda: None)
    monkeypatch.setattr(
        provider_mod,
        "get_disabled_plugins",
        lambda: {"user-provider-display"},
    )
    _clear_provider_caches()

    assert provider_mod.get_provider_profile("shared-provider-runtime") is None

    _clear_provider_caches()


def test_initial_model_aliases_exclude_disabled_provider(tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "plugins:\n  disabled:\n    - model-providers/anthropic\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    script = (
        "from hermes_cli.models import _KNOWN_PROVIDER_NAMES, parse_model_input\n"
        "assert 'claude' not in _KNOWN_PROVIDER_NAMES\n"
        "assert parse_model_input('claude:model', 'openrouter') == "
        "('openrouter', 'claude:model')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_provider_directory_leaf_is_not_a_global_config_alias(
    tmp_path,
    monkeypatch,
):
    import providers as provider_mod

    bundled_root = tmp_path / "bundled"
    plugin_dir = bundled_root / "openrouter"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "name: openrouter-provider\n"
        "kind: model-provider\n"
        "version: 0.0.1\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "register_provider(ProviderProfile(\n"
        "    name='openrouter-runtime',\n"
        "    env_vars=('OPENROUTER_TEST_KEY',),\n"
        "    base_url='https://openrouter.example/v1',\n"
        "    auth_type='api_key',\n"
        "))\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(provider_mod, "_BUNDLED_PLUGINS_DIR", bundled_root)
    monkeypatch.setattr(provider_mod, "_user_plugins_dir", lambda: None)
    monkeypatch.setattr(provider_mod, "_project_plugins_dir", lambda: None)
    monkeypatch.setattr(
        provider_mod,
        "get_disabled_plugins",
        lambda: {"openrouter"},
    )
    _clear_provider_caches()

    assert provider_mod.get_provider_profile("openrouter-runtime") is not None

    _clear_provider_caches()


def test_provider_first_import_preserves_profile_env_metadata(tmp_path):
    hermes_home = tmp_path / ".hermes"
    plugin_dir = (
        hermes_home / "plugins" / "model-providers" / "import-order-provider"
    )
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "name: import-order-provider\n"
        "kind: model-provider\n"
        "version: 0.0.1\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "register_provider(ProviderProfile(\n"
        "    name='import-order-provider',\n"
        "    env_vars=('IMPORT_ORDER_PROVIDER_KEY',),\n"
        "    base_url='https://import-order.example/v1',\n"
        "    auth_type='api_key',\n"
        "))\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import providers; "
                "providers.list_providers(); "
                "from hermes_cli.config import OPTIONAL_ENV_VARS; "
                "assert 'IMPORT_ORDER_PROVIDER_KEY' in OPTIONAL_ENV_VARS"
            ),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr


def test_dotenv_backed_disable_blocks_user_provider_import(tmp_path):
    hermes_home = tmp_path / ".hermes"
    plugin_dir = (
        hermes_home / "plugins" / "model-providers" / "dotenv-blocked-provider"
    )
    plugin_dir.mkdir(parents=True)
    sentinel = tmp_path / "provider-imported"
    (hermes_home / "config.yaml").write_text(
        "plugins:\n"
        "  disabled:\n"
        "    - ${PLUGIN_DENY}\n",
        encoding="utf-8",
    )
    (hermes_home / ".env").write_text(
        "PLUGIN_DENY=model-providers/dotenv-blocked-provider\n",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.yaml").write_text(
        "name: dotenv-blocked-provider\n"
        "kind: model-provider\n"
        "version: 0.0.1\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('imported', encoding='utf-8')\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    env["PLUGIN_DENY"] = "model-providers/stale-shell-value"
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import providers; "
                "assert providers.get_provider_profile("
                "'dotenv-blocked-provider') is None"
            ),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert not sentinel.exists()
