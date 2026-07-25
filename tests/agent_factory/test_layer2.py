"""Layer 2: rendered config + actual isolated tool-schema exposure.

CRITICAL 1 / MEDIUM 6: Layer 2 must not just re-check our own earlier
computation against the live registry — it must (a) parse the canonical
agent.yaml itself and independently recompute the expected effective
tools, (b) compare that recomputation exactly against rendered-config.json
and config.yaml (tamper detection), and (c) actually drive the *real*
runtime pipeline (hermes_cli.tools_config._get_platform_tools +
model_tools.get_tool_definitions) that a live agent session uses, and
compare the resulting live-exposed schema names against the canonical
effective allowlist. A tool that fails to appear because of a real
environment dependency (missing API key, etc.) is UNKNOWN, never a silent
PASS and never conflated with a structural break.
"""

from __future__ import annotations

import json
import os

import pytest
import yaml

from agent_factory.schema import load_spec
from agent_factory.staging import stage_release
from agent_factory.state import LayerEvidence, Verdict
from agent_factory.tests_layer2 import _staged_profile_context, run_layer2_config


@pytest.fixture
def catalog(tmp_path):
    bundled = tmp_path / "bundled-skills"
    bundled.mkdir()
    optional = tmp_path / "optional-skills"
    optional.mkdir()
    return bundled, optional


def _source_text(*, tools_allow: str, name: str = "demo-agent") -> str:
    return f"""\
apiVersion: agent-factory/v1
kind: AgentSpec
metadata:
  name: {name}
  version: 0.1.0
specification:
  role: Demo role
  mission: Demo mission.
tools:
  allow: [{tools_allow}]
skills:
  required: []
"""


def _stage(tmp_path, catalog, *, tools_allow: str, name: str = "demo-agent"):
    bundled, optional = catalog
    text = _source_text(tools_allow=tools_allow, name=name)
    spec = load_spec(yaml.safe_load(text))
    dest = tmp_path / "release"
    stage_release(spec, dest, source_text=text, bundled_root=bundled, optional_root=optional)
    return dest


def test_deterministic_toolset_passes_end_to_end(tmp_path, catalog):
    release_dir = _stage(tmp_path, catalog, tools_allow="todo")
    evidence = run_layer2_config(release_dir)
    assert isinstance(evidence, LayerEvidence)
    assert evidence.layer == "layer2_config"
    assert evidence.verdict == Verdict.PASS
    assert all(check["verdict"] == "PASS" for check in evidence.checks)


def test_multiple_deterministic_toolsets_pass(tmp_path, catalog):
    release_dir = _stage(tmp_path, catalog, tools_allow="todo, clarify, session_search")
    evidence = run_layer2_config(release_dir)
    assert evidence.verdict == Verdict.PASS


def test_environment_dependent_toolset_yields_unknown_not_silent_pass(tmp_path, catalog):
    """'web' needs a live API key Layer 2 cannot verify in this sandboxed
    test environment — it must be reported UNKNOWN (which blocks
    deployment), not silently treated as PASS or as a structural FAIL."""
    release_dir = _stage(tmp_path, catalog, tools_allow="web")
    evidence = run_layer2_config(release_dir)
    assert evidence.verdict == Verdict.UNKNOWN
    assert any(check["verdict"] == "UNKNOWN" for check in evidence.checks)
    assert not any(check["verdict"] == "FAIL" for check in evidence.checks)


def test_live_pipeline_uses_staged_profile_context_not_invoker(
    tmp_path, catalog, monkeypatch
):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from model_tools import _clear_tool_defs_cache
    from tools.registry import invalidate_check_fn_cache

    release_dir = _stage(tmp_path, catalog, tools_allow="todo")
    invoking_profile = tmp_path / "invoking-profile"
    invoking_profile.mkdir()
    invoking_profile.joinpath("config.yaml").write_text(
        "toolsets:\n  - kanban\n", encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_invoker")

    token = set_hermes_home_override(str(invoking_profile))
    try:
        invalidate_check_fn_cache()
        _clear_tool_defs_cache()
        evidence = run_layer2_config(release_dir)
    finally:
        reset_hermes_home_override(token)
        invalidate_check_fn_cache()
        _clear_tool_defs_cache()

    assert evidence.verdict == Verdict.PASS
    leak_check = next(c for c in evidence.checks if c["name"] == "no_schema_leak")
    assert leak_check["verdict"] == "PASS"


def test_staged_profile_context_restores_invoker_after_error(tmp_path, monkeypatch):
    from hermes_constants import (
        get_hermes_home,
        reset_hermes_home_override,
        set_hermes_home_override,
    )

    release_dir = tmp_path / "release"
    release_dir.mkdir()
    invoking_profile = tmp_path / "invoking-profile"
    invoking_profile.mkdir()
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_invoker")

    token = set_hermes_home_override(str(invoking_profile))
    try:
        with pytest.raises(RuntimeError, match="schema probe failed"):
            with _staged_profile_context(release_dir):
                assert get_hermes_home() == release_dir
                assert "HERMES_KANBAN_TASK" not in os.environ
                raise RuntimeError("schema probe failed")

        assert get_hermes_home() == invoking_profile
        assert os.environ["HERMES_KANBAN_TASK"] == "t_invoker"
    finally:
        reset_hermes_home_override(token)


def test_canonical_agent_yaml_is_source_of_truth_for_recomputation(tmp_path, catalog):
    """If the canonical agent.yaml itself is edited after staging (e.g. to
    grant a different toolset) while the derived artifacts still reflect
    the old policy, Layer 2 must recompute from the (now current) canonical
    file and flag the resulting mismatch — proving it never just trusts
    rendered-config.json."""
    release_dir = _stage(tmp_path, catalog, tools_allow="todo")
    tampered_text = _source_text(tools_allow="clarify")
    (release_dir / "agent.yaml").write_text(tampered_text, encoding="utf-8")

    evidence = run_layer2_config(release_dir)
    assert evidence.verdict == Verdict.FAIL
    assert any("rendered-config" in check["detail"].lower() or "mismatch" in check["detail"].lower() for check in evidence.checks)


def test_tampered_rendered_config_claiming_extra_tool_fails(tmp_path, catalog):
    release_dir = _stage(tmp_path, catalog, tools_allow="todo")
    rendered_path = release_dir / "rendered-config.json"
    rendered = json.loads(rendered_path.read_text(encoding="utf-8"))
    rendered["tools"]["allowed"] = sorted(set(rendered["tools"]["allowed"]) | {"kanban_create"})
    rendered_path.write_text(json.dumps(rendered), encoding="utf-8")

    evidence = run_layer2_config(release_dir)
    assert evidence.verdict == Verdict.FAIL


def test_tampered_config_yaml_platform_toolsets_fails(tmp_path, catalog):
    release_dir = _stage(tmp_path, catalog, tools_allow="todo")
    config_path = release_dir / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["platform_toolsets"]["cli"].append("kanban")
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    evidence = run_layer2_config(release_dir)
    assert evidence.verdict == Verdict.FAIL


def test_config_yaml_missing_no_mcp_sentinel_fails(tmp_path, catalog):
    release_dir = _stage(tmp_path, catalog, tools_allow="todo")
    config_path = release_dir / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["platform_toolsets"]["cli"] = [t for t in config["platform_toolsets"]["cli"] if t != "no_mcp"]
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    evidence = run_layer2_config(release_dir)
    assert evidence.verdict == Verdict.FAIL
    assert any("mcp" in check["detail"].lower() for check in evidence.checks)


def test_real_pipeline_never_exposes_more_than_recomputed_allowed(tmp_path, catalog):
    """Drives the actual hermes_cli.tools_config + model_tools pipeline —
    not just tools/registry.py directly — and proves it never exposes a
    tool outside the canonical effective allowlist."""
    release_dir = _stage(tmp_path, catalog, tools_allow="todo")
    evidence = run_layer2_config(release_dir)
    leak_check = next(c for c in evidence.checks if c["name"] == "no_schema_leak")
    assert leak_check["verdict"] == "PASS"
