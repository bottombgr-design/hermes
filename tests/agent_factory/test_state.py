"""Operational release-state.json / test-report.json / approval.json / deployment-record.json."""

from __future__ import annotations

import json

from agent_factory.state import (
    ApprovalRecord,
    DeploymentRecord,
    LayerEvidence,
    ReleaseState,
    TestReport,
    Verdict,
    build_test_report,
    read_approval,
    read_deployment_record,
    read_release_state,
    read_test_report,
    write_approval,
    write_deployment_record,
    write_release_state,
    write_test_report,
)


def _evidence(layer, verdict):
    return LayerEvidence(layer=layer, verdict=verdict, checks=(), detail="")


def test_build_test_report_all_pass_allows_deploy():
    report = build_test_report(
        "rel-1",
        [
            _evidence("layer1_static", Verdict.PASS),
            _evidence("layer2_config", Verdict.PASS),
            _evidence("layer3_prompt", Verdict.PASS),
            _evidence("layer4_kanban", Verdict.PASS),
        ],
        generated_at=1000,
    )
    assert report.overall_verdict == Verdict.PASS
    assert report.deploy_allowed is True


def test_build_test_report_any_fail_blocks_deploy():
    report = build_test_report(
        "rel-1",
        [_evidence("layer1_static", Verdict.PASS), _evidence("layer2_config", Verdict.FAIL)],
        generated_at=1000,
    )
    assert report.overall_verdict == Verdict.FAIL
    assert report.deploy_allowed is False


def test_build_test_report_skipped_blocks_deploy_even_if_rest_pass():
    report = build_test_report(
        "rel-1",
        [_evidence("layer1_static", Verdict.PASS), _evidence("layer3_prompt", Verdict.SKIPPED)],
        generated_at=1000,
    )
    assert report.overall_verdict == Verdict.SKIPPED
    assert report.deploy_allowed is False


def test_build_test_report_unknown_blocks_deploy():
    report = build_test_report(
        "rel-1",
        [_evidence("layer1_static", Verdict.PASS), _evidence("layer2_config", Verdict.UNKNOWN)],
        generated_at=1000,
    )
    assert report.overall_verdict == Verdict.UNKNOWN
    assert report.deploy_allowed is False


def test_release_state_roundtrip(tmp_path):
    state = ReleaseState(
        release_id="rel-1",
        spec_name="billing-helper",
        spec_version="0.1.0",
        spec_format_version="agent-factory/v1",
        manifest_combined_sha256="a" * 64,
        created_at=1000,
        status="staged",
    )
    write_release_state(tmp_path, state)
    loaded = read_release_state(tmp_path)
    assert loaded == state
    on_disk = json.loads((tmp_path / "release-state.json").read_text(encoding="utf-8"))
    assert on_disk["release_id"] == "rel-1"


def test_test_report_roundtrip(tmp_path):
    report = build_test_report("rel-1", [_evidence("layer1_static", Verdict.PASS)], generated_at=1000)
    write_test_report(tmp_path, report)
    loaded = read_test_report(tmp_path)
    assert loaded.overall_verdict == Verdict.PASS
    assert loaded.deploy_allowed is False  # only one of four layers present -> not all mandatory layers ran
    assert isinstance(loaded, TestReport)


def test_approval_roundtrip(tmp_path):
    approval = ApprovalRecord(
        release_id="rel-1",
        kanban_task_id="k-1",
        decision="approve",
        reviewer="alice",
        decided_at=1000,
        provenance_trusted=False,
        provenance_reason="no trusted identity provenance source configured",
    )
    write_approval(tmp_path, approval)
    loaded = read_approval(tmp_path)
    assert loaded == approval


def test_deployment_record_roundtrip(tmp_path):
    record = DeploymentRecord(
        release_id="rel-1",
        target_profile=None,
        attempted_at=1000,
        outcome="refused",
        failure_reason="provenance not trusted",
        verified_identity=None,
    )
    write_deployment_record(tmp_path, record)
    loaded = read_deployment_record(tmp_path)
    assert loaded == record


def test_test_report_binds_manifest_hash():
    """CRITICAL 2: test evidence must be bound to the exact artifact it was
    computed against, so a later hash mismatch can be caught."""
    report = build_test_report(
        "rel-1", [_evidence("layer1_static", Verdict.PASS)],
        generated_at=1000, manifest_combined_sha256="a" * 64,
    )
    assert report.manifest_combined_sha256 == "a" * 64


def test_test_report_manifest_hash_roundtrips(tmp_path):
    report = build_test_report(
        "rel-1", [_evidence("layer1_static", Verdict.PASS)],
        generated_at=1000, manifest_combined_sha256="b" * 64,
    )
    write_test_report(tmp_path, report)
    loaded = read_test_report(tmp_path)
    assert loaded.manifest_combined_sha256 == "b" * 64


def test_approval_record_binds_manifest_hash(tmp_path):
    approval = ApprovalRecord(
        release_id="rel-1",
        kanban_task_id="k-1",
        decision="approve",
        reviewer="alice",
        decided_at=1000,
        provenance_trusted=True,
        provenance_reason="test",
        manifest_combined_sha256="c" * 64,
    )
    write_approval(tmp_path, approval)
    loaded = read_approval(tmp_path)
    assert loaded.manifest_combined_sha256 == "c" * 64


def test_json_files_are_written_deterministically(tmp_path):
    state = ReleaseState(
        release_id="rel-1",
        spec_name="billing-helper",
        spec_version="0.1.0",
        spec_format_version="agent-factory/v1",
        manifest_combined_sha256="a" * 64,
        created_at=1000,
        status="staged",
    )
    write_release_state(tmp_path, state)
    text_a = (tmp_path / "release-state.json").read_text(encoding="utf-8")
    (tmp_path / "release-state.json").unlink()
    write_release_state(tmp_path, state)
    text_b = (tmp_path / "release-state.json").read_text(encoding="utf-8")
    assert text_a == text_b
