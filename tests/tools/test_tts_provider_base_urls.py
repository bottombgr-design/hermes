"""Class-level base_url parity: every cloud TTS provider honors config base_url.

xAI, MiniMax, Gemini, OpenAI and DeepInfra already read
``tts.<provider>.base_url`` from config.yaml. This locks in the same
contract for the ElevenLabs and Mistral sections (the two that used to
hardcode the SDK default endpoint).
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import tools.tts_tool as tts


# ── ElevenLabs: base_url/wss_url → ElevenLabsEnvironment ──────────────────


def _fake_elevenlabs_environment_module(captured: dict):
    mod = types.ModuleType("elevenlabs.environment")

    class ElevenLabsEnvironment:
        def __init__(self, base, wss):
            captured["base"] = base
            captured["wss"] = wss

    mod.ElevenLabsEnvironment = ElevenLabsEnvironment
    pkg = types.ModuleType("elevenlabs")
    pkg.environment = mod
    return pkg, mod


def test_elevenlabs_no_base_url_uses_sdk_default_environment():
    assert tts._elevenlabs_environment_kwargs({}) == {}
    assert tts._elevenlabs_environment_kwargs({"base_url": ""}) == {}


def test_elevenlabs_base_url_builds_environment(monkeypatch):
    captured: dict = {}
    pkg, mod = _fake_elevenlabs_environment_module(captured)
    monkeypatch.setitem(sys.modules, "elevenlabs", pkg)
    monkeypatch.setitem(sys.modules, "elevenlabs.environment", mod)

    kwargs = tts._elevenlabs_environment_kwargs(
        {"base_url": "https://el-proxy.example/", "wss_url": "wss://el-proxy.example/ws"}
    )
    assert "environment" in kwargs
    assert captured == {
        "base": "https://el-proxy.example",
        "wss": "wss://el-proxy.example/ws",
    }


def test_elevenlabs_wss_url_derived_from_base_url(monkeypatch):
    captured: dict = {}
    pkg, mod = _fake_elevenlabs_environment_module(captured)
    monkeypatch.setitem(sys.modules, "elevenlabs", pkg)
    monkeypatch.setitem(sys.modules, "elevenlabs.environment", mod)

    tts._elevenlabs_environment_kwargs({"base_url": "https://el-proxy.example"})
    assert captured["wss"] == "wss://el-proxy.example"


def test_elevenlabs_base_url_reaches_client_alongside_convert_options(tmp_path, monkeypatch):
    """Sync path: the environment kwarg and the built convert kwargs coexist."""
    captured: dict = {}
    pkg, mod = _fake_elevenlabs_environment_module(captured)
    monkeypatch.setitem(sys.modules, "elevenlabs", pkg)
    monkeypatch.setitem(sys.modules, "elevenlabs.environment", mod)

    mock_client = MagicMock()
    mock_client.text_to_speech.convert.return_value = iter([b"audio"])
    factory = MagicMock(return_value=mock_client)

    with patch.object(tts, "get_env_value", lambda k, *a: "el-key" if k == "ELEVENLABS_API_KEY" else None), \
         patch.object(tts, "_import_elevenlabs", return_value=factory):
        tts._generate_elevenlabs(
            "hi",
            str(tmp_path / "out.mp3"),
            {
                "elevenlabs": {
                    "base_url": "https://el-proxy.example",
                    "language_code": "en",
                }
            },
        )

    assert "environment" in factory.call_args.kwargs
    assert captured["base"] == "https://el-proxy.example"
    assert mock_client.text_to_speech.convert.call_args.kwargs["language_code"] == "en"


def test_elevenlabs_streamer_base_url_reaches_client_alongside_convert_options(monkeypatch):
    """Streaming path: the environment kwarg and the built convert kwargs coexist."""
    from tools import tts_streaming as ts

    captured: dict = {}
    pkg, mod = _fake_elevenlabs_environment_module(captured)
    monkeypatch.setitem(sys.modules, "elevenlabs", pkg)
    monkeypatch.setitem(sys.modules, "elevenlabs.environment", mod)

    mock_client = MagicMock()
    mock_client.text_to_speech.convert.return_value = iter([b"\x00\x00"])
    factory = MagicMock(return_value=mock_client)

    with patch.object(ts, "get_env_value", lambda k, *a: "el-key" if k == "ELEVENLABS_API_KEY" else None), \
         patch.object(tts, "_import_elevenlabs", return_value=factory):
        streamer = ts.ElevenLabsStreamer(
            {}, {"base_url": "https://el-proxy.example", "language_code": "en"}
        )
        list(streamer.stream("hi"))

    assert "environment" in factory.call_args.kwargs
    assert captured["base"] == "https://el-proxy.example"
    assert mock_client.text_to_speech.convert.call_args.kwargs["language_code"] == "en"


# ── Mistral: tts.mistral.base_url → SDK server_url ────────────────────────


def test_mistral_base_url_passed_as_server_url(tmp_path, monkeypatch):
    captured: dict = {}

    class _FakeMistral:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        class audio:  # noqa: N801 — mimic SDK attribute shape
            class speech:  # noqa: N801
                @staticmethod
                def complete(**kwargs):
                    return types.SimpleNamespace(audio_data="aGVsbG8=")  # "hello"

    out = tmp_path / "out.mp3"
    with patch.object(tts, "_import_mistral_client", return_value=_FakeMistral), \
         patch.object(tts, "get_env_value", lambda k, *a: "key" if k == "MISTRAL_API_KEY" else None):
        tts._generate_mistral_tts(
            "hi", str(out), {"mistral": {"base_url": "https://mistral-proxy.example/v1"}}
        )

    assert captured["api_key"] == "key"
    assert captured["server_url"] == "https://mistral-proxy.example/v1"
    assert out.read_bytes() == b"hello"


def test_mistral_no_base_url_omits_server_url(tmp_path):
    captured: dict = {}

    class _FakeMistral:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        class audio:  # noqa: N801
            class speech:  # noqa: N801
                @staticmethod
                def complete(**kwargs):
                    return types.SimpleNamespace(audio_data="aGVsbG8=")

    out = tmp_path / "out.mp3"
    with patch.object(tts, "_import_mistral_client", return_value=_FakeMistral), \
         patch.object(tts, "get_env_value", lambda k, *a: "key" if k == "MISTRAL_API_KEY" else None):
        tts._generate_mistral_tts("hi", str(out), {"mistral": {}})

    assert "server_url" not in captured
