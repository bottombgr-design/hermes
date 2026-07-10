"""Layer 4: minimal Kanban review-required / approval / deployment-control integration.

The review decision and the manifest hash a reviewer actually looked at are
always re-derived live from the kanban task itself (its event log and body)
— never from a separately-stored ``approval.json`` field, which could be
stale or forged. Provenance is independently re-verified through a
:class:`~agent_factory.provenance.ProvenanceVerifier` every time. A release
only clears this gate when all three hold: a live "approve" decision, a
manifest hash that still matches what was reviewed (no approve-then-swap),
and trusted provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from hermes_cli import kanban_db as kb

from agent_factory.provenance import ProvenanceResult, ProvenanceVerifier
from agent_factory.state import LayerEvidence, Verdict

LAYER_NAME = "layer4_kanban"

_MANIFEST_HASH_BODY_PREFIX = "Release-Manifest-SHA256:"


def build_review_summary(*, what_changed: str, what_should_be_reviewed: str, recommended_decision: str) -> str:
    """Build a reason string satisfying kanban's review_required summary labels."""
    return (
        f"What changed: {what_changed} "
        f"What should be reviewed: {what_should_be_reviewed} "
        f"Recommended decision: {recommended_decision}"
    )


def _task_body(*, release_id: str, manifest_sha256: str) -> str:
    return f"Release-ID: {release_id}\n{_MANIFEST_HASH_BODY_PREFIX} {manifest_sha256}"


def create_review_task(
    conn,
    *,
    release_id: str,
    manifest_sha256: str,
    title: str,
    assignee: str,
    summary: str,
) -> str:
    """Create a kanban task bound to this release's manifest hash and put it in review."""
    task_id = kb.create_task(
        conn,
        title=title,
        body=_task_body(release_id=release_id, manifest_sha256=manifest_sha256),
        assignee=assignee,
        initial_status="running",
    )
    kb.block_task(conn, task_id, reason=summary, kind="review_required")
    return task_id


@dataclass(frozen=True)
class LiveReviewDecision:
    task_id: str
    decision: Optional[str]
    reviewer: Optional[str]
    decided_at: Optional[int]


def read_live_review_decision(conn, task_id: str) -> LiveReviewDecision:
    """Re-derive the decision from the task's live kanban event log."""
    decision: Optional[str] = None
    reviewer: Optional[str] = None
    decided_at: Optional[int] = None
    for event in kb.list_events(conn, task_id):
        if event.kind == "review_decision" and event.payload:
            decision = event.payload.get("decision")
            reviewer = event.payload.get("reviewer")
            decided_at = event.created_at
    return LiveReviewDecision(task_id=task_id, decision=decision, reviewer=reviewer, decided_at=decided_at)


def verify_hash_binding(conn, task_id: str, expected_manifest_sha256: str) -> bool:
    task = kb.get_task(conn, task_id)
    if task is None or not task.body:
        return False
    return f"{_MANIFEST_HASH_BODY_PREFIX} {expected_manifest_sha256}" in task.body


@dataclass(frozen=True)
class DeploymentGateResult:
    task_id: str
    decision: Optional[str]
    reviewer: Optional[str]
    decided_at: Optional[int]
    hash_bound: bool
    provenance: ProvenanceResult
    passes: bool
    detail: str


def evaluate_deployment_gate(
    conn,
    task_id: str,
    *,
    release_id: str,
    manifest_sha256: str,
    verifier: ProvenanceVerifier,
    claimed_identity: Optional[str] = None,
) -> DeploymentGateResult:
    live = read_live_review_decision(conn, task_id)
    hash_ok = verify_hash_binding(conn, task_id, manifest_sha256)
    provenance = verifier.verify(release_id=release_id, kanban_task_id=task_id, claimed_identity=claimed_identity)

    failures: list[str] = []
    if live.decision != "approve":
        failures.append(f"live kanban decision is {live.decision!r}, not 'approve'")
    if not hash_ok:
        failures.append("release manifest hash does not match the reviewed task's hash binding")
    if not provenance.trusted:
        failures.append(f"provenance not trusted: {provenance.reason}")

    return DeploymentGateResult(
        task_id=task_id,
        decision=live.decision,
        reviewer=live.reviewer,
        decided_at=live.decided_at,
        hash_bound=hash_ok,
        provenance=provenance,
        passes=not failures,
        detail="deployment gate passed" if not failures else "; ".join(failures),
    )


def run_layer4_kanban(
    conn,
    task_id: Optional[str],
    *,
    release_id: str,
    manifest_sha256: str,
    verifier: ProvenanceVerifier,
    claimed_identity: Optional[str] = None,
) -> LayerEvidence:
    if task_id is None:
        return LayerEvidence(
            layer=LAYER_NAME,
            verdict=Verdict.SKIPPED,
            checks=(),
            detail="no kanban review task associated with this release",
        )

    gate = evaluate_deployment_gate(
        conn, task_id, release_id=release_id, manifest_sha256=manifest_sha256,
        verifier=verifier, claimed_identity=claimed_identity,
    )
    checks = (
        {
            "name": "review_decision_is_approve",
            "verdict": Verdict.PASS.value if gate.decision == "approve" else Verdict.FAIL.value,
            "detail": f"live decision: {gate.decision!r}",
        },
        {
            "name": "manifest_hash_binding",
            "verdict": Verdict.PASS.value if gate.hash_bound else Verdict.FAIL.value,
            "detail": "release manifest matches reviewed hash" if gate.hash_bound
            else "release manifest hash mismatch or missing binding",
        },
        {
            "name": "provenance_trusted",
            "verdict": Verdict.PASS.value if gate.provenance.trusted else Verdict.FAIL.value,
            "detail": gate.provenance.reason,
        },
    )
    return LayerEvidence(
        layer=LAYER_NAME,
        verdict=Verdict.PASS if gate.passes else Verdict.FAIL,
        checks=checks,
        detail=gate.detail,
    )
