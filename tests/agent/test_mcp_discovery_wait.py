"""Regression test: agent_init waits for MCP discovery before tool snapshot (#73739)."""

from __future__ import annotations

from pathlib import Path

# Read agent_init.py source directly to avoid heavy import chain (requests,
# certifi, etc.) which may not be available in minimal test environments.
_AGENT_INIT_SOURCE = (
    Path(__file__).resolve().parents[2] / "agent" / "agent_init.py"
).read_text(encoding="utf-8")


def test_agent_init_waits_for_mcp_discovery_before_tool_snapshot():
    """#73739: agent_init must call wait_for_mcp_discovery() BEFORE
    get_tool_definitions() so that background MCP discovery (npx/stdio
    servers exceeding mcp_discovery_timeout) completes before the tool
    snapshot is built.

    Without the wait, the snapshot races against the discovery thread:
    - tool_search=true masks this (bridge re-queries at call time)
    - tool_search=false exposes it (one-shot path never re-fetches)

    This is a source-ordering contract test: the wait must textually
    precede the snapshot call in agent_init's init_agent function.
    """
    wait_pos = _AGENT_INIT_SOURCE.find("wait_for_mcp_discovery")
    get_defs_pos = _AGENT_INIT_SOURCE.find("get_tool_definitions(")
    assert wait_pos != -1, "wait_for_mcp_discovery not found in agent_init"
    assert get_defs_pos != -1, "get_tool_definitions not found in agent_init"
    assert wait_pos < get_defs_pos, (
        "wait_for_mcp_discovery must appear BEFORE get_tool_definitions "
        "in agent_init to ensure MCP tools are registered before the "
        "tool snapshot is built (#73739)"
    )


def test_agent_init_mcp_wait_is_defensive():
    """The wait_for_mcp_discovery import in agent_init must be wrapped in
    try/except so non-CLI entry points (gateway, cron, ACP) that don't
    have hermes_cli.mcp_startup available are not broken."""
    wait_idx = _AGENT_INIT_SOURCE.find(
        "from hermes_cli.mcp_startup import wait_for_mcp_discovery"
    )
    assert wait_idx != -1, "wait_for_mcp_discovery import not found in agent_init"
    # Check it's inside a try block (look backwards for 'try:')
    preceding = _AGENT_INIT_SOURCE[max(0, wait_idx - 200):wait_idx]
    assert "try:" in preceding, (
        "wait_for_mcp_discovery import must be inside a try/except block "
        "for defensive import (non-CLI entry points may lack hermes_cli)"
    )
