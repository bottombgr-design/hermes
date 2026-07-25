"""Cross-session HERMES_SESSION_ID leak via the shared bash snapshot.

Regression coverage for the bug where a single long-lived backend serves many
sessions through ONE ``_active_environments["default"]`` LocalEnvironment (the
messaging gateway, TUI, and desktop/web dashboard all collapse the terminal to
"default"). That environment persists a bash *session snapshot* file and
``source``s it before every command. ``export -p`` dumped the FIRST session's
``HERMES_SESSION_ID`` into the snapshot, so every LATER session ``source``d that
stale value and its ``echo $HERMES_SESSION_ID`` reported a FOREIGN session's id
— overriding the correct per-command Popen env injected by
``_inject_session_context_env``.

The fix strips the per-session bridged vars (HERMES_SESSION_* / UI /
CRON_AUTO_DELIVER_) from the snapshot at both dump sites in
``tools/environments/base.py``; they are re-injected fresh on every command.
"""

import os
import re
import subprocess
import sys

import pytest

from tools.environments.base import (
    BaseEnvironment,
    _LOCAL_SNAPSHOT_EXCLUDED_ENV_REGEX,
    _SNAPSHOT_EXCLUDED_ENV_REGEX,
    _export_dump_excluding_session_vars,
)


# ---------------------------------------------------------------------------
# Unit: the exclusion regex matches exactly the bridged vars, nothing else.
# ---------------------------------------------------------------------------

def test_regex_matches_bridged_session_vars():
    rx = re.compile(_SNAPSHOT_EXCLUDED_ENV_REGEX)
    # Every var the gateway bridges must be excluded.
    from gateway.session_context import _VAR_MAP

    for name in _VAR_MAP:
        line = f'declare -x {name}="whatever"'
        assert rx.search(line), f"{name} should be excluded from the snapshot"


def test_local_regex_excludes_structural_env_and_preserves_user_env():
    rx = re.compile(_LOCAL_SNAPSHOT_EXCLUDED_ENV_REGEX)
    # HOME and HERMES_HOME are process/profile identity, not user shell state.
    # A shared persistent backend serves unrelated sessions; allowing one command
    # to snapshot either variable redirects every later session into its home.
    for line in (
        'declare -x HOME="/tmp/foreign-home"',
        'declare -x HERMES_HOME="/tmp/foreign-hermes"',
        'declare -x HERMES_REAL_HOME="/tmp/foreign-real-home"',
    ):
        assert rx.search(line), f"{line!r} must be excluded from the snapshot"

    for line in (
        'declare -x PATH="/usr/bin:/bin"',
        'declare -x HERMESX="x"',
        'declare -x HERMES_REAL_HOMEX="x"',
        'declare -x MY_HERMES_SESSION_ID="x"',  # prefix must anchor after "declare -x "
    ):
        assert not rx.search(line), f"{line!r} must be preserved in the snapshot"


def test_base_regex_preserves_structural_bootstrap_env():
    """Init-only backends rely on the snapshot after bootstrap."""
    rx = re.compile(_SNAPSHOT_EXCLUDED_ENV_REGEX)
    assert not rx.search('declare -x HOME="/home/container-user"')
    assert not rx.search('declare -x HERMES_HOME="/srv/hermes"')
    assert rx.search('declare -x HERMES_SESSION_ID="foreign"')


def test_export_snippet_shape():
    snippet = _export_dump_excluding_session_vars('"$__hermes_snap_tmp"')
    assert "export -p" in snippet
    # Unset-by-name (not line-grep): multi-line declare values must not leave
    # continuation lines in the snapshot (issue #71296).
    assert "unset" in snippet
    assert "${!HERMES_SESSION_*}" in snippet
    assert "${!HERMES_CRON_AUTO_DELIVER_*}" in snippet
    assert "HERMES_UI_SESSION_ID" in snippet
    assert "grep -vE" not in snippet
    assert '"$__hermes_snap_tmp"' in snippet
    # The redirection must be attached to the brace-group dump.
    assert snippet.lstrip().startswith("{ ")
    assert "|| true; }" in snippet
    assert snippet.rstrip().endswith('> "$__hermes_snap_tmp"')

    local_snippet = _export_dump_excluding_session_vars(
        '"$__hermes_snap_tmp"', exclude_structural_home=True
    )
    assert "unset" in local_snippet
    assert "HOME HERMES_HOME HERMES_REAL_HOME" in local_snippet
    assert "grep -vE" not in local_snippet


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX bash backend model")
def test_init_only_backend_preserves_bootstrap_hermes_home(tmp_path):
    """Docker-like backends must retain env forwarded only during init."""

    bootstrap_home = tmp_path / "bootstrap-home"
    bootstrap_hermes = bootstrap_home / ".hermes"
    bootstrap_home.mkdir()
    bootstrap_hermes.mkdir()

    class InitOnlyEnvironment(BaseEnvironment):
        def __init__(self):
            self._temp_dir = str(tmp_path)
            super().__init__(cwd=str(tmp_path), timeout=30)

        def get_temp_dir(self):
            return self._temp_dir

        def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
            # Model Docker's contract: configured values are forwarded to the
            # bootstrap exec only. Later spawns receive native HOME and recover
            # HERMES_HOME from the persisted snapshot.
            env = {"HOME": str(bootstrap_home), "PATH": os.environ["PATH"]}
            if login:
                env["HERMES_HOME"] = str(bootstrap_hermes)
                env["MY_BOOTSTRAP_VAR"] = "works"
            return subprocess.Popen(
                ["/bin/bash", "-c", cmd_string],
                cwd=self.cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )

        def cleanup(self):
            for path in (self._snapshot_path, self._cwd_file):
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass

    env = InitOnlyEnvironment()
    env.init_session()
    try:
        observed = env.execute('printf "%s\\n%s\\n" "$HERMES_HOME" "$MY_BOOTSTRAP_VAR"')
        assert observed.get("returncode") == 0
        assert observed.get("output", "").splitlines()[:2] == [
            str(bootstrap_hermes),
            "works",
        ]
    finally:
        env.cleanup()


# ---------------------------------------------------------------------------
# Integration: real LocalEnvironment, two sessions, no cross-contamination.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX bash snapshot path")
def test_shared_snapshot_no_cross_session_leak(tmp_path):
    import threading

    from gateway.session_context import _VAR_MAP, _UNSET, set_session_vars
    from tools.environments.local import LocalEnvironment

    env = LocalEnvironment(cwd=str(tmp_path), timeout=30)
    env.init_session()
    try:
        def run_as(sid):
            out = {}

            def worker():
                for v in _VAR_MAP.values():
                    v.set(_UNSET)
                set_session_vars(session_key="k" + sid, session_id=sid, source="desktop")
                out["r"] = env.execute('echo "[$HERMES_SESSION_ID]"')

            t = threading.Thread(target=worker)
            t.start()
            t.join()
            return out["r"].get("output", "")

        out_a = run_as("SIDAAA")
        out_b = run_as("SIDBBB")

        assert "SIDAAA" in out_a, f"session A saw {out_a!r}"
        # The core assertion: B must see its OWN id, not A's leaked via snapshot.
        assert "SIDBBB" in out_b, f"session B saw {out_b!r}"
        assert "SIDAAA" not in out_b, f"session B leaked A's id: {out_b!r}"

        # And the snapshot file must not carry the session id at all.
        snap = env._snapshot_path
        if os.path.exists(snap):
            with open(snap) as f:
                assert "HERMES_SESSION_ID" not in f.read()
    finally:
        env.cleanup()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX bash snapshot path")
def test_shared_snapshot_does_not_persist_structural_home_vars(tmp_path):
    """One session's test profile must not redirect later sessions."""
    from tools.environments.local import LocalEnvironment

    canonical_home = tmp_path / "owner-home"
    canonical_hermes = canonical_home / ".hermes"
    canonical_real_home = tmp_path / "owner-real-home"
    canonical_home.mkdir()
    canonical_hermes.mkdir()
    canonical_real_home.mkdir()
    env = LocalEnvironment(
        cwd=str(tmp_path),
        timeout=30,
        env={
            "HOME": str(canonical_home),
            "HERMES_HOME": str(canonical_hermes),
            "HERMES_REAL_HOME": str(canonical_real_home),
        },
    )
    env.init_session()
    try:
        poisoned_home = tmp_path / "foreign-home"
        poisoned_hermes = poisoned_home / "hermes-home"
        poisoned_real_home = tmp_path / "foreign-real-home"
        result = env.execute(
            f'export HOME="{poisoned_home}"; '
            f'export HERMES_HOME="{poisoned_hermes}"; '
            f'export HERMES_REAL_HOME="{poisoned_real_home}"; '
            'export HERMES_REAL_HOMEX="control"; true'
        )
        assert result.get("returncode") == 0

        observed = env.execute(
            'printf "%s\\n%s\\n%s\\n%s\\n" '
            '"$HOME" "$HERMES_HOME" "$HERMES_REAL_HOME" "$HERMES_REAL_HOMEX"'
        )
        assert observed.get("returncode") == 0
        assert observed.get("output", "").splitlines()[:4] == [
            str(canonical_home),
            str(canonical_hermes),
            str(canonical_real_home),
            "control",
        ]
        with open(env._snapshot_path) as f:
            snapshot = f.read()
        assert "declare -x HOME=" not in snapshot
        assert "declare -x HERMES_HOME=" not in snapshot
        assert "declare -x HERMES_REAL_HOME=" not in snapshot
        assert 'declare -x HERMES_REAL_HOMEX="control"' in snapshot
    finally:
        env.cleanup()
