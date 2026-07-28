"""Profile-isolation contracts for terminal and execute_code child envs."""

import json
import os
from unittest.mock import patch

import pytest
import yaml

from agent import secret_scope
from gateway import session_context
from hermes_constants import (
    reset_hermes_home_override,
    set_hermes_home_override,
)
from tools import env_passthrough


_TOOL_SECRET = "PROFILE_TOOL_TOKEN"


@pytest.fixture(autouse=True)
def _reset_context_state(monkeypatch):
    """Keep process-global test state from leaking into sibling tests."""
    was_multiplexed = secret_scope.is_multiplex_active()
    secret_scope.set_multiplex_active(False)
    env_passthrough.clear_env_passthrough()
    monkeypatch.setattr(env_passthrough, "_config_passthrough", None)
    env_passthrough._profile_config_passthrough.clear()
    monkeypatch.setattr(session_context, "_session_context_engaged", False)
    for var in session_context._VAR_MAP.values():
        var.set(session_context._UNSET)
    yield
    secret_scope.set_multiplex_active(was_multiplexed)
    env_passthrough.clear_env_passthrough()
    env_passthrough._profile_config_passthrough.clear()
    for var in session_context._VAR_MAP.values():
        var.set(session_context._UNSET)


def _install_profile_scope(tmp_path, monkeypatch, secrets):
    launch_home = tmp_path / "launch"
    profile_home = tmp_path / "profiles" / "work"
    launch_home.mkdir()
    profile_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(launch_home))
    monkeypatch.setenv(_TOOL_SECRET, "launch-secret")
    monkeypatch.setattr(
        "tools.env_passthrough.get_all_passthrough",
        lambda: frozenset({_TOOL_SECRET}),
    )
    home_token = set_hermes_home_override(profile_home)
    secret_token = secret_scope.set_secret_scope(secrets)
    return launch_home, profile_home, home_token, secret_token


@pytest.mark.parametrize("builder_name", ["foreground", "background"])
def test_local_terminal_env_uses_active_profile_secret(
    tmp_path, monkeypatch, builder_name
):
    """Both local terminal spawn paths replace the launch profile's value."""
    from tools.environments.local import _make_run_env, _sanitize_subprocess_env

    _, _, home_token, secret_token = _install_profile_scope(
        tmp_path, monkeypatch, {_TOOL_SECRET: "work-secret"}
    )
    try:
        if builder_name == "foreground":
            child_env = _make_run_env({})
        else:
            child_env = _sanitize_subprocess_env(dict(os.environ))
    finally:
        secret_scope.reset_secret_scope(secret_token)
        reset_hermes_home_override(home_token)

    assert child_env[_TOOL_SECRET] == "work-secret"


def test_profile_scope_miss_does_not_leak_launch_profile_secret(tmp_path, monkeypatch):
    """An absent profile key is authoritative while a profile override is active."""
    from tools.environments.local import _make_run_env

    _, _, home_token, secret_token = _install_profile_scope(tmp_path, monkeypatch, {})
    try:
        child_env = _make_run_env({})
    finally:
        secret_scope.reset_secret_scope(secret_token)
        reset_hermes_home_override(home_token)

    assert _TOOL_SECRET not in child_env


def test_single_profile_scope_keeps_process_env_fallback(monkeypatch):
    """A non-isolating scope remains an overlay for single-profile callers."""
    from tools.environments.local import _make_run_env

    monkeypatch.setenv(_TOOL_SECRET, "shell-secret")
    monkeypatch.setattr(
        "tools.env_passthrough.get_all_passthrough",
        lambda: frozenset({_TOOL_SECRET}),
    )
    secret_token = secret_scope.set_secret_scope({})
    try:
        child_env = _make_run_env({})
    finally:
        secret_scope.reset_secret_scope(secret_token)

    assert child_env[_TOOL_SECRET] == "shell-secret"


def test_profile_override_uses_its_own_config_allowlist(tmp_path, monkeypatch):
    launch_home = tmp_path / "launch"
    profile_home = tmp_path / "profiles" / "work"
    launch_home.mkdir()
    profile_home.mkdir(parents=True)
    (launch_home / "config.yaml").write_text(
        yaml.dump({"terminal": {"env_passthrough": ["LAUNCH_TOKEN"]}}),
        encoding="utf-8",
    )
    (profile_home / "config.yaml").write_text(
        yaml.dump({"terminal": {"env_passthrough": ["WORK_TOKEN"]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(launch_home))

    assert env_passthrough.is_env_passthrough("LAUNCH_TOKEN")
    assert not env_passthrough.is_env_passthrough("WORK_TOKEN")

    token = set_hermes_home_override(profile_home)
    try:
        assert env_passthrough.is_env_passthrough("WORK_TOKEN")
        assert not env_passthrough.is_env_passthrough("LAUNCH_TOKEN")
    finally:
        reset_hermes_home_override(token)

    assert env_passthrough.is_env_passthrough("LAUNCH_TOKEN")
    assert not env_passthrough.is_env_passthrough("WORK_TOKEN")


def test_docker_forward_env_prefers_active_profile_scope(tmp_path, monkeypatch):
    """A reused desktop process must forward the selected profile's secret."""
    from tools.environments import docker as docker_env

    env = docker_env.DockerEnvironment.__new__(docker_env.DockerEnvironment)
    env._forward_env = [_TOOL_SECRET]
    env._env = {}
    monkeypatch.setenv(_TOOL_SECRET, "launch-secret")
    monkeypatch.setattr(docker_env, "_load_hermes_env_vars", lambda: {})
    home_token = set_hermes_home_override(tmp_path / "profiles" / "work")
    secret_token = secret_scope.set_secret_scope({_TOOL_SECRET: "work-secret"})
    try:
        args = env._build_init_env_args()
    finally:
        secret_scope.reset_secret_scope(secret_token)
        reset_hermes_home_override(home_token)

    assert f"{_TOOL_SECRET}=work-secret" in args
    assert f"{_TOOL_SECRET}=launch-secret" not in args


def test_execute_code_child_env_bridges_profile_home_secret_and_session(
    tmp_path, monkeypatch
):
    """The execute_code builder carries all three context-local values."""
    from tools.code_execution_tool import _build_child_process_env

    launch_home, profile_home, home_token, secret_token = _install_profile_scope(
        tmp_path, monkeypatch, {_TOOL_SECRET: "work-secret"}
    )
    session_context.set_session_vars(
        session_key="session-key",
        session_id="session-id",
        profile="work",
    )
    try:
        child_env = _build_child_process_env({
            "PATH": os.environ.get("PATH", ""),
            "HERMES_HOME": str(launch_home),
            _TOOL_SECRET: "launch-secret",
        })
    finally:
        secret_scope.reset_secret_scope(secret_token)
        reset_hermes_home_override(home_token)

    assert child_env["HERMES_HOME"] == str(profile_home)
    assert child_env[_TOOL_SECRET] == "work-secret"
    assert child_env["HERMES_SESSION_PROFILE"] == "work"
    assert child_env["HERMES_SESSION_ID"] == "session-id"


def test_execute_code_real_child_uses_active_profile_values(tmp_path, monkeypatch):
    """Exercise the actual local subprocess, not only its env builder."""
    from tools.code_execution_tool import (
        SANDBOX_ALLOWED_TOOLS,
        check_sandbox_requirements,
        execute_code,
    )

    if not check_sandbox_requirements():
        pytest.skip("execute_code sandbox is unavailable on this platform")

    _, profile_home, home_token, secret_token = _install_profile_scope(
        tmp_path, monkeypatch, {_TOOL_SECRET: "work-secret"}
    )
    session_context.set_session_vars(
        session_key="session-key",
        session_id="session-id",
        profile="work",
    )
    code = (
        "import json, os\n"
        "print(json.dumps({"
        "'home': os.getenv('HERMES_HOME'), "
        "'secret': os.getenv('PROFILE_TOOL_TOKEN'), "
        "'profile': os.getenv('HERMES_SESSION_PROFILE')}))\n"
    )
    try:
        with (
            patch("model_tools.handle_function_call", return_value="{}"),
            patch(
                "tools.code_execution_tool._load_config",
                return_value={"timeout": 10, "max_tool_calls": 50},
            ),
        ):
            raw = execute_code(
                code,
                task_id="profile-env-e2e",
                enabled_tools=list(SANDBOX_ALLOWED_TOOLS),
            )
    finally:
        secret_scope.reset_secret_scope(secret_token)
        reset_hermes_home_override(home_token)

    result = json.loads(raw)
    assert result["status"] == "success", result
    observed = json.loads(result["output"].strip())
    assert observed == {
        "home": str(profile_home),
        "secret": "work-secret",
        "profile": "work",
    }
