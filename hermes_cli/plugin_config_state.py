"""Side-effect-free reads of plugin activation lists.

Provider discovery can run before :mod:`hermes_cli.config` finishes importing,
so importing that module here would create a providers/config cycle. This
module reads only the plugin subsection and mirrors managed-scope precedence.
When the full config module is already initialized, its cached loader remains
the source of truth.
"""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any, Optional

from dotenv import dotenv_values

from hermes_constants import get_config_path
from utils import fast_safe_load


_POLICY_READ_FAILED_KEY = "__hermes_plugin_policy_read_failed__"
_POLICY_FAIL_CLOSED_SENTINEL = "__hermes_fail_closed_non_bundled_plugins__"
_LAST_USER_CONFIG_BY_PATH: dict[str, dict[str, Any]] = {}


def plugin_config_aliases(name: str, key: str) -> set[str]:
    """Return accepted config identities for one plugin entry."""
    return {alias for alias in (str(name), str(key)) if alias}


def string_config_set(value: Any) -> set[str]:
    """Keep valid string entries without letting malformed YAML abort discovery."""
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def _expand_env_refs(value: Any, env: dict[str, str]) -> Any:
    """Expand config-style environment references without importing config.py."""
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            raw = match.group(0)
            inner = match.group(1).strip()
            if inner.startswith("env:"):
                name = inner[len("env:"):].strip()
                return env.get(name, raw) if name else raw
            if ":" in inner and re.match(r"^[a-z][a-z0-9_-]*:", inner):
                return raw
            return env.get(inner, raw)

        return re.sub(r"\${([^}]+)}", replace, value)
    if isinstance(value, dict):
        return {key: _expand_env_refs(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env_refs(item, env) for item in value]
    return value


def _read_dotenv(path: Path) -> dict[str, str]:
    """Parse one dotenv file without mutating the process environment."""
    if not path.is_file():
        return {}
    try:
        return {
            key: value
            for key, value in dotenv_values(path).items()
            if isinstance(key, str) and isinstance(value, str)
        }
    except Exception:
        return {}


def _activation_env() -> dict[str, str]:
    """Mirror dotenv precedence for activation references without side effects."""
    env = dict(os.environ)
    home = get_config_path().parent
    user_env_path = home / ".env"
    user_env = _read_dotenv(user_env_path)
    if user_env:
        env.update(user_env)

    op_env = _read_dotenv(home / ".op.env")
    if "OP_SERVICE_ACCOUNT_TOKEN" not in env:
        token = op_env.get("OP_SERVICE_ACCOUNT_TOKEN")
        if token:
            env["OP_SERVICE_ACCOUNT_TOKEN"] = token

    project_env = _read_dotenv(Path(__file__).resolve().parents[1] / ".env")
    if user_env_path.is_file():
        for key, value in project_env.items():
            env.setdefault(key, value)
    else:
        env.update(project_env)

    try:
        from hermes_cli.managed_scope import get_managed_dir

        managed_dir = get_managed_dir()
    except Exception:
        managed_dir = None
    if managed_dir is not None:
        env.update(_read_dotenv(managed_dir / ".env"))
    return env


def _read_user_config() -> dict[str, Any]:
    path = get_config_path()
    path_key = str(path)
    try:
        if not path.is_file():
            _LAST_USER_CONFIG_BY_PATH[path_key] = {}
            return {}
        with path.open(encoding="utf-8") as handle:
            parsed = fast_safe_load(handle)
        if parsed is None:
            parsed = {}
        if not isinstance(parsed, dict):
            raise ValueError("config.yaml must contain a mapping")
        _LAST_USER_CONFIG_BY_PATH[path_key] = copy.deepcopy(parsed)
        return parsed
    except Exception:
        previous = _LAST_USER_CONFIG_BY_PATH.get(path_key)
        if previous is not None:
            return copy.deepcopy(previous)
        return {_POLICY_READ_FAILED_KEY: True}


def _effective_config() -> dict[str, Any]:
    ignore_user_config = os.environ.get("HERMES_IGNORE_USER_CONFIG") == "1"
    config = {} if ignore_user_config else _read_user_config()
    try:
        from hermes_cli.managed_scope import load_managed_config

        managed = load_managed_config()
    except Exception:
        managed = {}

    activation_env = _activation_env()
    user_plugins = _expand_env_refs(config.get("plugins"), activation_env)
    merged_plugins = dict(user_plugins) if isinstance(user_plugins, dict) else {}
    managed_plugins = (
        _expand_env_refs(managed.get("plugins"), activation_env)
        if isinstance(managed, dict)
        else None
    )
    if isinstance(managed_plugins, dict):
        merged_plugins.update(managed_plugins)

    effective = dict(config)
    if merged_plugins or "plugins" in config or isinstance(managed_plugins, dict):
        effective["plugins"] = merged_plugins
    return effective


def get_disabled_plugins() -> set[str]:
    """Return the effective ``plugins.disabled`` deny-list."""
    config = _effective_config()
    plugins = config.get("plugins")
    result = set()
    if not isinstance(plugins, dict):
        disabled = None
    else:
        disabled = plugins.get("disabled")
        result.update(string_config_set(disabled))
    if config.get(_POLICY_READ_FAILED_KEY) is True:
        result.add(_POLICY_FAIL_CLOSED_SENTINEL)
    return result


def get_enabled_plugins() -> Optional[set[str]]:
    """Return the effective allow-list, or ``None`` when it is not configured."""
    plugins = _effective_config().get("plugins")
    if not isinstance(plugins, dict) or "enabled" not in plugins:
        return None
    enabled = plugins.get("enabled")
    return string_config_set(enabled) if isinstance(enabled, list) else None


def plugin_policy_failed_closed(disabled: set[str]) -> bool:
    """Return whether non-bundled plugins must stay inactive after a read failure."""
    return _POLICY_FAIL_CLOSED_SENTINEL in disabled


def persistable_disabled_plugins(disabled: set[str]) -> set[str]:
    """Remove internal runtime policy markers before writing user config."""
    return {
        plugin
        for plugin in disabled
        if plugin != _POLICY_FAIL_CLOSED_SENTINEL
    }
