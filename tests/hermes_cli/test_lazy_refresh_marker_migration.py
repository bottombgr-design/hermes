import shutil
import subprocess
from pathlib import Path

import pytest

from hermes_cli import _early_recovery as early_recovery


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGACY_MARKER = ".lazy-refresh-incomplete"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=check,
    )


def test_legacy_tracked_marker_is_inert(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    (tmp_path / LEGACY_MARKER).write_text("started=1\npid=2\n", encoding="utf-8")
    probes: list[bool] = []
    monkeypatch.setattr(
        early_recovery,
        "_probe_broken_packages",
        lambda: probes.append(True) or [],
    )

    early_recovery.recover_if_needed(project_root=tmp_path, argv=[])

    assert probes == []
    assert (tmp_path / LEGACY_MARKER).exists()
    assert not (tmp_path / early_recovery.LAZY_REFRESH_MARKER_NAME).exists()


def test_runtime_marker_contract_keeps_legacy_path_tracked_and_new_path_ignored():
    assert early_recovery.LAZY_REFRESH_MARKER_NAME != LEGACY_MARKER
    assert (PROJECT_ROOT / LEGACY_MARKER).is_file()
    tracked = _git(PROJECT_ROOT, "ls-files", "--error-unmatch", "--", LEGACY_MARKER)
    assert tracked.stdout.strip() == LEGACY_MARKER

    ignored = _git(
        PROJECT_ROOT,
        "check-ignore",
        "--no-index",
        "--quiet",
        "--",
        early_recovery.LAZY_REFRESH_MARKER_NAME,
        check=False,
    )
    assert ignored.returncode == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_modified_legacy_marker_does_not_conflict_with_update_autostash(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Hermes Test")
    _git(repo, "config", "user.email", "hermes@example.invalid")

    (repo / LEGACY_MARKER).write_text("started=old\npid=1\n", encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", LEGACY_MARKER, "README.md")
    _git(repo, "commit", "-qm", "legacy release")
    legacy_head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # The safe migration leaves the tracked legacy breadcrumb byte-identical and
    # moves live runtime state to a new ignored path.
    (repo / ".gitignore").write_text(
        f"{early_recovery.LAZY_REFRESH_MARKER_NAME}\n",
        encoding="utf-8",
    )
    (repo / "release.txt").write_text("new release\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "release.txt")
    _git(repo, "commit", "-qm", "safe marker migration")
    update_head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _git(repo, "checkout", "-q", legacy_head)
    (repo / LEGACY_MARKER).write_text("started=live\npid=999\n", encoding="utf-8")
    (repo / "README.md").write_text("user edit\n", encoding="utf-8")

    _git(repo, "stash", "push", "--include-untracked", "-m", "hermes-update-autostash")
    _git(repo, "merge", "--ff-only", update_head)
    restored = _git(repo, "stash", "apply", "stash@{0}", check=False)

    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert _git(repo, "diff", "--name-only", "--diff-filter=U").stdout == ""
    assert (repo / LEGACY_MARKER).read_text(encoding="utf-8") == "started=live\npid=999\n"
    assert (repo / "README.md").read_text(encoding="utf-8") == "user edit\n"
    assert (repo / "release.txt").read_text(encoding="utf-8") == "new release\n"
