"""Per-source snapshot assembly and single-flight TTL caching."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from control_centre.attention import sort_attention
from control_centre.schema import (
    AgentSummary,
    AttentionItem,
    BuildSummary,
    ControlCentreSnapshot,
    Freshness,
    ProductSummary,
    SourceError,
    SourceRef,
)


@dataclass(frozen=True)
class SnapshotContribution:
    products: tuple[ProductSummary, ...] = ()
    attention: tuple[AttentionItem, ...] = ()
    agents: tuple[AgentSummary, ...] = ()
    builds: tuple[BuildSummary, ...] = ()
    errors: tuple[SourceError, ...] = ()
    setup_guidance: tuple[str, ...] = ()


def assemble_snapshot(
    sources: Mapping[str, Callable[[float], SnapshotContribution]],
    *,
    generated_at: float,
    stale_after_seconds: int,
) -> ControlCentreSnapshot:
    products: list[ProductSummary] = []
    attention: list[AttentionItem] = []
    agents: list[AgentSummary] = []
    builds: list[BuildSummary] = []
    errors: list[SourceError] = []
    guidance: list[str] = []

    for name in sorted(sources):
        try:
            contribution = sources[name](generated_at)
            if not isinstance(contribution, SnapshotContribution):
                raise TypeError("source must return SnapshotContribution")
            products.extend(contribution.products)
            attention.extend(contribution.attention)
            agents.extend(contribution.agents)
            builds.extend(contribution.builds)
            errors.extend(contribution.errors)
            guidance.extend(contribution.setup_guidance)
        except Exception as exc:
            errors.append(
                SourceError(
                    source=SourceRef(
                        source=name,
                        source_id=name,
                        observed_at=generated_at,
                        freshness=Freshness.UNAVAILABLE,
                    ),
                    message=f"Source unavailable: {type(exc).__name__}",
                    recovery=f"Open the {name} source and inspect its status",
                )
            )

    if not products and not guidance:
        guidance.append(
            "Create ~/.hermes/control-centre/products.yaml to configure products."
        )

    return ControlCentreSnapshot(
        generated_at=generated_at,
        stale_after_seconds=stale_after_seconds,
        products=tuple(sorted(products, key=lambda item: item.product_id)),
        attention=sort_attention(attention),
        agents=tuple(sorted(agents, key=lambda item: item.profile)),
        builds=tuple(
            sorted(builds, key=lambda item: (item.product_id, item.release_id))
        ),
        source_errors=tuple(
            sorted(
                errors,
                key=lambda item: (
                    item.source.source,
                    item.source.source_id,
                    item.message,
                ),
            )
        ),
        setup_guidance=tuple(sorted(set(guidance))),
    )


def _stale_source(source: SourceRef) -> SourceRef:
    freshness = (
        source.freshness
        if source.freshness is Freshness.UNAVAILABLE
        else Freshness.STALE
    )
    return replace(source, freshness=freshness)


def _mark_snapshot_stale(
    snapshot: ControlCentreSnapshot,
    *,
    observed_at: float,
    failure_type: str,
) -> ControlCentreSnapshot:
    products = tuple(
        replace(item, source=_stale_source(item.source)) for item in snapshot.products
    )
    attention = tuple(
        replace(item, source=_stale_source(item.source)) for item in snapshot.attention
    )
    agents = tuple(
        replace(item, source=_stale_source(item.source)) for item in snapshot.agents
    )
    builds = tuple(
        replace(item, source=_stale_source(item.source)) for item in snapshot.builds
    )
    errors = tuple(
        replace(item, source=_stale_source(item.source))
        for item in snapshot.source_errors
    ) + (
        SourceError(
            source=SourceRef(
                source="snapshot_cache",
                source_id="refresh",
                observed_at=observed_at,
                freshness=Freshness.STALE,
            ),
            message=f"Snapshot refresh failed: {failure_type}",
            recovery="Retry the Control Centre after checking source health",
        ),
    )
    return replace(
        snapshot,
        products=products,
        attention=attention,
        agents=agents,
        builds=builds,
        source_errors=errors,
    )


class SnapshotCache:
    """Small process-local cache with one refresh in flight at a time."""

    def __init__(
        self,
        *,
        ttl_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._snapshot: ControlCentreSnapshot | None = None
        self._cached_at: float | None = None

    def get(
        self,
        builder: Callable[[], ControlCentreSnapshot],
    ) -> ControlCentreSnapshot:
        now = self._clock()
        if self._is_fresh(now):
            assert self._snapshot is not None
            return self._snapshot
        with self._lock:
            now = self._clock()
            if self._is_fresh(now):
                assert self._snapshot is not None
                return self._snapshot
            try:
                snapshot = builder()
                if not isinstance(snapshot, ControlCentreSnapshot):
                    raise TypeError("builder must return ControlCentreSnapshot")
            except Exception as exc:
                if self._snapshot is None:
                    raise
                snapshot = _mark_snapshot_stale(
                    self._snapshot,
                    observed_at=now,
                    failure_type=type(exc).__name__,
                )
            self._snapshot = snapshot
            self._cached_at = now
            return snapshot

    def _is_fresh(self, now: float) -> bool:
        return (
            self._snapshot is not None
            and self._cached_at is not None
            and now - self._cached_at < self._ttl_seconds
        )
