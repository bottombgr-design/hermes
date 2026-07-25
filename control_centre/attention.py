"""Deterministic ordering for attention-first operator views."""

from __future__ import annotations

from collections.abc import Iterable

from control_centre.schema import AttentionItem

_SEVERITY_ORDER = {"critical": 0, "error": 1, "warning": 2, "info": 3}


def sort_attention(items: Iterable[AttentionItem]) -> tuple[AttentionItem, ...]:
    return tuple(
        sorted(
            items,
            key=lambda item: (
                _SEVERITY_ORDER[item.severity],
                item.source.observed_at,
                item.source.source_id,
            ),
        )
    )
