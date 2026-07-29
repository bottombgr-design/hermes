"""Regression tests for runtime plugin disable gating.

Covers two residual bypasses addressed in the PR:

1. Plugin API routes mounted at startup remain callable even after the
   plugin is added to ``plugins.disabled`` at runtime.  The new
   ``_plugin_api_runtime_gate`` middleware blocks these requests.

2. Bundled plugin assets were served from the unauthenticated
   ``/dashboard-plugins/{name}/{path}`` route even when the bundled
   plugin was in ``plugins.disabled``.  The updated ``serve_plugin_asset``
   now applies the disabled check to bundled plugins too.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

from hermes_cli import web_server


@pytest.fixture(autouse=True)
def _reset_plugin_cache():
    """Bust the plugin cache before and after each test."""
    web_server._dashboard_plugins_cache = None
    web_server._dashboard_plugin_entries_cache = None
    web_server._dashboard_plugin_entries_loader = None
    yield
    web_server._dashboard_plugins_cache = None
    web_server._dashboard_plugin_entries_cache = None
    web_server._dashboard_plugin_entries_loader = None


def test_dashboard_runtime_entry_index_is_cached_until_rescan(
    monkeypatch,
    tmp_path,
):
    from hermes_cli import plugins_cmd

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    entry = (
        "example",
        "1.0.0",
        "",
        "bundled",
        plugin_dir,
        "tools/example",
        "standalone",
    )
    discovery_calls = []

    def _discover():
        discovery_calls.append(True)
        return [entry]

    monkeypatch.setattr(plugins_cmd, "_discover_all_plugins", _discover)
    monkeypatch.setattr(web_server, "_discover_dashboard_plugins", lambda: [])

    first = web_server._dashboard_plugin_entries_by_path()
    second = web_server._dashboard_plugin_entries_by_path()

    assert first == second
    assert discovery_calls == [True]

    web_server._get_dashboard_plugins(force_rescan=True)
    refreshed = web_server._dashboard_plugin_entries_by_path()

    assert refreshed == first
    assert discovery_calls == [True, True]


def test_dashboard_runtime_entry_index_falls_back_when_discovery_fails(
    monkeypatch,
):
    from hermes_cli import plugins_cmd

    discovery_calls = []

    def _discover():
        discovery_calls.append(True)
        raise RuntimeError("broken manifest")

    monkeypatch.setattr(plugins_cmd, "_discover_all_plugins", _discover)

    assert web_server._dashboard_plugin_entries_by_path() == {}
    assert web_server._dashboard_plugin_entries_by_path() == {}
    assert discovery_calls == [True]


@pytest.fixture
def test_client(monkeypatch, tmp_path):
    """Set up a Starlette TestClient with auth bypassed."""
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    # Isolate HERMES_HOME so config reads go to our tmp.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True)

    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user_plugin(tmp_path, name="hot"):
    """Create a minimal user plugin with a JS asset."""
    dashboard_dir = tmp_path / "plugins" / name / "dashboard"
    dashboard_dir.mkdir(parents=True)
    dist_dir = dashboard_dir / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.js").write_text("console.log('hello');")
    (dashboard_dir / "manifest.json").write_text(json.dumps({
        "name": name,
        "label": name.title(),
        "entry": "dist/index.js",
    }))
    return dashboard_dir


def _make_bundled_plugin(tmp_path, name="bundledx"):
    """Create a minimal bundled plugin with a JS asset."""
    dashboard_dir = tmp_path / "bundled" / name / "dashboard"
    dashboard_dir.mkdir(parents=True)
    dist_dir = dashboard_dir / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.js").write_text("console.log('bundled');")
    (dashboard_dir / "manifest.json").write_text(json.dumps({
        "name": name,
        "label": name.title(),
        "entry": "dist/index.js",
    }))
    return dashboard_dir


def test_truthy_safe_mode_skips_user_plugin_api_import(monkeypatch, tmp_path):
    plugin_dir = tmp_path / "plugin"
    dashboard_dir = plugin_dir / "dashboard"
    dashboard_dir.mkdir(parents=True)
    (dashboard_dir / "plugin_api.py").write_text(
        "raise AssertionError('must not import')\n",
        encoding="utf-8",
    )
    plugin = {
        "name": "unsafe",
        "source": "user",
        "_plugin_dir": str(plugin_dir),
        "_dir": str(dashboard_dir),
        "_api_file": "plugin_api.py",
    }
    entry = (
        "unsafe",
        "1.0.0",
        "",
        "user",
        plugin_dir,
        "tools/unsafe",
        "standalone",
    )
    monkeypatch.setenv("HERMES_SAFE_MODE", "true")

    with patch.object(web_server, "_get_dashboard_plugins", return_value=[plugin]), \
         patch.object(
             web_server,
             "_dashboard_plugin_entries_by_path",
             return_value={str(plugin_dir.resolve()): entry},
         ), \
         patch(
             "hermes_cli.plugins_cmd._get_enabled_set",
             return_value={"tools/unsafe"},
         ), \
         patch(
             "hermes_cli.plugins_cmd._get_disabled_set",
             return_value=set(),
         ), \
         patch.object(
             web_server.importlib.util,
             "spec_from_file_location",
         ) as import_spec:
        web_server._mount_plugin_api_routes()

    import_spec.assert_not_called()


def test_dashboard_only_fallback_honors_runtime_policy(monkeypatch):
    from hermes_cli import plugin_config_state

    plugin = {
        "name": "allowlisted-dashboard",
        "source": "user",
    }

    monkeypatch.setenv("HERMES_SAFE_MODE", "true")
    assert web_server._dashboard_plugin_runtime_status(
        plugin,
        {"allowlisted-dashboard"},
        set(),
        {},
    ) == "not enabled"

    monkeypatch.delenv("HERMES_SAFE_MODE")
    assert web_server._dashboard_plugin_runtime_status(
        plugin,
        {"allowlisted-dashboard"},
        {plugin_config_state._POLICY_FAIL_CLOSED_SENTINEL},
        {},
    ) == "not enabled"


# ---------------------------------------------------------------------------
# Test 1: Runtime-disabled user plugin API routes return 404
# ---------------------------------------------------------------------------


class TestPluginApiRuntimeGate:
    """After a user plugin is disabled at runtime, its mounted API routes
    must return 404 — not 200 — even though the router was already
    included at startup.  The _plugin_api_runtime_gate middleware enforces
    this at request time."""

    @pytest.mark.asyncio
    async def test_middleware_blocks_disabled_user_plugin(self):
        """Middleware returns 404 for a user plugin added to disabled set."""
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        fake_plugin = {
            "name": "hot",
            "source": "user",
        }

        # Simulate a request to /api/plugins/hot/probe
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/plugins/hot/probe",
            "query_string": b"",
            "headers": [],
            "state": {"token_authenticated": True},
        }
        request = Request(scope)

        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        with patch.object(web_server, "_get_dashboard_plugins", return_value=[fake_plugin]), \
             patch("hermes_cli.plugins_cmd._get_enabled_set", return_value={"hot"}), \
             patch("hermes_cli.plugins_cmd._get_disabled_set", return_value={"hot"}):
            response = await web_server._plugin_api_runtime_gate(request, call_next)

        assert response.status_code == 404
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_middleware_uses_canonical_plugin_key(self, tmp_path):
        """An enabled canonical key keeps a differently named API route live."""
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        plugin_dir = tmp_path / "runtime-plugin"
        fake_plugin = {
            "name": "dashboard-label",
            "source": "user",
            "_plugin_dir": str(plugin_dir),
        }
        runtime_entry = (
            "runtime-name",
            "1.0.0",
            "",
            "user",
            plugin_dir,
            "tools/runtime-key",
            "standalone",
        )
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/plugins/dashboard-label/probe",
            "query_string": b"",
            "headers": [],
            "state": {"token_authenticated": True},
        }
        request = Request(scope)
        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        with patch.object(
            web_server,
            "_get_dashboard_plugins",
            return_value=[fake_plugin],
        ), patch(
            "hermes_cli.plugins_cmd._discover_all_plugins",
            return_value=[runtime_entry],
        ), patch(
            "hermes_cli.plugins_cmd._get_enabled_set",
            return_value={"tools/runtime-key"},
        ), patch(
            "hermes_cli.plugins_cmd._get_disabled_set",
            return_value=set(),
        ):
            response = await web_server._plugin_api_runtime_gate(
                request,
                call_next,
            )

        assert response.status_code == 200
        call_next.assert_awaited_once_with(request)

    @pytest.mark.asyncio
    async def test_middleware_blocks_unenabled_user_plugin(self):
        """Middleware returns 404 when user plugin not in enabled set."""
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        fake_plugin = {
            "name": "hot",
            "source": "user",
        }

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/plugins/hot/probe",
            "query_string": b"",
            "headers": [],
            "state": {"token_authenticated": True},
        }
        request = Request(scope)

        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        with patch.object(web_server, "_get_dashboard_plugins", return_value=[fake_plugin]), \
             patch("hermes_cli.plugins_cmd._get_enabled_set", return_value=set()), \
             patch("hermes_cli.plugins_cmd._get_disabled_set", return_value=set()):
            response = await web_server._plugin_api_runtime_gate(request, call_next)

        assert response.status_code == 404
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_middleware_passes_enabled_user_plugin(self):
        """Middleware passes through for an enabled user plugin."""
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        fake_plugin = {
            "name": "hot",
            "source": "user",
        }

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/plugins/hot/probe",
            "query_string": b"",
            "headers": [],
            "state": {"token_authenticated": True},
        }
        request = Request(scope)

        expected_resp = JSONResponse({"ok": True})
        call_next = AsyncMock(return_value=expected_resp)

        with patch.object(web_server, "_get_dashboard_plugins", return_value=[fake_plugin]), \
             patch("hermes_cli.plugins_cmd._get_enabled_set", return_value={"hot"}), \
             patch("hermes_cli.plugins_cmd._get_disabled_set", return_value=set()):
            response = await web_server._plugin_api_runtime_gate(request, call_next)

        assert response is expected_resp
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_middleware_blocks_disabled_bundled_plugin(self):
        """Middleware returns 404 for a bundled plugin in disabled set."""
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        fake_plugin = {
            "name": "bundledx",
            "source": "bundled",
        }

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/plugins/bundledx/probe",
            "query_string": b"",
            "headers": [],
            "state": {"token_authenticated": True},
        }
        request = Request(scope)

        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        with patch.object(web_server, "_get_dashboard_plugins", return_value=[fake_plugin]), \
             patch("hermes_cli.plugins_cmd._get_enabled_set", return_value=set()), \
             patch("hermes_cli.plugins_cmd._get_disabled_set", return_value={"bundledx"}):
            response = await web_server._plugin_api_runtime_gate(request, call_next)

        assert response.status_code == 404
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_middleware_passes_enabled_bundled_plugin(self):
        """Middleware passes through for a bundled plugin not in disabled set."""
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        fake_plugin = {
            "name": "bundledx",
            "source": "bundled",
        }

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/plugins/bundledx/probe",
            "query_string": b"",
            "headers": [],
            "state": {"token_authenticated": True},
        }
        request = Request(scope)

        expected_resp = JSONResponse({"ok": True})
        call_next = AsyncMock(return_value=expected_resp)

        with patch.object(web_server, "_get_dashboard_plugins", return_value=[fake_plugin]), \
             patch("hermes_cli.plugins_cmd._get_enabled_set", return_value=set()), \
             patch("hermes_cli.plugins_cmd._get_disabled_set", return_value=set()):
            response = await web_server._plugin_api_runtime_gate(request, call_next)

        assert response is expected_resp
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_middleware_passes_non_plugin_api_routes(self):
        """Middleware ignores non-plugin API routes."""
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/status",
            "query_string": b"",
            "headers": [],
            "state": {"token_authenticated": True},
        }
        request = Request(scope)

        expected_resp = JSONResponse({"ok": True})
        call_next = AsyncMock(return_value=expected_resp)

        response = await web_server._plugin_api_runtime_gate(request, call_next)

        assert response is expected_resp
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_middleware_unknown_plugin_defaults_to_user_blocks(self):
        """Unknown plugin name (not in discovery cache) is treated as user
        plugin and blocked when not enabled."""
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/plugins/unknown/action",
            "query_string": b"",
            "headers": [],
            "state": {"token_authenticated": True},
        }
        request = Request(scope)

        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        with patch.object(web_server, "_get_dashboard_plugins", return_value=[]), \
             patch("hermes_cli.plugins_cmd._get_enabled_set", return_value=set()), \
             patch("hermes_cli.plugins_cmd._get_disabled_set", return_value=set()):
            response = await web_server._plugin_api_runtime_gate(request, call_next)

        assert response.status_code == 404
        call_next.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: Disabled bundled plugin assets return 404
# ---------------------------------------------------------------------------


class TestBundledPluginAssetGate:
    """Bundled plugins in ``plugins.disabled`` must have their static
    assets blocked — not just hidden from the listing endpoint."""

    def test_bundled_asset_returns_404_when_disabled(self, test_client, tmp_path, monkeypatch):
        """A disabled bundled plugin's JS asset must return 404."""
        plugin_dir = _make_bundled_plugin(tmp_path, "bundledx")

        fake_plugin = {
            "name": "bundledx",
            "label": "Bundledx",
            "source": "bundled",
            "entry": "dist/index.js",
            "_dir": str(plugin_dir),
        }

        with patch.object(web_server, "_get_dashboard_plugins", return_value=[fake_plugin]):
            # Sanity: asset is served when not disabled.
            with patch(
                "hermes_cli.plugins_cmd._get_enabled_set", return_value=set()
            ), patch(
                "hermes_cli.plugins_cmd._get_disabled_set", return_value=set()
            ):
                resp = test_client.get("/dashboard-plugins/bundledx/dist/index.js")
                assert resp.status_code == 200, (
                    "Sanity: bundled plugin asset should be served when not disabled"
                )

            # Disable it.
            with patch(
                "hermes_cli.plugins_cmd._get_enabled_set", return_value=set()
            ), patch(
                "hermes_cli.plugins_cmd._get_disabled_set", return_value={"bundledx"}
            ):
                resp = test_client.get("/dashboard-plugins/bundledx/dist/index.js")
                assert resp.status_code == 404, (
                    "Disabled bundled plugin asset must return 404"
                )

    def test_bundled_asset_served_when_not_disabled(self, test_client, tmp_path, monkeypatch):
        """Bundled plugin assets are served normally when not in disabled set."""
        plugin_dir = _make_bundled_plugin(tmp_path, "goodbundled")

        fake_plugin = {
            "name": "goodbundled",
            "label": "Good Bundled",
            "source": "bundled",
            "entry": "dist/index.js",
            "_dir": str(plugin_dir),
        }

        with patch.object(web_server, "_get_dashboard_plugins", return_value=[fake_plugin]):
            with patch(
                "hermes_cli.plugins_cmd._get_enabled_set", return_value=set()
            ), patch(
                "hermes_cli.plugins_cmd._get_disabled_set", return_value=set()
            ):
                resp = test_client.get("/dashboard-plugins/goodbundled/dist/index.js")
                assert resp.status_code == 200

    def test_user_plugin_asset_still_gated(self, test_client, tmp_path, monkeypatch):
        """User plugins still require enabled set membership for assets."""
        plugin_dir = _make_user_plugin(tmp_path, "userplugin")

        fake_plugin = {
            "name": "userplugin",
            "label": "User Plugin",
            "source": "user",
            "entry": "dist/index.js",
            "_dir": str(plugin_dir),
        }

        with patch.object(web_server, "_get_dashboard_plugins", return_value=[fake_plugin]):
            # Not in enabled set → 404.
            with patch(
                "hermes_cli.plugins_cmd._get_enabled_set", return_value=set()
            ), patch(
                "hermes_cli.plugins_cmd._get_disabled_set", return_value=set()
            ):
                resp = test_client.get("/dashboard-plugins/userplugin/dist/index.js")
                assert resp.status_code == 404

            # In enabled set → 200.
            with patch(
                "hermes_cli.plugins_cmd._get_enabled_set", return_value={"userplugin"}
            ), patch(
                "hermes_cli.plugins_cmd._get_disabled_set", return_value=set()
            ):
                resp = test_client.get("/dashboard-plugins/userplugin/dist/index.js")
                assert resp.status_code == 200

    def test_user_asset_uses_canonical_plugin_key(
        self,
        test_client,
        tmp_path,
    ):
        """Dashboard display names must not replace runtime plugin keys."""
        dashboard_dir = _make_user_plugin(tmp_path, "dashboard-label")
        fake_plugin = {
            "name": "dashboard-label",
            "label": "Dashboard Label",
            "source": "user",
            "entry": "dist/index.js",
            "_plugin_dir": str(dashboard_dir.parent),
            "_dir": str(dashboard_dir),
        }
        runtime_entry = (
            "runtime-name",
            "1.0.0",
            "",
            "user",
            dashboard_dir.parent,
            "tools/runtime-key",
            "standalone",
        )

        with patch.object(
            web_server,
            "_get_dashboard_plugins",
            return_value=[fake_plugin],
        ), patch(
            "hermes_cli.plugins_cmd._discover_all_plugins",
            return_value=[runtime_entry],
        ), patch(
            "hermes_cli.plugins_cmd._get_enabled_set",
            return_value={"tools/runtime-key"},
        ), patch(
            "hermes_cli.plugins_cmd._get_disabled_set",
            return_value=set(),
        ):
            resp = test_client.get(
                "/dashboard-plugins/dashboard-label/dist/index.js"
            )

        assert resp.status_code == 200
