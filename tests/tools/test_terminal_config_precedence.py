"""Tests for config.yaml terminal section taking precedence over .env TERMINAL_ENV."""

import os
import pytest
from unittest.mock import patch, MagicMock


class TestTerminalConfigPrecedence:
    """Verify config.yaml terminal section wins over stale .env values."""

    def _reset_bridge_state(self):
        """Reset the one-shot bridge guard."""
        import tools.terminal_tool as tt
        tt._terminal_config_bridge_attempted = False

    @patch("hermes_cli.config.apply_terminal_config_to_env")
    @patch("hermes_cli.config.read_raw_config")
    def test_config_yaml_wins_over_env_when_terminal_section_exists(
        self, mock_read_raw, mock_apply
    ):
        """config.yaml terminal section should override .env TERMINAL_ENV."""
        import tools.terminal_tool as tt

        self._reset_bridge_state()

        # config.yaml has terminal section
        mock_read_raw.return_value = {"terminal": {"backend": "local"}}

        # .env has stale SSH
        with patch.dict(os.environ, {"TERMINAL_ENV": "ssh"}, clear=False):
            tt._ensure_terminal_env_bridged()

        # Should call with override=True (config.yaml wins)
        mock_apply.assert_called_once_with(env=None, override=True)

    @patch("hermes_cli.config.apply_terminal_config_to_env")
    @patch("hermes_cli.config.read_raw_config")
    def test_env_kept_when_no_terminal_section_in_config(
        self, mock_read_raw, mock_apply
    ):
        """Without terminal section in config.yaml, .env values preserved."""
        import tools.terminal_tool as tt

        self._reset_bridge_state()

        # config.yaml has NO terminal section
        mock_read_raw.return_value = {"agent": {"max_turns": 100}}

        # .env has TERMINAL_ENV set
        with patch.dict(os.environ, {"TERMINAL_ENV": "ssh"}, clear=False):
            tt._ensure_terminal_env_bridged()

        # Should NOT call apply — env already set, no config override
        mock_apply.assert_not_called()

    @patch("hermes_cli.config.apply_terminal_config_to_env")
    @patch("hermes_cli.config.read_raw_config")
    def test_backfill_when_no_section_and_no_env(
        self, mock_read_raw, mock_apply
    ):
        """No terminal section and no .env → backfill from defaults."""
        import tools.terminal_tool as tt

        self._reset_bridge_state()

        mock_read_raw.return_value = {"agent": {"max_turns": 100}}

        env = {k: v for k, v in os.environ.items() if k != "TERMINAL_ENV"}
        with patch.dict(os.environ, env, clear=True):
            tt._ensure_terminal_env_bridged()

        # Should call with override=False (backfill only)
        mock_apply.assert_called_once_with(env=None, override=False)

    @patch("hermes_cli.config.apply_terminal_config_to_env")
    @patch("hermes_cli.config.read_raw_config")
    def test_config_yaml_docker_overrides_env_ssh(
        self, mock_read_raw, mock_apply
    ):
        """Switching from SSH to Docker in config.yaml should take effect."""
        import tools.terminal_tool as tt

        self._reset_bridge_state()

        # config.yaml says docker
        mock_read_raw.return_value = {"terminal": {"backend": "docker"}}

        # .env still says ssh
        with patch.dict(os.environ, {"TERMINAL_ENV": "ssh"}, clear=False):
            tt._ensure_terminal_env_bridged()

        # Config wins
        mock_apply.assert_called_once_with(env=None, override=True)

    @patch("hermes_cli.config.apply_terminal_config_to_env")
    @patch("hermes_cli.config.read_raw_config")
    def test_bridge_only_runs_once(
        self, mock_read_raw, mock_apply
    ):
        """Bridge should only run once per process (guard)."""
        import tools.terminal_tool as tt

        self._reset_bridge_state()

        mock_read_raw.return_value = {"terminal": {"backend": "local"}}

        with patch.dict(os.environ, {"TERMINAL_ENV": "ssh"}, clear=False):
            tt._ensure_terminal_env_bridged()
            tt._ensure_terminal_env_bridged()  # second call

        # Only one call despite two invocations
        mock_apply.assert_called_once()

    @patch("hermes_cli.config.apply_terminal_config_to_env")
    @patch("hermes_cli.config.read_raw_config")
    def test_bridge_exception_does_not_crash(
        self, mock_read_raw, mock_apply
    ):
        """Bridge failure should not crash terminal tool."""
        import tools.terminal_tool as tt

        self._reset_bridge_state()

        mock_read_raw.side_effect = Exception("config read failed")

        # Should not raise
        with patch.dict(os.environ, {"TERMINAL_ENV": "ssh"}, clear=False):
            tt._ensure_terminal_env_bridged()

        mock_apply.assert_not_called()
