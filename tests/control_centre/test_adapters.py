from __future__ import annotations

import json

import pytest

from agent_factory.state import (
    DeploymentRecord,
    LayerEvidence,
    ReleaseState,
    Verdict,
    build_test_report,
    write_deployment_record,
    write_release_state,
    write_test_report,
)
from agent_factory.staging import MANIFEST_FILENAME, recompute_manifest
from hermes_cli import kanban_db as kb
from hermes_cli.kanban_diagnostics import Diagnostic
from hermes_cli.profiles import ProfileInfo
from hermes_state import SessionDB

from control_centre.adapters.agent_factory import read_agent_factory_release
from control_centre.adapters.cron import read_cron_jobs
from control_centre.adapters.kanban import diagnostic_to_attention, read_kanban
from control_centre.adapters.profiles import read_profiles
from control_centre.adapters.sessions import read_sessions
from control_centre.adapters.system import read_system_health


def test_blocked_and_review_required_tasks_become_distinct_attention(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        blocked = kb.create_task(conn, title="Needs input", assignee="atlas")
        assert kb.block_task(conn, blocked, reason="Choose a format", kind="needs_input")
        review = kb.create_task(conn, title="Review patch", assignee="tobias")
        assert kb.block_task(
            conn,
            review,
            kind="review_required",
            reason=(
                "What changed: patch; What should be reviewed: tests; "
                "Recommended decision: approve"
            ),
        )

        result = read_kanban(conn, observed_at=1_000.0, stale_after_seconds=60)
    finally:
        conn.close()

    by_id = {item.source.source_id: item for item in result.attention}
    assert by_id[blocked].severity == "error"
    assert by_id[blocked].title.startswith("Blocked:")
    assert by_id[review].severity == "warning"
    assert by_id[review].title.startswith("Review required:")


def test_running_task_carries_run_heartbeat_lease_and_workspace(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        task_id = kb.create_task(
            conn,
            title="Build feature",
            assignee="atlas",
            workspace_kind="dir",
            workspace_path=str(tmp_path / "workspace"),
        )
        claimed = kb.claim_task(conn, task_id, ttl_seconds=300, claimer="test")
        assert claimed is not None
        task = kb.get_task(conn, task_id)
        assert task is not None and task.current_run_id is not None
        conn.execute(
            "UPDATE tasks SET last_heartbeat_at = ? WHERE id = ?",
            (990, task_id),
        )
        conn.execute(
            "UPDATE task_runs SET last_heartbeat_at = ? WHERE id = ?",
            (990, task.current_run_id),
        )
        conn.commit()

        result = read_kanban(conn, observed_at=1_000.0, stale_after_seconds=60)
    finally:
        conn.close()

    activity = result.activities["atlas"]
    assert activity.task_id == task_id
    assert activity.run_id == str(task.current_run_id)
    assert activity.last_heartbeat == 990
    assert activity.lease_expires_at is not None
    assert activity.workspace == str(tmp_path / "workspace")
    assert not any(item.title.startswith("Stale worker") for item in result.attention)


def test_stale_and_missing_heartbeats_are_not_conflated(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        stale_id = kb.create_task(conn, title="Stale", assignee="atlas")
        missing_id = kb.create_task(conn, title="Missing", assignee="tobias")
        assert kb.claim_task(conn, stale_id, claimer="stale")
        assert kb.claim_task(conn, missing_id, claimer="missing")
        conn.execute(
            "UPDATE tasks SET last_heartbeat_at = ? WHERE id = ?",
            (800, stale_id),
        )
        conn.execute(
            "UPDATE tasks SET last_heartbeat_at = NULL WHERE id = ?",
            (missing_id,),
        )
        conn.commit()

        result = read_kanban(conn, observed_at=1_000.0, stale_after_seconds=60)
    finally:
        conn.close()

    stale = [item for item in result.attention if item.title.startswith("Stale worker")]
    assert [item.source.source_id for item in stale] == [stale_id]
    assert result.activities["atlas"].heartbeat_state == "stale"
    assert result.activities["tobias"].heartbeat_state == "unknown"


def test_diagnostic_mapping_preserves_original_signal():
    diagnostic = Diagnostic(
        kind="loop_stuck",
        severity="critical",
        title="Worker loop is stuck",
        detail="Heartbeat continues but no progress event was observed",
    )

    item = diagnostic_to_attention(diagnostic, task_id="t_1", observed_at=10.0)

    assert item.severity == "critical"
    assert item.title == diagnostic.title
    assert item.detail == diagnostic.detail
    assert item.source.source_id == "t_1:diagnostic:loop_stuck"


def test_profiles_map_idle_working_and_blocked_without_mutating_state(tmp_path):
    profile_infos = [
        ProfileInfo("atlas", tmp_path / "atlas", False, False, description="Research"),
        ProfileInfo("tobias", tmp_path / "tobias", False, False, model="m", provider="p"),
        ProfileInfo("idle", tmp_path / "idle", False, False),
    ]
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        working = kb.create_task(conn, title="Working", assignee="atlas")
        assert kb.claim_task(conn, working, claimer="working")
        blocked = kb.create_task(conn, title="Blocked", assignee="tobias")
        assert kb.block_task(conn, blocked, reason="Need input", kind="needs_input")
        kanban = read_kanban(conn, observed_at=1_000.0, stale_after_seconds=60)
        before = conn.total_changes
        agents = read_profiles(
            kanban,
            observed_at=1_000.0,
            profile_infos=profile_infos,
        )
        assert conn.total_changes == before
    finally:
        conn.close()

    by_profile = {agent.profile: agent for agent in agents}
    assert by_profile["atlas"].state == "working"
    assert by_profile["atlas"].role == "Research"
    assert by_profile["tobias"].state == "blocked"
    assert by_profile["tobias"].model == "m"
    assert by_profile["tobias"].provider == "p"
    assert by_profile["idle"].state == "idle"


def test_missing_current_run_is_visible_as_source_error(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        task_id = kb.create_task(conn, title="Orphan", assignee="atlas")
        assert kb.claim_task(conn, task_id, claimer="orphan")
        conn.execute("DELETE FROM task_runs WHERE task_id = ?", (task_id,))
        conn.commit()

        result = read_kanban(conn, observed_at=1_000.0, stale_after_seconds=60)
    finally:
        conn.close()

    assert len(result.errors) == 1
    assert result.errors[0].source.source_id == task_id
    assert "run" in result.errors[0].message.lower()


def test_session_adapter_returns_metadata_and_links_without_message_bodies(tmp_path):
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path)
    try:
        db.create_session("session-1", "cli")
    finally:
        db.close()

    result = read_sessions(db_path, observed_at=100.0)

    assert result.count == 1
    assert result.sessions[0]["id"] == "session-1"
    assert result.sessions[0]["href"] == "/sessions?session=session-1"
    assert "preview" not in result.sessions[0]
    assert "messages" not in result.sessions[0]


def test_cron_adapter_reads_status_without_mutating_or_triggering_jobs(tmp_path):
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "job-1",
                        "name": "Daily brief",
                        "enabled": True,
                        "next_run": "2030-01-01T09:00:00Z",
                        "last_status": "error",
                        "last_error": "provider unavailable",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    before = jobs_file.read_bytes()

    result = read_cron_jobs(jobs_file, observed_at=100.0)

    assert jobs_file.read_bytes() == before
    assert result.jobs == (
        {
            "id": "job-1",
            "name": "Daily brief",
            "enabled": True,
            "next_run": "2030-01-01T09:00:00Z",
            "last_status": "error",
            "last_error": "provider unavailable",
            "href": "/cron?job=job-1",
        },
    )


def test_system_adapter_freshness_stamps_each_component():
    result = read_system_health(
        {"gateway": "ok", "dispatcher": "degraded", "dashboard": "ok"},
        observed_at=100.0,
    )

    assert [item.component for item in result] == [
        "dashboard",
        "dispatcher",
        "gateway",
    ]
    assert all(item.source.observed_at == 100.0 for item in result)
    assert next(item for item in result if item.component == "dispatcher").status == "degraded"


def _factory_release(tmp_path, verdict: Verdict = Verdict.PASS):
    release = tmp_path / "releases" / "release-1"
    release.mkdir(parents=True)
    (release / "agent.yaml").write_text("name: example\n", encoding="utf-8")
    manifest = recompute_manifest(release)
    (release / MANIFEST_FILENAME).write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    write_release_state(
        release,
        ReleaseState(
            release_id="release-1",
            spec_name="example",
            spec_version="1.0.0",
            spec_format_version="agent-factory/v1",
            manifest_combined_sha256=manifest["combined_sha256"],
            created_at=10,
            status="tested",
        ),
    )
    layers = tuple(
        LayerEvidence(layer=name, verdict=verdict, checks=())
        for name in (
            "layer1_static",
            "layer2_config",
            "layer3_prompt",
            "layer4_kanban",
        )
    )
    write_test_report(
        release,
        build_test_report(
            "release-1",
            layers,
            generated_at=11,
            manifest_combined_sha256=manifest["combined_sha256"],
        ),
    )
    (release / "review-packet.json").write_text(
        json.dumps(
            {
                "release_id": "release-1",
                "manifest_combined_sha256": manifest["combined_sha256"],
                "test_report_overall_verdict": verdict.value,
            }
        ),
        encoding="utf-8",
    )
    write_deployment_record(
        release,
        DeploymentRecord(
            release_id="release-1",
            target_profile=None,
            attempted_at=12,
            outcome="refused" if verdict != Verdict.PASS else "not_attempted",
            failure_reason=None,
            verified_identity=None,
        ),
    )
    return release


@pytest.mark.parametrize("verdict", [Verdict.SKIPPED, Verdict.UNKNOWN])
def test_factory_adapter_preserves_blocking_non_pass_verdicts(tmp_path, verdict):
    release = _factory_release(tmp_path, verdict)

    result = read_agent_factory_release(
        release,
        product_id="hermes-agent",
        configured_root=tmp_path / "releases",
        observed_at=100.0,
    )

    assert result.builds[0].state == verdict.value
    assert dict(result.builds[0].layer_verdicts)["layer1_static"] == verdict.value


def test_factory_manifest_tamper_is_critical_but_returns_partial_build(tmp_path):
    release = _factory_release(tmp_path)
    (release / "agent.yaml").write_text("name: tampered\n", encoding="utf-8")

    result = read_agent_factory_release(
        release,
        product_id="hermes-agent",
        configured_root=tmp_path / "releases",
        observed_at=100.0,
    )

    assert result.builds[0].state == "FAIL"
    assert any(error.severity == "critical" for error in result.errors)
    assert any("manifest" in error.message.lower() for error in result.errors)


def test_factory_pass_report_without_manifest_fails_closed(tmp_path):
    release = _factory_release(tmp_path, Verdict.PASS)
    (release / MANIFEST_FILENAME).unlink()

    result = read_agent_factory_release(
        release,
        product_id="hermes-agent",
        configured_root=tmp_path / "releases",
        observed_at=100.0,
    )

    assert result.builds[0].state == "FAIL"
    assert result.builds[0].manifest_hash is None
    assert any(
        error.severity == "critical" and "manifest is unavailable" in error.message
        for error in result.errors
    )


def test_factory_missing_operational_files_fail_closed(tmp_path):
    release = tmp_path / "releases" / "release-missing"
    release.mkdir(parents=True)

    result = read_agent_factory_release(
        release,
        product_id="hermes-agent",
        configured_root=tmp_path / "releases",
        observed_at=100.0,
    )

    assert result.builds[0].state == "FAIL"
    assert result.errors
    assert all(build.state != "PASS" for build in result.builds)


def test_factory_release_must_stay_inside_configured_root(tmp_path):
    outside = tmp_path / "outside" / "release"
    outside.mkdir(parents=True)

    result = read_agent_factory_release(
        outside,
        product_id="hermes-agent",
        configured_root=tmp_path / "configured",
        observed_at=100.0,
    )

    assert result.builds == ()
    assert result.errors[0].severity == "critical"
