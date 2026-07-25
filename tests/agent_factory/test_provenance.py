"""Approval provenance verification interface — must fail closed by default."""

from __future__ import annotations

from agent_factory.provenance import (
    DefaultFailClosedVerifier,
    ProvenanceResult,
    ProvenanceVerifier,
)


def test_default_verifier_is_always_untrusted():
    verifier = DefaultFailClosedVerifier()
    result = verifier.verify(release_id="rel-1", kanban_task_id="k-1", claimed_identity=None)
    assert isinstance(result, ProvenanceResult)
    assert result.trusted is False
    assert result.verified_identity is None
    assert "fail" in result.reason.lower() or "no trusted" in result.reason.lower()


def test_default_verifier_ignores_claimed_identity_string():
    """A manually-supplied identity string must never be enough to earn trust."""
    verifier = DefaultFailClosedVerifier()
    result = verifier.verify(release_id="rel-1", kanban_task_id="k-1", claimed_identity="definitely-the-ceo")
    assert result.trusted is False


def test_default_verifier_ignores_missing_kanban_task():
    verifier = DefaultFailClosedVerifier()
    result = verifier.verify(release_id="rel-1", kanban_task_id=None, claimed_identity=None)
    assert result.trusted is False


def test_custom_verifier_satisfies_the_protocol():
    class _AlwaysTrusted:
        def verify(self, *, release_id, kanban_task_id, claimed_identity=None):
            return ProvenanceResult(trusted=True, reason="test-only stub", verified_identity="test-operator")

    verifier: ProvenanceVerifier = _AlwaysTrusted()
    result = verifier.verify(release_id="rel-1", kanban_task_id="k-1")
    assert result.trusted is True
    assert result.verified_identity == "test-operator"
