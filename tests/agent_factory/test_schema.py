"""Layer 1 static tests: canonical agent.yaml intended-policy schema validation.

The schema covers the full intended-policy surface (role/mission/
responsibilities, tool and skill policy, knowledge/memory/kanban/review/
deployment declarations, change history) while excluding any actual
approval/test/deployment fact — those live only in the separate
operational JSON files (see agent_factory.state).
"""

from __future__ import annotations

import pytest

from agent_factory.policy import ALLOWED_TOOLSETS
from agent_factory.schema import (
    SPEC_API_VERSION,
    AgentSpec,
    SchemaIssue,
    load_spec,
    validate_spec_dict,
)


def _valid_spec_dict() -> dict:
    return {
        "apiVersion": SPEC_API_VERSION,
        "kind": "AgentSpec",
        "metadata": {
            "name": "billing-helper",
            "version": "0.1.0",
            "description": "Answers billing questions.",
        },
        "specification": {
            "role": "Billing support specialist",
            "mission": "Resolve billing questions accurately and escalate disputes.",
            "responsibilities": ["Answer billing FAQs", "Look up invoice status"],
            "non_responsibilities": ["Process refunds", "Modify account credentials"],
            "inputs": ["Customer billing questions via chat"],
            "outputs": ["Answers", "Escalation tickets"],
            "status": "draft",
        },
        "tools": {"allow": ["web", "todo"], "prohibited": ["code_execution"]},
        "skills": {"required": ["billing-faq"], "optional": ["invoice-lookup"], "prohibited": ["refund-processor"]},
        "knowledge_sources": ["docs/billing-faq.md"],
        "memory_policy": {"mode": "read_only"},
        "kanban": {
            "task_types": ["billing_question"],
            "handoff_contracts": ["escalate-to-human on dispute"],
            "escalation_rules": ["escalate if refund requested"],
        },
        "review": {
            "approval_required": True,
            "capability_tests": ["can answer FAQ"],
            "boundary_tests": ["cannot process refund"],
            "permission_tests": ["cannot access terminal"],
        },
        "deployment_policy": {"allow_deploy": True},
        "change_history": [{"version": "0.1.0", "date": "2026-01-01", "summary": "Initial release"}],
    }


def test_valid_spec_has_no_issues():
    issues = validate_spec_dict(_valid_spec_dict())
    assert issues == []


def test_valid_spec_parses_into_agent_spec():
    spec = load_spec(_valid_spec_dict())
    assert isinstance(spec, AgentSpec)
    assert spec.name == "billing-helper"
    assert spec.version == "0.1.0"
    assert spec.role == "Billing support specialist"
    assert spec.mission.startswith("Resolve billing questions")
    assert spec.responsibilities == ("Answer billing FAQs", "Look up invoice status")
    assert spec.non_responsibilities == ("Process refunds", "Modify account credentials")
    assert spec.inputs == ("Customer billing questions via chat",)
    assert spec.outputs == ("Answers", "Escalation tickets")
    assert spec.specification_status == "draft"
    assert spec.tools_allow == ("web", "todo")
    assert spec.tools_prohibited == ("code_execution",)
    assert spec.skills_required == ("billing-faq",)
    assert spec.skills_optional == ("invoice-lookup",)
    assert spec.skills_prohibited == ("refund-processor",)
    assert spec.knowledge_sources == ("docs/billing-faq.md",)
    assert spec.memory_policy_mode == "read_only"
    assert spec.kanban_task_types == ("billing_question",)
    assert spec.kanban_handoff_contracts == ("escalate-to-human on dispute",)
    assert spec.kanban_escalation_rules == ("escalate if refund requested",)
    assert spec.review_approval_required is True
    assert spec.review_capability_tests == ("can answer FAQ",)
    assert spec.review_boundary_tests == ("cannot process refund",)
    assert spec.review_permission_tests == ("cannot access terminal",)
    assert spec.deployment_policy_allow_deploy is True
    assert spec.department is None
    assert len(spec.change_history) == 1
    assert spec.change_history[0].version == "0.1.0"
    assert spec.change_history[0].summary == "Initial release"


def test_minimal_spec_only_requires_role_and_mission_within_specification():
    data = _valid_spec_dict()
    data["specification"] = {"role": "Billing support specialist", "mission": "Resolve billing questions."}
    del data["knowledge_sources"]
    del data["memory_policy"]
    del data["kanban"]
    del data["review"]
    del data["deployment_policy"]
    del data["change_history"]
    del data["tools"]["prohibited"]
    del data["skills"]["optional"]
    del data["skills"]["prohibited"]
    issues = validate_spec_dict(data)
    assert issues == []
    spec = load_spec(data)
    assert spec.responsibilities == ()
    assert spec.memory_policy_mode == "none"
    assert spec.review_approval_required is True
    assert spec.deployment_policy_allow_deploy is True
    assert spec.change_history == ()


def test_missing_specification_block_is_rejected():
    data = _valid_spec_dict()
    del data["specification"]
    issues = validate_spec_dict(data)
    assert any(i.field == "specification" for i in issues)


def test_missing_role_is_rejected():
    data = _valid_spec_dict()
    del data["specification"]["role"]
    issues = validate_spec_dict(data)
    assert any(i.field == "specification.role" for i in issues)


def test_missing_mission_is_rejected():
    data = _valid_spec_dict()
    del data["specification"]["mission"]
    issues = validate_spec_dict(data)
    assert any(i.field == "specification.mission" for i in issues)


def test_empty_role_is_rejected():
    data = _valid_spec_dict()
    data["specification"]["role"] = "  "
    issues = validate_spec_dict(data)
    assert any(i.field == "specification.role" for i in issues)


def test_invalid_specification_status_is_rejected():
    data = _valid_spec_dict()
    data["specification"]["status"] = "not-a-status"
    issues = validate_spec_dict(data)
    assert any(i.field == "specification.status" for i in issues)


def test_unknown_key_inside_specification_is_rejected():
    data = _valid_spec_dict()
    data["specification"]["extra_field"] = "surprise"
    issues = validate_spec_dict(data)
    assert any(i.field == "specification.extra_field" for i in issues)


def test_missing_api_version_is_rejected():
    data = _valid_spec_dict()
    del data["apiVersion"]
    issues = validate_spec_dict(data)
    assert any(i.field == "apiVersion" for i in issues)
    assert all(isinstance(i, SchemaIssue) for i in issues)


def test_wrong_api_version_is_rejected():
    data = _valid_spec_dict()
    data["apiVersion"] = "agent-factory/v99"
    issues = validate_spec_dict(data)
    assert any(i.field == "apiVersion" for i in issues)


def test_missing_name_is_rejected():
    data = _valid_spec_dict()
    del data["metadata"]["name"]
    issues = validate_spec_dict(data)
    assert any(i.field == "metadata.name" for i in issues)


def test_invalid_name_characters_are_rejected():
    data = _valid_spec_dict()
    data["metadata"]["name"] = "Not A Valid Name!"
    issues = validate_spec_dict(data)
    assert any(i.field == "metadata.name" for i in issues)


def test_missing_version_is_rejected():
    data = _valid_spec_dict()
    del data["metadata"]["version"]
    issues = validate_spec_dict(data)
    assert any(i.field == "metadata.version" for i in issues)


def test_non_semver_version_is_rejected():
    data = _valid_spec_dict()
    data["metadata"]["version"] = "not-a-version"
    issues = validate_spec_dict(data)
    assert any(i.field == "metadata.version" for i in issues)


def test_unknown_key_inside_metadata_is_rejected():
    data = _valid_spec_dict()
    data["metadata"]["surprise"] = "nope"
    issues = validate_spec_dict(data)
    assert any(i.field == "metadata.surprise" for i in issues)


def test_unknown_top_level_field_is_rejected():
    data = _valid_spec_dict()
    data["totally_unrecognized_field"] = {"anything": True}
    issues = validate_spec_dict(data)
    assert any(i.field == "totally_unrecognized_field" for i in issues)


def test_unknown_top_level_field_cannot_smuggle_operational_facts():
    """The strict unknown-field policy is what keeps actual test/approval/
    deployment facts out of the canonical spec — not just the four
    explicitly-named forbidden fields."""
    for sneaky_field in ("test_results", "approval_decision", "deployment_status", "review_decision"):
        data = _valid_spec_dict()
        data[sneaky_field] = {"whatever": "value"}
        issues = validate_spec_dict(data)
        assert any(i.field == sneaky_field for i in issues), f"expected rejection for {sneaky_field!r}"


def test_tools_allow_must_be_a_list():
    data = _valid_spec_dict()
    data["tools"]["allow"] = "web"
    issues = validate_spec_dict(data)
    assert any(i.field == "tools.allow" for i in issues)


def test_tools_allow_wildcard_is_rejected():
    data = _valid_spec_dict()
    data["tools"]["allow"] = ["*"]
    issues = validate_spec_dict(data)
    assert any(i.field == "tools.allow" and "wildcard" in i.message.lower() for i in issues)


def test_tools_allow_individual_tool_name_is_rejected():
    """Only toolset names are accepted — config.yaml can only restrict at
    toolset granularity, so an individual tool name would be a silent
    no-op (or worse, misleading) rather than a real grant."""
    data = _valid_spec_dict()
    data["tools"]["allow"] = ["read_file"]
    issues = validate_spec_dict(data)
    assert any(i.field == "tools.allow" for i in issues)


def test_tools_allow_composite_toolset_is_rejected():
    data = _valid_spec_dict()
    data["tools"]["allow"] = ["coding"]
    issues = validate_spec_dict(data)
    assert any(i.field == "tools.allow" for i in issues)


def test_tools_allow_high_authority_toolset_is_rejected():
    for toolset in ("terminal", "file", "code_execution", "delegation", "cronjob", "kanban", "memory", "skills"):
        data = _valid_spec_dict()
        data["tools"]["allow"] = [toolset]
        issues = validate_spec_dict(data)
        assert any(i.field == "tools.allow" for i in issues), f"expected rejection for toolset {toolset!r}"


def test_every_allowed_toolset_in_policy_is_individually_accepted():
    for toolset in sorted(ALLOWED_TOOLSETS):
        data = _valid_spec_dict()
        data["tools"]["allow"] = [toolset]
        issues = validate_spec_dict(data)
        assert issues == [], f"expected toolset {toolset!r} to be accepted, got {issues}"


def test_tools_deny_key_is_rejected_since_policy_is_default_deny():
    data = _valid_spec_dict()
    data["tools"]["deny"] = ["cronjob"]
    issues = validate_spec_dict(data)
    assert any(i.field == "tools.deny" for i in issues)


def test_tools_prohibited_overlapping_allow_is_rejected():
    data = _valid_spec_dict()
    data["tools"]["allow"] = ["web"]
    data["tools"]["prohibited"] = ["web"]
    issues = validate_spec_dict(data)
    assert any(i.field == "tools.prohibited" for i in issues)


def test_unknown_key_inside_tools_is_rejected():
    data = _valid_spec_dict()
    data["tools"]["surprise"] = True
    issues = validate_spec_dict(data)
    assert any(i.field == "tools.surprise" for i in issues)


def test_skills_required_wildcard_is_rejected():
    data = _valid_spec_dict()
    data["skills"]["required"] = ["*"]
    issues = validate_spec_dict(data)
    assert any(i.field == "skills.required" for i in issues)


def test_skills_prohibited_overlapping_required_is_rejected():
    data = _valid_spec_dict()
    data["skills"]["required"] = ["billing-faq"]
    data["skills"]["prohibited"] = ["billing-faq"]
    issues = validate_spec_dict(data)
    assert any(i.field == "skills.prohibited" for i in issues)


def test_unknown_key_inside_skills_is_rejected():
    data = _valid_spec_dict()
    data["skills"]["surprise"] = True
    issues = validate_spec_dict(data)
    assert any(i.field == "skills.surprise" for i in issues)


def test_memory_policy_invalid_mode_is_rejected():
    data = _valid_spec_dict()
    data["memory_policy"]["mode"] = "read_write_everything"
    issues = validate_spec_dict(data)
    assert any(i.field == "memory_policy.mode" for i in issues)


def test_unknown_key_inside_memory_policy_is_rejected():
    data = _valid_spec_dict()
    data["memory_policy"]["surprise"] = True
    issues = validate_spec_dict(data)
    assert any(i.field == "memory_policy.surprise" for i in issues)


def test_unknown_key_inside_kanban_is_rejected():
    data = _valid_spec_dict()
    data["kanban"]["surprise"] = True
    issues = validate_spec_dict(data)
    assert any(i.field == "kanban.surprise" for i in issues)


def test_unknown_key_inside_review_is_rejected():
    data = _valid_spec_dict()
    data["review"]["surprise"] = True
    issues = validate_spec_dict(data)
    assert any(i.field == "review.surprise" for i in issues)


def test_unknown_key_inside_deployment_policy_is_rejected():
    data = _valid_spec_dict()
    data["deployment_policy"]["surprise"] = True
    issues = validate_spec_dict(data)
    assert any(i.field == "deployment_policy.surprise" for i in issues)


def test_change_history_entry_requires_semver_version():
    data = _valid_spec_dict()
    data["change_history"][0]["version"] = "not-a-version"
    issues = validate_spec_dict(data)
    assert any(i.field.startswith("change_history") for i in issues)


def test_change_history_entry_rejects_unknown_key():
    data = _valid_spec_dict()
    data["change_history"][0]["surprise"] = True
    issues = validate_spec_dict(data)
    assert any(i.field.startswith("change_history") for i in issues)


def test_dangerous_authority_fields_are_rejected():
    for field in ("deploy", "approval", "self_config", "cross_profile"):
        data = _valid_spec_dict()
        data[field] = {"enabled": True}
        issues = validate_spec_dict(data)
        assert any(i.field == field for i in issues), f"expected rejection for top-level {field!r}"


def test_optional_inert_department_reference_is_accepted():
    data = _valid_spec_dict()
    data["department"] = "support-eng"
    issues = validate_spec_dict(data)
    assert issues == []
    spec = load_spec(data)
    assert spec.department == "support-eng"


def test_invalid_department_reference_is_rejected():
    data = _valid_spec_dict()
    data["department"] = "Not Valid!"
    issues = validate_spec_dict(data)
    assert any(i.field == "department" for i in issues)


def test_load_spec_raises_on_invalid_data():
    data = _valid_spec_dict()
    del data["apiVersion"]
    with pytest.raises(ValueError):
        load_spec(data)


def test_load_spec_from_yaml_text():
    from agent_factory.schema import load_spec_from_yaml

    text = """
    apiVersion: agent-factory/v1
    kind: AgentSpec
    metadata:
      name: demo-agent
      version: 1.0.0
    specification:
      role: Demo role
      mission: Demo mission
    tools:
      allow: [todo]
    skills:
      required: []
    """
    spec = load_spec_from_yaml(text)
    assert spec.name == "demo-agent"
