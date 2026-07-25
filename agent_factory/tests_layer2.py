"""Layer 2: rendered config + actual, isolated tool-schema exposure.

Never trusts a staged release's own derived artifacts. This module:

1. Parses the canonical ``agent.yaml`` itself and independently
   recomputes the expected effective tools (``effective_tools.py`` — the
   same computation used at render time, run again here).
2. Compares that recomputation *exactly* against ``rendered-config.json``
   and ``config.yaml`` — either one drifting from the canonical file
   (tampering, or a stale re-stage) fails this layer.
3. Drives the *real* runtime pipeline — the same
   ``hermes_cli.tools_config._get_platform_tools`` +
   ``model_tools.get_tool_definitions`` a live agent session uses — against
   the release's own ``config.yaml``, and compares the resulting
   live-exposed schema names against the canonical effective allowlist.

A tool that fails to show up in the live pipeline because of a real
environment dependency (missing API key, unavailable vision model, ...) is
reported UNKNOWN — not a silent PASS (we have not verified it), and not a
FAIL (nothing is structurally broken). A mandatory layer at UNKNOWN blocks
deployment exactly like FAIL (see ``agent_factory.state``).
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path

import yaml

from hermes_cli.tools_config import _get_platform_tools
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from model_tools import _clear_tool_defs_cache, get_tool_definitions
from tools.registry import invalidate_check_fn_cache
from toolsets import resolve_toolset

from agent_factory.effective_tools import compute_effective_tools
from agent_factory.policy import ENVIRONMENT_DEPENDENT_TOOLSETS
from agent_factory.schema import load_spec_from_yaml
from agent_factory.staging import (
    CANONICAL_SPEC_FILENAME,
    CONFIG_YAML_FILENAME,
    DEPLOYED_PLATFORM,
    RENDERED_CONFIG_FILENAME,
)
from agent_factory.state import LayerEvidence, Verdict

LAYER_NAME = "layer2_config"


@contextmanager
def _staged_profile_context(release_dir: Path):
    """Evaluate runtime schema gates as the staged profile, not the invoker."""
    prior_task = os.environ.pop("HERMES_KANBAN_TASK", None)
    token = None
    try:
        token = set_hermes_home_override(str(release_dir))
        invalidate_check_fn_cache()
        _clear_tool_defs_cache()
        yield
    finally:
        if token is not None:
            reset_hermes_home_override(token)
        if prior_task is not None:
            os.environ["HERMES_KANBAN_TASK"] = prior_task
        invalidate_check_fn_cache()
        _clear_tool_defs_cache()


def _tool_to_toolset_map(allowed_toolsets: tuple[str, ...]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for toolset in allowed_toolsets:
        for name in resolve_toolset(toolset):
            mapping[name] = toolset
    return mapping


def run_layer2_config(release_dir: Path) -> LayerEvidence:
    release_dir = Path(release_dir)
    checks: list[dict] = []
    verdict = Verdict.PASS

    def _fail(name: str, detail: str) -> None:
        nonlocal verdict
        verdict = Verdict.FAIL
        checks.append({"name": name, "verdict": Verdict.FAIL.value, "detail": detail})

    def _pass(name: str, detail: str) -> None:
        checks.append({"name": name, "verdict": Verdict.PASS.value, "detail": detail})

    def _unknown(name: str, detail: str) -> None:
        nonlocal verdict
        if verdict == Verdict.PASS:
            verdict = Verdict.UNKNOWN
        checks.append({"name": name, "verdict": Verdict.UNKNOWN.value, "detail": detail})

    # 1. Recompute independently from the canonical agent.yaml.
    spec = load_spec_from_yaml((release_dir / CANONICAL_SPEC_FILENAME).read_text(encoding="utf-8"))
    recomputed = compute_effective_tools(spec.tools_allow)

    # 2a. Cross-check rendered-config.json against the recomputation.
    rendered = json.loads((release_dir / RENDERED_CONFIG_FILENAME).read_text(encoding="utf-8"))
    rendered_allowed = set(rendered.get("tools", {}).get("allowed", []))
    if rendered_allowed != set(recomputed.allowed):
        _fail(
            "rendered_config_matches_canonical",
            f"rendered-config.json tools.allowed {sorted(rendered_allowed)} does not match the "
            f"canonical agent.yaml's recomputed effective tools {sorted(recomputed.allowed)}",
        )
    else:
        _pass("rendered_config_matches_canonical", "rendered-config.json matches the canonical recomputation")

    # 2b. Cross-check config.yaml against the recomputation.
    config_yaml = yaml.safe_load((release_dir / CONFIG_YAML_FILENAME).read_text(encoding="utf-8"))
    cli_toolsets = list(config_yaml.get("platform_toolsets", {}).get(DEPLOYED_PLATFORM, []))
    config_toolsets = set(cli_toolsets) - {"no_mcp"}
    if config_toolsets != set(recomputed.allowed_toolsets):
        _fail(
            "config_yaml_matches_canonical",
            f"config.yaml platform_toolsets.{DEPLOYED_PLATFORM} {sorted(config_toolsets)} does not match "
            f"the canonical agent.yaml's recomputed allowed toolsets {sorted(recomputed.allowed_toolsets)}",
        )
    else:
        _pass("config_yaml_matches_canonical", "config.yaml platform_toolsets matches the canonical recomputation")

    if "no_mcp" not in cli_toolsets:
        _fail(
            "no_undeclared_mcp",
            "config.yaml is missing the 'no_mcp' sentinel — MCP servers are not declared anywhere "
            "in agent-factory's schema and must never be included by default",
        )
    else:
        _pass("no_undeclared_mcp", "config.yaml explicitly excludes MCP servers")

    # 3. Drive the real runtime pipeline against the release's own config.yaml.
    with _staged_profile_context(release_dir):
        enabled_toolsets = _get_platform_tools(config_yaml, DEPLOYED_PLATFORM)
        schemas = get_tool_definitions(enabled_toolsets=sorted(enabled_toolsets), quiet_mode=True)
    exposed_names = {schema["function"]["name"] for schema in schemas}

    leaked = exposed_names - set(recomputed.allowed)
    if leaked:
        _fail("no_schema_leak", f"live pipeline exposed tools outside the canonical allowlist: {sorted(leaked)}")
    else:
        _pass("no_schema_leak", "live pipeline never exposes a tool outside the canonical effective allowlist")

    tool_to_toolset = _tool_to_toolset_map(recomputed.allowed_toolsets)
    missing_structural: list[str] = []
    missing_env_dependent: list[str] = []
    for name in recomputed.allowed:
        if name in exposed_names:
            continue
        owning_toolset = tool_to_toolset.get(name)
        if owning_toolset in ENVIRONMENT_DEPENDENT_TOOLSETS:
            missing_env_dependent.append(name)
        else:
            missing_structural.append(name)

    if missing_structural:
        _fail(
            "all_deterministic_tools_resolve",
            f"allowed tools with no external dependency failed to resolve in the live pipeline: {sorted(missing_structural)}",
        )
    elif not leaked:
        _pass("all_deterministic_tools_resolve", "every dependency-free allowed tool resolved live")

    if missing_env_dependent:
        _unknown(
            "environment_dependent_tools_unverified",
            "cannot verify these tools are actually exposed without live credentials/runtime "
            f"dependencies not available in this environment: {sorted(missing_env_dependent)}",
        )

    return LayerEvidence(
        layer=LAYER_NAME,
        verdict=verdict,
        checks=tuple(checks),
        detail=f"{len(exposed_names)} tool schema(s) exposed by the live pipeline",
    )
