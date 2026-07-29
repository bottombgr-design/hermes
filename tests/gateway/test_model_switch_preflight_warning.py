"""Gateway ``/model`` preflight-compression warning must survive the async session DB.

The gateway holds its session DB as :class:`hermes_state.AsyncSessionDB`, whose
generic ``__getattr__`` forwarder returns an awaitable for every method call.
``enrich_model_switch_warnings_for_gateway`` used to call
``get_messages_as_conversation`` without awaiting it, so ``messages`` became a
coroutine object instead of a list. ``_estimate_tokens`` then raised
``TypeError: object of type 'coroutine' has no len()``, which both gateway call
sites swallow at debug level — the warning was silently dead on every gateway
model switch.

These tests drive the real helper against a real on-disk SessionDB (no mock of
the code under test) and assert the behaviour contract: given a session large
enough to cross the new model's compression threshold, the switch result must
carry the preflight warning.
"""

import threading
from types import SimpleNamespace

import pytest

import hermes_state
from hermes_state import AsyncSessionDB, SessionDB
from hermes_cli.context_switch_guard import enrich_model_switch_warnings_for_gateway
from hermes_cli.model_switch import ModelSwitchResult


# --- minimal doubles for collaborators the helper only reads attributes from ---


class _Compressor:
    """Stands in for the agent's context compressor (attribute bag only)."""

    def __init__(self, context_length: int) -> None:
        self.context_length = context_length
        self.threshold_percent = 0.5
        self.protect_first_n = 3
        self.protect_last_n = 20
        self.last_prompt_tokens = 0
        self._ineffective_compression_count = 0


class _Agent:
    def __init__(self, context_length: int = 1_000_000) -> None:
        self.compression_enabled = True
        self.context_compressor = _Compressor(context_length)
        self.model = "gpt-4o"
        self.provider = "openai"
        self.base_url = ""
        self.api_key = ""
        self._cached_system_prompt = "system"
        self.tools = None
        self.session_prompt_tokens = 0


class _Entry:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


class _Store:
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    def get_or_create_session(self, source):  # noqa: ARG002 - signature parity
        return _Entry(self._session_id)


class _AsyncStore:
    """Mirrors AsyncSessionStore: every method call returns an awaitable."""

    def __init__(self, store) -> None:
        self._store = store

    def __getattr__(self, name):
        attr = getattr(self._store, name)
        if not callable(attr):
            return attr

        async def _offloaded(*args, **kwargs):
            return attr(*args, **kwargs)

        return _offloaded


class _Runner:
    """Mirrors the gateway runner surface the helper reaches into."""

    def __init__(self, session_db, session_id: str, agent: _Agent) -> None:
        self._session_db = session_db
        self.session_store = _Store(session_id)
        self.async_session_store = _AsyncStore(self.session_store)
        self._agent_cache_lock = threading.Lock()
        self._agent_cache = {"skey": (agent, None)}


def _switch_result() -> ModelSwitchResult:
    """A successful switch into a large-context model."""
    return ModelSwitchResult(
        success=True,
        new_model="claude-opus-4-8",
        target_provider="anthropic",
    )


def _seed_session(db: SessionDB, session_id: str, *, turns: int, chars: int) -> None:
    db.create_session(session_id, "test")
    for i in range(turns):
        db.append_message(
            session_id,
            "user" if i % 2 == 0 else "assistant",
            content="x" * chars,
        )


@pytest.mark.asyncio
async def test_preflight_warning_fires_through_async_session_db(tmp_path):
    """The gateway's async DB facade must not swallow the warning.

    Regression guard: this failed with TypeError (coroutine has no len())
    before the helper awaited the facade.
    """
    session_id = "s-preflight"
    sync_db = SessionDB(db_path=tmp_path / "state.db")
    _seed_session(sync_db, session_id, turns=800, chars=4000)

    runner = _Runner(AsyncSessionDB(sync_db), session_id, _Agent())
    result = _switch_result()

    await enrich_model_switch_warnings_for_gateway(
        result,
        runner,
        session_key="skey",
        source=object(),
    )

    assert result.warning_message, (
        "expected a preflight-compression warning for a session that exceeds "
        "the incoming model's compression threshold"
    )
    assert "preflight compression" in result.warning_message


@pytest.mark.asyncio
async def test_store_is_read_through_the_async_facade(tmp_path):
    """``get_or_create_session`` must go through the async store facade.

    It performs SQLite SELECTs plus a routing-index rewrite and ``os.fsync``,
    so a bare call would block the gateway event loop. Asserted by making the
    sync store raise if it is ever called directly.
    """
    session_id = "s-store-facade"
    sync_db = SessionDB(db_path=tmp_path / "state.db")
    _seed_session(sync_db, session_id, turns=800, chars=4000)

    runner = _Runner(AsyncSessionDB(sync_db), session_id, _Agent())

    # The async facade forwards to a private snapshot of the real store, so
    # poisoning the *public* sync attribute proves the helper never reaches for
    # it directly. (_AsyncStore captured the store instance at construction.)
    called_sync = []

    def _record_sync_call(source):  # noqa: ARG001 - signature parity
        called_sync.append(source)
        return _Entry(session_id)

    runner.session_store = SimpleNamespace(
        get_or_create_session=_record_sync_call
    )
    result = _switch_result()

    await enrich_model_switch_warnings_for_gateway(
        result,
        runner,
        session_key="skey",
        source=object(),
    )

    assert not called_sync, (
        "get_or_create_session was called on the sync store — that runs "
        "SQLite + os.fsync on the gateway event loop"
    )
    assert result.warning_message
    assert "preflight compression" in result.warning_message


@pytest.mark.asyncio
async def test_no_warning_when_session_is_small(tmp_path):
    """The warning is threshold-driven, not unconditional."""
    session_id = "s-small"
    sync_db = SessionDB(db_path=tmp_path / "state.db")
    _seed_session(sync_db, session_id, turns=30, chars=50)

    runner = _Runner(AsyncSessionDB(sync_db), session_id, _Agent())
    result = _switch_result()

    await enrich_model_switch_warnings_for_gateway(
        result,
        runner,
        session_key="skey",
        source=object(),
    )

    assert not result.warning_message


@pytest.mark.asyncio
async def test_session_db_failure_does_not_break_the_switch(tmp_path):
    """A DB error must degrade to "no warning", never propagate."""

    class _BoomDB:
        def get_messages_as_conversation(self, *a, **kw):
            raise RuntimeError("db exploded")

    runner = _Runner(AsyncSessionDB(_BoomDB()), "s-boom", _Agent())
    result = _switch_result()

    await enrich_model_switch_warnings_for_gateway(
        result,
        runner,
        session_key="skey",
        source=object(),
    )

    assert not result.warning_message
