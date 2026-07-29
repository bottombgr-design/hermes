"""Regression tests for the TUI resumable-session history."""

from __future__ import annotations

from contextlib import contextmanager

from tui_gateway import server


class _SessionDB:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[dict] = []

    def list_sessions_rich(self, **kwargs) -> list[dict]:
        self.calls.append(kwargs)
        return self.rows


def _with_db(monkeypatch, db: _SessionDB) -> None:
    @contextmanager
    def profile_db(_params: dict):
        yield db

    monkeypatch.setattr(server, "_profile_db", profile_db)


def test_session_list_excludes_cron_and_tool_runs(monkeypatch):
    db = _SessionDB([
        {"id": "cron-newest", "source": "cron", "title": "cron", "preview": "", "started_at": 3},
        {"id": "tool-newer", "source": "tool", "title": "tool", "preview": "", "started_at": 2},
        {"id": "human", "source": "cli", "title": "Human chat", "preview": "continue this", "started_at": 1},
    ])
    _with_db(monkeypatch, db)

    response = server._methods["session.list"]("request", {"limit": 10})

    assert response["result"]["sessions"] == [{
        "id": "human",
        "title": "Human chat",
        "preview": "continue this",
        "started_at": 1,
        "message_count": 0,
        "source": "cli",
    }]


def test_most_recent_skips_cron_and_tool_runs(monkeypatch):
    db = _SessionDB([
        {"id": "cron-newest", "source": "cron", "title": "cron", "started_at": 3},
        {"id": "tool-newer", "source": "tool", "title": "tool", "started_at": 2},
        {"id": "human", "source": "cli", "title": "Human chat", "started_at": 1},
    ])
    _with_db(monkeypatch, db)

    response = server._methods["session.most_recent"]("request", {})

    assert response["result"] == {
        "session_id": "human",
        "title": "Human chat",
        "started_at": 1,
        "source": "cli",
    }
