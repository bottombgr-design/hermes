"""Review packet assembly.

Combines a release's spec, manifest, effective-tools report, resolved
skills, test-report, and version comparison into one artifact for a human
reviewer — separate from the canonical ``agent.yaml``, which stays free of
any operational facts (test results, approvals, deploy history all live in
their own JSON files; see ``agent_factory.state``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from agent_factory.effective_tools import EffectiveToolsReport
from agent_factory.schema import AgentSpec
from agent_factory.skills_resolve import SkillAttachmentReport
from agent_factory.state import TestReport, Verdict
from agent_factory.tests_layer4 import build_review_summary
from agent_factory.version import VersionComparison, compare_versions


@dataclass(frozen=True)
class ReviewPacket:
    release_id: str
    spec_name: str
    spec_version: str
    spec_description: Optional[str]
    manifest_combined_sha256: str
    effective_tools_allowed: tuple[str, ...]
    effective_tools_denied_forbidden: tuple[tuple[str, str], ...]
    skills_attached: tuple[str, ...]
    version_comparison: str
    test_report_overall_verdict: str
    test_report_deploy_allowed: bool
    generated_at: int


def build_review_packet(
    *,
    release_id: str,
    spec: AgentSpec,
    manifest: dict,
    effective_tools: EffectiveToolsReport,
    skills: SkillAttachmentReport,
    test_report: Optional[TestReport],
    previous_version: Optional[str],
    generated_at: int,
) -> ReviewPacket:
    comparison = compare_versions(previous_version, spec.version)
    return ReviewPacket(
        release_id=release_id,
        spec_name=spec.name,
        spec_version=spec.version,
        spec_description=spec.description,
        manifest_combined_sha256=manifest["combined_sha256"],
        effective_tools_allowed=tuple(effective_tools.allowed),
        effective_tools_denied_forbidden=tuple(effective_tools.denied_forbidden),
        skills_attached=tuple(skills.attached),
        version_comparison=comparison.value,
        test_report_overall_verdict=(
            test_report.overall_verdict.value if test_report is not None else Verdict.UNKNOWN.value
        ),
        test_report_deploy_allowed=test_report.deploy_allowed if test_report is not None else False,
        generated_at=generated_at,
    )


def render_review_summary(packet: ReviewPacket) -> str:
    """Render the 3-label summary kanban's ``review_required`` block requires."""
    what_changed = (
        f"{packet.spec_name} v{packet.spec_version} ({packet.version_comparison}); "
        f"tools allowed: {', '.join(packet.effective_tools_allowed) or 'none'}; "
        f"skills: {', '.join(packet.skills_attached) or 'none'}."
    )
    what_should_be_reviewed = (
        f"Effective tool policy, attached skills, and test-report verdict "
        f"({packet.test_report_overall_verdict})."
    )
    recommended_decision = "approve" if packet.test_report_deploy_allowed else "request changes"
    return build_review_summary(
        what_changed=what_changed,
        what_should_be_reviewed=what_should_be_reviewed,
        recommended_decision=recommended_decision,
    )


def review_packet_to_dict(packet: ReviewPacket) -> dict:
    return asdict(packet)
