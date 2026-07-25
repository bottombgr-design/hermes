"""Layer 1: static schema/config validation.

Purely structural — no filesystem, no registry, no network. Never trusts
free-text wording; every check here is a concrete field-level rule from
``agent_factory.schema``.
"""

from __future__ import annotations

from typing import Any

from agent_factory.schema import validate_spec_dict
from agent_factory.state import LayerEvidence, Verdict

LAYER_NAME = "layer1_static"


def run_layer1_static(spec_data: Any) -> LayerEvidence:
    issues = validate_spec_dict(spec_data)
    if not issues:
        return LayerEvidence(layer=LAYER_NAME, verdict=Verdict.PASS, checks=(), detail="schema valid")

    checks = tuple(
        {"name": issue.field, "verdict": Verdict.FAIL.value, "detail": issue.message}
        for issue in issues
    )
    return LayerEvidence(
        layer=LAYER_NAME,
        verdict=Verdict.FAIL,
        checks=checks,
        detail=f"{len(issues)} schema issue(s)",
    )
