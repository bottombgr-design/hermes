"""Tests for _append_model_switch_marker role semantics (issue #48338).

The model switch marker is tagged and uses role="system" for correct transcript
semantics. The pre-call sanitizer demotes only tagged Hermes system markers to
role="user" for strict-provider compatibility; persisted transcript and Desktop
rendering keep the system role.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from tui_gateway.server import _append_model_switch_marker


class TestAppendModelSwitchMarkerRole:
    """Verify the marker uses role='system' (demoted to 'user' at API call time)."""

    def test_marker_uses_system_role(self) -> None:
        """The history entry must be role='system' for correct semantics."""
        session: dict = {"session_key": "test-session", "history": []}
        _append_model_switch_marker(session, model="gpt-4o", provider="openai")
        assert len(session["history"]) == 1
        entry = session["history"][0]
        assert entry["role"] == "system", (
            f"Expected role='system' but got role='{entry['role']}'. "
            "The sanitizer demotes to 'user' at API call time (#48338)."
        )
        assert entry["_hermes_internal_system_marker"] is True



    def test_no_marker_for_none_session(self) -> None:
        """None session should be a no-op."""
        _append_model_switch_marker(None, model="gpt-4o", provider="openai")



    def test_marker_role_after_turns(self) -> None:
        """Persist tagged model-switch markers with the system role."""
        db = MagicMock()
        session: dict = {
            "session_key": "sess-1",
            "history": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
            "history_version": 7,
            "agent": SimpleNamespace(_session_db=db),
        }
        _append_model_switch_marker(
            session, model="qwen3.6-35b", provider="vllm"
        )
        marker = session["history"][-1]
        assert marker["role"] == "system"
        assert marker["_hermes_internal_system_marker"] is True
        assert session["history_version"] == 8
        db.append_message.assert_called_once_with(
            session_id="sess-1",
            role="system",
            content=marker["content"],
            internal_system_marker=True,
        )
