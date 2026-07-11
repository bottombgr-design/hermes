"""Tests for _append_model_switch_marker role semantics (issue #48338).

The model switch marker uses role="system" for correct transcript semantics.
The pre-call sanitizer (sanitize_api_messages) demotes mid-conversation system
messages to role="user" for provider compatibility, so strict providers
(vLLM, Qwen) never see a mid-conversation system message on the wire — but
the stored transcript and Desktop rendering show the correct role.
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



    def test_no_marker_for_none_session(self) -> None:
        """None session should be a no-op."""
        _append_model_switch_marker(None, model="gpt-4o", provider="openai")



