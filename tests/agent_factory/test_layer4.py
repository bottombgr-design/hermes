"""Layer 4: minimal Kanban review-required / approval / deployment-control integration.

The live decision and hash binding are always re-derived from the kanban
task's own event log and body — never from a separately-stored, possibly
stale or forged record — and provenance is always independently
re-verified. No manual approval.json or plain identity string is ever
sufficient on its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb

from agent_factory.provenance import DefaultFailClosedVerifier, ProvenanceResult
from agent_factory.state import Verdict
from agent_factory.tests_layer4 import (
    build_review_summary,
    create_review_task,
    evaluate_deployment_gate,
    read_live_review_decision,
    run_layer4_kanban,
    verify_hash_binding,
)

MANIFEST_SHA = "a" * 64
OTHER_MANIFEST_SHA = "b" * 64


class _AlwaysTrustedVerifier:
    """Test-only stub — never shipped via the CLI."""

    def verify(self, *, release_id, kanban_task_id, claimed_identity=None):
        return ProvenanceResult(trusted=True, reason="test-only stub", verified_identity="test-operator")


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def test_build_review_summary_contains_required_labels():
    summary = build_review_summary(
        what_changed="new billing-helper agent", what_should_be_reviewed="tool policy",
        recommended_decision="approve",
    )
    lowered = summary.lower()
    assert "what changed" in lowered
    assert "what should be reviewed" in lowered
    assert "recommended decision" in lowered


def test_create_review_task_lands_in_review_required_lifecycle(kanban_home):
    with kb.connect_closing() as conn:
        summary = build_review_summary(what_changed="x", what_should_be_reviewed="y", recommended_decision="approve")
        task_id = create_review_task(
            conn, release_id="rel-1", manifest_sha256=MANIFEST_SHA,
            title="Review billing-helper 0.1.0", assignee="factory-bot", summary=summary,
        )
        task = kb.get_task(conn, task_id)
        assert task.status == "blocked"
        assert task.block_kind == "review_required"
        assert kb.effective_lifecycle_state(task) == "review_required"


def test_hash_binding_matches_reviewed_manifest(kanban_home):
    with kb.connect_closing() as conn:
        summary = build_review_summary(what_changed="x", what_should_be_reviewed="y", recommended_decision="approve")
        task_id = create_review_task(
            conn, release_id="rel-1", manifest_sha256=MANIFEST_SHA,
            title="Review", assignee="factory-bot", summary=summary,
        )
        assert verify_hash_binding(conn, task_id, MANIFEST_SHA) is True
        assert verify_hash_binding(conn, task_id, OTHER_MANIFEST_SHA) is False


def test_no_decision_yet_is_not_approve(kanban_home):
    with kb.connect_closing() as conn:
        summary = build_review_summary(what_changed="x", what_should_be_reviewed="y", recommended_decision="approve")
        task_id = create_review_task(
            conn, release_id="rel-1", manifest_sha256=MANIFEST_SHA,
            title="Review", assignee="factory-bot", summary=summary,
        )
        live = read_live_review_decision(conn, task_id)
        assert live.decision is None


def test_live_decision_reflects_kanban_event_log_not_a_stored_field(kanban_home):
    with kb.connect_closing() as conn:
        summary = build_review_summary(what_changed="x", what_should_be_reviewed="y", recommended_decision="approve")
        task_id = create_review_task(
            conn, release_id="rel-1", manifest_sha256=MANIFEST_SHA,
            title="Review", assignee="factory-bot", summary=summary,
        )
        assert kb.review_required_decision(conn, task_id, decision="approve", reviewer="reviewer-1", comment="lgtm")
        live = read_live_review_decision(conn, task_id)
        assert live.decision == "approve"
        assert live.reviewer == "reviewer-1"
        assert live.decided_at is not None


def test_deployment_gate_fails_closed_with_default_verifier_even_when_approved(kanban_home):
    """The kanban decision alone is never enough — the default provenance
    verifier is fail-closed, so the gate must still refuse."""
    with kb.connect_closing() as conn:
        summary = build_review_summary(what_changed="x", what_should_be_reviewed="y", recommended_decision="approve")
        task_id = create_review_task(
            conn, release_id="rel-1", manifest_sha256=MANIFEST_SHA,
            title="Review", assignee="factory-bot", summary=summary,
        )
        assert kb.review_required_decision(conn, task_id, decision="approve", reviewer="reviewer-1", comment="lgtm")

        gate = evaluate_deployment_gate(
            conn, task_id, release_id="rel-1", manifest_sha256=MANIFEST_SHA,
            verifier=DefaultFailClosedVerifier(),
        )
        assert gate.passes is False
        assert gate.decision == "approve"
        assert gate.provenance.trusted is False


def test_deployment_gate_passes_only_with_approval_matching_hash_and_trusted_provenance(kanban_home):
    with kb.connect_closing() as conn:
        summary = build_review_summary(what_changed="x", what_should_be_reviewed="y", recommended_decision="approve")
        task_id = create_review_task(
            conn, release_id="rel-1", manifest_sha256=MANIFEST_SHA,
            title="Review", assignee="factory-bot", summary=summary,
        )
        assert kb.review_required_decision(conn, task_id, decision="approve", reviewer="reviewer-1", comment="lgtm")

        gate = evaluate_deployment_gate(
            conn, task_id, release_id="rel-1", manifest_sha256=MANIFEST_SHA,
            verifier=_AlwaysTrustedVerifier(),
        )
        assert gate.passes is True


def test_deployment_gate_fails_when_release_manifest_changed_after_approval(kanban_home):
    """Guards against approve-then-swap: the reviewed hash must match the
    release actually being deployed."""
    with kb.connect_closing() as conn:
        summary = build_review_summary(what_changed="x", what_should_be_reviewed="y", recommended_decision="approve")
        task_id = create_review_task(
            conn, release_id="rel-1", manifest_sha256=MANIFEST_SHA,
            title="Review", assignee="factory-bot", summary=summary,
        )
        assert kb.review_required_decision(conn, task_id, decision="approve", reviewer="reviewer-1", comment="lgtm")

        gate = evaluate_deployment_gate(
            conn, task_id, release_id="rel-1", manifest_sha256=OTHER_MANIFEST_SHA,
            verifier=_AlwaysTrustedVerifier(),
        )
        assert gate.passes is False
        assert "hash" in gate.detail.lower()


def test_deployment_gate_fails_on_reject(kanban_home):
    with kb.connect_closing() as conn:
        summary = build_review_summary(what_changed="x", what_should_be_reviewed="y", recommended_decision="approve")
        task_id = create_review_task(
            conn, release_id="rel-1", manifest_sha256=MANIFEST_SHA,
            title="Review", assignee="factory-bot", summary=summary,
        )
        assert kb.review_required_decision(conn, task_id, decision="reject", reviewer="reviewer-1", comment="no")

        gate = evaluate_deployment_gate(
            conn, task_id, release_id="rel-1", manifest_sha256=MANIFEST_SHA,
            verifier=_AlwaysTrustedVerifier(),
        )
        assert gate.passes is False


def test_run_layer4_kanban_skipped_when_no_review_task_exists():
    evidence = run_layer4_kanban(
        None, None, release_id="rel-1", manifest_sha256=MANIFEST_SHA, verifier=DefaultFailClosedVerifier(),
    )
    assert evidence.layer == "layer4_kanban"
    assert evidence.verdict == Verdict.SKIPPED


def test_run_layer4_kanban_fail_with_default_verifier(kanban_home):
    with kb.connect_closing() as conn:
        summary = build_review_summary(what_changed="x", what_should_be_reviewed="y", recommended_decision="approve")
        task_id = create_review_task(
            conn, release_id="rel-1", manifest_sha256=MANIFEST_SHA,
            title="Review", assignee="factory-bot", summary=summary,
        )
        assert kb.review_required_decision(conn, task_id, decision="approve", reviewer="reviewer-1", comment="lgtm")

        evidence = run_layer4_kanban(
            conn, task_id, release_id="rel-1", manifest_sha256=MANIFEST_SHA, verifier=DefaultFailClosedVerifier(),
        )
        assert evidence.verdict == Verdict.FAIL
        assert any(c["name"] == "provenance_trusted" and c["verdict"] == "FAIL" for c in evidence.checks)


def test_run_layer4_kanban_pass_with_trusted_verifier(kanban_home):
    with kb.connect_closing() as conn:
        summary = build_review_summary(what_changed="x", what_should_be_reviewed="y", recommended_decision="approve")
        task_id = create_review_task(
            conn, release_id="rel-1", manifest_sha256=MANIFEST_SHA,
            title="Review", assignee="factory-bot", summary=summary,
        )
        assert kb.review_required_decision(conn, task_id, decision="approve", reviewer="reviewer-1", comment="lgtm")

        evidence = run_layer4_kanban(
            conn, task_id, release_id="rel-1", manifest_sha256=MANIFEST_SHA, verifier=_AlwaysTrustedVerifier(),
        )
        assert evidence.verdict == Verdict.PASS
        assert all(c["verdict"] == "PASS" for c in evidence.checks)
