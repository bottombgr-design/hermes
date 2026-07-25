"""Operational state files for a factory release.

Five deterministic, atomically-written JSON files live alongside a staged
release: ``release-state.json``, ``test-report.json``, ``review-packet.json``,
``approval.json``, ``deployment-record.json``. A mandatory Layer 1-4 verdict
of SKIPPED or UNKNOWN blocks ``deploy_allowed`` exactly like FAIL does —
evidence must be complete and green, not merely absent of failure.

None of these live inside the release's content manifest — see
``OPERATIONAL_FILENAMES`` and ``agent_factory.staging.recompute_manifest``,
which excludes them explicitly so writing a test report can never itself
shift the "has this release been edited" hash.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

RELEASE_STATE_FILENAME = "release-state.json"
TEST_REPORT_FILENAME = "test-report.json"
REVIEW_PACKET_FILENAME = "review-packet.json"
APPROVAL_FILENAME = "approval.json"
DEPLOYMENT_RECORD_FILENAME = "deployment-record.json"

OPERATIONAL_FILENAMES = frozenset({
    RELEASE_STATE_FILENAME, TEST_REPORT_FILENAME, REVIEW_PACKET_FILENAME,
    APPROVAL_FILENAME, DEPLOYMENT_RECORD_FILENAME,
})

MANDATORY_LAYERS = ("layer1_static", "layer2_config", "layer3_prompt", "layer4_kanban")


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LayerEvidence:
    layer: str
    verdict: Verdict
    checks: tuple[dict, ...]
    detail: str = ""


@dataclass(frozen=True)
class ReleaseState:
    release_id: str
    spec_name: str
    spec_version: str
    spec_format_version: str
    manifest_combined_sha256: str
    created_at: int
    status: str


@dataclass(frozen=True)
class TestReport:
    release_id: str
    layers: tuple[LayerEvidence, ...]
    overall_verdict: Verdict
    deploy_allowed: bool
    missing_mandatory_layers: tuple[str, ...]
    generated_at: int
    # Binds this evidence to the exact staged artifact it was computed
    # against (CRITICAL 2) — None only for legacy/ad-hoc reports that never
    # ran against a real staged release.
    manifest_combined_sha256: Optional[str] = None


@dataclass(frozen=True)
class ApprovalRecord:
    release_id: str
    kanban_task_id: Optional[str]
    decision: Optional[str]
    reviewer: Optional[str]
    decided_at: Optional[int]
    provenance_trusted: bool
    provenance_reason: str
    manifest_combined_sha256: Optional[str] = None


@dataclass(frozen=True)
class DeploymentRecord:
    release_id: str
    target_profile: Optional[str]
    attempted_at: int
    outcome: str
    failure_reason: Optional[str]
    verified_identity: Optional[str]


def build_test_report(
    release_id: str, layers, *, generated_at: int, manifest_combined_sha256: Optional[str] = None,
) -> TestReport:
    layers = tuple(layers)
    verdict_by_layer = {layer.layer: layer.verdict for layer in layers}

    if any(layer.verdict == Verdict.FAIL for layer in layers):
        overall = Verdict.FAIL
    elif any(layer.verdict == Verdict.UNKNOWN for layer in layers):
        overall = Verdict.UNKNOWN
    elif any(layer.verdict == Verdict.SKIPPED for layer in layers):
        overall = Verdict.SKIPPED
    else:
        overall = Verdict.PASS

    missing_mandatory = tuple(name for name in MANDATORY_LAYERS if name not in verdict_by_layer)
    deploy_allowed = (
        overall == Verdict.PASS
        and not missing_mandatory
        and all(verdict_by_layer.get(name) == Verdict.PASS for name in MANDATORY_LAYERS)
    )

    return TestReport(
        release_id=release_id,
        layers=layers,
        overall_verdict=overall,
        deploy_allowed=deploy_allowed,
        missing_mandatory_layers=missing_mandatory,
        generated_at=generated_at,
        manifest_combined_sha256=manifest_combined_sha256,
    )


def _write_json(dest_dir: Path, filename: str, payload: dict) -> None:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / filename
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(dest_dir: Path, filename: str) -> dict:
    return json.loads((Path(dest_dir) / filename).read_text(encoding="utf-8"))


def write_release_state(dest_dir: Path, state: ReleaseState) -> None:
    _write_json(dest_dir, RELEASE_STATE_FILENAME, asdict(state))


def read_release_state(dest_dir: Path) -> ReleaseState:
    return ReleaseState(**_read_json(dest_dir, RELEASE_STATE_FILENAME))


def write_test_report(dest_dir: Path, report: TestReport) -> None:
    payload = asdict(report)
    payload["overall_verdict"] = report.overall_verdict.value
    payload["layers"] = [
        {**asdict(layer), "verdict": layer.verdict.value} for layer in report.layers
    ]
    _write_json(dest_dir, TEST_REPORT_FILENAME, payload)


def read_test_report(dest_dir: Path) -> TestReport:
    data = _read_json(dest_dir, TEST_REPORT_FILENAME)
    layers = tuple(
        LayerEvidence(
            layer=layer["layer"],
            verdict=Verdict(layer["verdict"]),
            checks=tuple(layer["checks"]),
            detail=layer.get("detail", ""),
        )
        for layer in data["layers"]
    )
    return TestReport(
        release_id=data["release_id"],
        layers=layers,
        overall_verdict=Verdict(data["overall_verdict"]),
        deploy_allowed=data["deploy_allowed"],
        missing_mandatory_layers=tuple(data["missing_mandatory_layers"]),
        generated_at=data["generated_at"],
        manifest_combined_sha256=data.get("manifest_combined_sha256"),
    )


def write_approval(dest_dir: Path, approval: ApprovalRecord) -> None:
    _write_json(dest_dir, APPROVAL_FILENAME, asdict(approval))


def read_approval(dest_dir: Path) -> ApprovalRecord:
    return ApprovalRecord(**_read_json(dest_dir, APPROVAL_FILENAME))


def write_deployment_record(dest_dir: Path, record: DeploymentRecord) -> None:
    _write_json(dest_dir, DEPLOYMENT_RECORD_FILENAME, asdict(record))


def read_deployment_record(dest_dir: Path) -> DeploymentRecord:
    return DeploymentRecord(**_read_json(dest_dir, DEPLOYMENT_RECORD_FILENAME))
