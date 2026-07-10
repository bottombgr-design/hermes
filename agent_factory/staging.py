"""Deterministic staging: copy the canonical agent.yaml verbatim, attach
skills, write a derived rendered-config.json, and hash everything into a
SHA256 manifest.

The canonical ``agent.yaml`` written into a release is a byte-identical
copy of the reviewed source text — it never gains derived or operational
facts (effective tools, resolved skill paths, timestamps). Those live in
``rendered-config.json``, a clearly separate, clearly derived artifact.

Staging never writes anything outside the caller-supplied ``dest_dir`` and
never partially commits — on any failure (e.g. a required skill can't be
resolved) nothing is left on disk.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from agent_factory.effective_tools import EffectiveToolsReport, compute_effective_tools
from agent_factory.schema import AgentSpec, load_spec_from_yaml
from agent_factory.skills_resolve import SkillAttachmentReport, attach_required_skills
from agent_factory.state import OPERATIONAL_FILENAMES

MANIFEST_VERSION = 1
MANIFEST_FILENAME = "manifest.json"
CANONICAL_SPEC_FILENAME = "agent.yaml"
RENDERED_CONFIG_FILENAME = "rendered-config.json"
CONFIG_YAML_FILENAME = "config.yaml"
SOUL_FILENAME = "SOUL.md"

# The single platform a factory-deployed test profile is rendered for.
# See docs/design/agent-factory.md — config.yaml can only restrict tool
# exposure at toolset granularity, and only per-platform, so the render
# step commits to exactly one platform rather than leaving other
# platforms (discord, telegram, ...) with the default, unrestricted set.
DEPLOYED_PLATFORM = "cli"


@dataclass(frozen=True)
class StagingResult:
    dest_dir: Path
    effective_tools: EffectiveToolsReport
    skills: SkillAttachmentReport
    manifest: dict


def _rendered_config_json(spec: AgentSpec, effective_tools: EffectiveToolsReport) -> str:
    rendered = {
        "apiVersion": "agent-factory/v1",
        "kind": "RenderedAgentConfig",
        "metadata": {
            "name": spec.name,
            "version": spec.version,
            "description": spec.description,
        },
        "tools": {
            "default_deny": True,
            "allowed": list(effective_tools.allowed),
        },
        "skills": {
            "required": list(spec.skills_required),
        },
    }
    return json.dumps(rendered, indent=2, sort_keys=True) + "\n"


def _soul_markdown(spec: AgentSpec) -> str:
    """Render deterministic runtime identity from canonical identity fields.

    Department membership is deliberately absent: it is inert catalog
    metadata and must not affect runtime identity or permissions.
    """
    sections = [
        f"# {spec.role}",
        "",
        "Generated from canonical agent.yaml. Do not edit this rendered file directly.",
        "",
        "## Mission",
        "",
        spec.mission,
    ]
    for heading, values in (
        ("Responsibilities", spec.responsibilities),
        ("Non-responsibilities", spec.non_responsibilities),
        ("Inputs", spec.inputs),
        ("Outputs", spec.outputs),
        ("Handoff contracts", spec.kanban_handoff_contracts),
        ("Escalation rules", spec.kanban_escalation_rules),
    ):
        if values:
            sections.extend(("", f"## {heading}", ""))
            sections.extend(f"- {value}" for value in values)
    return "\n".join(sections) + "\n"


def _config_yaml_text(effective_tools: EffectiveToolsReport) -> str:
    """Render the actual profile config.yaml that restricts tool exposure.

    ``platform_toolsets.<platform>`` is the one config.yaml mechanism that
    genuinely restricts which tools ``model_tools.get_tool_definitions()``
    exposes at runtime (see docs/design/agent-factory.md for the empirical
    trace). The ``"no_mcp"`` sentinel is always included so no MCP server
    — declared nowhere in agent-factory's schema — can add tools.
    """
    config = {
        "platform_toolsets": {
            DEPLOYED_PLATFORM: sorted(effective_tools.allowed_toolsets) + ["no_mcp"],
        },
    }
    return yaml.safe_dump(config, sort_keys=True, default_flow_style=False)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recompute_manifest(dest_dir: Path) -> dict:
    """Hash every release-content file under *dest_dir*.

    Excludes the manifest itself and every operational filename
    (``agent_factory.state.OPERATIONAL_FILENAMES`` — release-state,
    test-report, review-packet, approval, deployment-record). Those are
    written into the same directory *after* staging; if they weren't
    excluded, writing a test report would itself change "has this release
    been edited" — defeating the whole point of the check (CRITICAL 2).
    """
    excluded = OPERATIONAL_FILENAMES | {MANIFEST_FILENAME}
    entries = []
    for path in sorted(dest_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(dest_dir).as_posix()
        if rel in excluded:
            continue
        entries.append({"path": rel, "sha256": _hash_file(path)})
    combined = hashlib.sha256(
        "\n".join(f"{e['path']}:{e['sha256']}" for e in entries).encode("utf-8")
    ).hexdigest()
    return {
        "version": MANIFEST_VERSION,
        "files": entries,
        "combined_sha256": combined,
    }


def stage_release(
    spec: AgentSpec,
    dest_dir: Path,
    *,
    source_text: str,
    bundled_root: Optional[Path] = None,
    optional_root: Optional[Path] = None,
) -> StagingResult:
    """Stage *spec* into *dest_dir*. Raises ``ValueError`` and leaves no trace on failure.

    ``source_text`` must be the exact ``agent.yaml`` text *spec* was parsed
    from — it is copied byte-for-byte into the release's canonical
    ``agent.yaml`` and cross-checked against *spec* so a caller can never
    accidentally stage a canonical file that doesn't match the policy that
    was actually validated and reviewed.
    """
    dest_dir = Path(dest_dir)
    if dest_dir.exists():
        raise ValueError(f"staging destination already exists: {dest_dir}")

    source_spec = load_spec_from_yaml(source_text)
    if source_spec != spec:
        raise ValueError(
            "source_text does not match spec — refusing to stage a canonical agent.yaml "
            "that doesn't match the policy that was actually validated"
        )

    effective_tools = compute_effective_tools(spec.tools_allow)

    try:
        dest_dir.mkdir(parents=True)
        (dest_dir / CANONICAL_SPEC_FILENAME).write_text(source_text, encoding="utf-8")
        (dest_dir / RENDERED_CONFIG_FILENAME).write_text(
            _rendered_config_json(spec, effective_tools), encoding="utf-8"
        )
        (dest_dir / CONFIG_YAML_FILENAME).write_text(
            _config_yaml_text(effective_tools), encoding="utf-8"
        )
        (dest_dir / SOUL_FILENAME).write_text(_soul_markdown(spec), encoding="utf-8")
        skills_report = attach_required_skills(
            spec.skills_required,
            dest_root=dest_dir / "skills",
            bundled_root=bundled_root,
            optional_root=optional_root,
        )
        manifest = recompute_manifest(dest_dir)
        (dest_dir / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except Exception:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise

    return StagingResult(
        dest_dir=dest_dir,
        effective_tools=effective_tools,
        skills=skills_report,
        manifest=manifest,
    )
