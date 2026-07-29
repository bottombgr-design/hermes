"""Regression tests for profile alias wrapper scripts (#74074)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def wrapper_dir(tmp_path):
    """Redirect _get_wrapper_dir to a temp directory."""
    with patch("hermes_cli.profiles._get_wrapper_dir", return_value=tmp_path):
        yield tmp_path


@pytest.fixture()
def mock_which():
    """Mock shutil.which to return a predictable hermes path."""
    fake_exe = "/usr/local/bin/hermes" if sys.platform != "win32" else r"C:\Tools\hermes.exe"
    with patch("shutil.which", return_value=fake_exe):
        yield fake_exe


class TestCreateWrapperScript:
    """create_wrapper_script generates correct wrappers (#74074)."""

    def test_windows_bat_is_subcommand_agnostic_passthrough(self, wrapper_dir, mock_which):
        """The .bat wrapper passes all args through without hardcoding a
        subcommand — bare ``hermes -p <profile>`` defaults to chat on its
        own, and ``<alias> gateway start`` etc. must still forward."""
        with patch("hermes_cli.profiles.sys") as mock_sys:
            mock_sys.platform = "win32"
            from hermes_cli.profiles import create_wrapper_script

            result = create_wrapper_script("myprofile")

        assert result is not None
        assert result.suffix == ".bat"
        content = result.read_text(encoding="utf-8")
        assert "-p myprofile %*" in content
        # Must NOT hardcode a subcommand (regression: #74074 review)
        assert "chat" not in content

    def test_windows_bat_quotes_hermes_exe_path(self, wrapper_dir, mock_which):
        """The .bat must quote the hermes path to handle spaces (#74074)."""
        with patch("hermes_cli.profiles.sys") as mock_sys:
            mock_sys.platform = "win32"
            from hermes_cli.profiles import create_wrapper_script

            result = create_wrapper_script("myprofile")

        content = result.read_text(encoding="utf-8")
        # The exe path should be quoted in the .bat
        assert f'"{ mock_which}"' in content

    def test_windows_bat_bypasses_hermes_cmd_shim(self, wrapper_dir, mock_which):
        """The .bat calls the resolved hermes.exe directly, not bare 'hermes'
        which would resolve to hermes.cmd and inject -p default (#74074)."""
        with patch("hermes_cli.profiles.sys") as mock_sys:
            mock_sys.platform = "win32"
            from hermes_cli.profiles import create_wrapper_script

            result = create_wrapper_script("myprofile")

        content = result.read_text(encoding="utf-8")
        # Must use the resolved exe path (quoted), not bare 'hermes'
        assert f'"{mock_which}" -p myprofile' in content

    def test_windows_creates_bash_script_alongside_bat(self, wrapper_dir, mock_which):
        """On Windows, a bash script is also created for git-bash (#74074)."""
        with patch("hermes_cli.profiles.sys") as mock_sys:
            mock_sys.platform = "win32"
            from hermes_cli.profiles import create_wrapper_script

            create_wrapper_script("myprofile")

        bash_path = wrapper_dir / "myprofile"
        assert bash_path.exists()
        content = bash_path.read_text(encoding="utf-8")
        assert content.startswith("#!/bin/sh")
        assert '-p myprofile "$@"' in content
        # Must NOT hardcode a subcommand
        assert "chat" not in content

    def test_posix_script_is_subcommand_agnostic_passthrough(self, wrapper_dir, mock_which):
        """The POSIX wrapper passes all args through without hardcoding a
        subcommand (#74074)."""
        with patch("hermes_cli.profiles.sys") as mock_sys:
            mock_sys.platform = "linux"
            from hermes_cli.profiles import create_wrapper_script

            result = create_wrapper_script("myprofile")

        assert result is not None
        content = result.read_text(encoding="utf-8")
        assert '-p myprofile "$@"' in content
        assert "chat" not in content

    def test_custom_alias_target_profile(self, wrapper_dir, mock_which):
        """A custom alias name targets the correct profile."""
        with patch("hermes_cli.profiles.sys") as mock_sys:
            mock_sys.platform = "linux"
            from hermes_cli.profiles import create_wrapper_script

            result = create_wrapper_script("jarvis", target="work")

        content = result.read_text(encoding="utf-8")
        assert "-p work" in content
        assert "jarvis" not in content  # alias name only in filename, not content


class TestRemoveWrapperScript:
    """remove_wrapper_script removes all wrappers (#74074)."""

    def test_windows_removes_both_bat_and_bash(self, wrapper_dir):
        """On Windows, both .bat and bash script are removed."""
        bat = wrapper_dir / "myprofile.bat"
        bash = wrapper_dir / "myprofile"
        bat.write_text('@echo off\r\n"C:\\Tools\\hermes.exe" -p myprofile %*\r\n')
        bash.write_text('#!/bin/sh\nexec /c/Tools/hermes.exe -p myprofile "$@"\n')

        with patch("hermes_cli.profiles.sys") as mock_sys:
            mock_sys.platform = "win32"
            from hermes_cli.profiles import remove_wrapper_script

            result = remove_wrapper_script("myprofile")

        assert result is True
        assert not bat.exists()
        assert not bash.exists()
