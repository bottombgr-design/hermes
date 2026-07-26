"""Best-effort "last used" timestamps for the Context dashboard/mobile view.

Derives real usage recency for MCP servers, messaging channels, and API keys
from ``state.db`` — never invents a timestamp. Everything here is read-only
(a ``mode=ro`` SQLite URI) and bounded (a row-count ``LIMIT`` on the message
scan) so it stays cheap enough for mobile pull-to-refresh even against a
large, long-lived database.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from tools.mcp_tool import MCP_TOOL_NAME_PREFIX, _MCP_NAME_DELIM

DEFAULT_LOOKBACK_MESSAGES = 50_000


def _split_mcp_server(tool_name: str) -> Optional[str]:
    """Return the server name embedded in an ``mcp__<server>__<tool>`` name.

    Uses the live ``mcp__``/``__`` convention from ``tools.mcp_tool`` rather
    than a hardcoded copy, so this stays correct if that convention changes.
    Returns ``None`` for non-MCP tool names.
    """
    if not tool_name.startswith(MCP_TOOL_NAME_PREFIX):
        return None
    remainder = tool_name[len(MCP_TOOL_NAME_PREFIX):]
    server, sep, _tool = remainder.partition(_MCP_NAME_DELIM)
    if not sep or not server:
        return None
    return server


def _iter_tool_call_names(tool_calls_json: str) -> Iterable[str]:
    """Yield each function name in a stored ``tool_calls`` JSON blob."""
    try:
        calls = json.loads(tool_calls_json)
    except (TypeError, ValueError):
        return
    if not isinstance(calls, list):
        return
    for call in calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function")
        name = function.get("name") if isinstance(function, dict) else call.get("name")
        if isinstance(name, str) and name:
            yield name


def _tool_to_unique_env_key() -> Dict[str, str]:
    """Map tool name -> env key, for tools backed by exactly one key.

    Built from ``OPTIONAL_ENV_VARS``' existing ``tools`` lists so it tracks
    that catalog automatically. Many tools (``web_search``, ``browser_navigate``,
    ...) are served by whichever of several configured providers wins at
    runtime — attributing those to any single key would be a guess, so they
    are excluded here rather than guessed wrong.
    """
    from hermes_cli.config import OPTIONAL_ENV_VARS

    tool_keys: Dict[str, list] = {}
    for key, info in OPTIONAL_ENV_VARS.items():
        for tool_name in info.get("tools") or ():
            tool_keys.setdefault(tool_name, []).append(key)
    return {tool: keys[0] for tool, keys in tool_keys.items() if len(keys) == 1}


def compute_context_last_used(
    *,
    home: Optional[Path] = None,
    lookback_messages: int = DEFAULT_LOOKBACK_MESSAGES,
    mcp_server_names: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Compute real last-used timestamps from ``state.db``.

    ``mcp_server_names``, when given, lets the message scan stop early once
    every configured server already has a hit, instead of always walking the
    full ``lookback_messages`` window.

    Returns:
        {
          "mcp": {"<server_name>": <unix_ts>, ...},
          "channels": {"<platform_id>": <unix_ts>, ...},
          "keys": {"<ENV_VAR>": <unix_ts>, ...},
          "computed_at": <unix_ts>,
        }
    """
    from hermes_constants import get_hermes_home

    resolved_home = Path(home) if home is not None else get_hermes_home()
    db_path = resolved_home / "state.db"
    result: Dict[str, Any] = {
        "mcp": {},
        "channels": {},
        "keys": {},
        "computed_at": time.time(),
    }
    if not db_path.exists():
        return result

    mcp_last: Dict[str, float] = {}
    tool_last: Dict[str, float] = {}
    channels: Dict[str, float] = {}
    pending_servers = set(mcp_server_names) if mcp_server_names else None

    conn = None
    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, timeout=1.0, isolation_level=None
        )
        conn.row_factory = sqlite3.Row

        # The inner LIMIT bounds rows actually scanned (not rows matched) to
        # `lookback_messages`, walking newest-first via the rowid/PK order so
        # a cold multi-GB history never triggers a full table scan.
        cursor = conn.execute(
            """
            SELECT tool_calls, timestamp FROM (
                SELECT tool_calls, timestamp, id FROM messages
                ORDER BY id DESC
                LIMIT ?
            )
            WHERE tool_calls IS NOT NULL
            """,
            (lookback_messages,),
        )
        for row in cursor:
            ts = row["timestamp"]
            if ts is None:
                continue
            for name in _iter_tool_call_names(row["tool_calls"]):
                server = _split_mcp_server(name)
                if server is not None:
                    if ts > mcp_last.get(server, 0.0):
                        mcp_last[server] = ts
                    if pending_servers is not None:
                        pending_servers.discard(server)
                elif ts > tool_last.get(name, 0.0):
                    tool_last[name] = ts
            if pending_servers is not None and not pending_servers:
                # Every configured MCP server already has a newest-first hit;
                # older messages can only add earlier (non-newest) timestamps
                # for MCP, so there's nothing left worth the extra scan.
                break

        for row in conn.execute(
            "SELECT source, MAX(COALESCE(ended_at, started_at)) AS ts "
            "FROM sessions GROUP BY source"
        ):
            if row["source"] and row["ts"] is not None:
                channels[row["source"]] = row["ts"]
    finally:
        if conn is not None:
            conn.close()

    keys: Dict[str, float] = {}
    for tool_name, env_key in _tool_to_unique_env_key().items():
        ts = tool_last.get(tool_name)
        if ts is not None:
            keys[env_key] = ts

    result["mcp"] = mcp_last
    result["channels"] = channels
    result["keys"] = keys
    return result
