"""Freshness-stamped component health summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from control_centre.schema import Freshness, SourceRef


@dataclass(frozen=True)
class SystemHealth:
    source: SourceRef
    component: str
    status: str


def read_system_health(
    statuses: Mapping[str, str],
    *,
    observed_at: float,
) -> tuple[SystemHealth, ...]:
    allowed = {"ok", "degraded", "unavailable", "unknown"}
    result: list[SystemHealth] = []
    for component, raw_status in statuses.items():
        status = raw_status if raw_status in allowed else "unknown"
        freshness = Freshness.LIVE if status == "ok" else Freshness.UNAVAILABLE
        result.append(
            SystemHealth(
                source=SourceRef(
                    source="system",
                    source_id=component,
                    observed_at=observed_at,
                    freshness=freshness,
                ),
                component=component,
                status=status,
            )
        )
    return tuple(sorted(result, key=lambda item: item.component))
