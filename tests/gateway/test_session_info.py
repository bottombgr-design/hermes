"""Tests for GatewayRunner._format_session_info — session config surfacing."""

import pytest
from unittest.mock import patch

from gateway.config import ChannelOverride, GatewayConfig, Platform, PlatformConfig
from gateway.run import GatewayRunner
from gateway.session import SessionSource


@pytest.fixture()
def runner():
    """Create a bare GatewayRunner without __init__."""
    return GatewayRunner.__new__(GatewayRunner)


def _patch_info(tmp_path, config_yaml, model, runtime):
    """Return a context-manager stack that patches _format_session_info deps."""
    cfg_path = tmp_path / "config.yaml"
    if config_yaml is not None:
        cfg_path.write_text(config_yaml)
    return (
        patch("gateway.run._hermes_home", tmp_path),
        patch("gateway.run._resolve_gateway_model", return_value=model),
        patch("gateway.run._resolve_runtime_agent_kwargs", return_value=runtime),
    )


class TestFormatSessionInfo:

    def test_includes_model_name(self, runner, tmp_path):
        p1, p2, p3 = _patch_info(tmp_path, "model:\n  default: anthropic/claude-opus-4.6\n  provider: openrouter\n",
                                  "anthropic/claude-opus-4.6",
                                  {"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1", "api_key": "k"})
        with p1, p2, p3:
            info = runner._format_session_info()
        assert "claude-opus-4.6" in info

    def test_includes_provider(self, runner, tmp_path):
        p1, p2, p3 = _patch_info(tmp_path, "model:\n  default: test-model\n  provider: openrouter\n",
                                  "test-model",
                                  {"provider": "openrouter", "base_url": "", "api_key": ""})
        with p1, p2, p3:
            info = runner._format_session_info()
        assert "openrouter" in info

    def test_config_context_length(self, runner, tmp_path):
        p1, p2, p3 = _patch_info(tmp_path, "model:\n  default: test-model\n  context_length: 32768\n",
                                  "test-model",
                                  {"provider": "custom", "base_url": "", "api_key": ""})
        with p1, p2, p3:
            info = runner._format_session_info()
        assert "32K" in info
        assert "config" in info

    def test_default_fallback_hint(self, runner, tmp_path):
        p1, p2, p3 = _patch_info(tmp_path, "model:\n  default: unknown-model-xyz\n",
                                  "unknown-model-xyz",
                                  {"provider": "", "base_url": "", "api_key": ""})
        with p1, p2, p3:
            info = runner._format_session_info()
        assert "256K" in info
        assert "model.context_length" in info

    def test_local_endpoint_shown(self, runner, tmp_path):
        p1, p2, p3 = _patch_info(
            tmp_path,
            "model:\n  default: qwen3:8b\n  provider: custom\n  base_url: http://localhost:11434/v1\n  context_length: 8192\n",
            "qwen3:8b",
            {"provider": "custom", "base_url": "http://localhost:11434/v1", "api_key": ""})
        with p1, p2, p3:
            info = runner._format_session_info()
        assert "localhost:11434" in info
        assert "8K" in info

    def test_cloud_endpoint_hidden(self, runner, tmp_path):
        p1, p2, p3 = _patch_info(tmp_path, "model:\n  default: test-model\n  provider: openrouter\n",
                                  "test-model",
                                  {"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1", "api_key": "k"})
        with p1, p2, p3:
            info = runner._format_session_info()
        assert "Endpoint" not in info

    def test_million_context_format(self, runner, tmp_path):
        p1, p2, p3 = _patch_info(tmp_path, "model:\n  default: test-model\n  context_length: 1000000\n",
                                  "test-model",
                                  {"provider": "", "base_url": "", "api_key": ""})
        with p1, p2, p3:
            info = runner._format_session_info()
        assert "1.0M" in info

    def test_custom_context_is_scoped_to_active_runtime_route(self, runner, tmp_path):
        config = """
model:
  default: shared-model
  provider: custom
custom_providers:
  - name: large-route
    base_url: https://example.com/v1//
    models:
      shared-model:
        context_length: 1048576
"""
        p1, p2, p3 = _patch_info(
            tmp_path,
            config,
            "shared-model",
            {
                "provider": "custom",
                "base_url": "https://example.com/v1",
                "api_key": "k",
            },
        )

        with p1, p2, p3:
            info = runner._format_session_info()

        assert "1.0M" not in info
        assert "(config)" not in info

    def test_global_context_is_scoped_to_active_runtime_route(self, runner, tmp_path):
        config = """
model:
  default: shared-model
  provider: custom
  base_url: https://large.example/v1
  context_length: 1048576
"""
        p1, p2, p3 = _patch_info(
            tmp_path,
            config,
            "shared-model",
            {
                "provider": "custom",
                "base_url": "https://small.example/v1",
                "api_key": "k",
            },
        )

        with p1, p2, p3:
            info = runner._format_session_info()

        assert "1.0M" not in info
        assert "(config)" not in info

    def test_missing_config(self, runner, tmp_path):
        """No config.yaml should not crash."""
        p1, p2, p3 = _patch_info(tmp_path, None,  # don't create config
                                  "anthropic/claude-sonnet-4.6",
                                  {"provider": "openrouter", "base_url": "", "api_key": ""})
        with p1, p2, p3:
            info = runner._format_session_info()
        assert "Model" in info
        assert "Context" in info

    def test_runtime_resolution_failure_doesnt_crash(self, runner, tmp_path):
        """If runtime resolution raises, should still produce output."""
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("model:\n  default: test-model\n  context_length: 4096\n")
        with patch("gateway.run._hermes_home", tmp_path), \
             patch("gateway.run._resolve_gateway_model", return_value="test-model"), \
             patch("gateway.run._resolve_runtime_agent_kwargs", side_effect=RuntimeError("no creds")):
            info = runner._format_session_info()
        assert "4K" in info
        assert "config" in info


class TestResetNoticeSessionInfo:
    """#59003: the auto-reset banner must report the serving profile's config,
    not the multiplexer's base config."""

    _RUNTIME = {"provider": "", "base_url": "", "api_key": ""}

    def _source(self):
        from gateway.config import Platform
        from gateway.session import SessionSource
        return SessionSource(
            platform=Platform.TELEGRAM, chat_id="123", user_id="u1",
            profile="planner",
        )

    def _homes(self, tmp_path):
        base = tmp_path / "base"
        profile = tmp_path / "profiles" / "planner"
        profile.mkdir(parents=True)
        base.mkdir()
        base.joinpath("config.yaml").write_text(
            "model:\n  default: base-model\n  provider: custom\n  context_length: 1000\n")
        profile.joinpath("config.yaml").write_text(
            "model:\n  default: profile-model\n  provider: anthropic\n  context_length: 2000\n")
        return base, profile

    def test_multiplex_uses_profile_config(self, runner, tmp_path):
        from types import SimpleNamespace
        base, profile = self._homes(tmp_path)
        runner.config = SimpleNamespace(multiplex_profiles=True)
        with patch("gateway.run._hermes_home", base), \
             patch.object(GatewayRunner, "_resolve_profile_home_for_source", return_value=profile), \
             patch("gateway.run._resolve_runtime_agent_kwargs", return_value=self._RUNTIME):
            info = runner._reset_notice_session_info(self._source())
        assert "profile-model" in info
        assert "anthropic" in info
        assert "base-model" not in info

    def test_single_profile_uses_base_config(self, runner, tmp_path):
        from types import SimpleNamespace
        base, _profile = self._homes(tmp_path)
        runner.config = SimpleNamespace(multiplex_profiles=False)
        with patch("gateway.run._hermes_home", base), \
             patch("gateway.run._resolve_runtime_agent_kwargs", return_value=self._RUNTIME):
            info = runner._reset_notice_session_info(self._source())
        assert "base-model" in info
        assert "profile-model" not in info


class TestFormatSessionInfoChannelOverride:
    """#72838: /new and /model display must reflect channel_overrides, not
    just the global default. The actual turn dispatch already resolves
    channel_overrides — these display paths used to bypass that resolution."""

    _RUNTIME = {"provider": "", "base_url": "", "api_key": ""}

    def _config_with_override(self):
        return GatewayConfig(
            platforms={
                Platform.TELEGRAM: PlatformConfig(
                    enabled=True,
                    channel_overrides={
                        "123": ChannelOverride(model="channel-override-model"),
                    },
                ),
            },
        )

    def _source(self):
        return SessionSource(
            platform=Platform.TELEGRAM, chat_id="123", user_id="u1",
        )

    def test_format_session_info_shows_channel_override_model(self, runner, tmp_path):
        """Layer 1: _format_session_info(source) must resolve the channel
        override model, not the global default."""
        runner.config = self._config_with_override()
        p1, p2, p3 = _patch_info(
            tmp_path,
            "model:\n  default: global-default-model\n",
            "global-default-model",
            self._RUNTIME,
        )
        with p1, p2, p3:
            info = runner._format_session_info(self._source())
        assert "channel-override-model" in info
        assert "global-default-model" not in info

    def test_format_session_info_shows_channel_override_provider(self, runner, tmp_path):
        """When the channel override sets a provider, the session info block
        must show it (not the global config provider)."""
        runner.config = GatewayConfig(
            platforms={
                Platform.TELEGRAM: PlatformConfig(
                    enabled=True,
                    channel_overrides={
                        "123": ChannelOverride(
                            model="channel-override-model",
                            provider="anthropic",
                        ),
                    },
                ),
            },
        )
        p1, p2, p3 = _patch_info(
            tmp_path,
            "model:\n  default: global-default-model\n  provider: openrouter\n",
            "global-default-model",
            self._RUNTIME,
        )
        with p1, p2, p3:
            info = runner._format_session_info(self._source())
        assert "anthropic" in info
        assert "channel-override-model" in info

    def test_format_session_info_falls_back_when_no_override(self, runner, tmp_path):
        """When the source's channel has no override, the global default is
        shown — same as the no-source path."""
        runner.config = GatewayConfig(
            platforms={
                Platform.TELEGRAM: PlatformConfig(
                    enabled=True, channel_overrides={},
                ),
            },
        )
        p1, p2, p3 = _patch_info(
            tmp_path,
            "model:\n  default: global-default-model\n",
            "global-default-model",
            self._RUNTIME,
        )
        with p1, p2, p3:
            info = runner._format_session_info(self._source())
        assert "global-default-model" in info

    def test_format_session_info_falls_back_when_wrong_channel(self, runner, tmp_path):
        """A source whose chat_id doesn't match any override falls back to
        the global default."""
        runner.config = self._config_with_override()
        wrong_source = SessionSource(
            platform=Platform.TELEGRAM, chat_id="999", user_id="u2",
        )
        p1, p2, p3 = _patch_info(
            tmp_path,
            "model:\n  default: global-default-model\n",
            "global-default-model",
            self._RUNTIME,
        )
        with p1, p2, p3:
            info = runner._format_session_info(wrong_source)
        assert "global-default-model" in info
        assert "channel-override-model" not in info

    def test_format_session_info_no_source_uses_global(self, runner, tmp_path):
        """Backward-compat: calling without a source still uses the global
        default (existing callers that don't have a source in scope)."""
        runner.config = self._config_with_override()
        p1, p2, p3 = _patch_info(
            tmp_path,
            "model:\n  default: global-default-model\n",
            "global-default-model",
            self._RUNTIME,
        )
        with p1, p2, p3:
            info = runner._format_session_info()
        assert "global-default-model" in info

    def test_reset_notice_shows_channel_override_model(self, runner, tmp_path):
        """Layer 1 (via the /new entry point): _reset_notice_session_info
        must thread source through to _format_session_info so the auto-reset
        banner shows the channel override model."""
        runner.config = GatewayConfig(
            platforms={
                Platform.TELEGRAM: PlatformConfig(
                    enabled=True,
                    channel_overrides={
                        "123": ChannelOverride(model="channel-override-model"),
                    },
                ),
            },
            multiplex_profiles=False,
        )
        with patch("gateway.run._hermes_home", tmp_path), \
             patch("gateway.run._resolve_gateway_model", return_value="global-default-model"), \
             patch("gateway.run._resolve_runtime_agent_kwargs", return_value=self._RUNTIME):
            info = runner._reset_notice_session_info(self._source())
        assert "channel-override-model" in info
        assert "global-default-model" not in info
