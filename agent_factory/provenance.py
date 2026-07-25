"""Approval provenance verification interface.

Deployment must never trust a manually-written ``approval.json`` field or a
plain identity string on its own — "No manual approval.json or identity
string is trusted." A :class:`ProvenanceVerifier` is the only thing that can
turn a release+reviewer decision into deploy-eligible trust, and the
shipped default fails closed: until a real trust root (SSO, hardware key,
signed attestation, ...) is wired in, no factory-generated deploy can
proceed through the stock CLI. Tests may inject an alternate verifier that
returns ``trusted=True`` to exercise the guarded success path — the CLI
itself never offers a way to do so (see ``hermes_cli/agent_factory_cmd.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class ProvenanceResult:
    trusted: bool
    reason: str
    verified_identity: Optional[str] = None


@runtime_checkable
class ProvenanceVerifier(Protocol):
    def verify(
        self,
        *,
        release_id: str,
        kanban_task_id: Optional[str],
        claimed_identity: Optional[str] = None,
    ) -> ProvenanceResult:
        ...


class DefaultFailClosedVerifier:
    """Always returns ``trusted=False``. No real identity provenance source is configured."""

    def verify(
        self,
        *,
        release_id: str,
        kanban_task_id: Optional[str],
        claimed_identity: Optional[str] = None,
    ) -> ProvenanceResult:
        return ProvenanceResult(
            trusted=False,
            reason=(
                "no trusted identity provenance source is configured; "
                "the default verifier fails closed for every release"
            ),
            verified_identity=None,
        )
