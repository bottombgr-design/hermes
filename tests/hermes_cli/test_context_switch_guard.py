"""Tests for hermes_cli.context_switch_guard."""

from __future__ import annotations

import gc
import threading
import warnings
from types import SimpleNamespace

from hermes_cli.context_switch_guard import (
    enrich_model_switch_warnings_for_gateway,
    merge_preflight_compression_warning,
)
from hermes_cli.model_switch import ModelSwitchResult


def _result(*, model: str = "small-model") -> ModelSwitchResult:
    return ModelSwitchResult(
        success=True,
        new_model=model,
        target_provider="openrouter",
        provider_changed=False,
        api_key="k",
        base_url="https://example.com/v1",
        api_mode="chat_completions",
        provider_label="openrouter",
        model_info={"context_length": 32_000},
    )


def _compressor(monkeypatch, *, context_length: int = 200_000):
    from agent.context_compressor import ContextCompressor

    monkeypatch.setattr(
        "agent.context_compressor.get_model_context_length",
        lambda *a, **k: context_length,
    )
    return ContextCompressor(
        model="big-model",
        threshold_percent=0.5,
        protect_first_n=3,
        protect_last_n=20,
        quiet_mode=True,
        config_context_length=context_length,
    )




def test_merge_appends_to_existing_warning(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.context_switch_guard._estimate_tokens",
        lambda *a, **k: 90_000,
    )
    monkeypatch.setattr(
        "hermes_cli.context_switch_guard.resolve_display_context_length",
        lambda *a, **k: 32_000,
    )
    cc = _compressor(monkeypatch)
    agent = SimpleNamespace(
        context_compressor=cc,
        compression_enabled=True,
        base_url="",
        api_key="",
    )
    result = _result()
    result.warning_message = "expensive"
    merge_preflight_compression_warning(result, agent=agent)
    assert "expensive" in result.warning_message
    assert "preflight compression" in result.warning_message


def test_custom_provider_context_avoids_false_shrink_warning(monkeypatch):
    """Classic CLI used to omit custom_providers from the shrink warning.

    Repro: switch onto a custom endpoint with models.<id>.context_length=1M
    while session ~147k. Probe fails → hardcoded catalog match on "qwen"
    (131072) → false "Context window shrinks (... → 131,072)" warning, even
    though /model confirmation and the status bar correctly show 1M.
    """
    custom_provs = [
        {
            "name": "qwen-token-plan",
            "base_url": "https://token-plan.example/compatible-mode/v1",
            "models": {
                "qwen3.8-max-preview": {"context_length": 1_048_576},
            },
        }
    ]
    # Force the probe-down path that hit the "qwen" → 131072 catalog match
    # when custom_providers was not threaded through.
    monkeypatch.setattr(
        "agent.model_metadata._resolve_endpoint_context_length",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "agent.model_metadata._query_ollama_api_show",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "hermes_cli.context_switch_guard._estimate_tokens",
        lambda *a, **k: 147_053,
    )
    cc = _compressor(monkeypatch, context_length=1_000_000)
    agent = SimpleNamespace(
        model="MiniMax-M3",
        provider="minimax",
        context_compressor=cc,
        compression_enabled=True,
        conversation_history=[],
        base_url="https://api.minimax.example/v1",
        api_key="",
        _custom_providers=custom_provs,
    )
    result = ModelSwitchResult(
        success=True,
        new_model="qwen3.8-max-preview",
        target_provider="qwen-token-plan",
        provider_changed=True,
        api_key="k",
        base_url="https://token-plan.example/compatible-mode/v1",
        api_mode="chat_completions",
        provider_label="qwen-token-plan",
        model_info=None,
    )

    # Explicit custom_providers — no false shrink warning (1M > 147k*2).
    merge_preflight_compression_warning(
        result,
        agent=agent,
        custom_providers=custom_provs,
    )
    assert not result.warning_message

    # Agent snapshot alone (classic CLI historically forgot to pass the kwarg).
    result2 = ModelSwitchResult(
        success=True,
        new_model="qwen3.8-max-preview",
        target_provider="qwen-token-plan",
        provider_changed=True,
        api_key="k",
        base_url="https://token-plan.example/compatible-mode/v1",
        api_mode="chat_completions",
        provider_label="qwen-token-plan",
        model_info=None,
    )
    merge_preflight_compression_warning(result2, agent=agent)
    assert not result2.warning_message

    # Without any custom_providers source, catalog match still warns (131k).
    agent_no_cp = SimpleNamespace(
        model="MiniMax-M3",
        provider="minimax",
        context_compressor=cc,
        compression_enabled=True,
        conversation_history=[],
        base_url="https://api.minimax.example/v1",
        api_key="",
        _custom_providers=None,
    )
    result3 = ModelSwitchResult(
        success=True,
        new_model="qwen3.8-max-preview",
        target_provider="qwen-token-plan",
        provider_changed=True,
        api_key="k",
        base_url="https://token-plan.example/compatible-mode/v1",
        api_mode="chat_completions",
        provider_label="qwen-token-plan",
        model_info=None,
    )
    merge_preflight_compression_warning(result3, agent=agent_no_cp)
    assert result3.warning_message
    assert "preflight compression" in result3.warning_message
    assert "shrinks" in result3.warning_message
    # Must not honor the unused 1M custom override when no providers were passed.
    assert "1,048,576" not in result3.warning_message
class _FakeSyncSessionDB:
    """Stands in for the real synchronous hermes_state.SessionDB."""

    def __init__(self, messages):
        self._messages = messages

    def get_messages_as_conversation(self, session_id):
        return self._messages


class _FakeAsyncSessionDB:
    """Mirrors hermes_state.AsyncSessionDB.__getattr__: every callable
    attribute access on the real class returns an async-offloaded wrapper,
    so calling a method here without ``await`` yields a bare coroutine
    rather than the actual result."""

    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        attr = getattr(self._db, name)

        async def _offloaded(*args, **kwargs):
            return attr(*args, **kwargs)

        return _offloaded


def test_enrich_unwraps_async_session_db_before_sync_read(monkeypatch):
    """Regression for #63712: the gateway's runner._session_db is an
    AsyncSessionDB. Calling a method on it synchronously (no await) hands
    merge_preflight_compression_warning a stray coroutine instead of the
    real message list, and leaks an un-awaited coroutine (RuntimeWarning).
    """
    captured = {}

    def _fake_merge(result, *, agent=None, messages=None, **kwargs):
        captured["messages"] = messages

    monkeypatch.setattr(
        "hermes_cli.context_switch_guard.merge_preflight_compression_warning",
        _fake_merge,
    )

    real_messages = [{"role": "user", "content": "hi"}]
    async_db = _FakeAsyncSessionDB(_FakeSyncSessionDB(real_messages))
    agent = SimpleNamespace()
    runner = SimpleNamespace(
        _agent_cache_lock=threading.Lock(),
        _agent_cache={"sess-key": (agent, None)},
        _session_db=async_db,
        session_store=SimpleNamespace(
            get_or_create_session=lambda source: SimpleNamespace(session_id="s1"),
        ),
    )
    result = _result()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        enrich_model_switch_warnings_for_gateway(
            result,
            runner,
            session_key="sess-key",
            source=SimpleNamespace(),
        )
        # A dropped, un-awaited coroutine only warns at GC time — force it
        # so the assertion below is deterministic, not a coin flip.
        gc.collect()

    assert captured["messages"] == real_messages
    assert not any(
        issubclass(w.category, RuntimeWarning) and "coroutine" in str(w.message)
        for w in caught
    )
