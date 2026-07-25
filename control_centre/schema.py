"""Strict, freshness-stamped read models for the Control Centre API."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Freshness(str, Enum):
    """How recently and reliably a source was observed."""

    LIVE = "live"
    CACHED = "cached"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SourceRef:
    source: str
    source_id: str
    observed_at: float
    freshness: Freshness
    href: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty string")
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("source_id must be a non-empty string")
        if isinstance(self.observed_at, bool) or not isinstance(
            self.observed_at, (int, float)
        ):
            raise ValueError("observed_at must be a numeric timestamp")
        if self.observed_at < 0:
            raise ValueError("observed_at must not be negative")
        if not isinstance(self.freshness, Freshness):
            raise ValueError("freshness must be a valid Freshness value")
        if self.href is not None and not isinstance(self.href, str):
            raise ValueError("href must be a string or null")


@dataclass(frozen=True)
class AttentionItem:
    source: SourceRef
    severity: str
    title: str
    detail: str
    product_id: str | None = None
    agent_id: str | None = None

    def __post_init__(self) -> None:
        if self.severity not in {"critical", "error", "warning", "info"}:
            raise ValueError("severity must be critical, error, warning, or info")


@dataclass(frozen=True)
class AgentSummary:
    source: SourceRef
    profile: str
    state: str
    role: str | None = None
    product_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    last_heartbeat: float | None = None
    lease_expires_at: float | None = None
    model: str | None = None
    provider: str | None = None
    skill_count: int | None = None
    workspace: str | None = None
    chat_href: str | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProductSummary:
    source: SourceRef
    product_id: str
    name: str
    configuration_state: str
    repositories: tuple[dict[str, Any], ...] = ()
    kanban_href: str | None = None
    chat_href: str | None = None
    sessions_href: str | None = None
    knowledge_roots: tuple[str, ...] = ()
    environments: tuple[dict[str, Any], ...] = ()
    recovery: str | None = None


@dataclass(frozen=True)
class BuildSummary:
    source: SourceRef
    product_id: str
    release_id: str
    state: str
    manifest_hash: str | None = None
    layer_verdicts: tuple[tuple[str, str], ...] = ()
    review_state: str | None = None
    deployment_outcome: str | None = None


@dataclass(frozen=True)
class SourceError:
    source: SourceRef
    message: str
    recovery: str | None = None
    severity: str = "error"

    def __post_init__(self) -> None:
        if self.severity not in {"critical", "error", "warning", "info"}:
            raise ValueError("severity must be critical, error, warning, or info")


@dataclass(frozen=True)
class ControlCentreSnapshot:
    generated_at: float
    stale_after_seconds: int
    products: tuple[ProductSummary, ...] = ()
    attention: tuple[AttentionItem, ...] = ()
    agents: tuple[AgentSummary, ...] = ()
    builds: tuple[BuildSummary, ...] = ()
    source_errors: tuple[SourceError, ...] = ()
    setup_guidance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        tuple_fields = (
            "products",
            "attention",
            "agents",
            "builds",
            "source_errors",
            "setup_guidance",
        )
        for name in tuple_fields:
            if not isinstance(getattr(self, name), tuple):
                raise ValueError(f"{name} must be an immutable tuple")
        if self.stale_after_seconds < 0:
            raise ValueError("stale_after_seconds must not be negative")


def source_ref_to_dict(source: SourceRef) -> dict[str, Any]:
    return {
        "source": source.source,
        "source_id": source.source_id,
        "observed_at": source.observed_at,
        "freshness": source.freshness.value,
        "href": source.href,
    }


def attention_to_dict(item: AttentionItem) -> dict[str, Any]:
    return {
        "source": source_ref_to_dict(item.source),
        "severity": item.severity,
        "title": item.title,
        "detail": item.detail,
        "product_id": item.product_id,
        "agent_id": item.agent_id,
    }


def agent_to_dict(agent: AgentSummary) -> dict[str, Any]:
    return {
        "source": source_ref_to_dict(agent.source),
        "profile": agent.profile,
        "state": agent.state,
        "role": agent.role,
        "product_id": agent.product_id,
        "task_id": agent.task_id,
        "run_id": agent.run_id,
        "last_heartbeat": agent.last_heartbeat,
        "lease_expires_at": agent.lease_expires_at,
        "model": agent.model,
        "provider": agent.provider,
        "skill_count": agent.skill_count,
        "workspace": agent.workspace,
        "chat_href": agent.chat_href,
        "diagnostics": list(agent.diagnostics),
    }


def product_to_dict(product: ProductSummary) -> dict[str, Any]:
    return {
        "source": source_ref_to_dict(product.source),
        "product_id": product.product_id,
        "name": product.name,
        "configuration_state": product.configuration_state,
        "repositories": [dict(item) for item in product.repositories],
        "kanban_href": product.kanban_href,
        "chat_href": product.chat_href,
        "sessions_href": product.sessions_href,
        "knowledge_roots": list(product.knowledge_roots),
        "environments": [dict(item) for item in product.environments],
        "recovery": product.recovery,
    }


def build_to_dict(build: BuildSummary) -> dict[str, Any]:
    return {
        "source": source_ref_to_dict(build.source),
        "product_id": build.product_id,
        "release_id": build.release_id,
        "state": build.state,
        "manifest_hash": build.manifest_hash,
        "layer_verdicts": dict(build.layer_verdicts),
        "review_state": build.review_state,
        "deployment_outcome": build.deployment_outcome,
    }


def source_error_to_dict(error: SourceError) -> dict[str, Any]:
    return {
        "source": source_ref_to_dict(error.source),
        "message": error.message,
        "recovery": error.recovery,
        "severity": error.severity,
    }


def snapshot_to_dict(snapshot: ControlCentreSnapshot) -> dict[str, Any]:
    severity_order = {"critical": 0, "error": 1, "warning": 2, "info": 3}
    return {
        "schema_version": "control-centre/v1",
        "generated_at": snapshot.generated_at,
        "stale_after_seconds": snapshot.stale_after_seconds,
        "products": [
            product_to_dict(item)
            for item in sorted(snapshot.products, key=lambda item: item.product_id)
        ],
        "attention": [
            attention_to_dict(item)
            for item in sorted(
                snapshot.attention,
                key=lambda item: (
                    severity_order[item.severity],
                    item.source.source_id,
                ),
            )
        ],
        "agents": [
            agent_to_dict(item)
            for item in sorted(snapshot.agents, key=lambda item: item.profile)
        ],
        "builds": [
            build_to_dict(item)
            for item in sorted(
                snapshot.builds,
                key=lambda item: (item.product_id, item.release_id),
            )
        ],
        "source_errors": [
            source_error_to_dict(item)
            for item in sorted(
                snapshot.source_errors,
                key=lambda item: (severity_order[item.severity], item.source.source_id),
            )
        ],
        "setup_guidance": list(snapshot.setup_guidance),
    }
