"""Shared minimal-valid agent.yaml spec dict for agent_factory tests."""

from __future__ import annotations


def minimal_spec_dict(
    *,
    name: str = "demo-agent",
    version: str = "0.1.0",
    description: str | None = None,
    tools_allow=("todo",),
    skills_required=(),
) -> dict:
    metadata = {"name": name, "version": version}
    if description is not None:
        metadata["description"] = description
    return {
        "apiVersion": "agent-factory/v1",
        "kind": "AgentSpec",
        "metadata": metadata,
        "specification": {
            "role": "Demo role",
            "mission": "Demo mission for automated tests.",
        },
        "tools": {"allow": list(tools_allow)},
        "skills": {"required": list(skills_required)},
    }
