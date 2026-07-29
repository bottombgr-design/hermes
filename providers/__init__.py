"""Provider module registry.

Provider profiles can live in two places:

1. Bundled plugins: ``plugins/model-providers/<name>/`` (shipped with hermes-agent)
2. User plugins: ``$HERMES_HOME/plugins/model-providers/<name>/``
3. Project plugins: ``./.hermes/plugins/model-providers/<name>/`` when opted in

Each plugin directory contains:
  - ``__init__.py`` — calls ``register_provider(profile)`` at import
  - ``plugin.yaml`` — manifest (name, kind: model-provider, version, description)

Discovery is lazy: the first call to ``get_provider_profile()`` or
``list_providers()`` scans these locations and imports every selected plugin.
User and project plugins override earlier sources on key collision, so third
parties can replace a built-in profile without editing the repo.

For backward compatibility, ``providers/*.py`` files (other than ``base.py``
and ``__init__.py``) are still discovered via ``pkgutil.iter_modules``.
This lets out-of-tree users drop a single-file profile into an editable
install without the plugin dir structure. New profiles should prefer the
plugin layout.

Usage::

    from providers import get_provider_profile
    profile = get_provider_profile("nvidia")   # ProviderProfile or None
    profile = get_provider_profile("kimi")     # checks name + aliases
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from hermes_cli.plugin_config_state import (
    get_disabled_plugins,
    plugin_policy_failed_closed,
    plugin_config_aliases,
)
from providers.base import OMIT_TEMPERATURE, ProviderProfile  # noqa: F401
from utils import env_var_enabled, fast_safe_load

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, ProviderProfile] = {}
_ALIASES: dict[str, str] = {}
_discovered = False
_discovering = False
_DISCOVERY_LOCK = threading.RLock()
_IMPORTED_PROVIDER_MODULES: set[str] = set()
_PROVIDER_REFRESH_HOOKS: list[Callable[[], None]] = []
_PLUGIN_MANAGED_PROVIDER_IDS: set[str] = set()

# Repo-root ``plugins/model-providers/`` — populated at discovery time.
_BUNDLED_PLUGINS_DIR = (
    Path(__file__).resolve().parent.parent / "plugins" / "model-providers"
)


def register_provider(profile: ProviderProfile) -> None:
    """Register a provider profile by name and aliases.

    Later registrations with the same name replace earlier ones — so user
    plugins under ``$HERMES_HOME/plugins/model-providers/`` can override
    bundled profiles without editing repo code.
    """
    with _DISCOVERY_LOCK:
        _REGISTRY[profile.name] = profile
        for alias in profile.aliases:
            _ALIASES[alias] = profile.name


def get_provider_profile(name: str) -> ProviderProfile | None:
    """Look up a provider profile by name or alias.

    Returns None if the provider has no profile (falls back to generic).
    """
    with _DISCOVERY_LOCK:
        if not _discovered:
            _discover_providers()
        canonical = _ALIASES.get(name, name)
        return _REGISTRY.get(canonical)


def list_providers() -> list[ProviderProfile]:
    """Return all registered provider profiles (one per canonical name)."""
    with _DISCOVERY_LOCK:
        if not _discovered:
            _discover_providers()
        # Deduplicate: _REGISTRY has canonical names; _ALIASES points to same objects
        seen: set[int] = set()
        result: list[ProviderProfile] = []
        for profile in _REGISTRY.values():
            pid = id(profile)
            if pid not in seen:
                seen.add(pid)
                result.append(profile)
        return result


def register_provider_refresh_hook(callback: Callable[[], None]) -> None:
    """Register an in-process index that derives state from provider discovery."""
    with _DISCOVERY_LOCK:
        if callback not in _PROVIDER_REFRESH_HOOKS:
            _PROVIDER_REFRESH_HOOKS.append(callback)


def invalidate_provider_discovery() -> None:
    """Drop provider discovery and rebuild loaded process-wide derived indexes."""
    global _discovered, _discovering
    with _DISCOVERY_LOCK:
        _REGISTRY.clear()
        _ALIASES.clear()
        _PLUGIN_MANAGED_PROVIDER_IDS.clear()
        for module_name in list(sys.modules):
            if any(
                module_name == prefix
                or module_name.startswith(f"{prefix}.")
                for prefix in _IMPORTED_PROVIDER_MODULES
            ):
                sys.modules.pop(module_name, None)
        _IMPORTED_PROVIDER_MODULES.clear()
        _discovered = False
        _discovering = False
        for callback in tuple(_PROVIDER_REFRESH_HOOKS):
            try:
                callback()
            except Exception:
                logger.warning(
                    "Failed to refresh a provider-derived registry",
                    exc_info=True,
                )


def _user_plugins_dir() -> Path | None:
    """Return ``$HERMES_HOME/plugins/model-providers/`` if it exists."""
    if env_var_enabled("HERMES_SAFE_MODE"):
        return None
    try:
        from hermes_constants import get_hermes_home

        d = get_hermes_home() / "plugins" / "model-providers"
        return d if d.is_dir() else None
    except Exception:
        return None


def _project_plugins_dir() -> Path | None:
    """Return the opt-in project model-provider directory when available."""
    if env_var_enabled("HERMES_SAFE_MODE") or not env_var_enabled(
        "HERMES_ENABLE_PROJECT_PLUGINS"
    ):
        return None
    directory = Path.cwd() / ".hermes" / "plugins" / "model-providers"
    return directory if directory.is_dir() else None


@dataclass(frozen=True)
class _ProviderPlugin:
    path: Path
    source: str
    key: str
    aliases: frozenset[str]
    provider_ids: frozenset[str]


def _provider_plugin(plugin_dir: Path, source: str) -> _ProviderPlugin:
    """Return provider identity without importing the plugin module."""
    key = f"model-providers/{plugin_dir.name}"
    aliases = {key}
    provider_ids = {plugin_dir.name}
    manifest_file = plugin_dir / "plugin.yaml"
    if not manifest_file.exists():
        manifest_file = plugin_dir / "plugin.yml"
    if manifest_file.exists():
        try:
            data = fast_safe_load(manifest_file.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                manifest_name = data.get("name")
                if isinstance(manifest_name, str) and manifest_name.strip():
                    aliases.update(
                        plugin_config_aliases(manifest_name.strip(), key)
                    )
                manifest_provider_ids = data.get("provider_ids")
                if isinstance(manifest_provider_ids, list):
                    declared_provider_ids = {
                        value.strip()
                        for value in manifest_provider_ids
                        if isinstance(value, str) and value.strip()
                    }
                    if declared_provider_ids:
                        provider_ids = declared_provider_ids
        except Exception:
            logger.debug(
                "Could not parse provider plugin manifest identity: %s",
                plugin_dir,
                exc_info=True,
            )
    return _ProviderPlugin(
        path=plugin_dir,
        source=source,
        key=key,
        aliases=frozenset(aliases),
        provider_ids=frozenset(provider_ids),
    )


def is_plugin_managed_provider_id(provider_id: str) -> bool:
    """Return whether a runtime provider ID is declared by any plugin."""
    with _DISCOVERY_LOCK:
        if not _discovered:
            _discover_providers()
        return provider_id in _PLUGIN_MANAGED_PROVIDER_IDS


def is_provider_plugin_active(provider_id: str) -> bool:
    """Return whether a canonical provider ID is available after plugin policy."""
    with _DISCOVERY_LOCK:
        if not _discovered:
            _discover_providers()
        if provider_id not in _PLUGIN_MANAGED_PROVIDER_IDS:
            return True
        return provider_id in _REGISTRY


def _import_plugin_dir(plugin_dir: Path, source: str) -> None:
    """Import a single plugin directory so it self-registers.

    ``source`` is "bundled", "user", or "project", used for module identity
    and log messages.
    """
    init_file = plugin_dir / "__init__.py"
    if not init_file.exists():
        return

    # Give bundled plugins a stable import path (``plugins.model_providers.<name>``)
    # so relative imports within the plugin work. User plugins load via
    # ``importlib.util.spec_from_file_location`` with a unique module name so
    # multiple HERMES_HOME profiles don't alias each other.
    safe_name = plugin_dir.name.replace("-", "_")
    if source == "bundled":
        module_name = f"plugins.model_providers.{safe_name}"
    else:
        module_name = f"_hermes_{source}_provider_{safe_name}"

    if module_name in sys.modules:
        _IMPORTED_PROVIDER_MODULES.add(module_name)
        return  # already imported

    try:
        spec = importlib.util.spec_from_file_location(
            module_name, init_file, submodule_search_locations=[str(plugin_dir)]
        )
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        _IMPORTED_PROVIDER_MODULES.add(module_name)
    except Exception as exc:
        logger.warning(
            "Failed to load %s provider plugin %s: %s", source, plugin_dir.name, exc
        )
        sys.modules.pop(module_name, None)


def _discover_providers() -> None:
    """Run provider discovery once, without caching a partial sweep."""
    global _discovered, _discovering
    with _DISCOVERY_LOCK:
        if _discovered or _discovering:
            return
        _discovering = True
        try:
            _discover_providers_inner()
        except BaseException:
            _discovered = False
            raise
        else:
            _discovered = True
        finally:
            _discovering = False


def _discover_providers_inner() -> None:
    """Populate the registry by importing every provider plugin.

    Order:
      1. Bundled plugins at ``<repo>/plugins/model-providers/<name>/``
      2. User plugins at ``$HERMES_HOME/plugins/model-providers/<name>/``
      3. Opt-in project plugins at ``./.hermes/plugins/model-providers/<name>/``
      4. Legacy per-file modules at ``providers/<name>.py`` (back-compat)

    Each step imports its plugins, which call ``register_provider()`` at
    module-level. Later steps win on name collision.
    """
    disabled = get_disabled_plugins()
    policy_failed_closed = plugin_policy_failed_closed(disabled)
    candidates: list[_ProviderPlugin] = []
    winners: dict[str, _ProviderPlugin] = {}

    # 1. Bundled plugins — shipped with hermes-agent.
    if _BUNDLED_PLUGINS_DIR.is_dir():
        for child in sorted(_BUNDLED_PLUGINS_DIR.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            plugin = _provider_plugin(child, "bundled")
            candidates.append(plugin)
            winners[plugin.key] = plugin

    # 2. User plugins — under $HERMES_HOME/plugins/model-providers/<name>/.
    #    These can override any bundled profile of the same name (last-writer-wins
    #    in register_provider()).
    user_dir = None if policy_failed_closed else _user_plugins_dir()
    if user_dir is not None:
        for child in sorted(user_dir.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            plugin = _provider_plugin(child, "user")
            candidates.append(plugin)
            winners[plugin.key] = plugin

    project_dir = None if policy_failed_closed else _project_plugins_dir()
    if project_dir is not None:
        for child in sorted(project_dir.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            plugin = _provider_plugin(child, "project")
            candidates.append(plugin)
            winners[plugin.key] = plugin

    _PLUGIN_MANAGED_PROVIDER_IDS.clear()
    for plugin in candidates:
        _PLUGIN_MANAGED_PROVIDER_IDS.update(plugin.provider_ids)

    # The final source controls whether a canonical plugin key is active, but
    # enabled sources still load in source order. A user override may replace
    # one runtime profile from a bundled module without deleting the other
    # profiles that module registers.
    disabled_keys = {
        key
        for key, winner in winners.items()
        if winner.aliases & disabled
    }
    for plugin in candidates:
        if plugin.key in disabled_keys:
            logger.debug("Skipping disabled provider plugin '%s'", plugin.key)
            continue
        _import_plugin_dir(plugin.path, plugin.source)

    # 4. Legacy single-file profiles at providers/<name>.py. Kept for
    #    back-compat — if someone drops a ``providers/foo.py`` into an
    #    editable install, it still works without the plugin layout. Safe mode
    #    excludes this user-extension path along with user/project plugins.
    if not env_var_enabled("HERMES_SAFE_MODE"):
        try:
            import pkgutil

            import providers as _pkg

            for _importer, modname, _ispkg in pkgutil.iter_modules(_pkg.__path__):
                if modname.startswith("_") or modname == "base":
                    continue
                try:
                    module_name = f"providers.{modname}"
                    importlib.import_module(module_name)
                    _IMPORTED_PROVIDER_MODULES.add(module_name)
                except ImportError as exc:
                    logger.warning(
                        "Failed to import legacy provider module %s: %s",
                        modname,
                        exc,
                    )
        except Exception:
            pass
