from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from control_centre.schema import (
    AgentSummary,
    AttentionItem,
    BuildSummary,
    ControlCentreSnapshot,
    Freshness,
    ProductSummary,
    SourceError,
    SourceRef,
    snapshot_to_dict,
)


def ref(source_id: str, freshness: Freshness = Freshness.LIVE) -> SourceRef:
    return SourceRef(
        source="kanban",
        source_id=source_id,
        observed_at=100.0,
        freshness=freshness,
        href=f"/kanban?task={source_id}",
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_id": "t_1", "observed_at": 1.0, "freshness": Freshness.LIVE},
        {"source": "kanban", "observed_at": 1.0, "freshness": Freshness.LIVE},
        {"source": "kanban", "source_id": "t_1", "freshness": Freshness.LIVE},
    ],
)
def test_source_ref_requires_source_identity_and_observation(kwargs):
    with pytest.raises(TypeError):
        SourceRef(**kwargs)


def test_source_ref_is_frozen_and_rejects_invalid_values():
    source = ref("t_1")

    with pytest.raises(FrozenInstanceError):
        source.source_id = "changed"  # type: ignore[misc]

    with pytest.raises(ValueError, match="freshness"):
        SourceRef(
            source="kanban",
            source_id="t_1",
            observed_at=1.0,
            freshness="fresh-ish",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="source"):
        SourceRef(
            source="",
            source_id="t_1",
            observed_at=1.0,
            freshness=Freshness.LIVE,
        )


def test_snapshot_serialization_is_explicit_and_deterministic():
    snapshot = ControlCentreSnapshot(
        generated_at=200.0,
        stale_after_seconds=60,
        products=(
            ProductSummary(ref("p-z"), "z-product", "Zulu", "configured"),
            ProductSummary(ref("p-a"), "a-product", "Alpha", "incomplete"),
        ),
        attention=(
            AttentionItem(ref("t-2"), "warning", "Second", "Later"),
            AttentionItem(ref("t-1"), "critical", "First", "Now"),
        ),
        agents=(
            AgentSummary(ref("z-agent"), "z-agent", "idle"),
            AgentSummary(ref("a-agent"), "a-agent", "working"),
        ),
        builds=(
            BuildSummary(ref("r-2"), "a-product", "release-2", "FAIL"),
            BuildSummary(ref("r-1"), "a-product", "release-1", "PASS"),
        ),
        source_errors=(
            SourceError(ref("profiles", Freshness.UNAVAILABLE), "profiles unavailable"),
        ),
        setup_guidance=("Configure products.yaml",),
    )

    data = snapshot_to_dict(snapshot)

    assert data["schema_version"] == "control-centre/v1"
    assert data["generated_at"] == 200.0
    assert data["stale_after_seconds"] == 60
    assert [item["product_id"] for item in data["products"]] == [
        "a-product",
        "z-product",
    ]
    assert [item["source"]["source_id"] for item in data["attention"]] == [
        "t-1",
        "t-2",
    ]
    assert [item["profile"] for item in data["agents"]] == ["a-agent", "z-agent"]
    assert [item["release_id"] for item in data["builds"]] == [
        "release-1",
        "release-2",
    ]
    assert data["attention"][0]["source"]["freshness"] == "live"
    assert data["source_errors"][0]["source"]["freshness"] == "unavailable"
    assert data["setup_guidance"] == ["Configure products.yaml"]


def test_snapshot_collections_are_immutable_tuples():
    snapshot = ControlCentreSnapshot(generated_at=1.0, stale_after_seconds=30)

    assert snapshot.products == ()
    assert snapshot.attention == ()
    with pytest.raises(FrozenInstanceError):
        snapshot.generated_at = 2.0  # type: ignore[misc]
