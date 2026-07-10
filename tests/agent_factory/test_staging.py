"""Deterministic staging: copy the canonical agent.yaml verbatim, attach
skills, write a derived rendered-config.json, and hash everything into a
manifest.

The canonical ``agent.yaml`` written into a release must be byte-identical
to the reviewed source — no operational or derived facts (effective tools,
resolved skills) may leak into it. Those live in ``rendered-config.json``.
"""

from __future__ import annotations

import hashlib
import json

import pytest
import yaml

from agent_factory.schema import load_spec
from agent_factory.staging import StagingResult, stage_release


@pytest.fixture
def catalog(tmp_path):
    bundled = tmp_path / "bundled-skills"
    bundled.mkdir()
    (bundled / "alpha-skill").mkdir()
    (bundled / "alpha-skill" / "SKILL.md").write_text("# Alpha\n", encoding="utf-8")
    optional = tmp_path / "optional-skills"
    optional.mkdir()
    return bundled, optional


SOURCE_TEXT = """\
apiVersion: agent-factory/v1
kind: AgentSpec
metadata:
  name: billing-helper
  version: 0.1.0
  description: Billing Q&A
specification:
  role: Billing helper
  mission: Answer billing questions.
tools:
  allow: [todo]
skills:
  required: [alpha-skill]
"""


@pytest.fixture
def spec():
    return load_spec(yaml.safe_load(SOURCE_TEXT))


def test_stage_release_copies_canonical_agent_yaml_byte_identical(catalog, spec, tmp_path):
    bundled, optional = catalog
    dest = tmp_path / "release"
    result = stage_release(spec, dest, source_text=SOURCE_TEXT, bundled_root=bundled, optional_root=optional)
    assert isinstance(result, StagingResult)
    assert (dest / "agent.yaml").read_text(encoding="utf-8") == SOURCE_TEXT
    assert (dest / "skills" / "alpha-skill" / "SKILL.md").exists()


def test_stage_release_writes_derived_rendered_config_separately(catalog, spec, tmp_path):
    bundled, optional = catalog
    dest = tmp_path / "release"
    stage_release(spec, dest, source_text=SOURCE_TEXT, bundled_root=bundled, optional_root=optional)
    rendered = json.loads((dest / "rendered-config.json").read_text(encoding="utf-8"))
    assert rendered["tools"]["allowed"] == ["todo"]
    assert rendered["tools"]["default_deny"] is True
    assert rendered["skills"]["required"] == ["alpha-skill"]
    # The canonical agent.yaml itself carries none of these derived facts.
    agent_yaml_text = (dest / "agent.yaml").read_text(encoding="utf-8")
    assert "default_deny" not in agent_yaml_text
    assert "allowed" not in agent_yaml_text


def test_stage_release_writes_manifest_with_sha256(catalog, spec, tmp_path):
    bundled, optional = catalog
    dest = tmp_path / "release"
    stage_release(spec, dest, source_text=SOURCE_TEXT, bundled_root=bundled, optional_root=optional)
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in manifest["files"]}
    assert "agent.yaml" in paths
    assert "rendered-config.json" in paths
    assert "skills/alpha-skill/SKILL.md" in paths
    assert "manifest.json" not in paths

    expected_hash = hashlib.sha256((dest / "skills" / "alpha-skill" / "SKILL.md").read_bytes()).hexdigest()
    entry = next(e for e in manifest["files"] if e["path"] == "skills/alpha-skill/SKILL.md")
    assert entry["sha256"] == expected_hash
    assert "combined_sha256" in manifest


def test_stage_release_is_byte_deterministic(catalog, spec, tmp_path):
    bundled, optional = catalog
    dest_a = tmp_path / "release-a"
    dest_b = tmp_path / "release-b"
    stage_release(spec, dest_a, source_text=SOURCE_TEXT, bundled_root=bundled, optional_root=optional)
    stage_release(spec, dest_b, source_text=SOURCE_TEXT, bundled_root=bundled, optional_root=optional)
    manifest_a = (dest_a / "manifest.json").read_text(encoding="utf-8")
    manifest_b = (dest_b / "manifest.json").read_text(encoding="utf-8")
    assert manifest_a == manifest_b

    agent_yaml_a = (dest_a / "agent.yaml").read_text(encoding="utf-8")
    agent_yaml_b = (dest_b / "agent.yaml").read_text(encoding="utf-8")
    assert agent_yaml_a == agent_yaml_b


def test_stage_release_includes_effective_tools_report(catalog, spec, tmp_path):
    bundled, optional = catalog
    dest = tmp_path / "release"
    result = stage_release(spec, dest, source_text=SOURCE_TEXT, bundled_root=bundled, optional_root=optional)
    assert "todo" in result.effective_tools.allowed


def test_stage_release_fails_closed_on_missing_required_skill(catalog, tmp_path):
    bundled, optional = catalog
    dest = tmp_path / "release"
    bad_text = SOURCE_TEXT.replace("alpha-skill", "does-not-exist")
    bad_spec = load_spec(yaml.safe_load(bad_text))
    with pytest.raises(ValueError):
        stage_release(bad_spec, dest, source_text=bad_text, bundled_root=bundled, optional_root=optional)
    assert not dest.exists()


def test_stage_release_rejects_source_text_that_does_not_match_spec(catalog, spec, tmp_path):
    """Guards against a caller accidentally passing mismatched (spec, source_text) pairs."""
    bundled, optional = catalog
    dest = tmp_path / "release"
    mismatched_text = SOURCE_TEXT.replace("billing-helper", "totally-different-name")
    with pytest.raises(ValueError):
        stage_release(spec, dest, source_text=mismatched_text, bundled_root=bundled, optional_root=optional)
    assert not dest.exists()


def test_stage_release_writes_real_config_yaml_restricting_toolsets(catalog, spec, tmp_path):
    """CRITICAL 1: staging must render an actual profile config.yaml that
    restricts tool exposure — not just a JSON description of intent."""
    bundled, optional = catalog
    dest = tmp_path / "release"
    stage_release(spec, dest, source_text=SOURCE_TEXT, bundled_root=bundled, optional_root=optional)
    config = yaml.safe_load((dest / "config.yaml").read_text(encoding="utf-8"))
    cli_toolsets = config["platform_toolsets"]["cli"]
    assert set(cli_toolsets) == {"todo", "no_mcp"}


def test_config_yaml_is_included_in_the_manifest(catalog, spec, tmp_path):
    bundled, optional = catalog
    dest = tmp_path / "release"
    stage_release(spec, dest, source_text=SOURCE_TEXT, bundled_root=bundled, optional_root=optional)
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in manifest["files"]}
    assert "config.yaml" in paths


def test_recompute_manifest_excludes_operational_files(catalog, spec, tmp_path):
    """Operational files (test-report.json, approval.json, ...) get written
    into the same directory as the staged release after the fact — CRITICAL
    2 requires recomputing the manifest to detect real edits, so it must
    never itself be perturbed by writing evidence/approval/deployment
    records alongside the release."""
    from agent_factory.staging import recompute_manifest

    bundled, optional = catalog
    dest = tmp_path / "release"
    result = stage_release(spec, dest, source_text=SOURCE_TEXT, bundled_root=bundled, optional_root=optional)
    original_hash = result.manifest["combined_sha256"]

    for filename in (
        "release-state.json", "test-report.json", "review-packet.json",
        "approval.json", "deployment-record.json",
    ):
        (dest / filename).write_text('{"anything": true}\n', encoding="utf-8")

    recomputed = recompute_manifest(dest)
    assert recomputed["combined_sha256"] == original_hash
    paths = {entry["path"] for entry in recomputed["files"]}
    assert not (paths & {
        "release-state.json", "test-report.json", "review-packet.json",
        "approval.json", "deployment-record.json",
    })


def test_recompute_manifest_detects_real_content_edit(catalog, spec, tmp_path):
    from agent_factory.staging import recompute_manifest

    bundled, optional = catalog
    dest = tmp_path / "release"
    result = stage_release(spec, dest, source_text=SOURCE_TEXT, bundled_root=bundled, optional_root=optional)
    original_hash = result.manifest["combined_sha256"]

    (dest / "agent.yaml").write_text(SOURCE_TEXT + "\n# tampered\n", encoding="utf-8")

    recomputed = recompute_manifest(dest)
    assert recomputed["combined_sha256"] != original_hash


def test_stage_release_renders_deterministic_soul_from_canonical_identity(catalog, spec, tmp_path):
    bundled, optional = catalog
    dest = tmp_path / "release"
    stage_release(spec, dest, source_text=SOURCE_TEXT, bundled_root=bundled, optional_root=optional)

    soul = (dest / "SOUL.md").read_text(encoding="utf-8")
    assert "# Billing helper" in soul
    assert "Answer billing questions." in soul
    assert "Generated from canonical agent.yaml" in soul

    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert "SOUL.md" in {entry["path"] for entry in manifest["files"]}


def test_department_reference_is_not_rendered_into_runtime_configuration(catalog, tmp_path):
    bundled, optional = catalog
    source = SOURCE_TEXT + "department: finance\n"
    department_spec = load_spec(yaml.safe_load(source))
    dest = tmp_path / "release"
    stage_release(
        department_spec, dest, source_text=source,
        bundled_root=bundled, optional_root=optional,
    )

    assert "finance" not in (dest / "config.yaml").read_text(encoding="utf-8")
    assert "finance" not in (dest / "rendered-config.json").read_text(encoding="utf-8")
    assert "finance" not in (dest / "SOUL.md").read_text(encoding="utf-8")
