from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from control_centre.schema import (
    ControlCentreSnapshot,
    Freshness,
    ProductSummary,
    SourceRef,
)
from control_centre.snapshot import (
    SnapshotCache,
    SnapshotContribution,
    assemble_snapshot,
)


def product(observed_at: float = 10.0) -> ProductSummary:
    return ProductSummary(
        source=SourceRef(
            source="registry",
            source_id="hermes-agent",
            observed_at=observed_at,
            freshness=Freshness.LIVE,
        ),
        product_id="hermes-agent",
        name="Hermes Agent",
        configuration_state="configured",
    )


def test_source_failure_does_not_hide_other_sections_and_redacts_exception_text():
    def registry(_observed_at):
        return SnapshotContribution(products=(product(),))

    def sessions(_observed_at):
        raise RuntimeError(
            "/home/marc/.hermes/profiles/tobias/config.yaml token=do-not-leak"
        )

    snapshot = assemble_snapshot(
        {"registry": registry, "sessions": sessions},
        generated_at=10.0,
        stale_after_seconds=30,
    )

    assert [item.product_id for item in snapshot.products] == ["hermes-agent"]
    assert len(snapshot.source_errors) == 1
    assert snapshot.source_errors[0].source.source_id == "sessions"
    assert snapshot.source_errors[0].message == "Source unavailable: RuntimeError"
    rendered = repr(snapshot)
    assert "/home/marc" not in rendered
    assert "do-not-leak" not in rendered


def test_empty_product_catalog_has_explicit_setup_guidance():
    snapshot = assemble_snapshot(
        {"registry": lambda _now: SnapshotContribution()},
        generated_at=10.0,
        stale_after_seconds=30,
    )

    assert snapshot.products == ()
    assert snapshot.setup_guidance == (
        "Create ~/.hermes/control-centre/products.yaml to configure products.",
    )


def test_snapshot_order_is_deterministic_across_source_order():
    alpha = ProductSummary(
        source=SourceRef("registry", "alpha", 10, Freshness.LIVE),
        product_id="alpha",
        name="Alpha",
        configuration_state="configured",
    )
    zulu = ProductSummary(
        source=SourceRef("registry", "zulu", 10, Freshness.LIVE),
        product_id="zulu",
        name="Zulu",
        configuration_state="configured",
    )

    snapshot = assemble_snapshot(
        {
            "z": lambda _now: SnapshotContribution(products=(zulu,)),
            "a": lambda _now: SnapshotContribution(products=(alpha,)),
        },
        generated_at=10,
        stale_after_seconds=30,
    )

    assert [item.product_id for item in snapshot.products] == ["alpha", "zulu"]


def test_snapshot_cache_honours_ttl_without_extra_refreshes():
    now = [0.0]
    calls = 0

    def builder():
        nonlocal calls
        calls += 1
        return ControlCentreSnapshot(
            generated_at=now[0],
            stale_after_seconds=30,
            products=(product(now[0]),),
        )

    cache = SnapshotCache(ttl_seconds=10, clock=lambda: now[0])
    first = cache.get(builder)
    now[0] = 9
    second = cache.get(builder)
    now[0] = 11
    third = cache.get(builder)

    assert first is second
    assert third is not second
    assert calls == 2


def test_snapshot_cache_refresh_is_single_flight():
    calls = 0
    entered = threading.Event()
    release = threading.Event()

    def builder():
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=2)
        return ControlCentreSnapshot(generated_at=1, stale_after_seconds=30)

    cache = SnapshotCache(ttl_seconds=10, clock=lambda: 1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(cache.get, builder) for _ in range(4)]
        assert entered.wait(timeout=2)
        time.sleep(0.05)
        release.set()
        snapshots = [future.result(timeout=2) for future in futures]

    assert calls == 1
    assert all(snapshot is snapshots[0] for snapshot in snapshots)


def test_snapshot_cache_returns_explicitly_stale_copy_when_refresh_fails():
    now = [0.0]
    cache = SnapshotCache(ttl_seconds=10, clock=lambda: now[0])
    fresh = cache.get(
        lambda: ControlCentreSnapshot(
            generated_at=0,
            stale_after_seconds=30,
            products=(product(0),),
        )
    )
    assert fresh.products[0].source.freshness is Freshness.LIVE

    now[0] = 11

    def broken():
        raise OSError("/secret/path")

    stale = cache.get(broken)

    assert stale.products[0].source.freshness is Freshness.STALE
    assert stale.source_errors[-1].source.source == "snapshot_cache"
    assert stale.source_errors[-1].message == "Snapshot refresh failed: OSError"
    assert "/secret/path" not in repr(stale)
