"""Layer 1 static validation for the canonical ``agent.yaml`` intended-policy spec.

This module only validates *declared intent* — it never touches the tool
registry, the skills catalog, or the filesystem. That resolution happens in
``agent_factory.effective_tools`` and ``agent_factory.skills_resolve``.
``agent_factory.policy.ALLOWED_TOOLSETS`` is imported for a static
membership check only; it carries no registry dependency of its own.

The spec covers the full intended-policy surface — role, mission,
responsibilities, tool/skill policy, knowledge sources, memory policy,
Kanban task types, review requirements, deployment policy, and change
history — while a strict unknown-field policy at every level guarantees it
can never carry an actual approval/test/deployment fact: those live only
in the separate operational JSON files (see ``agent_factory.state``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

import yaml

from agent_factory.policy import ALLOWED_TOOLSETS

SPEC_API_VERSION = "agent-factory/v1"
SPEC_KIND = "AgentSpec"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_DEPARTMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_SPECIFICATION_STATUSES = {"draft", "active", "deprecated"}
_MEMORY_POLICY_MODES = {"none", "read_only", "read_write"}

# Declaring any of these top-level fields would claim authority this MVP must
# never grant to a generated agent config. Reject outright at Layer 1 rather
# than silently ignoring them. This is a stronger-worded subset of the
# general strict unknown-field policy below — kept separate so the error
# message can explain *why*, not just *that* the field is unrecognized.
_FORBIDDEN_TOP_LEVEL_FIELDS = ("deploy", "approval", "self_config", "cross_profile")

_KNOWN_TOP_LEVEL_FIELDS = frozenset({
    "apiVersion", "kind", "metadata", "specification", "tools", "skills",
    "knowledge_sources", "memory_policy", "kanban", "review",
    "deployment_policy", "department", "change_history",
})
_KNOWN_METADATA_FIELDS = frozenset({"name", "version", "description"})
_KNOWN_SPECIFICATION_FIELDS = frozenset({
    "role", "mission", "responsibilities", "non_responsibilities", "inputs", "outputs", "status",
})
_KNOWN_TOOLS_FIELDS = frozenset({"allow", "prohibited"})
_KNOWN_SKILLS_FIELDS = frozenset({"required", "optional", "prohibited"})
_KNOWN_MEMORY_POLICY_FIELDS = frozenset({"mode"})
_KNOWN_KANBAN_FIELDS = frozenset({"task_types", "handoff_contracts", "escalation_rules"})
_KNOWN_REVIEW_FIELDS = frozenset({"approval_required", "capability_tests", "boundary_tests", "permission_tests"})
_KNOWN_DEPLOYMENT_POLICY_FIELDS = frozenset({"allow_deploy"})
_KNOWN_CHANGE_HISTORY_ENTRY_FIELDS = frozenset({"version", "date", "summary"})

_WILDCARDS = {"*", "all"}


@dataclass(frozen=True)
class SchemaIssue:
    field: str
    message: str


@dataclass(frozen=True)
class ChangeHistoryEntry:
    version: str
    date: str
    summary: str


@dataclass(frozen=True)
class AgentSpec:
    name: str
    version: str
    description: Optional[str]
    role: str
    mission: str
    responsibilities: tuple[str, ...]
    non_responsibilities: tuple[str, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    specification_status: Optional[str]
    tools_allow: tuple[str, ...]
    tools_prohibited: tuple[str, ...]
    skills_required: tuple[str, ...]
    skills_optional: tuple[str, ...]
    skills_prohibited: tuple[str, ...]
    knowledge_sources: tuple[str, ...]
    memory_policy_mode: str
    kanban_task_types: tuple[str, ...]
    kanban_handoff_contracts: tuple[str, ...]
    kanban_escalation_rules: tuple[str, ...]
    review_approval_required: bool
    review_capability_tests: tuple[str, ...]
    review_boundary_tests: tuple[str, ...]
    review_permission_tests: tuple[str, ...]
    deployment_policy_allow_deploy: bool
    department: Optional[str]
    change_history: tuple[ChangeHistoryEntry, ...]


def _issue(field: str, message: str) -> SchemaIssue:
    return SchemaIssue(field=field, message=message)


def _check_unknown_keys(obj: dict, known: frozenset, prefix: str, issues: list[SchemaIssue]) -> None:
    for key in obj:
        if key not in known:
            issues.append(_issue(f"{prefix}.{key}", f"unknown field '{key}' is not permitted under '{prefix}'"))


def _string_list(obj: dict, key: str, field_path: str, issues: list[SchemaIssue], *, reject_wildcards: bool = False) -> tuple[str, ...]:
    value = obj.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        issues.append(_issue(field_path, f"{field_path} must be a list of strings"))
        return ()
    if reject_wildcards and any(v in _WILDCARDS for v in value):
        issues.append(_issue(field_path, f"{field_path} may not contain a wildcard ('*' or 'all')"))
        return ()
    return tuple(value)


def validate_spec_dict(data: Any) -> list[SchemaIssue]:
    """Return a list of :class:`SchemaIssue`; empty means the spec is valid."""
    issues: list[SchemaIssue] = []

    if not isinstance(data, dict):
        return [_issue("$", "spec must be a mapping")]

    for field in _FORBIDDEN_TOP_LEVEL_FIELDS:
        if field in data:
            issues.append(_issue(
                field,
                f"top-level '{field}' is not permitted — generated agent configs may "
                "never declare deployment, approval, self-config, or cross-profile authority",
            ))

    # Strict unknown-field policy at the top level: bare key names (not
    # "$.<key>") to match the forbidden-field messages above.
    for key in data:
        if key not in _KNOWN_TOP_LEVEL_FIELDS and key not in _FORBIDDEN_TOP_LEVEL_FIELDS:
            issues.append(_issue(key, f"unknown top-level field '{key}' is not permitted"))

    api_version = data.get("apiVersion")
    if api_version is None:
        issues.append(_issue("apiVersion", "apiVersion is required"))
    elif api_version != SPEC_API_VERSION:
        issues.append(_issue("apiVersion", f"unsupported apiVersion {api_version!r}; expected {SPEC_API_VERSION!r}"))

    kind = data.get("kind")
    if kind is not None and kind != SPEC_KIND:
        issues.append(_issue("kind", f"unsupported kind {kind!r}; expected {SPEC_KIND!r}"))

    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        issues.append(_issue("metadata", "metadata is required and must be a mapping"))
        metadata = {}
    else:
        _check_unknown_keys(metadata, _KNOWN_METADATA_FIELDS, "metadata", issues)

    name = metadata.get("name")
    if not name:
        issues.append(_issue("metadata.name", "metadata.name is required"))
    elif not isinstance(name, str) or not _NAME_RE.match(name):
        issues.append(_issue("metadata.name", "metadata.name must be a lowercase slug matching ^[a-z0-9][a-z0-9-]{0,63}$"))

    version = metadata.get("version")
    if not version:
        issues.append(_issue("metadata.version", "metadata.version is required"))
    elif not isinstance(version, str) or not _VERSION_RE.match(version):
        issues.append(_issue("metadata.version", "metadata.version must be MAJOR.MINOR.PATCH (e.g. '1.0.0')"))

    description = metadata.get("description")
    if description is not None and not isinstance(description, str):
        issues.append(_issue("metadata.description", "metadata.description must be a string"))

    # --- specification: role, mission, responsibilities, ... -------------
    specification = data.get("specification")
    if not isinstance(specification, dict):
        issues.append(_issue("specification", "specification is required and must be a mapping (role, mission, ...)"))
        specification = {}
    else:
        _check_unknown_keys(specification, _KNOWN_SPECIFICATION_FIELDS, "specification", issues)

    role = specification.get("role")
    if not isinstance(role, str) or not role.strip():
        issues.append(_issue("specification.role", "specification.role is required and must be a non-empty string"))

    mission = specification.get("mission")
    if not isinstance(mission, str) or not mission.strip():
        issues.append(_issue("specification.mission", "specification.mission is required and must be a non-empty string"))

    responsibilities = _string_list(specification, "responsibilities", "specification.responsibilities", issues)
    non_responsibilities = _string_list(specification, "non_responsibilities", "specification.non_responsibilities", issues)
    spec_inputs = _string_list(specification, "inputs", "specification.inputs", issues)
    spec_outputs = _string_list(specification, "outputs", "specification.outputs", issues)

    specification_status = specification.get("status")
    if specification_status is not None and specification_status not in _SPECIFICATION_STATUSES:
        issues.append(_issue("specification.status", f"specification.status must be one of {sorted(_SPECIFICATION_STATUSES)}"))

    # --- tools -------------------------------------------------------------
    tools = data.get("tools") or {}
    if not isinstance(tools, dict):
        issues.append(_issue("tools", "tools must be a mapping"))
        tools = {}
    else:
        _check_unknown_keys(tools, _KNOWN_TOOLS_FIELDS | {"deny"}, "tools", issues)
    if "deny" in tools:
        issues.append(_issue(
            "tools.deny",
            "tools.deny is not permitted — the policy is default-deny; declare only tools.allow",
        ))
    tools_allow = tools.get("allow", [])
    if not isinstance(tools_allow, list) or not all(isinstance(t, str) for t in tools_allow):
        issues.append(_issue("tools.allow", "tools.allow must be a list of tool or toolset name strings"))
        tools_allow = []
    elif any(t in _WILDCARDS for t in tools_allow):
        issues.append(_issue("tools.allow", "tools.allow may not contain a wildcard ('*' or 'all') — broad toolsets are not permitted"))
    else:
        disallowed = sorted(t for t in tools_allow if t not in ALLOWED_TOOLSETS)
        if disallowed:
            issues.append(_issue(
                "tools.allow",
                "tools.allow entries must be one of the factory's narrow allowed toolsets "
                f"{sorted(ALLOWED_TOOLSETS)} — individual tool names and composite/high-authority "
                f"toolsets are not accepted (config.yaml can only restrict at toolset granularity); "
                f"got disallowed: {disallowed}",
            ))

    tools_prohibited = _string_list(tools, "prohibited", "tools.prohibited", issues)
    if set(tools_allow) & set(tools_prohibited):
        issues.append(_issue("tools.prohibited", "tools.prohibited may not overlap with tools.allow"))

    # --- skills --------------------------------------------------------------
    skills = data.get("skills") or {}
    if not isinstance(skills, dict):
        issues.append(_issue("skills", "skills must be a mapping"))
        skills = {}
    else:
        _check_unknown_keys(skills, _KNOWN_SKILLS_FIELDS, "skills", issues)
    skills_required = _string_list(skills, "required", "skills.required", issues, reject_wildcards=True)
    skills_optional = _string_list(skills, "optional", "skills.optional", issues, reject_wildcards=True)
    skills_prohibited = _string_list(skills, "prohibited", "skills.prohibited", issues)
    if (set(skills_required) | set(skills_optional)) & set(skills_prohibited):
        issues.append(_issue("skills.prohibited", "skills.prohibited may not overlap with skills.required or skills.optional"))

    # --- knowledge_sources ----------------------------------------------------
    knowledge_sources = _string_list(data, "knowledge_sources", "knowledge_sources", issues)

    # --- memory_policy ----------------------------------------------------
    memory_policy = data.get("memory_policy") or {}
    if not isinstance(memory_policy, dict):
        issues.append(_issue("memory_policy", "memory_policy must be a mapping"))
        memory_policy = {}
    else:
        _check_unknown_keys(memory_policy, _KNOWN_MEMORY_POLICY_FIELDS, "memory_policy", issues)
    memory_policy_mode = memory_policy.get("mode", "none")
    if memory_policy_mode not in _MEMORY_POLICY_MODES:
        issues.append(_issue("memory_policy.mode", f"memory_policy.mode must be one of {sorted(_MEMORY_POLICY_MODES)}"))

    # --- kanban ------------------------------------------------------------
    kanban = data.get("kanban") or {}
    if not isinstance(kanban, dict):
        issues.append(_issue("kanban", "kanban must be a mapping"))
        kanban = {}
    else:
        _check_unknown_keys(kanban, _KNOWN_KANBAN_FIELDS, "kanban", issues)
    kanban_task_types = _string_list(kanban, "task_types", "kanban.task_types", issues)
    kanban_handoff_contracts = _string_list(kanban, "handoff_contracts", "kanban.handoff_contracts", issues)
    kanban_escalation_rules = _string_list(kanban, "escalation_rules", "kanban.escalation_rules", issues)

    # --- review ------------------------------------------------------------
    review = data.get("review") or {}
    if not isinstance(review, dict):
        issues.append(_issue("review", "review must be a mapping"))
        review = {}
    else:
        _check_unknown_keys(review, _KNOWN_REVIEW_FIELDS, "review", issues)
    review_approval_required = review.get("approval_required", True)
    if not isinstance(review_approval_required, bool):
        issues.append(_issue("review.approval_required", "review.approval_required must be a boolean"))
    review_capability_tests = _string_list(review, "capability_tests", "review.capability_tests", issues)
    review_boundary_tests = _string_list(review, "boundary_tests", "review.boundary_tests", issues)
    review_permission_tests = _string_list(review, "permission_tests", "review.permission_tests", issues)

    # --- deployment_policy — purely declarative, zero effect on real gating --
    deployment_policy = data.get("deployment_policy") or {}
    if not isinstance(deployment_policy, dict):
        issues.append(_issue("deployment_policy", "deployment_policy must be a mapping"))
        deployment_policy = {}
    else:
        _check_unknown_keys(deployment_policy, _KNOWN_DEPLOYMENT_POLICY_FIELDS, "deployment_policy", issues)
    deployment_policy_allow_deploy = deployment_policy.get("allow_deploy", True)
    if not isinstance(deployment_policy_allow_deploy, bool):
        issues.append(_issue("deployment_policy.allow_deploy", "deployment_policy.allow_deploy must be a boolean"))

    # --- department (optional, inert) --------------------------------------
    department = data.get("department")
    if department is not None and (not isinstance(department, str) or not _DEPARTMENT_RE.match(department)):
        issues.append(_issue("department", "department must be a lowercase slug matching ^[a-z0-9][a-z0-9-]{0,63}$"))

    # --- change_history ------------------------------------------------------
    change_history_raw = data.get("change_history", [])
    if not isinstance(change_history_raw, list):
        issues.append(_issue("change_history", "change_history must be a list of entries"))
        change_history_raw = []
    for idx, entry in enumerate(change_history_raw):
        field_prefix = f"change_history[{idx}]"
        if not isinstance(entry, dict):
            issues.append(_issue(field_prefix, f"{field_prefix} must be a mapping"))
            continue
        _check_unknown_keys(entry, _KNOWN_CHANGE_HISTORY_ENTRY_FIELDS, field_prefix, issues)
        entry_version = entry.get("version")
        if not isinstance(entry_version, str) or not _VERSION_RE.match(entry_version):
            issues.append(_issue(f"{field_prefix}.version", f"{field_prefix}.version must be MAJOR.MINOR.PATCH"))
        if not isinstance(entry.get("date"), str):
            issues.append(_issue(f"{field_prefix}.date", f"{field_prefix}.date must be a string"))
        if not isinstance(entry.get("summary"), str):
            issues.append(_issue(f"{field_prefix}.summary", f"{field_prefix}.summary must be a string"))

    return issues


def load_spec(data: Any) -> AgentSpec:
    """Validate and parse ``data`` into an :class:`AgentSpec`. Raises ``ValueError`` if invalid."""
    issues = validate_spec_dict(data)
    if issues:
        joined = "; ".join(f"{i.field}: {i.message}" for i in issues)
        raise ValueError(f"invalid agent.yaml spec: {joined}")

    metadata = data["metadata"]
    specification = data.get("specification") or {}
    tools = data.get("tools") or {}
    skills = data.get("skills") or {}
    memory_policy = data.get("memory_policy") or {}
    kanban = data.get("kanban") or {}
    review = data.get("review") or {}
    deployment_policy = data.get("deployment_policy") or {}
    change_history = tuple(
        ChangeHistoryEntry(version=e["version"], date=e["date"], summary=e["summary"])
        for e in data.get("change_history", [])
    )

    return AgentSpec(
        name=metadata["name"],
        version=metadata["version"],
        description=metadata.get("description"),
        role=specification["role"],
        mission=specification["mission"],
        responsibilities=tuple(specification.get("responsibilities", [])),
        non_responsibilities=tuple(specification.get("non_responsibilities", [])),
        inputs=tuple(specification.get("inputs", [])),
        outputs=tuple(specification.get("outputs", [])),
        specification_status=specification.get("status"),
        tools_allow=tuple(tools.get("allow", [])),
        tools_prohibited=tuple(tools.get("prohibited", [])),
        skills_required=tuple(skills.get("required", [])),
        skills_optional=tuple(skills.get("optional", [])),
        skills_prohibited=tuple(skills.get("prohibited", [])),
        knowledge_sources=tuple(data.get("knowledge_sources", [])),
        memory_policy_mode=memory_policy.get("mode", "none"),
        kanban_task_types=tuple(kanban.get("task_types", [])),
        kanban_handoff_contracts=tuple(kanban.get("handoff_contracts", [])),
        kanban_escalation_rules=tuple(kanban.get("escalation_rules", [])),
        review_approval_required=review.get("approval_required", True),
        review_capability_tests=tuple(review.get("capability_tests", [])),
        review_boundary_tests=tuple(review.get("boundary_tests", [])),
        review_permission_tests=tuple(review.get("permission_tests", [])),
        deployment_policy_allow_deploy=deployment_policy.get("allow_deploy", True),
        department=data.get("department"),
        change_history=change_history,
    )


def load_spec_from_yaml(text: str) -> AgentSpec:
    """Parse YAML text into an :class:`AgentSpec`, validating along the way."""
    data = yaml.safe_load(text)
    return load_spec(data)
