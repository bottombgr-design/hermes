"""Guarded deploy: exactly one NEW factory-managed TEST profile, fail-closed by default."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.profiles import get_profile_dir

from agent_factory.deploy import TEST_PROFILE_PREFIX, deploy_release
from agent_factory.provenance import DefaultFailClosedVerifier, ProvenanceResult
from agent_factory.schema import load_spec
from agent_factory.staging import stage_release
from agent_factory.state import LayerEvidence, Verdict, build_test_report
from agent_factory.tests_layer4 import build_review_summary, create_review_task


class _AlwaysTrustedVerifier:
    def verify(self, *, release_id, kanban_task_id, claimed_identity=None):
        return ProvenanceResult(trusted=True, reason="test-only stub", verified_identity="test-operator")


@pytest.fixture
def hermes_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


@pytest.fixture
def catalog(tmp_path):
    bundled = tmp_path / "bundled-skills"
    bundled.mkdir()
    (bundled / "alpha-skill").mkdir()
    (bundled / "alpha-skill" / "SKILL.md").write_text("# Alpha\n", encoding="utf-8")
    optional = tmp_path / "optional-skills"
    optional.mkdir()
    return bundled, optional


_SOURCE_TEXT = """\
apiVersion: agent-factory/v1
kind: AgentSpec
metadata:
  name: billing-helper
  version: 0.1.0
specification:
  role: Billing helper
  mission: Answer billing questions.
tools:
  allow: [todo]
skills:
  required: [alpha-skill]
"""


def _staged_release(tmp_path, catalog):
    import yaml

    bundled, optional = catalog
    spec = load_spec(yaml.safe_load(_SOURCE_TEXT))
    dest = tmp_path / "release"
    return stage_release(spec, dest, source_text=_SOURCE_TEXT, bundled_root=bundled, optional_root=optional)


def _passing_test_report(manifest_sha256):
    return build_test_report(
        "rel-1",
        [
            LayerEvidence(layer="layer1_static", verdict=Verdict.PASS, checks=(), detail=""),
            LayerEvidence(layer="layer2_config", verdict=Verdict.PASS, checks=(), detail=""),
            LayerEvidence(layer="layer3_prompt", verdict=Verdict.PASS, checks=(), detail=""),
            LayerEvidence(layer="layer4_kanban", verdict=Verdict.PASS, checks=(), detail=""),
        ],
        generated_at=1000,
        manifest_combined_sha256=manifest_sha256,
    )


def _approved_review_task(conn, manifest_sha256):
    summary = build_review_summary(what_changed="x", what_should_be_reviewed="y", recommended_decision="approve")
    task_id = create_review_task(
        conn, release_id="rel-1", manifest_sha256=manifest_sha256,
        title="Review", assignee="factory-bot", summary=summary,
    )
    assert kb.review_required_decision(conn, task_id, decision="approve", reviewer="reviewer-1", comment="lgtm")
    return task_id


def test_refuses_target_name_without_test_prefix(hermes_home, tmp_path, catalog):
    result = _staged_release(tmp_path, catalog)
    with kb.connect_closing() as conn:
        result_deploy = deploy_release(
            result.dest_dir, "billing-helper-prod", release_id="rel-1", manifest=result.manifest,
            kanban_conn=conn, kanban_task_id=None, verifier=DefaultFailClosedVerifier(),
            test_report=_passing_test_report(result.manifest["combined_sha256"]),
        )
    assert result_deploy.outcome == "refused"
    assert TEST_PROFILE_PREFIX in result_deploy.failure_reason
    assert not get_profile_dir("billing-helper-prod").exists()


def test_refuses_collision_with_existing_profile(hermes_home, tmp_path, catalog):
    target = f"{TEST_PROFILE_PREFIX}collide"
    existing = get_profile_dir(target)
    existing.mkdir(parents=True)
    (existing / "marker.txt").write_text("do not touch", encoding="utf-8")

    result = _staged_release(tmp_path, catalog)
    with kb.connect_closing() as conn:
        result_deploy = deploy_release(
            result.dest_dir, target, release_id="rel-1", manifest=result.manifest,
            kanban_conn=conn, kanban_task_id=None, verifier=DefaultFailClosedVerifier(),
            test_report=_passing_test_report(result.manifest["combined_sha256"]),
        )
    assert result_deploy.outcome == "refused"
    assert "already exists" in result_deploy.failure_reason
    assert (existing / "marker.txt").read_text(encoding="utf-8") == "do not touch"


def test_refuses_when_test_report_does_not_allow_deploy(hermes_home, tmp_path, catalog):
    result = _staged_release(tmp_path, catalog)
    failing_report = build_test_report(
        "rel-1", [LayerEvidence(layer="layer1_static", verdict=Verdict.FAIL, checks=(), detail="")],
        generated_at=1000,
    )
    with kb.connect_closing() as conn:
        result_deploy = deploy_release(
            result.dest_dir, f"{TEST_PROFILE_PREFIX}x", release_id="rel-1", manifest=result.manifest,
            kanban_conn=conn, kanban_task_id=None, verifier=DefaultFailClosedVerifier(),
            test_report=failing_report,
        )
    assert result_deploy.outcome == "refused"
    assert "test-report" in result_deploy.failure_reason


def test_refuses_when_no_kanban_review_task(hermes_home, tmp_path, catalog):
    result = _staged_release(tmp_path, catalog)
    with kb.connect_closing() as conn:
        result_deploy = deploy_release(
            result.dest_dir, f"{TEST_PROFILE_PREFIX}x", release_id="rel-1", manifest=result.manifest,
            kanban_conn=conn, kanban_task_id=None, verifier=_AlwaysTrustedVerifier(),
            test_report=_passing_test_report(result.manifest["combined_sha256"]),
        )
    assert result_deploy.outcome == "refused"
    assert "kanban" in result_deploy.failure_reason.lower()


def test_refuses_with_default_fail_closed_verifier_even_when_approved(hermes_home, tmp_path, catalog):
    result = _staged_release(tmp_path, catalog)
    target = f"{TEST_PROFILE_PREFIX}x"
    with kb.connect_closing() as conn:
        task_id = _approved_review_task(conn, result.manifest["combined_sha256"])
        result_deploy = deploy_release(
            result.dest_dir, target, release_id="rel-1", manifest=result.manifest,
            kanban_conn=conn, kanban_task_id=task_id, verifier=DefaultFailClosedVerifier(),
            test_report=_passing_test_report(result.manifest["combined_sha256"]),
        )
    assert result_deploy.outcome == "refused"
    assert "provenance" in result_deploy.failure_reason.lower() or "gate" in result_deploy.failure_reason.lower()
    assert not get_profile_dir(target).exists()


def test_successful_deploy_creates_new_test_profile_with_rendered_content(hermes_home, tmp_path, catalog):
    result = _staged_release(tmp_path, catalog)
    target = f"{TEST_PROFILE_PREFIX}billing-helper"
    with kb.connect_closing() as conn:
        task_id = _approved_review_task(conn, result.manifest["combined_sha256"])
        result_deploy = deploy_release(
            result.dest_dir, target, release_id="rel-1", manifest=result.manifest,
            kanban_conn=conn, kanban_task_id=task_id, verifier=_AlwaysTrustedVerifier(),
            test_report=_passing_test_report(result.manifest["combined_sha256"]),
        )
    assert result_deploy.outcome == "deployed"
    assert result_deploy.verified_identity == "test-operator"
    profile_dir = result_deploy.profile_dir
    assert profile_dir is not None
    assert profile_dir == get_profile_dir(target)
    assert (profile_dir / "agent.yaml").read_text(encoding="utf-8") == _SOURCE_TEXT
    assert (profile_dir / "config.yaml").read_bytes() == (result.dest_dir / "config.yaml").read_bytes()
    assert (profile_dir / "SOUL.md").read_bytes() == (result.dest_dir / "SOUL.md").read_bytes()
    assert (profile_dir / "rendered-config.json").exists()
    assert (profile_dir / "manifest.json").exists()
    assert (profile_dir / "skills" / "alpha-skill" / "SKILL.md").exists()
    assert (profile_dir / ".env").exists()
    assert (profile_dir / ".no-bundled-skills").exists()

    manifest = json.loads((profile_dir / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        deployed = profile_dir / entry["path"]
        assert deployed.is_file(), entry["path"]
        assert hashlib.sha256(deployed.read_bytes()).hexdigest() == entry["sha256"]


def test_successful_deploy_never_touches_other_profiles(hermes_home, tmp_path, catalog):
    sibling = get_profile_dir("some-other-profile")
    sibling.mkdir(parents=True)
    (sibling / "marker.txt").write_text("untouched", encoding="utf-8")

    result = _staged_release(tmp_path, catalog)
    target = f"{TEST_PROFILE_PREFIX}billing-helper"
    with kb.connect_closing() as conn:
        task_id = _approved_review_task(conn, result.manifest["combined_sha256"])
        deploy_release(
            result.dest_dir, target, release_id="rel-1", manifest=result.manifest,
            kanban_conn=conn, kanban_task_id=task_id, verifier=_AlwaysTrustedVerifier(),
            test_report=_passing_test_report(result.manifest["combined_sha256"]),
        )
    assert (sibling / "marker.txt").read_text(encoding="utf-8") == "untouched"


def test_build_failure_cleans_up_temp_destination_and_leaves_no_profile(hermes_home, tmp_path, catalog, monkeypatch):
    result = _staged_release(tmp_path, catalog)
    target = f"{TEST_PROFILE_PREFIX}broken"

    # Corrupt the staged release after gate-check preconditions would pass,
    # forcing the build phase itself to fail.
    (result.dest_dir / "manifest.json").unlink()

    import agent_factory.deploy as deploy_mod

    seen_temp_dirs = []
    original_mkdtemp = deploy_mod.tempfile.mkdtemp

    def _tracking_mkdtemp(*args, **kwargs):
        path = original_mkdtemp(*args, **kwargs)
        seen_temp_dirs.append(Path(path))
        return path

    monkeypatch.setattr(deploy_mod.tempfile, "mkdtemp", _tracking_mkdtemp)

    with kb.connect_closing() as conn:
        task_id = _approved_review_task(conn, result.manifest["combined_sha256"])
        result_deploy = deploy_release(
            result.dest_dir, target, release_id="rel-1", manifest=result.manifest,
            kanban_conn=conn, kanban_task_id=task_id, verifier=_AlwaysTrustedVerifier(),
            test_report=_passing_test_report(result.manifest["combined_sha256"]),
        )

    assert result_deploy.outcome == "refused"
    assert not get_profile_dir(target).exists()
    assert seen_temp_dirs, "expected the build phase to have started (temp dir created)"
    for temp_dir in seen_temp_dirs:
        assert not temp_dir.exists(), f"temp destination {temp_dir} was not cleaned up on failure"


def test_refuses_when_spec_disables_deployment(hermes_home, tmp_path, catalog):
    import yaml

    bundled, optional = catalog
    source_text = _SOURCE_TEXT + "deployment_policy:\n  allow_deploy: false\n"
    spec = load_spec(yaml.safe_load(source_text))
    result = stage_release(
        spec,
        tmp_path / "release",
        source_text=source_text,
        bundled_root=bundled,
        optional_root=optional,
    )
    target = f"{TEST_PROFILE_PREFIX}disabled"
    with kb.connect_closing() as conn:
        task_id = _approved_review_task(conn, result.manifest["combined_sha256"])
        result_deploy = deploy_release(
            result.dest_dir,
            target,
            release_id="rel-1",
            manifest=result.manifest,
            kanban_conn=conn,
            kanban_task_id=task_id,
            verifier=_AlwaysTrustedVerifier(),
            test_report=_passing_test_report(result.manifest["combined_sha256"]),
        )

    assert result_deploy.outcome == "refused"
    assert result_deploy.failure_reason is not None
    assert "allow_deploy" in result_deploy.failure_reason
    assert not get_profile_dir(target).exists()

def test_refuses_release_content_changed_after_approval(hermes_home, tmp_path, catalog):
    result = _staged_release(tmp_path, catalog)
    target = f"{TEST_PROFILE_PREFIX}tampered"
    with kb.connect_closing() as conn:
        task_id = _approved_review_task(conn, result.manifest["combined_sha256"])
        (result.dest_dir / "SOUL.md").write_text(
            "tampered after approval\n", encoding="utf-8"
        )
        result_deploy = deploy_release(
            result.dest_dir,
            target,
            release_id="rel-1",
            manifest=result.manifest,
            kanban_conn=conn,
            kanban_task_id=task_id,
            verifier=_AlwaysTrustedVerifier(),
            test_report=_passing_test_report(result.manifest["combined_sha256"]),
        )

    assert result_deploy.outcome == "refused"
    assert result_deploy.failure_reason is not None
    assert "manifest" in result_deploy.failure_reason.lower()
    assert not get_profile_dir(target).exists()

def test_refuses_test_report_bound_to_other_manifest(hermes_home, tmp_path, catalog):
    result = _staged_release(tmp_path, catalog)
    target = f"{TEST_PROFILE_PREFIX}wrong-report"
    with kb.connect_closing() as conn:
        task_id = _approved_review_task(conn, result.manifest["combined_sha256"])
        result_deploy = deploy_release(
            result.dest_dir,
            target,
            release_id="rel-1",
            manifest=result.manifest,
            kanban_conn=conn,
            kanban_task_id=task_id,
            verifier=_AlwaysTrustedVerifier(),
            test_report=_passing_test_report("0" * 64),
        )

    assert result_deploy.outcome == "refused"
    assert result_deploy.failure_reason is not None
    assert "test-report" in result_deploy.failure_reason.lower()
    assert "manifest" in result_deploy.failure_reason.lower()
    assert not get_profile_dir(target).exists()
