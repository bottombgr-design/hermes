"""High-level orchestration for the agent-factory CLI.

Wires schema validation, deterministic staging, the four Layer test
runners, review-packet assembly, and guarded deploy together. This is the
*only* place that writes the operational JSON files
(``release-state.json``, ``test-report.json``, ``review-packet.json``,
``approval.json``, ``deployment-record.json``) alongside a staged release —
the canonical ``agent.yaml`` copied into a release directory never gains
any of these facts; see ``agent_factory.staging``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

import yaml

from agent_factory.deploy import DeployResult, deploy_release
from agent_factory.effective_tools import EffectiveToolsReport
from agent_factory.provenance import ProvenanceVerifier
from agent_factory.review_packet import ReviewPacket, build_review_packet, review_packet_to_dict
from agent_factory.schema import load_spec_from_yaml
from agent_factory.skills_resolve import SkillAttachmentReport
from agent_factory.staging import (
    CANONICAL_SPEC_FILENAME,
    MANIFEST_FILENAME,
    RENDERED_CONFIG_FILENAME,
    recompute_manifest,
    stage_release,
)
from agent_factory.state import (
    REVIEW_PACKET_FILENAME,
    ApprovalRecord,
    LayerEvidence,
    ReleaseState,
    TestReport,
    Verdict,
    build_test_report,
    read_release_state,
    read_test_report,
    write_approval,
    write_deployment_record,
    write_release_state,
    write_test_report,
)
from agent_factory.tests_layer1 import run_layer1_static
from agent_factory.tests_layer2 import run_layer2_config
from agent_factory.tests_layer3 import PromptRunner, PromptScenario, run_layer3_prompt_tests
from agent_factory.tests_layer4 import run_layer4_kanban
from agent_factory.version import SPEC_FORMAT_VERSION

def validate(spec_text: str) -> LayerEvidence:
    """Layer 1: validate raw ``agent.yaml`` text, no filesystem/registry access."""
    data = yaml.safe_load(spec_text)
    return run_layer1_static(data)


def render(
    spec_path: Path,
    out_dir: Path,
    *,
    release_id: str,
    generated_at: int,
    bundled_root: Optional[Path] = None,
    optional_root: Optional[Path] = None,
):
    """Validate + stage *spec_path* into *out_dir*, and write release-state.json."""
    source_text = Path(spec_path).read_text(encoding="utf-8")
    spec = load_spec_from_yaml(source_text)
    result = stage_release(spec, out_dir, source_text=source_text, bundled_root=bundled_root, optional_root=optional_root)

    state = ReleaseState(
        release_id=release_id,
        spec_name=spec.name,
        spec_version=spec.version,
        spec_format_version=SPEC_FORMAT_VERSION,
        manifest_combined_sha256=result.manifest["combined_sha256"],
        created_at=generated_at,
        status="staged",
    )
    write_release_state(out_dir, state)
    return result


def _rendered_effective_tools(release_dir: Path) -> EffectiveToolsReport:
    """Re-derive the effective tools report from the staged artifact on disk.

    Reading it back from ``rendered-config.json`` (rather than trusting an
    in-memory object) is what makes Layer 2 a check of the actual staged
    artifact, not just of whatever the current process happens to remember.
    """
    rendered = json.loads((Path(release_dir) / RENDERED_CONFIG_FILENAME).read_text(encoding="utf-8"))
    allowed = tuple(rendered["tools"]["allowed"])
    return EffectiveToolsReport(requested=allowed, allowed=allowed, denied_forbidden=(), denied_unknown=())


def _verified_manifest(release_dir: Path, release_id: str) -> dict:
    release_dir = Path(release_dir)
    stored = json.loads((release_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    if recompute_manifest(release_dir) != stored:
        raise ValueError("release contents no longer match manifest.json")
    state = read_release_state(release_dir)
    if state.release_id != release_id:
        raise ValueError("release-state release_id does not match the requested release")
    if state.manifest_combined_sha256 != stored["combined_sha256"]:
        raise ValueError("release-state manifest hash does not match manifest.json")
    return stored


def _verified_test_report(release_dir: Path, release_id: str, manifest: dict) -> TestReport:
    try:
        report = read_test_report(release_dir)
    except (KeyError, OSError, ValueError) as exc:
        raise ValueError(f"test-report is invalid: {exc}") from exc
    if report.release_id != release_id:
        raise ValueError("test-report release_id does not match the requested release")
    if report.manifest_combined_sha256 != manifest["combined_sha256"]:
        raise ValueError("test-report manifest hash does not match manifest.json")
    return report


def run_tests(
    release_dir: Path,
    *,
    release_id: str,
    generated_at: int,
    prompt_scenarios: Iterable[PromptScenario] = (),
    prompt_runner: Optional[PromptRunner] = None,
    kanban_conn=None,
    kanban_task_id: Optional[str] = None,
    verifier: Optional[ProvenanceVerifier] = None,
) -> TestReport:
    release_dir = Path(release_dir)
    spec_data = yaml.safe_load((release_dir / CANONICAL_SPEC_FILENAME).read_text(encoding="utf-8"))
    manifest = _verified_manifest(release_dir, release_id)
    effective_tools = _rendered_effective_tools(release_dir)

    layer1 = run_layer1_static(spec_data)
    layer2 = run_layer2_config(release_dir)
    layer3 = run_layer3_prompt_tests(
        prompt_scenarios,
        effective_tools=effective_tools,
        runner=prompt_runner,
    )

    if kanban_conn is None or kanban_task_id is None or verifier is None:
        layer4 = LayerEvidence(
            layer="layer4_kanban", verdict=Verdict.SKIPPED, checks=(),
            detail="no kanban review task / provenance verifier supplied to this test run",
        )
    else:
        layer4 = run_layer4_kanban(
            kanban_conn, kanban_task_id, release_id=release_id,
            manifest_sha256=manifest["combined_sha256"], verifier=verifier,
        )
        # approval.json is an audit record only.  Deployment re-derives the
        # live Kanban decision, hash binding, and provenance independently;
        # writing this file never grants authority by itself.
        from agent_factory.tests_layer4 import read_live_review_decision

        live = read_live_review_decision(kanban_conn, kanban_task_id)
        provenance_check = next(
            (check for check in layer4.checks if check.get("name") == "provenance_trusted"),
            None,
        )
        provenance_trusted = bool(
            provenance_check and provenance_check.get("verdict") == Verdict.PASS.value
        )
        write_approval(
            release_dir,
            ApprovalRecord(
                release_id=release_id,
                kanban_task_id=kanban_task_id,
                decision=live.decision,
                reviewer=live.reviewer,
                decided_at=live.decided_at,
                provenance_trusted=provenance_trusted,
                provenance_reason=(
                    str(provenance_check.get("detail", ""))
                    if provenance_check
                    else "provenance check missing from Layer 4 evidence"
                ),
            ),
        )

    report = build_test_report(
        release_id,
        [layer1, layer2, layer3, layer4],
        generated_at=generated_at,
        manifest_combined_sha256=manifest["combined_sha256"],
    )
    write_test_report(release_dir, report)
    return report


def build_report(
    release_dir: Path,
    *,
    release_id: str,
    generated_at: int,
    previous_version: Optional[str] = None,
) -> ReviewPacket:
    release_dir = Path(release_dir)
    spec = load_spec_from_yaml((release_dir / CANONICAL_SPEC_FILENAME).read_text(encoding="utf-8"))
    manifest = _verified_manifest(release_dir, release_id)
    effective_tools = _rendered_effective_tools(release_dir)
    rendered = json.loads((release_dir / RENDERED_CONFIG_FILENAME).read_text(encoding="utf-8"))
    skills = SkillAttachmentReport(attached=tuple(rendered["skills"]["required"]), missing=())

    test_report_path = release_dir / "test-report.json"
    test_report = (
        _verified_test_report(release_dir, release_id, manifest)
        if test_report_path.exists()
        else None
    )

    packet = build_review_packet(
        release_id=release_id,
        spec=spec,
        manifest=manifest,
        effective_tools=effective_tools,
        skills=skills,
        test_report=test_report,
        previous_version=previous_version,
        generated_at=generated_at,
    )
    payload = review_packet_to_dict(packet)
    (release_dir / REVIEW_PACKET_FILENAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return packet


def deploy(
    release_dir: Path,
    target_profile_name: str,
    *,
    release_id: str,
    generated_at: int,
    kanban_conn,
    kanban_task_id: Optional[str],
    verifier: ProvenanceVerifier,
    claimed_identity: Optional[str] = None,
) -> DeployResult:
    release_dir = Path(release_dir)
    test_report_path = release_dir / "test-report.json"

    try:
        manifest = _verified_manifest(release_dir, release_id)
        test_report = (
            _verified_test_report(release_dir, release_id, manifest)
            if test_report_path.exists()
            else None
        )
        result = deploy_release(
            release_dir,
            target_profile_name,
            release_id=release_id,
            manifest=manifest,
            kanban_conn=kanban_conn,
            kanban_task_id=kanban_task_id,
            verifier=verifier,
            test_report=test_report,
            claimed_identity=claimed_identity,
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        result = DeployResult(
            outcome="refused",
            target_profile=target_profile_name,
            profile_dir=None,
            failure_reason=f"release verification failed: {exc}",
            verified_identity=None,
        )

    from agent_factory.state import DeploymentRecord

    write_deployment_record(
        release_dir,
        DeploymentRecord(
            release_id=release_id,
            target_profile=result.target_profile if result.outcome == "deployed" else None,
            attempted_at=generated_at,
            outcome=result.outcome,
            failure_reason=result.failure_reason,
            verified_identity=result.verified_identity,
        ),
    )
    return result
