"""Review packet assembly: spec + manifest + effective tools + skills + test-report + version, for kanban review."""

from __future__ import annotations

from agent_factory.effective_tools import EffectiveToolsReport
from agent_factory.review_packet import ReviewPacket, build_review_packet, render_review_summary, review_packet_to_dict
from agent_factory.schema import load_spec
from agent_factory.skills_resolve import SkillAttachmentReport
from agent_factory.state import LayerEvidence, Verdict, build_test_report
from agent_factory.version import VersionComparison


def _spec():
    return load_spec({
        "apiVersion": "agent-factory/v1",
        "kind": "AgentSpec",
        "metadata": {"name": "billing-helper", "version": "0.2.0", "description": "Billing Q&A"},
        "specification": {"role": "Billing helper", "mission": "Answer billing questions."},
        "tools": {"allow": ["todo"]},
        "skills": {"required": ["alpha-skill"]},
    })


def _effective_tools():
    return EffectiveToolsReport(
        requested=("todo",), allowed_toolsets=("todo",), allowed=("todo",),
        denied_forbidden=(("cronjob", "forbidden"),), denied_unknown=(),
    )


def _skills():
    return SkillAttachmentReport(attached=("alpha-skill",), missing=())


def _passing_test_report():
    return build_test_report(
        "rel-1",
        [
            LayerEvidence(layer="layer1_static", verdict=Verdict.PASS, checks=(), detail=""),
            LayerEvidence(layer="layer2_config", verdict=Verdict.PASS, checks=(), detail=""),
            LayerEvidence(layer="layer3_prompt", verdict=Verdict.PASS, checks=(), detail=""),
            LayerEvidence(layer="layer4_kanban", verdict=Verdict.PASS, checks=(), detail=""),
        ],
        generated_at=1000,
    )


def test_build_review_packet_captures_all_inputs():
    packet = build_review_packet(
        release_id="rel-1",
        spec=_spec(),
        manifest={"combined_sha256": "c" * 64, "files": []},
        effective_tools=_effective_tools(),
        skills=_skills(),
        test_report=_passing_test_report(),
        previous_version=None,
        generated_at=1000,
    )
    assert isinstance(packet, ReviewPacket)
    assert packet.release_id == "rel-1"
    assert packet.spec_name == "billing-helper"
    assert packet.spec_version == "0.2.0"
    assert packet.manifest_combined_sha256 == "c" * 64
    assert packet.effective_tools_allowed == ("todo",)
    assert packet.effective_tools_denied_forbidden == (("cronjob", "forbidden"),)
    assert packet.skills_attached == ("alpha-skill",)
    assert packet.version_comparison == VersionComparison.INITIAL.value
    assert packet.test_report_deploy_allowed is True


def test_build_review_packet_computes_version_comparison_against_previous():
    packet = build_review_packet(
        release_id="rel-2", spec=_spec(), manifest={"combined_sha256": "d" * 64, "files": []},
        effective_tools=_effective_tools(), skills=_skills(), test_report=None,
        previous_version="0.1.0", generated_at=1000,
    )
    assert packet.version_comparison == VersionComparison.UPGRADE.value
    assert packet.test_report_overall_verdict == Verdict.UNKNOWN.value
    assert packet.test_report_deploy_allowed is False


def test_render_review_summary_has_three_required_labels():
    packet = build_review_packet(
        release_id="rel-1", spec=_spec(), manifest={"combined_sha256": "c" * 64, "files": []},
        effective_tools=_effective_tools(), skills=_skills(), test_report=_passing_test_report(),
        previous_version=None, generated_at=1000,
    )
    summary = render_review_summary(packet)
    lowered = summary.lower()
    assert "what changed" in lowered
    assert "what should be reviewed" in lowered
    assert "recommended decision" in lowered
    assert "billing-helper" in summary


def test_render_review_summary_recommends_request_changes_when_deploy_not_allowed():
    packet = build_review_packet(
        release_id="rel-1", spec=_spec(), manifest={"combined_sha256": "c" * 64, "files": []},
        effective_tools=_effective_tools(), skills=_skills(), test_report=None,
        previous_version=None, generated_at=1000,
    )
    summary = render_review_summary(packet)
    recommendation = summary.split("Recommended decision:", 1)[1].lower()
    assert "request changes" in recommendation
    assert "approve" not in recommendation


def test_review_packet_to_dict_is_json_serializable():
    import json

    packet = build_review_packet(
        release_id="rel-1", spec=_spec(), manifest={"combined_sha256": "c" * 64, "files": []},
        effective_tools=_effective_tools(), skills=_skills(), test_report=_passing_test_report(),
        previous_version=None, generated_at=1000,
    )
    payload = review_packet_to_dict(packet)
    text = json.dumps(payload, sort_keys=True)
    assert '"spec_name": "billing-helper"' in text


def test_review_packet_never_carries_deployment_or_approval_authority():
    packet = build_review_packet(
        release_id="rel-1", spec=_spec(), manifest={"combined_sha256": "c" * 64, "files": []},
        effective_tools=_effective_tools(), skills=_skills(), test_report=_passing_test_report(),
        previous_version=None, generated_at=1000,
    )
    payload = review_packet_to_dict(packet)
    for forbidden_key in ("deploy", "approval", "self_config", "cross_profile"):
        assert forbidden_key not in payload
