"""Layer 1: static schema/config validation evidence."""

from __future__ import annotations

from agent_factory.state import LayerEvidence, Verdict
from agent_factory.tests_layer1 import run_layer1_static

from tests.agent_factory._fixtures import minimal_spec_dict


def test_valid_spec_produces_pass_evidence():
    evidence = run_layer1_static(minimal_spec_dict())
    assert isinstance(evidence, LayerEvidence)
    assert evidence.layer == "layer1_static"
    assert evidence.verdict == Verdict.PASS
    assert evidence.checks == ()


def test_invalid_spec_produces_fail_evidence_with_checks():
    evidence = run_layer1_static({"apiVersion": "wrong"})
    assert evidence.verdict == Verdict.FAIL
    assert len(evidence.checks) >= 1
    assert any(check["name"] == "apiVersion" for check in evidence.checks)
