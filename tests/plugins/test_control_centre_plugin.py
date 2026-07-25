"""Control Centre dashboard plugin API contract tests."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hermes_cli.profiles import ProfileInfo

from control_centre.schema import (
    AgentSummary,
    ControlCentreSnapshot,
    Freshness,
    ProductSummary,
    SourceRef,
)
from control_centre.snapshot import SnapshotCache


def load_plugin():
    root = Path(__file__).resolve().parents[2]
    plugin_file = root / "plugins" / "control-centre" / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location(
        "hermes_dashboard_plugin_control_centre_test",
        plugin_file,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def snapshot(tmp_path: Path) -> ControlCentreSnapshot:
    return ControlCentreSnapshot(
        generated_at=10,
        stale_after_seconds=30,
        products=(
            ProductSummary(
                source=SourceRef("registry", "hermes", 10, Freshness.LIVE),
                product_id="hermes",
                name="Hermes",
                configuration_state="configured",
                repositories=(
                    {
                        "id": "core",
                        "path": str(tmp_path / "private-repository"),
                        "remote": "https://example.invalid/hermes.git",
                    },
                ),
                knowledge_roots=(str(tmp_path / "private-vault"),),
            ),
        ),
        agents=(
            AgentSummary(
                source=SourceRef("profiles", "tobias", 10, Freshness.LIVE),
                profile="tobias",
                state="working",
                workspace=str(tmp_path / "private-worktree"),
            ),
        ),
    )


def client_for(module) -> TestClient:
    app = FastAPI()
    app.include_router(module.router, prefix="/api/plugins/control-centre")
    return TestClient(app)


def test_manifest_declares_plugin_edge_and_get_only_api():
    module = load_plugin()
    manifest = module.PLUGIN_ROOT / "manifest.json"
    data = __import__("json").loads(manifest.read_text(encoding="utf-8"))

    assert data["name"] == "control-centre"
    assert data["label"] == "Control Centre"
    assert data["icon"] == "Activity"
    assert data["version"] == "0.1.0"
    assert data["tab"]["path"] == "/control-centre"
    assert data["tab"]["position"] == "before:sessions"
    assert data["entry"] == "dist/index.js"
    assert data["css"] == "dist/style.css"
    assert data["api"] == "plugin_api.py"
    methods = {
        method
        for route in module.router.routes
        for method in getattr(route, "methods", set())
    }
    assert methods == {"GET"}


def test_snapshot_endpoint_has_stable_shape_headers_and_redacted_paths(tmp_path):
    module = load_plugin()
    module._cache = SnapshotCache(ttl_seconds=10)
    module._snapshot_builder = lambda: snapshot(tmp_path)
    client = client_for(module)

    response = client.get("/api/plugins/control-centre/snapshot")

    assert response.status_code == 200
    assert set(response.json()) == {
        "schema_version",
        "generated_at",
        "stale_after_seconds",
        "products",
        "attention",
        "agents",
        "builds",
        "source_errors",
        "setup_guidance",
    }
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    rendered = response.text
    assert str(tmp_path) not in rendered
    assert "workspace" not in response.json()["agents"][0]
    assert "path" not in response.json()["products"][0]["repositories"][0]
    assert "knowledge_roots" not in response.json()["products"][0]


def test_section_endpoints_are_stable_subsets(tmp_path):
    module = load_plugin()
    module._cache = SnapshotCache(ttl_seconds=10)
    module._snapshot_builder = lambda: snapshot(tmp_path)
    client = client_for(module)

    expected = {
        "attention": list,
        "products": list,
        "agents": list,
        "builds": list,
    }
    for path, value_type in expected.items():
        response = client.get(f"/api/plugins/control-centre/{path}")
        assert response.status_code == 200
        assert isinstance(response.json()[path], value_type)
        assert response.headers["cache-control"] == "private, no-store"

    health = client.get("/api/plugins/control-centre/health")
    assert health.status_code == 200
    assert set(health.json()) == {"status", "generated_at", "sources"}


def test_cache_avoids_duplicate_snapshot_builds(tmp_path):
    module = load_plugin()
    module._cache = SnapshotCache(ttl_seconds=10)
    calls = 0

    def builder():
        nonlocal calls
        calls += 1
        return snapshot(tmp_path)

    module._snapshot_builder = builder
    client = client_for(module)

    assert client.get("/api/plugins/control-centre/snapshot").status_code == 200
    assert client.get("/api/plugins/control-centre/products").status_code == 200
    assert client.get("/api/plugins/control-centre/agents").status_code == 200
    assert calls == 1


def test_uncached_builder_failure_is_explicit_and_does_not_leak_exception_text():
    module = load_plugin()
    module._cache = SnapshotCache(ttl_seconds=10)

    def broken():
        raise RuntimeError("token=secret-value /private/path")

    module._snapshot_builder = broken
    client = client_for(module)

    response = client.get("/api/plugins/control-centre/snapshot")

    assert response.status_code == 200
    assert response.json()["products"] == []
    assert response.json()["source_errors"][0]["message"] == (
        "Snapshot unavailable: RuntimeError"
    )
    assert "secret-value" not in response.text
    assert "/private/path" not in response.text


def test_profiles_remain_visible_when_kanban_activity_is_unavailable(
    tmp_path, monkeypatch
):
    module = load_plugin()

    def broken_connect():
        raise RuntimeError("token=secret-value /private/path")

    monkeypatch.setattr(module, "_open_kanban_read_only", broken_connect)
    monkeypatch.setattr(
        "control_centre.adapters.profiles.list_profiles",
        lambda: [
            ProfileInfo(
                "tobias",
                tmp_path / "tobias",
                False,
                False,
                description="Mechanic",
            )
        ],
    )

    contribution = module._profiles_contribution(observed_at=100.0)

    assert [agent.profile for agent in contribution.agents] == ["tobias"]
    assert contribution.agents[0].state == "idle"
    assert len(contribution.errors) == 1
    warning = contribution.errors[0]
    assert warning.severity == "warning"
    assert warning.source.source_id == "activity"
    assert warning.message == "Agent activity unavailable: RuntimeError"
    assert warning.recovery == "Open Kanban and inspect board health"
    assert "secret-value" not in warning.message
    assert "/private/path" not in warning.message


def test_read_only_kanban_open_does_not_create_a_missing_database(
    tmp_path, monkeypatch
):
    missing = tmp_path / "absent" / "kanban.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(missing))
    module = load_plugin()

    with pytest.raises(sqlite3.OperationalError):
        module._open_kanban_read_only()

    assert not missing.exists()
    assert not missing.parent.exists()


def test_control_centre_kanban_connection_rejects_writes(tmp_path, monkeypatch):
    path = tmp_path / "kanban.db"
    module = load_plugin()
    writable = module.kanban_db.connect(path)
    try:
        task_id = module.kanban_db.create_task(
            writable, title="Fixture", assignee="tobias"
        )
    finally:
        writable.close()
    before = path.read_bytes()
    monkeypatch.setenv("HERMES_KANBAN_DB", str(path))

    readonly = module._open_kanban_read_only()
    try:
        assert readonly.execute("PRAGMA query_only").fetchone()[0] == 1
        assert module.kanban_db.get_task(readonly, task_id) is not None
        with pytest.raises(sqlite3.OperationalError):
            readonly.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    finally:
        readonly.close()

    assert path.read_bytes() == before
