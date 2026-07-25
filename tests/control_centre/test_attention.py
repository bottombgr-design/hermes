from __future__ import annotations

from control_centre.attention import sort_attention
from control_centre.schema import AttentionItem, Freshness, SourceRef


def item(source_id: str, severity: str, observed_at: float) -> AttentionItem:
    return AttentionItem(
        source=SourceRef(
            source="kanban",
            source_id=source_id,
            observed_at=observed_at,
            freshness=Freshness.LIVE,
        ),
        severity=severity,
        title=source_id,
        detail=source_id,
    )


def test_attention_sort_is_severity_then_oldest_then_source_id():
    values = (
        item("warning-new", "warning", 20),
        item("critical-new", "critical", 20),
        item("critical-old-z", "critical", 10),
        item("critical-old-a", "critical", 10),
    )

    ordered = sort_attention(values)

    assert [entry.source.source_id for entry in ordered] == [
        "critical-old-a",
        "critical-old-z",
        "critical-new",
        "warning-new",
    ]
