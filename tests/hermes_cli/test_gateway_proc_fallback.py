"""Tests for /proc-based gateway PID detection in Docker environments.

Verifies that _scan_gateway_pids() uses /proc/*/cmdline when available
(Docker without procps) and falls back to ps only when /proc is absent.

See: NousResearch/hermes-agent#7622
"""

import os
from unittest.mock import MagicMock, patch

import hermes_cli.gateway as gateway_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GATEWAY_CMD = "python -m hermes_cli.main gateway run"
_OTHER_CMD = "python -m some_other_thing"


def _fake_proc_dir(entries: dict):
    """Return side_effects that simulate /proc: isdir → True, listdir → pids,
    open(cmdline) → null-delimited command bytes."""
    def _isdir(path):
        return str(path) == "/proc"

    def _listdir(path):
        if str(path) == "/proc":
            return [str(pid) for pid in entries] + ["self", "version"]
        raise FileNotFoundError(path)

    def _open(path, mode="r", **kwargs):
        path_str = str(path)
        if "/cmdline" in path_str:
            pid = int(path_str.split("/proc/")[1].split("/")[0])
            raw = entries.get(pid, "").encode("utf-8").replace(b" ", b"\x00")
            m = MagicMock()
            m.read.return_value = raw
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            return m
        raise FileNotFoundError(path)

    return _isdir, _listdir, _open


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProcFallback:
    """_scan_gateway_pids reads /proc when available, skips ps."""

    def test_detects_gateway_pid_via_proc(self):
        my_pid = os.getpid()
        entries = {
            my_pid: "python -m hermes_cli.main",   # own process — excluded
            12345: _GATEWAY_CMD,
            99999: _OTHER_CMD,
        }
        _isdir, _listdir, _open = _fake_proc_dir(entries)

        with (
            patch("hermes_cli.gateway.is_windows", return_value=False),
            patch("os.path.isdir", side_effect=_isdir),
            patch("os.listdir", side_effect=_listdir),
            patch("builtins.open", side_effect=_open),
            patch("hermes_cli.gateway._get_ancestor_pids", return_value=set()),
            patch("subprocess.run") as mock_ps,
        ):
            pids = gateway_mod._scan_gateway_pids(set(), all_profiles=True)

        assert 12345 in pids
        assert 99999 not in pids
        mock_ps.assert_not_called()  # ps must NOT be called when /proc worked

    def test_detects_no_supervisor_restart_process_only_when_enabled(self):
        entries = {
            12345: "python -m hermes_cli.main gateway restart",
            99999: _OTHER_CMD,
        }
        _isdir, _listdir, _open = _fake_proc_dir(entries)

        with (
            patch("hermes_cli.gateway.is_windows", return_value=False),
            patch("os.path.isdir", side_effect=_isdir),
            patch("os.listdir", side_effect=_listdir),
            patch("builtins.open", side_effect=_open),
            patch("hermes_cli.gateway._get_ancestor_pids", return_value=set()),
            patch("subprocess.run") as mock_ps,
        ):
            strict_pids = gateway_mod._scan_gateway_pids(set(), all_profiles=True)

        _isdir, _listdir, _open = _fake_proc_dir(entries)
        with (
            patch("hermes_cli.gateway.is_windows", return_value=False),
            patch("os.path.isdir", side_effect=_isdir),
            patch("os.listdir", side_effect=_listdir),
            patch("builtins.open", side_effect=_open),
            patch("hermes_cli.gateway._get_ancestor_pids", return_value=set()),
            patch("subprocess.run") as mock_ps_enabled,
        ):
            fallback_pids = gateway_mod._scan_gateway_pids(
                set(),
                all_profiles=True,
                include_restart_managers=True,
            )

        assert strict_pids == []
        assert fallback_pids == [12345]
        mock_ps.assert_not_called()
        mock_ps_enabled.assert_not_called()

    def test_excludes_own_pid_from_proc_scan(self):
        my_pid = os.getpid()
        entries = {my_pid: _GATEWAY_CMD}
        _isdir, _listdir, _open = _fake_proc_dir(entries)

        with (
            patch("hermes_cli.gateway.is_windows", return_value=False),
            patch("os.path.isdir", side_effect=_isdir),
            patch("os.listdir", side_effect=_listdir),
            patch("builtins.open", side_effect=_open),
            patch("hermes_cli.gateway._get_ancestor_pids", return_value=set()),
            patch("subprocess.run"),
        ):
            pids = gateway_mod._scan_gateway_pids(set(), all_profiles=True)

        assert my_pid not in pids

    def test_falls_back_to_ps_when_proc_absent(self):
        ps_output = f"12345 {_GATEWAY_CMD}\n99999 {_OTHER_CMD}\n"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ps_output

        with (
            patch("hermes_cli.gateway.is_windows", return_value=False),
            patch("os.path.isdir", return_value=False),
            patch("hermes_cli.gateway._get_ancestor_pids", return_value=set()),
            patch("subprocess.run", return_value=mock_result) as mock_ps,
        ):
            pids = gateway_mod._scan_gateway_pids(set(), all_profiles=True)

        mock_ps.assert_called_once()
        assert 12345 in pids

    def test_proc_permission_error_skips_pid(self):
        def _isdir(path):
            return str(path) == "/proc"

        def _listdir(path):
            if str(path) == "/proc":
                return ["12345", "self"]
            raise FileNotFoundError

        def _open(path, mode="r", **kwargs):
            raise PermissionError("no access")

        with (
            patch("hermes_cli.gateway.is_windows", return_value=False),
            patch("os.path.isdir", side_effect=_isdir),
            patch("os.listdir", side_effect=_listdir),
            patch("builtins.open", side_effect=_open),
            patch("hermes_cli.gateway._get_ancestor_pids", return_value=set()),
            patch("subprocess.run") as mock_ps,
        ):
            pids = gateway_mod._scan_gateway_pids(set(), all_profiles=True)

        # PermissionError swallowed — empty result, no crash
        assert 12345 not in pids
        mock_ps.assert_not_called()  # /proc dir existed, so ps not called


# ---------------------------------------------------------------------------
# BSD/macOS ps-flag compatibility (#73626)
# ---------------------------------------------------------------------------

class TestPsFallbackBsdCompat:
    """The ps fallback must not use flags that are illegal on BSD/macOS ps.

    macOS/BSD ``ps`` rejects ``-A eww`` with "illegal argument: eww" (rc 1),
    so the scan silently returned ``[]`` on every macOS machine.  The matcher
    only needs argv — environment variables (the ``e`` flag) are unnecessary.
    """

    def test_ps_fallback_finds_gateway_without_bsd_illegal_e_flag(self):
        """Simulate a BSD ps that rejects 'e'/'eww' but accepts '-Aww'.

        On the broken code the function sends ``-A eww`` → BSD ps rejects →
        returns ``[]``.  After the fix it sends ``-Aww`` → succeeds.
        """
        captured = []

        def _fake_run(cmd, **kwargs):
            captured.append(list(cmd))
            if cmd[:3] == ["ps", "-o", "ppid="]:
                m = MagicMock()
                m.returncode = 1
                m.stdout = ""
                m.stderr = ""
                return m
            ps_args = set(cmd)
            # BSD ps: standalone 'e' or 'eww' is illegal
            if "eww" in ps_args or (len(ps_args) == 2 and "e" in ps_args):
                m = MagicMock()
                m.returncode = 1
                m.stdout = ""
                m.stderr = "ps: illegal argument: eww"
                return m
            m = MagicMock()
            m.returncode = 0
            m.stdout = f"12345 {_GATEWAY_CMD}\n"
            m.stderr = ""
            return m

        with (
            patch("hermes_cli.gateway.is_windows", return_value=False),
            patch("os.path.isdir", return_value=False),  # no /proc
            patch("hermes_cli.gateway._get_ancestor_pids", return_value=set()),
            patch("subprocess.run", side_effect=_fake_run),
        ):
            pids = gateway_mod._scan_gateway_pids(set(), all_profiles=True)

        # The pid-scan ps call must not include the BSD-illegal 'e' flag
        ps_scan_cmds = [c for c in captured if "pid=,command=" in c]
        assert ps_scan_cmds, "ps pid-scan was never invoked"
        assert "eww" not in ps_scan_cmds[0], (
            f"ps used BSD-illegal 'eww' flag: {ps_scan_cmds[0]}"
        )
        assert "-Aww" in ps_scan_cmds[0], (
            f"ps should use '-Aww' for BSD compatibility: {ps_scan_cmds[0]}"
        )
        # And the scan must actually find the gateway
        assert 12345 in pids, (
            f"scan returned {pids} — BSD-compatible ps should find PID 12345"
        )

    def test_ps_fallback_does_not_request_environment(self):
        """The ps command must not include the 'e' (show-env) flag at all."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = f"12345 {_GATEWAY_CMD}\n"
        mock_result.stderr = ""

        with (
            patch("hermes_cli.gateway.is_windows", return_value=False),
            patch("os.path.isdir", return_value=False),
            patch("hermes_cli.gateway._get_ancestor_pids", return_value=set()),
            patch("subprocess.run", return_value=mock_result) as mock_ps,
        ):
            gateway_mod._scan_gateway_pids(set(), all_profiles=True)

        mock_ps.assert_called_once()
        ps_cmd = mock_ps.call_args[0][0]
        # No standalone 'e' flag, no 'eww'
        assert "e" not in ps_cmd, (
            f"ps command must not request env vars (BSD-illegal): {ps_cmd}"
        )
        assert "eww" not in ps_cmd, f"ps used BSD-illegal 'eww': {ps_cmd}"
