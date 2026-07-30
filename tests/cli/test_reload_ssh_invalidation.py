"""Tests for /reload command invalidating cached SSH environments on SSH config change."""

import os
import pytest
from unittest.mock import patch, MagicMock


class TestReloadSSHInvalidation:
    """Verify /reload clears cached remote environments when SSH config changes."""

    def _make_cli_instance(self):
        """Create a minimal HermesCLI-like object with process_command."""
        # Import the actual class to test the real handler
        from cli import HermesCLI
        # We only need process_command, so mock everything else
        cli = MagicMock(spec=HermesCLI)
        cli.process_command = HermesCLI.process_command.__get__(cli)
        return cli

    @patch("hermes_cli.config.reload_env", return_value=1)
    @patch("tools.terminal_tool.cleanup_all_environments", return_value=2)
    def test_reload_clears_envs_when_ssh_port_changes(
        self, mock_cleanup, mock_reload, capsys
    ):
        """When TERMINAL_SSH_PORT changes, /reload should clean up cached envs."""
        cli = self._make_cli_instance()

        # Set initial port
        with patch.dict(os.environ, {"TERMINAL_SSH_PORT": "38050"}, clear=False):
            # Simulate reload changing the port
            def fake_reload():
                os.environ["TERMINAL_SSH_PORT"] = "23755"
                return 1

            mock_reload.side_effect = fake_reload
            cli.process_command("/reload")

        mock_cleanup.assert_called_once()
        output = capsys.readouterr().out
        assert "Cleared 2 cached environment(s)" in output

    @patch("hermes_cli.config.reload_env", return_value=0)
    @patch("tools.terminal_tool.cleanup_all_environments", return_value=0)
    def test_reload_skips_cleanup_when_ssh_config_unchanged(
        self, mock_cleanup, mock_reload, capsys
    ):
        """When SSH config doesn't change, /reload should NOT clean up."""
        cli = self._make_cli_instance()

        with patch.dict(
            os.environ,
            {"TERMINAL_SSH_PORT": "23755", "TERMINAL_SSH_HOST": "1.2.3.4"},
            clear=False,
        ):
            cli.process_command("/reload")

        mock_cleanup.assert_not_called()
        output = capsys.readouterr().out
        assert "Cleared" not in output

    @patch("hermes_cli.config.reload_env", return_value=1)
    @patch("tools.terminal_tool.cleanup_all_environments", return_value=0)
    def test_reload_no_message_when_no_envs_to_clean(
        self, mock_cleanup, mock_reload, capsys
    ):
        """When SSH config changes but no envs cached, don't print cleanup msg."""
        cli = self._make_cli_instance()

        with patch.dict(os.environ, {"TERMINAL_SSH_PORT": "38050"}, clear=False):

            def fake_reload():
                os.environ["TERMINAL_SSH_PORT"] = "23755"
                return 1

            mock_reload.side_effect = fake_reload
            cli.process_command("/reload")

        mock_cleanup.assert_called_once()
        output = capsys.readouterr().out
        assert "Cleared" not in output  # cleaned=0, no message

    @patch("hermes_cli.config.reload_env", return_value=1)
    @patch("tools.terminal_tool.cleanup_all_environments", side_effect=Exception("boom"))
    def test_reload_handles_cleanup_failure_gracefully(
        self, mock_cleanup, mock_reload, capsys
    ):
        """If cleanup_all_environments raises, /reload should not crash."""
        cli = self._make_cli_instance()

        with patch.dict(os.environ, {"TERMINAL_SSH_PORT": "38050"}, clear=False):

            def fake_reload():
                os.environ["TERMINAL_SSH_PORT"] = "23755"
                return 1

            mock_reload.side_effect = fake_reload
            # Should not raise
            cli.process_command("/reload")

        output = capsys.readouterr().out
        assert "Reloaded .env" in output
        # No crash = pass

    @patch("hermes_cli.config.reload_env", return_value=1)
    @patch("tools.terminal_tool.cleanup_all_environments", return_value=1)
    def test_reload_detects_ssh_host_change(
        self, mock_cleanup, mock_reload, capsys
    ):
        """SSH host change should also trigger cleanup."""
        cli = self._make_cli_instance()

        with patch.dict(os.environ, {"TERMINAL_SSH_HOST": "1.2.3.4"}, clear=False):

            def fake_reload():
                os.environ["TERMINAL_SSH_HOST"] = "5.6.7.8"
                return 1

            mock_reload.side_effect = fake_reload
            cli.process_command("/reload")

        mock_cleanup.assert_called_once()

    @patch("hermes_cli.config.reload_env", return_value=1)
    @patch("tools.terminal_tool.cleanup_all_environments", return_value=1)
    def test_reload_detects_ssh_key_change(
        self, mock_cleanup, mock_reload, capsys
    ):
        """SSH key path change should also trigger cleanup."""
        cli = self._make_cli_instance()

        with patch.dict(os.environ, {"TERMINAL_SSH_KEY": "/old/key"}, clear=False):

            def fake_reload():
                os.environ["TERMINAL_SSH_KEY"] = "/new/key"
                return 1

            mock_reload.side_effect = fake_reload
            cli.process_command("/reload")

        mock_cleanup.assert_called_once()

    @patch("hermes_cli.config.reload_env", return_value=1)
    @patch("tools.terminal_tool.cleanup_all_environments", return_value=1)
    def test_reload_detects_ssh_user_change(
        self, mock_cleanup, mock_reload, capsys
    ):
        """SSH user change should also trigger cleanup."""
        cli = self._make_cli_instance()

        with patch.dict(os.environ, {"TERMINAL_SSH_USER": "root"}, clear=False):

            def fake_reload():
                os.environ["TERMINAL_SSH_USER"] = "ubuntu"
                return 1

            mock_reload.side_effect = fake_reload
            cli.process_command("/reload")

        mock_cleanup.assert_called_once()
