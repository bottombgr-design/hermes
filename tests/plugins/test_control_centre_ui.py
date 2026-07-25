"""Behavior harness for the Control Centre plugin IIFE."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins" / "control-centre" / "dashboard" / "src" / "index.tsx"
DIST = ROOT / "plugins" / "control-centre" / "dashboard" / "dist" / "index.js"
STYLE = ROOT / "plugins" / "control-centre" / "dashboard" / "dist" / "style.css"


def run_harness(payload=None, error=None):
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const bundle = fs.readFileSync(process.argv[1], "utf8");
const fixture = JSON.parse(process.argv[2]);
let registered = null;
let states = [];
let hookIndex = 0;
let effects = [];
function createElement(type, props, ...children) {
  const merged = Object.assign({}, props || {}, { children });
  if (typeof type === "function") return type(merged);
  return { type, props: merged, children };
}
function useState(initial) {
  const index = hookIndex++;
  if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
  return [states[index], function (value) {
    states[index] = typeof value === "function" ? value(states[index]) : value;
  }];
}
function useEffect(effect) { effects.push(effect); }
function useCallback(fn) { return fn; }
function useMemo(fn) { return fn(); }
function component(tag) {
  return function (props) { return createElement(tag, props, ...(props.children || [])); };
}
global.window = {
  __HERMES_PLUGIN_SDK__: {
    React: { createElement },
    hooks: { useState, useEffect, useCallback, useMemo },
    components: {
      Card: component("section"), CardContent: component("div"),
      Badge: component("span"), Button: component("button")
    },
    fetchJSON: function () {
      if (fixture.error) return Promise.reject(new Error(fixture.error));
      return Promise.resolve(fixture.payload);
    },
    utils: { cn: (...parts) => parts.filter(Boolean).join(" ") }
  },
  __HERMES_PLUGINS__: { register: function (_name, value) { registered = value; } }
};
global.document = {};
vm.runInThisContext(bundle, { filename: "control-centre.js" });
if (!registered) throw new Error("plugin did not register");
function render() { hookIndex = 0; effects = []; return registered({}); }
function text(node) {
  if (node === null || node === undefined || node === false) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  return (node.children || []).map(text).join(" ");
}
function findButton(node, label) {
  if (!node || typeof node !== "object") return null;
  if (node.type === "button" && text(node).trim() === label) return node;
  for (const child of node.children || []) {
    const found = findButton(child, label);
    if (found) return found;
  }
  return null;
}
function collect(node, key) {
  if (!node || typeof node !== "object") return [];
  const own = node.props && node.props[key] ? [String(node.props[key])] : [];
  return own.concat(...(node.children || []).map((child) => collect(child, key)));
}
function findAll(node, type) {
  if (!node || typeof node !== "object") return [];
  const own = node.type === type ? [node] : [];
  return own.concat(...(node.children || []).map((child) => findAll(child, type)));
}
(async function () {
  const initial = render();
  const initialText = text(initial);
  const pending = effects.slice();
  for (const effect of pending) effect();
  await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
  const loaded = render();
  const loadedText = text(loaded);
  const productSelect = findAll(loaded, "select")[0];
  if (productSelect && productSelect.props.onChange) {
    productSelect.props.onChange({ target: { value: "hermes" } });
  }
  const productFiltered = render();
  const agentsButton = findButton(loaded, "Agents");
  if (agentsButton && agentsButton.props.onClick) agentsButton.props.onClick();
  const agents = render();
  const productsButton = findButton(agents, "Products");
  if (productsButton && productsButton.props.onClick) productsButton.props.onClick();
  const products = render();
  console.log(JSON.stringify({
    initialText,
    loadedText,
    agentsText: text(agents),
    productsText: text(products),
    productFilteredText: text(productFiltered),
    loadedTree: loaded,
    agentsHrefs: collect(agents, "href"),
    productClasses: collect(products, "className")
  }));
})().catch((error) => { console.error(error); process.exit(1); });
"""
    fixture = {"payload": payload, "error": error}
    result = subprocess.run(
        ["node", "-e", harness, str(DIST), json.dumps(fixture)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def full_payload():
    source = {
        "source": "kanban",
        "source_id": "task-1",
        "observed_at": 10,
        "freshness": "live",
        "href": "/kanban",
    }
    return {
        "schema_version": "control-centre/v1",
        "generated_at": 10,
        "stale_after_seconds": 30,
        "products": [
            {
                "source": {**source, "source": "registry", "source_id": "hermes"},
                "product_id": "hermes",
                "name": "Hermes Agent",
                "configuration_state": "configured",
                "repositories": [{"id": "core", "remote": "https://example.invalid"}],
                "kanban_href": "/kanban",
                "chat_href": "/chat",
                "sessions_href": "/sessions",
                "environments": [{"name": "production", "production": True}],
                "recovery": None,
            }
        ],
        "attention": [
            {
                "source": source,
                "severity": "critical",
                "title": "Worker blocked",
                "detail": "Needs operator input",
                "product_id": "hermes",
                "agent_id": "tobias",
            }
        ],
        "agents": [
            {
                "source": {**source, "source": "profiles", "source_id": "tobias"},
                "profile": "tobias",
                "state": "working",
                "role": "Mechanic",
                "task_id": "task-1",
                "run_id": "7",
                "last_heartbeat": 9,
                "lease_expires_at": 99,
                "chat_href": "/chat?profile=tobias",
                "diagnostics": [],
            }
        ],
        "builds": [
            {
                "source": {**source, "source": "agent_factory", "source_id": "rel-1"},
                "product_id": "hermes",
                "release_id": "rel-1",
                "state": "UNKNOWN",
                "manifest_hash": "abc",
                "layer_verdicts": {"layer4_kanban": "UNKNOWN"},
                "review_state": "pending",
                "deployment_outcome": None,
            }
        ],
        "source_errors": [
            {
                "source": {**source, "source": "sessions", "source_id": "sessions"},
                "message": "Session metadata unavailable",
                "recovery": "Open Sessions",
                "severity": "warning",
            }
        ],
        "setup_guidance": [],
    }


def test_source_uses_host_sdk_authenticated_fetch_and_registers_read_only_page():
    text = SOURCE.read_text(encoding="utf-8")
    assert "SDK.fetchJSON" in text
    assert 'register("control-centre"' in text
    assert 'method: "POST"' not in text
    assert 'method: "PUT"' not in text
    assert 'method: "PATCH"' not in text
    assert 'method: "DELETE"' not in text
    assert "__HERMES_SESSION_TOKEN__" not in text


def test_loading_data_and_filter_interaction_are_exercised():
    result = run_harness(payload=full_payload())

    assert "Loading Control Centre" in result["initialText"]
    assert "Worker blocked" in result["loadedText"]
    assert "Hermes Agent" in result["loadedText"]
    assert "Tobias" in result["agentsText"] or "tobias" in result["agentsText"]
    assert "Mechanic" in result["agentsText"]


def test_product_filter_keeps_unassociated_agents_diagnostics_and_builds_visible():
    payload = full_payload()
    payload["attention"].append(
        {
            "source": {
                **payload["attention"][0]["source"],
                "source_id": "task-2:diagnostic:loop-stuck",
            },
            "severity": "critical",
            "title": "Unassociated diagnostic",
            "detail": "No product mapping is available",
            "product_id": None,
            "agent_id": "tobias",
        }
    )
    payload["agents"][0]["product_id"] = None
    payload["builds"][0]["product_id"] = "unassigned"

    result = run_harness(payload=payload)

    assert "Unassociated diagnostic" in result["productFilteredText"]
    assert "Mechanic" in result["productFilteredText"]
    assert "rel-1" in result["productFilteredText"]


def test_empty_catalog_explains_setup_instead_of_rendering_blank():
    payload = full_payload()
    payload.update(
        products=[],
        attention=[],
        agents=[],
        builds=[],
        source_errors=[],
        setup_guidance=["Create products.yaml to configure products."],
    )

    result = run_harness(payload=payload)

    assert "No products configured" in result["loadedText"]
    assert "Create products.yaml to configure products." in result["loadedText"]


def test_fetch_failure_is_an_accessible_explicit_error():
    result = run_harness(error="503 unavailable")

    assert "Control Centre unavailable" in result["loadedText"]
    assert "503 unavailable" in result["loadedText"]
    tree = result["loadedTree"]
    assert tree["props"]["role"] == "alert"


def test_product_and_agent_inspectors_render_safe_explicit_detail():
    result = run_harness(payload=full_payload())

    assert "Not configured" in result["agentsText"]
    assert "/chat?profile=tobias" in result["agentsHrefs"]
    assert "production" in result["productsText"].lower()
    assert "Production environment" in result["productsText"]
    assert any(
        "hermes-control-production" in value
        for value in result["productClasses"]
    )
    assert "undefined" not in result["agentsText"]
    assert "undefined" not in result["productsText"]


def test_styles_cover_responsive_grid_focus_and_reduced_motion():
    css = STYLE.read_text(encoding="utf-8")
    assert "@media (max-width:" in css
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
    assert ".hermes-control-grid" in css
