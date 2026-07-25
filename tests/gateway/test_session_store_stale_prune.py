"""Tests for SessionStore._prune_stale_sessions_locked — crash self-healing.

When a gateway crashes (exit code 1) the graceful shutdown path is skipped and
sessions.json is left pointing at sessions already ended in state.db. On the
next startup _ensure_loaded_locked calls _prune_stale_sessions_locked to detect
and remove those stale routing entries before get_or_create_session() can reuse
them and silently route incoming messages into a closed session (#52804).
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, SessionResetPolicy
from gateway.session import SessionEntry, SessionSource, SessionStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(key: str, session_id: str) -> SessionEntry:
    now = datetime.now()
    return SessionEntry(
        session_key=key,
        session_id=session_id,
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=1),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )


def _make_entry_with_origin(key: str, session_id: str) -> SessionEntry:
    entry = _make_entry(key, session_id)
    entry.origin = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="5140768830",
        chat_type="dm",
        user_id="5140768830",
        user_name="João",
    )
    return entry


def _make_store_with_db(tmp_path, db_mock) -> SessionStore:
    """Build a SessionStore with a mock SessionDB, bypassing disk load."""
    config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
    with patch("gateway.session.SessionStore._ensure_loaded"):
        store = SessionStore(sessions_dir=tmp_path, config=config)
    store._db = db_mock
    store._loaded = True
    return store


def _db_returning(rows: dict) -> MagicMock:
    """SessionDB mock where get_session maps session_id -> row dict."""
    db = MagicMock()
    db.get_session.side_effect = lambda sid: rows.get(sid)
    return db


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------

class TestPruneStaleSessionsLocked:
    def test_prunes_ended_session(self, tmp_path):
        db = _db_returning({"sid_dm": {"end_reason": "agent_close", "id": "sid_dm"}})
        store = _make_store_with_db(tmp_path, db)
        store._entries["dm_key"] = _make_entry("dm_key", "sid_dm")

        store._prune_stale_sessions_locked()

        assert "dm_key" not in store._entries

    def test_keeps_live_session(self, tmp_path):
        db = _db_returning({"sid_live": {"end_reason": None, "id": "sid_live"}})
        store = _make_store_with_db(tmp_path, db)
        store._entries["live_key"] = _make_entry("live_key", "sid_live")

        store._prune_stale_sessions_locked()

        assert "live_key" in store._entries

    def test_keeps_session_absent_from_db(self, tmp_path):
        """Entry for a session_id not in state.db (legacy) is left alone."""
        db = _db_returning({})
        store = _make_store_with_db(tmp_path, db)
        store._entries["legacy_key"] = _make_entry("legacy_key", "sid_legacy")

        store._prune_stale_sessions_locked()

        assert "legacy_key" in store._entries

    def test_prunes_multiple_stale_entries(self, tmp_path):
        db = _db_returning({
            "sid_a": {"end_reason": "agent_close", "id": "sid_a"},
            "sid_b": {"end_reason": "session_reset", "id": "sid_b"},
            "sid_c": {"end_reason": None, "id": "sid_c"},  # alive — keep
        })
        store = _make_store_with_db(tmp_path, db)
        store._entries["key_a"] = _make_entry("key_a", "sid_a")
        store._entries["key_b"] = _make_entry("key_b", "sid_b")
        store._entries["key_c"] = _make_entry("key_c", "sid_c")

        store._prune_stale_sessions_locked()

        assert "key_a" not in store._entries
        assert "key_b" not in store._entries
        assert "key_c" in store._entries

    def test_repoints_stale_compression_parent_to_latest_live_child(self, tmp_path):
        """Compression-ended parents should recover their live child mapping.

        A gateway crash can leave sessions.json pointing at the pre-compression
        parent (end_reason='compression') even though the agent already rotated
        into a live child session. If the child has gateway peer metadata, the
        startup prune pass must repoint the route instead of deleting it, or
        restart auto-resume and queued follow-ups have no session to continue.
        """
        key = "agent:main:telegram:dm:5140768830"
        db = _db_returning({
            "sid_parent": {"end_reason": "compression", "id": "sid_parent"},
        })
        db.find_latest_gateway_session_for_peer.return_value = {
            "id": "sid_child",
            "started_at": 1782744974.0,
        }
        store = _make_store_with_db(tmp_path, db)
        store._entries[key] = _make_entry_with_origin(key, "sid_parent")

        store._prune_stale_sessions_locked()

        assert key in store._entries
        assert store._entries[key].session_id == "sid_child"
        db.find_latest_gateway_session_for_peer.assert_called_once()
        db.reopen_session.assert_called_once_with("sid_child")

    def test_prunes_stale_entry_when_recovery_only_finds_same_ended_session(self, tmp_path):
        key = "agent:main:telegram:dm:5140768830"
        db = _db_returning({"sid_parent": {"end_reason": "agent_close", "id": "sid_parent"}})
        db.find_latest_gateway_session_for_peer.return_value = {
            "id": "sid_parent",
            "started_at": 1782744974.0,
        }
        store = _make_store_with_db(tmp_path, db)
        store._entries[key] = _make_entry_with_origin(key, "sid_parent")

        store._prune_stale_sessions_locked()

        assert key not in store._entries

    def test_keeps_stale_entry_when_recovery_lookup_raises(self, tmp_path):
        """Indeterminate recovery must not delete the only routing handle.

        Startup pruning sees an ended parent and tries to repoint it to the
        latest live gateway child.  If that recovery query raises, deleting the
        sessions.json entry loses the routing key entirely; keeping it lets the
        runtime stale guard retry recovery on the next message.
        """
        key = "agent:main:telegram:dm:5140768830"
        db = _db_returning({"sid_parent": {"end_reason": "compression", "id": "sid_parent"}})
        db.find_latest_gateway_session_for_peer.side_effect = RuntimeError("db busy")
        store = _make_store_with_db(tmp_path, db)
        store._entries[key] = _make_entry_with_origin(key, "sid_parent")

        store._prune_stale_sessions_locked()

        assert key in store._entries
        assert store._entries[key].session_id == "sid_parent"

    def test_noop_when_db_is_none(self, tmp_path):
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._db = None
        store._loaded = True
        store._entries["key"] = _make_entry("key", "sid_x")

        store._prune_stale_sessions_locked()  # must not raise

        assert "key" in store._entries

    def test_noop_when_no_entries(self, tmp_path):
        db = MagicMock()
        store = _make_store_with_db(tmp_path, db)

        store._prune_stale_sessions_locked()

        db.get_session.assert_not_called()

    def test_db_error_is_non_fatal(self, tmp_path):
        db = MagicMock()
        db.get_session.side_effect = Exception("DB locked")
        store = _make_store_with_db(tmp_path, db)
        store._entries["key"] = _make_entry("key", "sid_x")

        store._prune_stale_sessions_locked()  # must not raise

        assert "key" in store._entries  # safe fallback — keep on error

    def test_sessions_json_rewritten_after_pruning(self, tmp_path):
        db = _db_returning({"sid_stale": {"end_reason": "agent_close", "id": "sid_stale"}})
        store = _make_store_with_db(tmp_path, db)
        store._entries["stale_key"] = _make_entry("stale_key", "sid_stale")

        with patch.object(store, "_save") as mock_save:
            store._prune_stale_sessions_locked()
            mock_save.assert_called_once()

    def test_sessions_json_not_rewritten_when_nothing_pruned(self, tmp_path):
        db = _db_returning({"sid_live": {"end_reason": None, "id": "sid_live"}})
        store = _make_store_with_db(tmp_path, db)
        store._entries["live_key"] = _make_entry("live_key", "sid_live")

        with patch.object(store, "_save") as mock_save:
            store._prune_stale_sessions_locked()
            mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# Recovered entries must carry real last-activity, not "now"
# ---------------------------------------------------------------------------

class TestRecoveredEntryTimestamps:
    def _source(self) -> SessionSource:
        return SessionSource(
            platform=Platform.WEIXIN,
            chat_id="contact-1",
            chat_type="dm",
            user_id="contact-1",
        )

    def test_updated_at_comes_from_row_last_active(self, tmp_path):
        """updated_at is the sole input to _should_reset.

        Stamping a recovered row as "just active" made it immune to the very
        next idle/daily check, so a session recovered long after its reset
        boundary kept absorbing new messages into stale history.
        """
        store = _make_store_with_db(tmp_path, MagicMock())
        last_active = datetime.now() - timedelta(days=5)
        started_at = last_active - timedelta(minutes=2)

        entry = store._create_entry_from_recovered_row(
            row={
                "id": "sid_old",
                "started_at": started_at.timestamp(),
                "last_active": last_active.timestamp(),
            },
            session_key="agent:main:weixin:dm:contact-1",
            source=self._source(),
            now=datetime.now(),
        )

        assert entry.updated_at == pytest.approx(last_active, abs=timedelta(seconds=1))
        assert entry.created_at == pytest.approx(started_at, abs=timedelta(seconds=1))

    def test_updated_at_falls_back_to_now_without_last_active(self, tmp_path):
        """Hand-built rows (no last_active column) keep the old behaviour."""
        store = _make_store_with_db(tmp_path, MagicMock())
        now = datetime.now()

        entry = store._create_entry_from_recovered_row(
            row={"id": "sid_x", "started_at": (now - timedelta(hours=3)).timestamp()},
            session_key="agent:main:weixin:dm:contact-1",
            source=self._source(),
            now=now,
        )

        assert entry.updated_at == now

    def test_recovered_stale_session_is_rotated_by_reset_policy(self, tmp_path):
        """End-to-end: carrying real last-activity re-arms the existing policy.

        This is what makes the next inbound message open a FRESH session
        instead of continuing a long-dead conversation — no separate freshness
        rule, the operator's own session_reset config stays the single source
        of truth.
        """
        config = GatewayConfig(
            default_reset_policy=SessionResetPolicy(
                mode="both", at_hour=4, idle_minutes=1440
            )
        )
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path, config=config)
        store._db = MagicMock()
        store._loaded = True

        source = self._source()
        last_active = datetime.now() - timedelta(days=5)
        entry = store._create_entry_from_recovered_row(
            row={
                "id": "sid_old",
                "started_at": last_active.timestamp(),
                "last_active": last_active.timestamp(),
            },
            session_key=store._generate_session_key(source),
            source=source,
            now=datetime.now(),
        )

        assert store._should_reset(entry, source) in {"idle", "daily"}


# ---------------------------------------------------------------------------
# Integration: _ensure_loaded_locked calls _prune_stale_sessions_locked
# ---------------------------------------------------------------------------

class TestEnsureLoadedCallsPrune:
    def test_stale_entry_pruned_during_load(self, tmp_path):
        entry = _make_entry("dm_key", "sid_stale")
        (tmp_path / "sessions.json").write_text(
            json.dumps({"dm_key": entry.to_dict()}, indent=2), encoding="utf-8"
        )
        db = _db_returning({"sid_stale": {"end_reason": "agent_close", "id": "sid_stale"}})
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        store = SessionStore(sessions_dir=tmp_path, config=config)
        store._db = db

        store._ensure_loaded()

        assert "dm_key" not in store._entries

    def test_live_entry_survives_load(self, tmp_path):
        entry = _make_entry("active_key", "sid_live")
        (tmp_path / "sessions.json").write_text(
            json.dumps({"active_key": entry.to_dict()}, indent=2), encoding="utf-8"
        )
        db = _db_returning({"sid_live": {"end_reason": None, "id": "sid_live"}})
        config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
        store = SessionStore(sessions_dir=tmp_path, config=config)
        store._db = db

        store._ensure_loaded()

        assert "active_key" in store._entries
