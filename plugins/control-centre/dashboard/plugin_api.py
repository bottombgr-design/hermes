"""Read-only Control Centre dashboard plugin API.

Mounted by the dashboard plugin loader at ``/api/plugins/control-centre``.
Core dashboard authentication applies to every plugin route; this router adds
no bypass and exposes GET handlers only.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from hermes_cli import kanban_db
from hermes_constants import get_hermes_home

from control_centre.adapters.agent_factory import read_agent_factory_release
from control_centre.adapters.cron import read_cron_jobs
from control_centre.adapters.kanban import KanbanReadResult, read_kanban
from control_centre.adapters.profiles import read_profiles
from control_centre.adapters.sessions import read_sessions
from control_centre.registry import load_product_registry
from control_centre.schema import (
    ControlCentreSnapshot,
    Freshness,
    SourceError,
    SourceRef,
    snapshot_to_dict,
)
from control_centre.snapshot import (
    SnapshotCache,
    SnapshotContribution,
    assemble_snapshot,
)

PLUGIN_ROOT = Path(__file__).resolve().parent
router = APIRouter()
_cache = SnapshotCache(ttl_seconds=5)
_STALE_AFTER_SECONDS = 15

_RESPONSE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


def _registry_contribution(home: Path, observed_at: float) -> SnapshotContribution:
    result = load_product_registry(
        home / "control-centre" / "products.yaml",
        observed_at=observed_at,
        allowed_roots=(Path.home(), Path("/workspace")),
    )
    return SnapshotContribution(products=result.products, errors=result.errors)


def _open_kanban_read_only() -> sqlite3.Connection:
    path = kanban_db.kanban_db_path()
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
    except Exception:
        conn.close()
        raise
    return conn


def _kanban_contribution(observed_at: float) -> SnapshotContribution:
    conn = _open_kanban_read_only()
    try:
        result = read_kanban(
            conn,
            observed_at=observed_at,
            stale_after_seconds=_STALE_AFTER_SECONDS,
        )
    finally:
        conn.close()
    return SnapshotContribution(
        attention=result.attention,
        errors=result.errors,
    )


def _profiles_contribution(observed_at: float) -> SnapshotContribution:
    try:
        conn = _open_kanban_read_only()
        try:
            kanban = read_kanban(
                conn,
                observed_at=observed_at,
                stale_after_seconds=_STALE_AFTER_SECONDS,
            )
        finally:
            conn.close()
        errors = ()
    except Exception as exc:
        kanban = KanbanReadResult()
        errors = (
            SourceError(
                source=SourceRef(
                    source="profiles",
                    source_id="activity",
                    observed_at=observed_at,
                    freshness=Freshness.UNAVAILABLE,
                ),
                message=f"Agent activity unavailable: {type(exc).__name__}",
                recovery="Open Kanban and inspect board health",
                severity="warning",
            ),
        )
    return SnapshotContribution(
        agents=read_profiles(kanban, observed_at=observed_at),
        errors=errors,
    )


def _sessions_contribution(home: Path, observed_at: float) -> SnapshotContribution:
    result = read_sessions(home / "state.db", observed_at=observed_at)
    return SnapshotContribution(errors=result.errors)


def _cron_contribution(home: Path, observed_at: float) -> SnapshotContribution:
    result = read_cron_jobs(home / "cron" / "jobs.json", observed_at=observed_at)
    return SnapshotContribution(errors=result.errors)


def _factory_contribution(home: Path, observed_at: float) -> SnapshotContribution:
    root = home / "agent-factory" / "releases"
    if not root.is_dir():
        return SnapshotContribution()
    builds = []
    errors = []
    for release in sorted(path for path in root.iterdir() if path.is_dir()):
        result = read_agent_factory_release(
            release,
            product_id="unassigned",
            configured_root=root,
            observed_at=observed_at,
        )
        builds.extend(result.builds)
        errors.extend(result.errors)
    return SnapshotContribution(builds=tuple(builds), errors=tuple(errors))


def _build_live_snapshot() -> ControlCentreSnapshot:
    observed_at = time.time()
    home = get_hermes_home().resolve()
    return assemble_snapshot(
        {
            "agent_factory": lambda now: _factory_contribution(home, now),
            "cron": lambda now: _cron_contribution(home, now),
            "kanban": _kanban_contribution,
            "profiles": _profiles_contribution,
            "registry": lambda now: _registry_contribution(home, now),
            "sessions": lambda now: _sessions_contribution(home, now),
        },
        generated_at=observed_at,
        stale_after_seconds=_STALE_AFTER_SECONDS,
    )


_snapshot_builder = _build_live_snapshot


def _snapshot() -> ControlCentreSnapshot:
    try:
        return _cache.get(_snapshot_builder)
    except Exception as exc:
        observed_at = time.time()
        return ControlCentreSnapshot(
            generated_at=observed_at,
            stale_after_seconds=_STALE_AFTER_SECONDS,
            source_errors=(
                SourceError(
                    source=SourceRef(
                        source="control_centre",
                        source_id="snapshot",
                        observed_at=observed_at,
                        freshness=Freshness.UNAVAILABLE,
                    ),
                    message=f"Snapshot unavailable: {type(exc).__name__}",
                    recovery="Check Control Centre source health and retry",
                    severity="critical",
                ),
            ),
            setup_guidance=(
                "Create ~/.hermes/control-centre/products.yaml to configure products.",
            ),
        )


def _public_payload(snapshot: ControlCentreSnapshot) -> dict[str, Any]:
    payload = snapshot_to_dict(snapshot)
    for product in payload["products"]:
        product.pop("knowledge_roots", None)
        product["repositories"] = [
            {key: value for key, value in repository.items() if key != "path"}
            for repository in product["repositories"]
        ]
    for agent in payload["agents"]:
        agent.pop("workspace", None)
    return payload


def _response(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(payload, headers=_RESPONSE_HEADERS)


@router.get("/snapshot")
def snapshot_endpoint() -> JSONResponse:
    return _response(_public_payload(_snapshot()))


def _section(name: str) -> JSONResponse:
    payload = _public_payload(_snapshot())
    return _response(
        {
            name: payload[name],
            "generated_at": payload["generated_at"],
            "source_errors": payload["source_errors"],
        }
    )


@router.get("/attention")
def attention_endpoint() -> JSONResponse:
    return _section("attention")


@router.get("/products")
def products_endpoint() -> JSONResponse:
    return _section("products")


@router.get("/agents")
def agents_endpoint() -> JSONResponse:
    return _section("agents")


@router.get("/builds")
def builds_endpoint() -> JSONResponse:
    return _section("builds")


@router.get("/health")
def health_endpoint() -> JSONResponse:
    payload = _public_payload(_snapshot())
    sources: dict[str, str] = {}
    for section in ("products", "attention", "agents", "builds"):
        for item in payload[section]:
            ref = item["source"]
            sources[ref["source"]] = ref["freshness"]
    for item in payload["source_errors"]:
        ref = item["source"]
        sources[ref["source"]] = ref["freshness"]
    status = "ok" if not payload["source_errors"] else "degraded"
    return _response(
        {
            "status": status,
            "generated_at": payload["generated_at"],
            "sources": dict(sorted(sources.items())),
        }
    )
