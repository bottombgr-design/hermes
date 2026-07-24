import shutil
import subprocess

from pathlib import Path
from subprocess import CalledProcessError
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli import config as hermes_config
from hermes_cli import main as hermes_main


# ---------------------------------------------------------------------------
# Managed-uv compatibility for tests that patch shutil.which
# ---------------------------------------------------------------------------
# The production code now uses ``ensure_uv()`` / ``update_managed_uv()``
# instead of ``shutil.which("uv")``.  Many tests in this file patch
# ``shutil.which`` to control whether uv is "available" — these autouse
# fixtures make the managed_uv functions delegate to the patched
# ``shutil.which`` so the existing test setup keeps working without
# per-test changes.
@pytest.fixture(autouse=True)
def _patch_managed_uv(request):
    """Make managed_uv helpers follow shutil.which mocking in tests."""
    import shutil

    # resolve_uv delegates to shutil.which("uv") so that test patches
    # on shutil.which flow through naturally.
    def _fake_resolve_uv(**kwargs):
        return shutil.which("uv")

    def _fake_ensure_uv(**kwargs):
        return shutil.which("uv")

    def _fake_update_managed_uv(**kwargs):
        return None  # never actually self-update in tests

    with patch("hermes_cli.managed_uv.resolve_uv", side_effect=_fake_resolve_uv), \
         patch("hermes_cli.managed_uv.ensure_uv", side_effect=_fake_ensure_uv), \
         patch("hermes_cli.managed_uv.update_managed_uv", side_effect=_fake_update_managed_uv):
        yield

def test_stash_local_changes_if_needed_returns_none_when_tree_clean(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[-2:] == ["status", "--porcelain"]:
            return SimpleNamespace(stdout="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    stash_ref = hermes_main._stash_local_changes_if_needed(["git"], tmp_path)

    assert stash_ref is None
    assert [cmd[-2:] for cmd, _ in calls] == [["status", "--porcelain"]]


def test_stash_local_changes_if_needed_returns_specific_stash_commit(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[-2:] == ["status", "--porcelain"]:
            return SimpleNamespace(stdout=" M hermes_cli/main.py\n?? notes.txt\n", returncode=0)
        if cmd[-2:] == ["ls-files", "--unmerged"]:
            return SimpleNamespace(stdout="", returncode=0)
        if cmd[1:4] == ["stash", "push", "--include-untracked"]:
            return SimpleNamespace(stdout="Saved working directory\n", returncode=0)
        if cmd[-3:] == ["rev-parse", "--verify", "refs/stash"]:
            return SimpleNamespace(stdout="abc123\n", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    stash_ref = hermes_main._stash_local_changes_if_needed(["git"], tmp_path)

    assert stash_ref == "abc123"
    assert calls[1][0][-2:] == ["ls-files", "--unmerged"]
    # Pre-push probe of refs/stash (baseline for detecting a fresh entry),
    # then the push, then the post-push probe.
    assert calls[2][0][-3:] == ["rev-parse", "--verify", "refs/stash"]
    assert calls[3][0][1:4] == ["stash", "push", "--include-untracked"]
    assert calls[4][0][-3:] == ["rev-parse", "--verify", "refs/stash"]





def test_restore_stashed_changes_prompts_before_applying(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:3] == ["stash", "apply"]:
            return SimpleNamespace(stdout="applied\n", stderr="", returncode=0)
        if cmd[1:3] == ["diff", "--name-only"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "list"]:
            return SimpleNamespace(stdout="stash@{1} abc123\n", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "drop"]:
            return SimpleNamespace(stdout="dropped\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)
    monkeypatch.setattr("builtins.input", lambda: "")

    restored = hermes_main._restore_stashed_changes(["git"], tmp_path, "abc123", prompt_user=True)

    assert restored is True
    assert calls[0][0] == ["git", "stash", "apply", "abc123"]
    assert calls[1][0] == ["git", "diff", "--name-only", "--diff-filter=U"]
    assert len(calls) == 2
    out = capsys.readouterr().out
    assert "Restore local changes now? [Y/n]" in out
    assert "restored on top of the updated codebase" in out
    assert "git diff" in out
    assert "git status" in out


def test_restore_stashed_changes_can_skip_restore_and_keep_stash(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)
    monkeypatch.setattr("builtins.input", lambda: "n")

    restored = hermes_main._restore_stashed_changes(["git"], tmp_path, "abc123", prompt_user=True)

    assert restored is None
    assert calls == []
    out = capsys.readouterr().out
    assert "Restore local changes now? [Y/n]" in out
    assert "Your changes are still preserved in git stash." in out
    assert "git stash apply abc123" in out


def test_restore_stashed_changes_applies_without_prompt_when_disabled(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:3] == ["stash", "apply"]:
            return SimpleNamespace(stdout="applied\n", stderr="", returncode=0)
        if cmd[1:3] == ["diff", "--name-only"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "list"]:
            return SimpleNamespace(stdout="stash@{0} abc123\n", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "drop"]:
            return SimpleNamespace(stdout="dropped\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    restored = hermes_main._restore_stashed_changes(["git"], tmp_path, "abc123", prompt_user=False)

    assert restored is True
    assert calls[0][0] == ["git", "stash", "apply", "abc123"]
    assert calls[1][0] == ["git", "diff", "--name-only", "--diff-filter=U"]
    assert len(calls) == 2
    assert "Restore local changes now?" not in capsys.readouterr().out






def test_restore_stashed_changes_keeps_going_when_stash_entry_cannot_be_resolved(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:3] == ["stash", "apply"]:
            return SimpleNamespace(stdout="applied\n", stderr="", returncode=0)
        if cmd[1:3] == ["diff", "--name-only"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "list"]:
            return SimpleNamespace(stdout="stash@{0} def456\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    restored = hermes_main._restore_stashed_changes(["git"], tmp_path, "abc123", prompt_user=False)

    assert restored is True
    _utf8 = {"encoding": "utf-8", "errors": "replace"}
    assert calls[0] == (["git", "stash", "apply", "abc123"], {"cwd": tmp_path, "capture_output": True, "text": True, **_utf8})
    assert calls[1] == (["git", "diff", "--name-only", "--diff-filter=U"], {"cwd": tmp_path, "capture_output": True, "text": True, **_utf8})
    assert len(calls) == 2
    out = capsys.readouterr().out
    assert "immutable autostash is retained" in out
    assert "abc123" in out



def test_restore_stashed_changes_keeps_going_when_drop_fails(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:3] == ["stash", "apply"]:
            return SimpleNamespace(stdout="applied\n", stderr="", returncode=0)
        if cmd[1:3] == ["diff", "--name-only"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "list"]:
            return SimpleNamespace(stdout="stash@{0} abc123\n", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "drop"]:
            return SimpleNamespace(stdout="", stderr="drop failed\n", returncode=1)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    restored = hermes_main._restore_stashed_changes(["git"], tmp_path, "abc123", prompt_user=False)

    assert restored is True
    assert len(calls) == 2
    out = capsys.readouterr().out
    assert "immutable autostash is retained" in out
    assert "abc123" in out


def test_restore_stashed_changes_never_resets_on_conflict(monkeypatch, tmp_path, capsys):
    """Conflicts preserve the worktree and stash and return False."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:3] == ["stash", "apply"]:
            return SimpleNamespace(stdout="conflict output\n", stderr="conflict stderr\n", returncode=1)
        if cmd[1:3] == ["diff", "--name-only"]:
            return SimpleNamespace(stdout="hermes_cli/main.py\n", stderr="", returncode=0)
        if cmd[1:3] == ["reset", "--hard"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)
    monkeypatch.setattr("builtins.input", lambda: "y")

    result = hermes_main._restore_stashed_changes(["git"], tmp_path, "abc123", prompt_user=True)

    assert result is False
    out = capsys.readouterr().out
    assert "Conflicted files:" in out
    assert "hermes_cli/main.py" in out
    assert "The stash remains preserved" in out
    assert "will not reset this worktree" in out
    reset_calls = [c for c, _ in calls if c[1:3] == ["reset", "--hard"]]
    assert reset_calls == []


def test_restore_stashed_changes_non_interactive_conflict_does_not_reset(monkeypatch, tmp_path, capsys):
    """Non-interactive conflicts preserve worktree state and return False."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:3] == ["stash", "apply"]:
            return SimpleNamespace(stdout="applied\n", stderr="", returncode=0)
        if cmd[1:3] == ["diff", "--name-only"]:
            return SimpleNamespace(stdout="cli.py\n", stderr="", returncode=0)
        if cmd[1:3] == ["reset", "--hard"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    result = hermes_main._restore_stashed_changes(["git"], tmp_path, "abc123", prompt_user=False)

    assert result is False
    out = capsys.readouterr().out
    assert "will not reset this worktree" in out
    reset_calls = [c for c, _ in calls if c[1:3] == ["reset", "--hard"]]
    assert reset_calls == []


def test_stash_local_changes_if_needed_raises_when_stash_ref_missing(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        if cmd[-2:] == ["status", "--porcelain"]:
            return SimpleNamespace(stdout=" M hermes_cli/main.py\n", returncode=0)
        if cmd[-2:] == ["ls-files", "--unmerged"]:
            return SimpleNamespace(stdout="", returncode=0)
        if cmd[1:4] == ["stash", "push", "--include-untracked"]:
            return SimpleNamespace(stdout="Saved working directory\n", returncode=0)
        if cmd[-3:] == ["rev-parse", "--verify", "refs/stash"]:
            raise CalledProcessError(returncode=128, cmd=cmd)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    with pytest.raises(CalledProcessError):
        hermes_main._stash_local_changes_if_needed(["git"], Path(tmp_path))


def test_discard_lockfile_churn_skips_lock_when_package_json_dirty(tmp_path):
    """Intentional dependency edits update package.json and lockfile together."""
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not available")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True
        )

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "package.json").write_text('{"dependencies":{"a":"1"}}\n')
    (tmp_path / "package-lock.json").write_text('{"lock":"old"}\n')
    git("add", "package.json", "package-lock.json")
    git("commit", "-qm", "init")

    (tmp_path / "package.json").write_text('{"dependencies":{"a":"2"}}\n')
    (tmp_path / "package-lock.json").write_text('{"lock":"new"}\n')

    hermes_main._discard_lockfile_churn(["git"], tmp_path)

    assert (tmp_path / "package-lock.json").read_text() == '{"lock":"new"}\n'


def test_discard_lockfile_churn_restores_lock_when_package_json_clean(tmp_path):
    """Runtime npm lockfile rewrites are still discarded on managed updates."""
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not available")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True
        )

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "package.json").write_text('{"dependencies":{"a":"1"}}\n')
    (tmp_path / "package-lock.json").write_text('{"lock":"old"}\n')
    git("add", "package.json", "package-lock.json")
    git("commit", "-qm", "init")

    (tmp_path / "package-lock.json").write_text('{"lock":"runtime-churn"}\n')

    hermes_main._discard_lockfile_churn(["git"], tmp_path)

    assert (tmp_path / "package-lock.json").read_text() == '{"lock":"old"}\n'


# ---------------------------------------------------------------------------
# Update uses .[all] with fallback to .
# ---------------------------------------------------------------------------

def _setup_update_mocks(monkeypatch, tmp_path):
    """Common setup for cmd_update tests without touching machine lifecycle."""
    (tmp_path / ".git").mkdir(exist_ok=True)
    monkeypatch.setattr(hermes_main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(hermes_main, "_pause_windows_gateways_for_update", lambda: None)
    monkeypatch.setattr(
        hermes_main, "_resume_windows_gateways_after_update", lambda _token: None
    )
    monkeypatch.setattr(
        hermes_main,
        "_capture_update_checkout_identity",
        lambda *a, **kw: {"ref": "refs/heads/main", "head": "old123", "error": None},
    )
    def _fake_sha(_git, _root, ref):
        if ref.startswith("refs/remotes/origin/"):
            return "target123"
        if ref.startswith("refs/heads/") or ref == "HEAD":
            return "old123"
        return None
    monkeypatch.setattr(hermes_main, "_git_update_commit_sha", _fake_sha)
    monkeypatch.setattr(
        hermes_main,
        "_ensure_update_merge_base",
        lambda *a, **kw: {"merge_base": "base123", "error": None, "fetch_steps": []},
    )
    monkeypatch.setattr(hermes_main, "_discard_lockfile_churn", lambda *a, **kw: None)
    monkeypatch.setattr(hermes_main, "_stash_local_changes_if_needed", lambda *a, **kw: None)
    monkeypatch.setattr(hermes_main, "_restore_stashed_changes", lambda *a, **kw: True)
    monkeypatch.setattr(
        hermes_main,
        "_apply_pinned_default_update",
        lambda *a, **kw: {"success": True, "safe_to_restore_stash": True, "error": None},
    )
    monkeypatch.setattr(hermes_main, "_rollback_pinned_default_update", lambda *a, **kw: True)
    monkeypatch.setattr(hermes_main, "_pinned_fast_forward_error", lambda *a, **kw: None)
    monkeypatch.setattr(hermes_config, "get_missing_env_vars", lambda required_only=True: [])
    monkeypatch.setattr(hermes_config, "get_missing_config_fields", lambda: [])
    monkeypatch.setattr(hermes_config, "check_config_version", lambda: (5, 5))
    monkeypatch.setattr(hermes_config, "migrate_config", lambda **kw: {"env_added": [], "config_added": []})
    monkeypatch.setattr(hermes_main, "_upgrade_pip_before_lazy_refresh", lambda *a, **kw: None)
    monkeypatch.setattr(hermes_main, "_refresh_active_lazy_features", lambda *a, **kw: True)
    empty_sync = {"copied": [], "updated": [], "user_modified": [], "cleaned": []}
    monkeypatch.setattr("tools.skills_sync.sync_skills", lambda *a, **kw: empty_sync)
    monkeypatch.setattr("hermes_cli.profiles.list_profiles", lambda: [])


def test_cmd_update_retries_optional_extras_individually_when_all_fails(monkeypatch, tmp_path, capsys):
    """When .[all] fails, update should keep base deps and retry extras individually."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(hermes_main, "_is_termux_env", lambda env=None: False)
    monkeypatch.setattr(hermes_main, "_load_installable_optional_extras", lambda group="all": ["matrix", "mcp"])

    recorded = []

    def fake_run(cmd, **kwargs):
        recorded.append(cmd)
        if cmd == ["git", "fetch", "origin", "main"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return SimpleNamespace(stdout="main\n", stderr="", returncode=0)
        if "rev-list" in cmd:
            return SimpleNamespace(stdout="1\n", stderr="", returncode=0)
        if cmd == ["git", "pull", "--ff-only", "origin", "main"]:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        if cmd == ["/usr/bin/uv", "pip", "install", "-e", ".[all]"]:
            raise CalledProcessError(returncode=1, cmd=cmd)
        if cmd == ["/usr/bin/uv", "pip", "install", "-e", "."]:
            return SimpleNamespace(returncode=0)
        if cmd == ["/usr/bin/uv", "pip", "install", "-e", ".[matrix]"]:
            raise CalledProcessError(returncode=1, cmd=cmd)
        if cmd == ["/usr/bin/uv", "pip", "install", "-e", ".[mcp]"]:
            return SimpleNamespace(returncode=0)
        # Catch-all must include stdout/stderr so consumers that parse
        # output (e.g. the dashboard-restart `ps -A` scan added in the
        # updater) don't crash on AttributeError.
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    hermes_main.cmd_update(SimpleNamespace())

    install_cmds = [c for c in recorded if "pip" in c and "install" in c]
    assert install_cmds == [
        ["/usr/bin/uv", "pip", "install", "-e", ".[all]"],
        ["/usr/bin/uv", "pip", "install", "-e", "."],
        ["/usr/bin/uv", "pip", "install", "-e", ".[matrix]"],
        ["/usr/bin/uv", "pip", "install", "-e", ".[mcp]"],
    ]

    out = capsys.readouterr().out
    assert "retrying extras individually" in out
    assert "Reinstalled optional extras individually: mcp" in out
    assert "Skipped optional extras that still failed: matrix" in out


def test_cmd_update_succeeds_with_extras(monkeypatch, tmp_path):
    """When .[all] succeeds, no fallback should be attempted."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(hermes_main, "_is_termux_env", lambda env=None: False)

    recorded = []

    def fake_run(cmd, **kwargs):
        recorded.append(cmd)
        if cmd == ["git", "fetch", "origin", "main"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return SimpleNamespace(stdout="main\n", stderr="", returncode=0)
        if "rev-list" in cmd:
            return SimpleNamespace(stdout="1\n", stderr="", returncode=0)
        if cmd == ["git", "pull", "--ff-only", "origin", "main"]:
            return SimpleNamespace(stdout="Updating\n", stderr="", returncode=0)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    hermes_main.cmd_update(SimpleNamespace())

    install_cmds = [c for c in recorded if "pip" in c and "install" in c]
    assert len(install_cmds) == 1
    assert ".[all]" in install_cmds[0]


def test_install_with_optional_fallback_honors_custom_group(monkeypatch):
    """Termux update path should target .[termux-all] when requested."""
    calls = []
    monkeypatch.setattr(
        hermes_main,
        "_load_installable_optional_extras",
        lambda group="all": ["termux", "mcp"] if group == "termux-all" else [],
    )

    def fake_run_with_heartbeat(cmd, **kwargs):
        calls.append(cmd)
        if cmd[-1] == ".[termux-all]":
            raise CalledProcessError(returncode=1, cmd=cmd)
        return None

    monkeypatch.setattr(hermes_main, "_run_install_with_heartbeat", fake_run_with_heartbeat)

    hermes_main._install_python_dependencies_with_optional_fallback(
        ["/usr/bin/uv", "pip"],
        group="termux-all",
    )

    assert calls == [
        ["/usr/bin/uv", "pip", "install", "-e", ".[termux-all]"],
        ["/usr/bin/uv", "pip", "install", "-e", "."],
        ["/usr/bin/uv", "pip", "install", "-e", ".[termux]"],
        ["/usr/bin/uv", "pip", "install", "-e", ".[mcp]"],
    ]


def test_install_heartbeat_prints_when_dependency_install_is_silent(monkeypatch, capsys):
    """Long quiet installs should emit periodic heartbeat lines."""

    def fake_run(cmd, **kwargs):
        hermes_main._time.sleep(1.2)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    hermes_main._run_install_with_heartbeat(
        ["uv", "pip", "install", "-e", "."],
        heartbeat_interval_seconds=1,
    )

    out = capsys.readouterr().out
    assert "still installing dependencies" in out


# ---------------------------------------------------------------------------
# ff-only fallback to reset --hard on diverged history
# ---------------------------------------------------------------------------

def _make_update_side_effect(
    current_branch="main",
    commit_count="3",
    ff_only_fails=False,
    reset_fails=False,
    fetch_fails=False,
    fetch_stderr="",
):
    """Build a subprocess.run side_effect for cmd_update tests."""
    recorded = []

    def side_effect(cmd, **kwargs):
        recorded.append(cmd)
        joined = " ".join(str(c) for c in cmd)
        if "fetch" in joined and "origin" in joined:
            if fetch_fails:
                return SimpleNamespace(stdout="", stderr=fetch_stderr, returncode=128)
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if "rev-parse" in joined and "--abbrev-ref" in joined:
            return SimpleNamespace(stdout=f"{current_branch}\n", stderr="", returncode=0)
        if "checkout" in joined and "main" in joined:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if "rev-list" in joined:
            return SimpleNamespace(stdout=f"{commit_count}\n", stderr="", returncode=0)
        if "--ff-only" in joined:
            if ff_only_fails:
                return SimpleNamespace(
                    stdout="",
                    stderr="fatal: Not possible to fast-forward, aborting.\n",
                    returncode=128,
                )
            return SimpleNamespace(stdout="Updating abc..def\n", stderr="", returncode=0)
        if "reset" in joined and "--hard" in joined:
            if reset_fails:
                return SimpleNamespace(stdout="", stderr="error: unable to write\n", returncode=1)
            return SimpleNamespace(stdout="HEAD is now at abc123\n", stderr="", returncode=0)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return side_effect, recorded


def test_cmd_update_uses_pinned_apply_without_pull_or_reset(monkeypatch, tmp_path):
    _setup_update_mocks(monkeypatch, tmp_path)
    applied = []
    monkeypatch.setattr(
        hermes_main,
        "_apply_pinned_default_update",
        lambda *args, **kwargs: applied.append(args) or {
            "success": True,
            "safe_to_restore_stash": True,
            "error": None,
        },
    )
    side_effect, recorded = _make_update_side_effect(ff_only_fails=True)
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    assert len(applied) == 1
    assert applied[0][2:5] == ("refs/heads/main", "old123", "target123")
    joined = [" ".join(c) for c in recorded]
    assert not any("pull --ff-only" in c for c in joined)
    assert not any("reset --hard" in c for c in joined)


def test_cmd_update_no_reset_when_ff_only_succeeds(monkeypatch, tmp_path):
    """When --ff-only succeeds, no reset is attempted."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)

    side_effect, recorded = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    reset_calls = [c for c in recorded if "reset" in c and "--hard" in c]
    assert len(reset_calls) == 0


# ---------------------------------------------------------------------------
# Non-main branch → auto-checkout main
# ---------------------------------------------------------------------------

def test_cmd_update_feature_checkout_is_pinned_before_apply(monkeypatch, tmp_path, capsys):
    _setup_update_mocks(monkeypatch, tmp_path)
    identity = {"ref": "refs/heads/fix/something", "head": "feature123", "error": None}
    monkeypatch.setattr(hermes_main, "_capture_update_checkout_identity", lambda *a: identity.copy())
    applied = []
    monkeypatch.setattr(
        hermes_main,
        "_apply_pinned_default_update",
        lambda *args, **kwargs: applied.append(args) or {
            "success": True,
            "safe_to_restore_stash": False,
            "error": None,
        },
    )
    side_effect, recorded = _make_update_side_effect(current_branch="fix/something")
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    assert applied[0][5:7] == ("refs/heads/fix/something", "feature123")
    assert not any("checkout" in c for c in recorded)
    assert "pinned checkout" in capsys.readouterr().out


def test_cmd_update_detached_checkout_is_pinned_before_apply(monkeypatch, tmp_path, capsys):
    _setup_update_mocks(monkeypatch, tmp_path)
    identity = {"ref": None, "head": "detached123", "error": None}
    monkeypatch.setattr(hermes_main, "_capture_update_checkout_identity", lambda *a: identity.copy())
    applied = []
    monkeypatch.setattr(
        hermes_main,
        "_apply_pinned_default_update",
        lambda *args, **kwargs: applied.append(args) or {
            "success": True,
            "safe_to_restore_stash": False,
            "error": None,
        },
    )
    side_effect, recorded = _make_update_side_effect(current_branch="HEAD")
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    assert applied[0][5:7] == (None, "detached123")
    assert not any("checkout" in c for c in recorded)
    assert "detached HEAD" in capsys.readouterr().out


def test_cmd_update_exact_target_skips_stash_and_checkout(monkeypatch, tmp_path, capsys):
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        hermes_main,
        "_git_update_commit_sha",
        lambda _git, _root, ref: "same123",
    )
    monkeypatch.setattr(
        hermes_main,
        "_stash_local_changes_if_needed",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no-op update must not stash")),
    )
    side_effect, recorded = _make_update_side_effect(commit_count="0")
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    assert not any("checkout" in c for c in recorded)
    assert "Already up to date" in capsys.readouterr().out


def test_cmd_update_no_checkout_when_already_on_main(monkeypatch, tmp_path):
    """When already on main, no checkout is needed."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)

    side_effect, recorded = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    checkout_calls = [c for c in recorded if "checkout" in c]
    assert len(checkout_calls) == 0


def test_cmd_update_fetch_is_scoped_to_target_branch(monkeypatch, tmp_path):
    _setup_update_mocks(monkeypatch, tmp_path)
    side_effect, recorded = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    fetch_calls = [c for c in recorded if "fetch" in c]
    assert len(fetch_calls) == 1
    assert fetch_calls[0][-3:] == ["fetch", "origin", "main"]


# ---------------------------------------------------------------------------
# Fetch failure — friendly error messages
# ---------------------------------------------------------------------------

def test_cmd_update_network_error_shows_friendly_message(monkeypatch, tmp_path, capsys):
    """Network failures during fetch show a user-friendly message."""
    _setup_update_mocks(monkeypatch, tmp_path)

    side_effect, _ = _make_update_side_effect(
        fetch_fails=True,
        fetch_stderr="fatal: unable to access 'https://...': Could not resolve host: github.com",
    )
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    with pytest.raises(SystemExit, match="1"):
        hermes_main.cmd_update(SimpleNamespace())

    out = capsys.readouterr().out
    assert "Network error" in out


def test_cmd_update_auth_error_shows_friendly_message(monkeypatch, tmp_path, capsys):
    """Auth failures during fetch show a user-friendly message."""
    _setup_update_mocks(monkeypatch, tmp_path)

    side_effect, _ = _make_update_side_effect(
        fetch_fails=True,
        fetch_stderr="fatal: Authentication failed for 'https://...'",
    )
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    with pytest.raises(SystemExit, match="1"):
        hermes_main.cmd_update(SimpleNamespace())

    out = capsys.readouterr().out
    assert "Authentication failed" in out


# ---------------------------------------------------------------------------
# reset --hard failure — don't attempt stash restore
# ---------------------------------------------------------------------------

def test_cmd_update_preserves_stash_when_pinned_apply_fails(monkeypatch, tmp_path, capsys):
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        hermes_main, "_stash_local_changes_if_needed", lambda *a, **kw: "abc123deadbeef"
    )
    restore_calls = []
    monkeypatch.setattr(
        hermes_main,
        "_restore_stashed_changes",
        lambda *a, **kw: restore_calls.append(1) or True,
    )
    monkeypatch.setattr(
        hermes_main,
        "_apply_pinned_default_update",
        lambda *a, **kw: {
            "success": False,
            "safe_to_restore_stash": False,
            "error": "Local target branch changed during compare-and-swap",
        },
    )
    side_effect, _ = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    with pytest.raises(SystemExit, match="1"):
        hermes_main.cmd_update(SimpleNamespace())

    assert restore_calls == []
    out = capsys.readouterr().out
    assert "preserved in stash" in out
    assert "compare-and-swap" in out


# ---------------------------------------------------------------------------
# Non-interactive update.non_interactive_local_changes setting
# (chat app / gateway): "discard" throws stashed changes away, "stash"
# (default) restores them. Interactive terminal updates ignore the setting
# and always go through the restore path.
# ---------------------------------------------------------------------------

def _setup_setting_test(monkeypatch, tmp_path, mode):
    """Common wiring: real stash returns a ref, restore + discard are
    recorded, and load_config reports the given non_interactive_local_changes
    mode."""
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(
        hermes_main, "_stash_local_changes_if_needed",
        lambda *a, **kw: "abc123deadbeef",
    )
    restore_calls = []
    discard_calls = []
    monkeypatch.setattr(
        hermes_main, "_restore_stashed_changes",
        lambda *a, **kw: restore_calls.append(1) or True,
    )
    monkeypatch.setattr(
        hermes_main, "_discard_stashed_changes",
        lambda *a, **kw: discard_calls.append(1) or True,
    )
    monkeypatch.setattr(
        hermes_config, "load_config",
        lambda *a, **kw: {"updates": {"non_interactive_local_changes": mode}},
    )
    side_effect, recorded = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)
    return restore_calls, discard_calls, recorded


def test_non_interactive_discard_throws_changes_away(monkeypatch, tmp_path):
    """Gateway/chat-app update with discard mode drops the stash, never restores."""
    restore_calls, discard_calls, _ = _setup_setting_test(monkeypatch, tmp_path, "discard")

    hermes_main.cmd_update(SimpleNamespace(gateway=True))

    assert len(discard_calls) == 1
    assert len(restore_calls) == 0


def test_non_interactive_stash_restores_changes(monkeypatch, tmp_path):
    """Gateway/chat-app update with the default stash mode restores, never discards."""
    restore_calls, discard_calls, _ = _setup_setting_test(monkeypatch, tmp_path, "stash")

    hermes_main.cmd_update(SimpleNamespace(gateway=True))

    assert len(restore_calls) == 1
    assert len(discard_calls) == 0


def test_interactive_update_ignores_discard_setting(monkeypatch, tmp_path):
    """An interactive (TTY) terminal update always restores — the discard
    setting only governs non-interactive updates."""
    restore_calls, discard_calls, _ = _setup_setting_test(monkeypatch, tmp_path, "discard")
    # Force an interactive TTY so _non_interactive_update is False even though
    # the config says discard.
    monkeypatch.setattr(hermes_main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(hermes_main.sys.stdout, "isatty", lambda: True)

    hermes_main.cmd_update(SimpleNamespace())  # no gateway, no --yes

    assert len(restore_calls) == 1
    assert len(discard_calls) == 0


def test_non_interactive_defaults_to_stash_when_setting_absent(monkeypatch, tmp_path):
    """A config with no update section falls back to stash (safe default)."""
    restore_calls, discard_calls, _ = _setup_setting_test(monkeypatch, tmp_path, "stash")
    # Override load_config to return a config with NO update section at all.
    monkeypatch.setattr(hermes_config, "load_config", lambda *a, **kw: {"model": {}})

    hermes_main.cmd_update(SimpleNamespace(gateway=True))

    assert len(restore_calls) == 1
    assert len(discard_calls) == 0


def test_bootstrap_marker_not_autostashed_by_update(tmp_path):
    """#38529: the Desktop bootstrap marker must be git-ignored so that
    ``hermes update``'s ``git stash push --include-untracked`` does not sweep it
    into an autostash on every run.

    Behavioral + hermetic: build a throwaway repo that adopts the project's real
    ``.gitignore`` (the contract under test), drop the marker, and confirm the
    same stash invocation the updater uses leaves it untouched.
    """
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not available")

    repo_gitignore = Path(hermes_main.__file__).resolve().parents[1] / ".gitignore"

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True
        )

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / ".gitignore").write_text(repo_gitignore.read_text())
    (tmp_path / "tracked.txt").write_text("x\n")
    git("add", "-A")
    git("commit", "-qm", "init")

    marker = tmp_path / ".hermes-bootstrap-complete"
    marker.write_text("")

    # Exact flags used by hermes update (hermes_cli/main.py).
    git("stash", "push", "--include-untracked", "-m", "hermes-update-autostash")

    assert marker.exists(), (
        ".hermes-bootstrap-complete was swept into the update autostash — it must "
        "be listed in .gitignore so `git stash -u` skips it (#38529)."
    )
    # It must not even register as a dirty/untracked change.
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert ".hermes-bootstrap-complete" not in status


def test_install_method_marker_not_autostashed_by_update(tmp_path):
    """#66189: the installer ``.install_method`` stamp must be git-ignored so
    ``hermes update``'s ``git stash push --include-untracked`` does not sweep it
    into an autostash on every run.

    ``scripts/install.sh`` writes ``$INSTALL_DIR/.install_method`` as runtime
    metadata; it is a sibling of ``.hermes-bootstrap-complete`` /
    ``.update-incomplete`` and must be ignored the same way. Behavioral +
    hermetic: adopt the project's real ``.gitignore`` (the contract under test),
    drop the marker, and confirm the exact stash invocation the updater uses
    leaves it untouched.
    """
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not available")

    repo_gitignore = Path(hermes_main.__file__).resolve().parents[1] / ".gitignore"

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True
        )

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / ".gitignore").write_text(repo_gitignore.read_text())
    (tmp_path / "tracked.txt").write_text("x\n")
    git("add", "-A")
    git("commit", "-qm", "init")

    marker = tmp_path / ".install_method"
    marker.write_text("managed\n")

    # Exact flags used by hermes update (hermes_cli/main.py).
    git("stash", "push", "--include-untracked", "-m", "hermes-update-autostash")

    assert marker.exists(), (
        ".install_method was swept into the update autostash — it must be listed "
        "in .gitignore so `git stash -u` skips it (#66189)."
    )
    # It must not even register as a dirty/untracked change.
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert ".install_method" not in status


# ---------------------------------------------------------------------------
# Permission-denied autostash class: undeletable untracked files (root-owned
# packaging/ etc.) must not abort the update when the stash entry was created.
# ---------------------------------------------------------------------------


def test_stash_push_partial_removal_failure_continues_when_stash_created(
    monkeypatch, tmp_path, capsys
):
    """git stash push exits 1 ("failed to remove ...: Permission denied") but
    the stash entry exists → treat as success, return the new ref."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[-2:] == ["status", "--porcelain"]:
            return SimpleNamespace(stdout=" M x.py\n?? packaging/\n", returncode=0)
        if cmd[-2:] == ["ls-files", "--unmerged"]:
            return SimpleNamespace(stdout="", returncode=0)
        if cmd[-3:] == ["rev-parse", "--verify", "refs/stash"]:
            # Before push: no stash. After push: new entry.
            probes = [c for c, _ in calls if c[-3:] == ["rev-parse", "--verify", "refs/stash"]]
            if len(probes) == 1:
                return SimpleNamespace(stdout="", returncode=1)
            return SimpleNamespace(stdout="newref123\n", returncode=0)
        if cmd[1:4] == ["stash", "push", "--include-untracked"]:
            return SimpleNamespace(
                stdout="Saved working directory and index state\n",
                stderr=(
                    "warning: failed to remove packaging/homebrew/hermes-agent.rb: "
                    "Permission denied\n"
                ),
                returncode=1,
            )
        if cmd[1:3] == ["reset", "--hard"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    stash_ref = hermes_main._stash_local_changes_if_needed(["git"], tmp_path)

    assert stash_ref == "newref123"
    # Tracked mods are saved in the stash but the failed push leaves them in
    # the tree — the follow-up reset must run so the checkout/pull can proceed.
    assert any(c[1:3] == ["reset", "--hard"] for c, _ in calls)
    out = capsys.readouterr().out
    assert "could not be removed" in out
    assert "update will continue" in out


def test_stash_push_failure_without_stash_entry_still_raises(monkeypatch, tmp_path, capsys):
    """git stash push fails AND no stash entry was created → real failure."""

    def fake_run(cmd, **kwargs):
        if cmd[-2:] == ["status", "--porcelain"]:
            return SimpleNamespace(stdout=" M x.py\n", returncode=0)
        if cmd[-2:] == ["ls-files", "--unmerged"]:
            return SimpleNamespace(stdout="", returncode=0)
        if cmd[-3:] == ["rev-parse", "--verify", "refs/stash"]:
            return SimpleNamespace(stdout="", returncode=1)
        if cmd[1:4] == ["stash", "push", "--include-untracked"]:
            return SimpleNamespace(
                stdout="", stderr="fatal: unable to write new index file\n",
                returncode=1, args=cmd,
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    with pytest.raises(CalledProcessError):
        hermes_main._stash_local_changes_if_needed(["git"], tmp_path)
    out = capsys.readouterr().out
    assert "update aborted" in out


def test_stash_push_failure_with_preexisting_stash_unchanged_still_raises(
    monkeypatch, tmp_path
):
    """A pre-existing stash entry must not be mistaken for a fresh save."""

    def fake_run(cmd, **kwargs):
        if cmd[-2:] == ["status", "--porcelain"]:
            return SimpleNamespace(stdout=" M x.py\n", returncode=0)
        if cmd[-2:] == ["ls-files", "--unmerged"]:
            return SimpleNamespace(stdout="", returncode=0)
        if cmd[-3:] == ["rev-parse", "--verify", "refs/stash"]:
            return SimpleNamespace(stdout="oldref456\n", returncode=0)
        if cmd[1:4] == ["stash", "push", "--include-untracked"]:
            return SimpleNamespace(stdout="", stderr="boom\n", returncode=1, args=cmd)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    with pytest.raises(CalledProcessError):
        hermes_main._stash_local_changes_if_needed(["git"], tmp_path)


def test_stash_apply_untracked_only_failure_detector():
    fn = hermes_main._stash_apply_failed_only_on_existing_untracked
    assert fn(
        "packaging/homebrew/hermes-agent.rb already exists, no checkout\n"
        "error: could not restore untracked files from stash\n"
    ) is True
    # Tracked-apply failure lines must NOT be classified as benign.
    assert fn(
        "error: Your local changes to the following files would be overwritten by merge:\n"
        "\ttracked.txt\n"
        "Please commit your changes or stash them before you merge.\n"
        "Aborting\n"
        "packaging/homebrew/hermes-agent.rb already exists, no checkout\n"
        "error: could not restore untracked files from stash\n"
    ) is False
    assert fn("") is False
    assert fn("warning: something harmless\n") is False


def test_restore_treats_existing_untracked_only_failure_as_restored(
    monkeypatch, tmp_path, capsys
):
    """stash apply rc=1 purely from already-present untracked files → restored,
    stash dropped, no destructive reset."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:3] == ["stash", "apply"]:
            return SimpleNamespace(
                stdout="",
                stderr=(
                    "packaging/homebrew/hermes-agent.rb already exists, no checkout\n"
                    "error: could not restore untracked files from stash\n"
                ),
                returncode=1,
            )
        if cmd[1:3] == ["diff", "--name-only"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "list"]:
            return SimpleNamespace(stdout="stash@{0} abc123\n", stderr="", returncode=0)
        if cmd[1:3] == ["stash", "drop"]:
            return SimpleNamespace(stdout="dropped\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    restored = hermes_main._restore_stashed_changes(
        ["git"], tmp_path, "abc123", prompt_user=False
    )

    assert restored is True
    # No reset --hard in the command stream.
    assert not any("reset" in c for c, _ in calls)
    out = capsys.readouterr().out
    assert "kept as-is" in out
    assert "hit conflicts" not in out


def test_update_autostash_survives_undeletable_untracked_dir(tmp_path):
    """Behavioral E2E of the whole permission-denied class with real git:
    root-owned-style undeletable untracked dir → stash succeeds, update-style
    reset works, restore round-trips, nothing lost. (#70127 follow-up)"""
    import os
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not available")
    if os.name == "nt":
        pytest.skip("POSIX permission semantics")
    if os.geteuid() == 0:
        pytest.skip("root ignores directory write bits")

    def git(*args, check=True):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=check
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "tracked.txt").write_text("v1\n")
    git("add", "-A")
    git("commit", "-qm", "init")

    (tmp_path / "tracked.txt").write_text("v2 local change\n")
    pkg = tmp_path / "packaging" / "homebrew"
    pkg.mkdir(parents=True)
    (pkg / "hermes-agent.rb").write_text("formula\n")
    os.chmod(pkg, 0o555)  # undeletable contents, like a root-owned dir
    try:
        stash_ref = hermes_main._stash_local_changes_if_needed(["git"], tmp_path)
        assert stash_ref

        # The tracked change is stashed; simulate the updater's checkout window.
        assert (tmp_path / "tracked.txt").read_text() == "v1\n"

        restored = hermes_main._restore_stashed_changes(
            ["git"], tmp_path, stash_ref, prompt_user=False
        )
        assert restored is True
        assert (tmp_path / "tracked.txt").read_text() == "v2 local change\n"
        assert (pkg / "hermes-agent.rb").read_text() == "formula\n"
    finally:
        os.chmod(pkg, 0o755)


# ---------------------------------------------------------------------------
# Pinned update boundary and CAS regression tests
# ---------------------------------------------------------------------------


def _fixture_git(repo: Path, *args: str, check: bool = True):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=check
    )


def _make_shallow_update_fixture(tmp_path: Path):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    source = tmp_path / "source"
    remote = tmp_path / "remote.git"
    client = tmp_path / "client"
    source.mkdir()
    _fixture_git(source, "init", "-q", "-b", "main")
    _fixture_git(source, "config", "user.email", "t@example.com")
    _fixture_git(source, "config", "user.name", "t")
    tracked = source / "tracked.bin"
    tracked.write_bytes(b"base\x00payload\n")
    _fixture_git(source, "add", "tracked.bin")
    _fixture_git(source, "commit", "-qm", "base")
    for index in range(5):
        tracked.write_bytes(tracked.read_bytes() + f"remote-{index}\n".encode())
        _fixture_git(source, "commit", "-qam", f"remote-{index}")
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    remote_path = str(remote).replace("\\", "/")
    _fixture_git(source, "remote", "add", "origin", remote_path)
    _fixture_git(source, "push", "-q", "origin", "main")
    subprocess.run(
        ["git", "clone", "-q", "--branch", "main", remote_path, str(client)],
        check=True,
    )
    _fixture_git(client, "config", "user.email", "t@example.com")
    _fixture_git(client, "config", "user.name", "t")
    target_head = _fixture_git(client, "rev-parse", "HEAD").stdout.strip()
    _fixture_git(source, "reset", "--hard", "HEAD~3")
    original_head = _fixture_git(source, "rev-parse", "HEAD").stdout.strip()
    _fixture_git(source, "push", "-q", "--force", "origin", "main")
    _fixture_git(client, "fetch", "-q", "--depth", "1", "origin", "main")
    _fixture_git(client, "reset", "--hard", original_head)
    _fixture_git(source, "reset", "--hard", target_head)
    _fixture_git(source, "push", "-q", "--force", "origin", "main")
    _fixture_git(client, "fetch", "-q", "--depth", "1", "origin", "main")
    assert _fixture_git(client, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    assert _fixture_git(client, "merge-base", original_head, target_head, check=False).returncode != 0
    return client, original_head, target_head


def _make_cas_update_fixture(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _fixture_git(repo, "init", "-q", "-b", "main")
    _fixture_git(repo, "config", "user.email", "t@example.com")
    _fixture_git(repo, "config", "user.name", "t")
    (repo / "value.txt").write_text("main-old\n")
    _fixture_git(repo, "add", "value.txt")
    _fixture_git(repo, "commit", "-qm", "main-old")
    old_main = _fixture_git(repo, "rev-parse", "HEAD").stdout.strip()
    _fixture_git(repo, "checkout", "-qb", "feature")
    (repo / "feature.txt").write_text("feature\n")
    _fixture_git(repo, "add", "feature.txt")
    _fixture_git(repo, "commit", "-qm", "feature")
    feature_head = _fixture_git(repo, "rev-parse", "HEAD").stdout.strip()
    _fixture_git(repo, "checkout", "-q", "main")
    (repo / "value.txt").write_text("main-target\n")
    _fixture_git(repo, "commit", "-qam", "main-target")
    target = _fixture_git(repo, "rev-parse", "HEAD").stdout.strip()
    _fixture_git(repo, "reset", "--hard", old_main)
    _fixture_git(repo, "checkout", "-q", "feature")
    return repo, old_main, target, feature_head


def test_update_merge_base_repairs_default_shallow_checkout(tmp_path):
    client, original_head, target_head = _make_shallow_update_fixture(tmp_path)
    outcome = hermes_main._ensure_update_merge_base(
        ["git"], client, "main", original_head, target_head
    )
    assert outcome["error"] is None
    assert outcome["merge_base"] == original_head
    assert outcome["fetch_steps"]


def test_update_merge_base_fails_closed_for_unrelated_full_histories(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir(); right.mkdir()
    for repo, payload in ((left, "left"), (right, "right")):
        _fixture_git(repo, "init", "-q", "-b", "main")
        _fixture_git(repo, "config", "user.email", "t@example.com")
        _fixture_git(repo, "config", "user.name", "t")
        (repo / "value.txt").write_text(payload)
        _fixture_git(repo, "add", "value.txt")
        _fixture_git(repo, "commit", "-qm", payload)
    target = _fixture_git(right, "rev-parse", "HEAD").stdout.strip()
    _fixture_git(left, "remote", "add", "origin", str(right).replace("\\", "/"))
    _fixture_git(left, "fetch", "-q", "origin", "main")
    original = _fixture_git(left, "rev-parse", "HEAD").stdout.strip()
    before = _fixture_git(left, "status", "--porcelain").stdout
    outcome = hermes_main._ensure_update_merge_base(["git"], left, "main", original, target)
    assert outcome["merge_base"] is None
    assert "unrelated" in outcome["error"].lower()
    assert _fixture_git(left, "rev-parse", "HEAD").stdout.strip() == original
    assert _fixture_git(left, "status", "--porcelain").stdout == before


def test_pinned_default_update_uses_full_ref_cas_and_checks_out_target(tmp_path):
    repo, old_main, target, feature_head = _make_cas_update_fixture(tmp_path)
    outcome = hermes_main._apply_pinned_default_update(
        ["git"], repo, "refs/heads/main", old_main, target,
        "refs/heads/feature", feature_head,
    )
    assert outcome == {"success": True, "safe_to_restore_stash": False, "error": None}
    assert _fixture_git(repo, "rev-parse", "refs/heads/main").stdout.strip() == target
    assert _fixture_git(repo, "rev-parse", "refs/heads/feature").stdout.strip() == feature_head
    assert _fixture_git(repo, "symbolic-ref", "--quiet", "HEAD").stdout.strip() == "refs/heads/main"
    assert _fixture_git(repo, "rev-parse", "HEAD").stdout.strip() == target


def test_pinned_default_update_fails_cas_without_overwriting_foreign_tip(monkeypatch, tmp_path):
    repo, old_main, target, feature_head = _make_cas_update_fixture(tmp_path)
    _fixture_git(repo, "checkout", "-q", "main")
    (repo / "foreign.txt").write_text("foreign\n")
    _fixture_git(repo, "add", "foreign.txt")
    _fixture_git(repo, "commit", "-qm", "foreign")
    foreign = _fixture_git(repo, "rev-parse", "HEAD").stdout.strip()
    _fixture_git(repo, "reset", "--hard", old_main)
    _fixture_git(repo, "checkout", "-q", "feature")
    real_run = subprocess.run
    injected = False
    def racing_run(cmd, *args, **kwargs):
        nonlocal injected
        result = real_run(cmd, *args, **kwargs)
        if not injected and "checkout" in cmd and "--detach" in cmd and cmd[-1] == old_main:
            injected = True
            real_run(["git", "update-ref", "refs/heads/main", foreign, old_main], cwd=repo, check=True)
        return result
    monkeypatch.setattr(hermes_main.subprocess, "run", racing_run)
    outcome = hermes_main._apply_pinned_default_update(
        ["git"], repo, "refs/heads/main", old_main, target,
        "refs/heads/feature", feature_head,
    )
    assert outcome["success"] is False
    assert "changed" in outcome["error"].lower()
    assert _fixture_git(repo, "rev-parse", "refs/heads/main").stdout.strip() == foreign
    assert _fixture_git(repo, "rev-parse", "refs/heads/feature").stdout.strip() == feature_head


def test_pinned_default_update_does_not_override_concurrent_checkout(monkeypatch, tmp_path):
    repo, old_main, target, feature_head = _make_cas_update_fixture(tmp_path)
    _fixture_git(repo, "checkout", "-q", "main")
    real_run = subprocess.run
    injected = False
    def racing_run(cmd, *args, **kwargs):
        nonlocal injected
        result = real_run(cmd, *args, **kwargs)
        if not injected and "checkout" in cmd and "--detach" in cmd and cmd[-1] == old_main:
            injected = True
            real_run(["git", "checkout", "-q", "feature"], cwd=repo, check=True)
        return result
    monkeypatch.setattr(hermes_main.subprocess, "run", racing_run)
    outcome = hermes_main._apply_pinned_default_update(
        ["git"], repo, "refs/heads/main", old_main, target,
        "refs/heads/main", old_main,
    )
    assert outcome["success"] is False
    assert "checkout changed" in outcome["error"].lower()
    assert _fixture_git(repo, "rev-parse", "refs/heads/main").stdout.strip() == old_main
    assert _fixture_git(repo, "symbolic-ref", "--quiet", "HEAD").stdout.strip() == "refs/heads/feature"
    assert _fixture_git(repo, "rev-parse", "HEAD").stdout.strip() == feature_head


def test_pinned_default_update_creates_missing_branch_with_expected_absent_cas(tmp_path):
    repo, old_main, target, feature_head = _make_cas_update_fixture(tmp_path)
    outcome = hermes_main._apply_pinned_default_update(
        ["git"], repo, "refs/heads/repaired-main", None, target,
        "refs/heads/feature", feature_head,
    )
    assert outcome["success"] is True
    assert outcome["safe_to_restore_stash"] is False
    assert _fixture_git(repo, "rev-parse", "refs/heads/repaired-main").stdout.strip() == target
    assert _fixture_git(repo, "symbolic-ref", "--quiet", "HEAD").stdout.strip() == "refs/heads/repaired-main"
    assert _fixture_git(repo, "rev-parse", "HEAD").stdout.strip() == target



def test_cmd_update_preflight_failure_has_no_worktree_mutation(monkeypatch, tmp_path):
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        hermes_main,
        "_ensure_update_merge_base",
        lambda *a, **kw: {
            "merge_base": None,
            "error": "histories are unrelated",
            "fetch_steps": [],
        },
    )
    monkeypatch.setattr(
        hermes_main,
        "_discard_lockfile_churn",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("lockfile mutated")),
    )
    monkeypatch.setattr(
        hermes_main,
        "_stash_local_changes_if_needed",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("stash mutated")),
    )
    side_effect, _ = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    with pytest.raises(SystemExit, match="1"):
        hermes_main.cmd_update(SimpleNamespace())


def test_cmd_update_orders_preflight_before_lockfile_stash_and_cas(monkeypatch, tmp_path):
    _setup_update_mocks(monkeypatch, tmp_path)
    events = []
    monkeypatch.setattr(
        hermes_main,
        "_ensure_update_merge_base",
        lambda *a, **kw: events.append("preflight") or {
            "merge_base": "base123",
            "error": None,
            "fetch_steps": [],
        },
    )
    monkeypatch.setattr(
        hermes_main,
        "_discard_lockfile_churn",
        lambda *a, **kw: events.append("lockfile"),
    )
    monkeypatch.setattr(
        hermes_main,
        "_stash_local_changes_if_needed",
        lambda *a, **kw: events.append("stash") or None,
    )
    monkeypatch.setattr(
        hermes_main,
        "_apply_pinned_default_update",
        lambda *a, **kw: events.append("cas") or {
            "success": False,
            "safe_to_restore_stash": False,
            "error": "intentional stop after ordering gate",
        },
    )
    side_effect, _ = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    with pytest.raises(SystemExit, match="1"):
        hermes_main.cmd_update(SimpleNamespace())

    assert events == ["preflight", "lockfile", "stash", "cas"]



def test_pinned_rollback_restores_existing_target_and_original_feature(tmp_path):
    repo, old_main, target, feature_head = _make_cas_update_fixture(tmp_path)
    applied = hermes_main._apply_pinned_default_update(
        ["git"], repo, "refs/heads/main", old_main, target,
        "refs/heads/feature", feature_head,
    )
    assert applied["success"] is True
    assert hermes_main._rollback_pinned_default_update(
        ["git"], repo, "refs/heads/main", old_main, target,
        "refs/heads/feature", feature_head,
    ) is True
    assert _fixture_git(repo, "rev-parse", "refs/heads/main").stdout.strip() == old_main
    assert _fixture_git(repo, "symbolic-ref", "--quiet", "HEAD").stdout.strip() == "refs/heads/feature"
    assert _fixture_git(repo, "rev-parse", "HEAD").stdout.strip() == feature_head


def test_pinned_rollback_deletes_new_target_and_restores_original_feature(tmp_path):
    repo, _old_main, target, feature_head = _make_cas_update_fixture(tmp_path)
    applied = hermes_main._apply_pinned_default_update(
        ["git"], repo, "refs/heads/repaired-main", None, target,
        "refs/heads/feature", feature_head,
    )
    assert applied["success"] is True
    assert hermes_main._rollback_pinned_default_update(
        ["git"], repo, "refs/heads/repaired-main", None, target,
        "refs/heads/feature", feature_head,
    ) is True
    assert _fixture_git(repo, "show-ref", "--verify", "refs/heads/repaired-main", check=False).returncode != 0
    assert _fixture_git(repo, "symbolic-ref", "--quiet", "HEAD").stdout.strip() == "refs/heads/feature"
    assert _fixture_git(repo, "rev-parse", "HEAD").stdout.strip() == feature_head



def test_fork_boundary_failure_precedes_lockfile_and_stash(monkeypatch, tmp_path):
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        hermes_main,
        "_get_origin_url",
        lambda *a: "https://github.com/example/hermes-agent.git",
    )
    monkeypatch.setattr(
        hermes_main,
        "_pin_fork_upstream_target",
        lambda *a: {
            "target_sha": "target123",
            "origin_sha": "target123",
            "sync_needed": False,
            "error": "UpdaterBoundaryChanged: refs/remotes/upstream/main moved",
        },
    )
    monkeypatch.setattr(
        hermes_main,
        "_discard_lockfile_churn",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("lockfile mutated")),
    )
    monkeypatch.setattr(
        hermes_main,
        "_stash_local_changes_if_needed",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("stash mutated")),
    )
    side_effect, _ = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    with pytest.raises(SystemExit, match="1"):
        hermes_main.cmd_update(SimpleNamespace())


def test_syntax_rollback_reports_stash_when_restore_fails(
    monkeypatch, tmp_path, capsys
):
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        hermes_main, "_stash_local_changes_if_needed", lambda *a: "stash@{0}"
    )
    monkeypatch.setattr(
        hermes_main, "_restore_stashed_changes", lambda *a, **kw: False
    )
    monkeypatch.setattr(
        hermes_main,
        "_validate_critical_files_syntax",
        lambda *a: (False, "hermes_cli/main.py", "invalid syntax"),
    )
    monkeypatch.setattr(
        hermes_main, "_rollback_pinned_default_update", lambda *a: True
    )
    side_effect, _ = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    with pytest.raises(SystemExit, match="1"):
        hermes_main.cmd_update(SimpleNamespace())

    output = capsys.readouterr().out
    assert "Code rollback complete" in output
    assert "Local changes remain preserved in stash stash@{0}" in output



def test_update_merge_base_upstream_move_fails_closed(monkeypatch, tmp_path):
    original_sha = "a" * 40
    target_sha = "b" * 40
    commands = []

    monkeypatch.setattr(
        hermes_main, "_git_update_merge_base", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        hermes_main,
        "_git_update_commit_sha",
        lambda *a, **kw: "c" * 40,
    )

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        if "--is-shallow-repository" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="true\n", stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)
    result = hermes_main._ensure_update_merge_base(
        ["git"],
        tmp_path,
        "main",
        original_sha,
        target_sha,
        remote="upstream",
    )

    assert result["merge_base"] is None
    assert result["error"] == (
        "UpdaterBoundaryChanged: refs/remotes/upstream/main moved during history repair."
    )
    assert ["git", "fetch", "--deepen=32", "upstream", "main"] in commands



def test_pinned_apply_recovers_ref_and_checkout_after_final_checkout_failure(
    monkeypatch, tmp_path
):
    repo, old_main, target, feature_head = _make_cas_update_fixture(tmp_path)
    real_run = subprocess.run

    def failing_attach(cmd, *args, **kwargs):
        if (
            "checkout" in cmd
            and "--no-guess" in cmd
            and cmd[-1] == "main"
        ):
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="simulated checkout failure"
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(hermes_main.subprocess, "run", failing_attach)
    outcome = hermes_main._apply_pinned_default_update(
        ["git"],
        repo,
        "refs/heads/main",
        old_main,
        target,
        "refs/heads/feature",
        feature_head,
    )

    assert outcome["success"] is False
    assert outcome["safe_to_restore_stash"] is True
    assert "checkout failed" in outcome["error"].lower()
    assert _fixture_git(repo, "rev-parse", "refs/heads/main").stdout.strip() == old_main
    assert _fixture_git(repo, "symbolic-ref", "--quiet", "HEAD").stdout.strip() == "refs/heads/feature"
    assert _fixture_git(repo, "rev-parse", "HEAD").stdout.strip() == feature_head
    assert _fixture_git(repo, "status", "--porcelain").stdout == ""


def test_stash_restore_refuses_foreign_checkout_and_preserves_stash(tmp_path):
    repo, old_main, _target, feature_head = _make_cas_update_fixture(tmp_path)
    _fixture_git(repo, "checkout", "-q", "main")
    (repo / "value.txt").write_text("local customization\n")
    _fixture_git(repo, "stash", "push", "-q", "-m", "update-autostash")
    stash_ref = _fixture_git(repo, "rev-parse", "stash@{0}").stdout.strip()
    _fixture_git(repo, "checkout", "-q", "feature")

    restored = hermes_main._restore_stashed_changes(
        ["git"],
        repo,
        stash_ref,
        expected_identity={"ref": "refs/heads/main", "head": old_main},
    )

    assert restored is False
    assert _fixture_git(repo, "symbolic-ref", "--quiet", "HEAD").stdout.strip() == "refs/heads/feature"
    assert _fixture_git(repo, "rev-parse", "HEAD").stdout.strip() == feature_head
    assert (repo / "value.txt").read_text() == "main-old\n"
    assert _fixture_git(repo, "status", "--porcelain").stdout == ""
    assert stash_ref in _fixture_git(repo, "stash", "list", "--format=%H").stdout



def test_cmd_update_real_shallow_default_path_stashes_cas_and_restores(
    monkeypatch, tmp_path
):
    client, original_head, target_head = _make_shallow_update_fixture(tmp_path)
    local_note = client / "local-note.txt"
    local_note.write_text("preserve me\n")

    class IntegrationStop(RuntimeError):
        pass

    monkeypatch.setattr(hermes_main, "PROJECT_ROOT", client)
    monkeypatch.setattr(hermes_main, "_is_windows", lambda: False)
    monkeypatch.setattr(hermes_main, "_run_pre_update_backup", lambda *a: None)
    monkeypatch.setattr(
        hermes_main, "_pause_windows_gateways_for_update", lambda: None
    )
    monkeypatch.setattr(
        hermes_main, "_resume_windows_gateways_after_update", lambda *a: None
    )
    monkeypatch.setattr(
        hermes_main,
        "_get_origin_url",
        lambda *a: "https://github.com/NousResearch/hermes-agent.git",
    )
    monkeypatch.setattr(
        hermes_main,
        "_validate_critical_files_syntax",
        lambda *a: (True, None, None),
    )
    monkeypatch.setattr(
        hermes_main,
        "_install_hangup_protection",
        lambda **kw: None,
    )
    monkeypatch.setattr(hermes_main, "_finalize_update_output", lambda *a: None)
    monkeypatch.setattr(
        hermes_main,
        "_invalidate_update_cache",
        lambda: (_ for _ in ()).throw(IntegrationStop()),
    )
    monkeypatch.setattr(hermes_config, "is_managed", lambda: False)
    monkeypatch.setattr(
        hermes_config, "detect_install_method", lambda _root: "git"
    )
    monkeypatch.setattr(
        hermes_config,
        "load_config",
        lambda: {"updates": {"non_interactive_local_changes": "stash"}},
    )

    with pytest.raises(IntegrationStop):
        hermes_main.cmd_update(
            SimpleNamespace(yes=True, force=False, force_venv=False)
        )

    assert _fixture_git(client, "symbolic-ref", "--quiet", "HEAD").stdout.strip() == "refs/heads/main"
    assert _fixture_git(client, "rev-parse", "HEAD").stdout.strip() == target_head
    assert _fixture_git(client, "rev-parse", "refs/heads/main").stdout.strip() == target_head
    assert original_head != target_head
    assert local_note.read_text() == "preserve me\n"
    assert "?? local-note.txt" in _fixture_git(client, "status", "--porcelain").stdout
    retained = _fixture_git(client, "stash", "list", "--format=%H").stdout.splitlines()
    assert len(retained) == 1



def test_validate_critical_syntax_at_commit_reads_exact_target(monkeypatch, tmp_path):
    target = "c" * 40
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "ls-tree" in cmd:
            relpath = cmd[-1]
            line = f"100644 blob {'a' * 40}\t{relpath}\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=line, stderr="")
        if "show" in cmd:
            source = b"def broken(:\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=source, stderr=b"")
        raise AssertionError(cmd)

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)
    ok, failing_path, error = hermes_main._validate_critical_files_syntax_at_commit(
        ["git"], tmp_path, target
    )

    assert ok is False
    assert failing_path is not None and failing_path.startswith(target + ":")
    assert error and "SyntaxError" in error
    assert any(target in cmd and "ls-tree" in cmd for cmd in calls)
    assert any(any(arg.startswith(target + ":") for arg in cmd) and "show" in cmd for cmd in calls)


def test_exact_target_fork_validates_pinned_commit_not_feature_worktree(
    monkeypatch, tmp_path
):
    _setup_update_mocks(monkeypatch, tmp_path)
    origin_sha = "a" * 40
    target_sha = "c" * 40
    feature_sha = "f" * 40
    original_identity = {
        "ref": "refs/heads/feature",
        "head": feature_sha,
        "error": None,
    }
    monkeypatch.setattr(
        hermes_main, "_capture_update_checkout_identity", lambda *a: original_identity
    )

    def fake_sha(_git, _root, ref):
        if ref == "refs/remotes/origin/main":
            return origin_sha
        if ref == "refs/heads/main":
            return target_sha
        if ref == "HEAD":
            return feature_sha
        return None

    monkeypatch.setattr(hermes_main, "_git_update_commit_sha", fake_sha)
    monkeypatch.setattr(
        hermes_main,
        "_get_origin_url",
        lambda *a: "https://github.com/example/hermes-agent.git",
    )
    monkeypatch.setattr(
        hermes_main,
        "_pin_fork_upstream_target",
        lambda *a: {
            "target_sha": target_sha,
            "origin_sha": origin_sha,
            "sync_needed": True,
            "error": None,
        },
    )
    monkeypatch.setattr(
        hermes_main,
        "_validate_critical_files_syntax",
        lambda *a: (_ for _ in ()).throw(
            AssertionError("feature worktree must not authorize target push")
        ),
    )
    validated = []
    monkeypatch.setattr(
        hermes_main,
        "_validate_critical_files_syntax_at_commit",
        lambda _git, _root, sha: validated.append(sha) or (True, None, None),
    )
    pushed = []
    monkeypatch.setattr(
        hermes_main,
        "_push_pinned_fork_target",
        lambda _git, _root, target, expected: pushed.append((target, expected)) or True,
    )
    side_effect, _recorded = _make_update_side_effect(commit_count="0")
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    assert validated == [target_sha]
    assert pushed == [(target_sha, origin_sha)]



def test_commit_syntax_validation_is_independent_of_current_worktree(
    monkeypatch, tmp_path
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _fixture_git(repo, "init", "-q", "-b", "main")
    _fixture_git(repo, "config", "user.email", "t@example.com")
    _fixture_git(repo, "config", "user.name", "t")
    critical = repo / "critical.py"
    critical.write_text("def valid():\n    return 1\n")
    _fixture_git(repo, "add", "critical.py")
    _fixture_git(repo, "commit", "-qm", "valid target")
    target = _fixture_git(repo, "rev-parse", "HEAD").stdout.strip()
    critical.write_text("def broken(:\n")
    monkeypatch.setattr(hermes_main, "_UPDATE_CRITICAL_FILES", ["critical.py"])

    commit_result = hermes_main._validate_critical_files_syntax_at_commit(
        ["git"], repo, target
    )
    worktree_result = hermes_main._validate_critical_files_syntax(repo)

    assert commit_result == (True, None, None)
    assert worktree_result[0] is False



def test_fast_forward_guard_rejects_divergent_local_branch(tmp_path):
    repo, old_main, target, _feature_head = _make_cas_update_fixture(tmp_path)
    _fixture_git(repo, "checkout", "-q", "main")
    (repo / "local-only.txt").write_text("local\n")
    _fixture_git(repo, "add", "local-only.txt")
    _fixture_git(repo, "commit", "-qm", "local-only")
    local_tip = _fixture_git(repo, "rev-parse", "HEAD").stdout.strip()

    error = hermes_main._pinned_fast_forward_error(
        ["git"], repo, local_tip, target
    )

    assert error is not None
    assert "fast-forward" in error.lower() or "local commit" in error.lower()
    assert _fixture_git(repo, "rev-parse", "refs/heads/main").stdout.strip() == local_tip
    assert old_main != local_tip


def test_stash_apply_failure_after_checkout_race_preserves_foreign_edit(
    monkeypatch, tmp_path
):
    repo, old_main, _target, feature_head = _make_cas_update_fixture(tmp_path)
    _fixture_git(repo, "checkout", "-q", "main")
    (repo / "value.txt").write_text("stashed customization\n")
    _fixture_git(repo, "stash", "push", "-q", "-m", "update-autostash")
    stash_ref = _fixture_git(repo, "rev-parse", "stash@{0}").stdout.strip()
    real_run = subprocess.run
    raced = False

    def racing_apply(cmd, *args, **kwargs):
        nonlocal raced
        if not raced and "stash" in cmd and "apply" in cmd:
            raced = True
            real_run(["git", "checkout", "-q", "feature"], cwd=repo, check=True)
            (repo / "value.txt").write_text("foreign uncommitted edit\n")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(hermes_main.subprocess, "run", racing_apply)
    restored = hermes_main._restore_stashed_changes(
        ["git"],
        repo,
        stash_ref,
        expected_identity={"ref": "refs/heads/main", "head": old_main},
    )

    assert restored is False
    assert (repo / "value.txt").read_text() == "foreign uncommitted edit\n"
    assert real_run(
        ["git", "symbolic-ref", "--quiet", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.strip() == "refs/heads/feature"
    assert real_run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip() == feature_head
    assert stash_ref in real_run(
        ["git", "stash", "list", "--format=%H"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout


def test_successful_stash_restore_keeps_immutable_recovery_copy(tmp_path):
    repo, old_main, _target, _feature_head = _make_cas_update_fixture(tmp_path)
    _fixture_git(repo, "checkout", "-q", "main")
    (repo / "value.txt").write_text("restored customization\n")
    _fixture_git(repo, "stash", "push", "-q", "-m", "update-autostash")
    stash_ref = _fixture_git(repo, "rev-parse", "stash@{0}").stdout.strip()

    restored = hermes_main._restore_stashed_changes(
        ["git"],
        repo,
        stash_ref,
        expected_identity={"ref": "refs/heads/main", "head": old_main},
    )

    assert restored is True
    assert (repo / "value.txt").read_text() == "restored customization\n"
    assert stash_ref in _fixture_git(repo, "stash", "list", "--format=%H").stdout


def test_pinned_apply_recovers_transient_identity_failure_after_detach(
    monkeypatch, tmp_path
):
    repo, old_main, target, feature_head = _make_cas_update_fixture(tmp_path)
    real_capture = hermes_main._capture_update_checkout_identity
    injected = False

    def flaky_capture(git_cmd, root):
        nonlocal injected
        actual = real_capture(git_cmd, root)
        if not injected and actual["ref"] is None and actual["head"] == old_main:
            injected = True
            return {"ref": None, "head": old_main, "error": "transient read failure"}
        return actual

    monkeypatch.setattr(hermes_main, "_capture_update_checkout_identity", flaky_capture)
    outcome = hermes_main._apply_pinned_default_update(
        ["git"], repo, "refs/heads/main", old_main, target,
        "refs/heads/feature", feature_head,
    )

    assert outcome["success"] is False
    assert outcome["safe_to_restore_stash"] is True
    assert _fixture_git(repo, "symbolic-ref", "--quiet", "HEAD").stdout.strip() == "refs/heads/feature"
    assert _fixture_git(repo, "rev-parse", "refs/heads/main").stdout.strip() == old_main


def test_pinned_apply_recovers_when_attach_mutates_then_reports_failure(
    monkeypatch, tmp_path
):
    repo, old_main, target, feature_head = _make_cas_update_fixture(tmp_path)
    real_run = subprocess.run
    injected = False

    def late_failing_attach(cmd, *args, **kwargs):
        nonlocal injected
        if (
            not injected
            and "checkout" in cmd
            and "--no-guess" in cmd
            and cmd[-1] == "main"
        ):
            injected = True
            landed = real_run(cmd, *args, **kwargs)
            assert landed.returncode == 0
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="simulated late checkout error"
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(hermes_main.subprocess, "run", late_failing_attach)
    outcome = hermes_main._apply_pinned_default_update(
        ["git"], repo, "refs/heads/main", old_main, target,
        "refs/heads/feature", feature_head,
    )

    assert outcome["success"] is False
    assert outcome["safe_to_restore_stash"] is True
    assert _fixture_git(repo, "symbolic-ref", "--quiet", "HEAD").stdout.strip() == "refs/heads/feature"
    assert _fixture_git(repo, "rev-parse", "refs/heads/main").stdout.strip() == old_main
    assert _fixture_git(repo, "status", "--porcelain").stdout == ""



def test_cmd_update_non_fast_forward_stops_before_any_mutation(
    monkeypatch, tmp_path
):
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        hermes_main,
        "_pinned_fast_forward_error",
        lambda *a: "Pinned update is not a fast-forward; local commits are preserved.",
    )
    monkeypatch.setattr(
        hermes_main,
        "_discard_lockfile_churn",
        lambda *a: (_ for _ in ()).throw(AssertionError("lockfile mutated")),
    )
    monkeypatch.setattr(
        hermes_main,
        "_stash_local_changes_if_needed",
        lambda *a: (_ for _ in ()).throw(AssertionError("stash mutated")),
    )
    monkeypatch.setattr(
        hermes_main,
        "_apply_pinned_default_update",
        lambda *a: (_ for _ in ()).throw(AssertionError("branch CAS attempted")),
    )
    side_effect, _ = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    with pytest.raises(SystemExit, match="1"):
        hermes_main.cmd_update(SimpleNamespace())



def test_cmd_update_stops_after_failed_stash_restore_before_post_update_mutation(
    monkeypatch, tmp_path
):
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        hermes_main, "_stash_local_changes_if_needed", lambda *a: "stash-commit"
    )
    monkeypatch.setattr(
        hermes_main, "_restore_stashed_changes", lambda *a, **kw: False
    )
    monkeypatch.setattr(
        hermes_main,
        "_invalidate_update_cache",
        lambda: (_ for _ in ()).throw(AssertionError("post-update mutation ran")),
    )
    side_effect, _ = _make_update_side_effect()
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    with pytest.raises(SystemExit, match="1"):
        hermes_main.cmd_update(SimpleNamespace())



def test_commit_syntax_validation_rejects_symlink_mode(monkeypatch, tmp_path):
    target = "e" * 40

    def symlink_tree(cmd, **kwargs):
        assert "ls-tree" in cmd
        relpath = cmd[-1]
        line = f"120000 blob {'a' * 40}\t{relpath}\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=line, stderr="")

    monkeypatch.setattr(hermes_main, "_UPDATE_CRITICAL_FILES", ["critical.py"])
    monkeypatch.setattr(hermes_main.subprocess, "run", symlink_tree)
    ok, failing_path, error = hermes_main._validate_critical_files_syntax_at_commit(
        ["git"], tmp_path, target
    )

    assert ok is False
    assert failing_path == f"{target}:critical.py"
    assert error and "regular blob" in error.lower()


def test_commit_syntax_validation_allows_missing_critical_path(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _fixture_git(repo, "init", "-q", "-b", "main")
    _fixture_git(repo, "config", "user.email", "t@example.com")
    _fixture_git(repo, "config", "user.name", "t")
    (repo / "other.txt").write_text("ok\n")
    _fixture_git(repo, "add", "other.txt")
    _fixture_git(repo, "commit", "-qm", "missing critical path")
    target = _fixture_git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(hermes_main, "_UPDATE_CRITICAL_FILES", ["critical.py"])

    assert hermes_main._validate_critical_files_syntax_at_commit(
        ["git"], repo, target
    ) == (True, None, None)


def test_commit_syntax_validation_fails_closed_on_ls_tree_error(
    monkeypatch, tmp_path
):
    target = "d" * 40

    def failed_ls_tree(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="bad object")

    monkeypatch.setattr(hermes_main.subprocess, "run", failed_ls_tree)
    ok, failing_path, error = hermes_main._validate_critical_files_syntax_at_commit(
        ["git"], tmp_path, target
    )

    assert ok is False
    assert failing_path is not None and failing_path.startswith(target + ":")
    assert error and "bad object" in error
