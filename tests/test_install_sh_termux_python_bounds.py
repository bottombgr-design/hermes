"""Behavioral regression tests for Termux Python selection in install.sh."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


def _extract_shell_function(name: str) -> str:
    """Return a shell function definition from install.sh.

    The installer is not sourceable in tests because it dispatches to main at
    EOF, so these tests execute the real function body in a tiny harness with
    the globals/stubs it needs.
    """

    lines = INSTALL_SH.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line == f"{name}() {{")
    depth = 0
    selected: list[str] = []
    for line in lines[start:]:
        selected.append(line)
        depth += line.count("{") - line.count("}")
        if depth == 0:
            break
    return "\n".join(selected)


def _write_fake_python(bin_dir: Path, name: str, version: str) -> Path:
    path = bin_dir / name
    path.write_text(
        f"""#!/bin/sh
version='{version}'
if [ "${{1:-}}" = '--version' ]; then
    echo "Python $version"
    exit 0
fi
if [ "${{1:-}}" = '-c' ]; then
    case "$version" in
        3.11.*|3.12.*|3.13.*) exit 0 ;;
        *) exit 1 ;;
    esac
fi
exit 0
"""
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _write_pkg_stub(bin_dir: Path) -> None:
    path = bin_dir / "pkg"
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_check_python(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    harness = tmp_path / "run-check-python.sh"
    harness.write_text(
        "\n".join(
            [
                "set -e",
                "DISTRO=termux",
                "PYTHON_PATH=",
                "PYTHON_FOUND_VERSION=",
                "log_info() { printf 'INFO:%s\\n' \"$*\"; }",
                "log_success() { printf 'SUCCESS:%s\\n' \"$*\"; }",
                "log_error() { printf 'ERROR:%s\\n' \"$*\"; }",
                _extract_shell_function("check_python"),
                "check_python",
                "printf 'SELECTED=%s\\n' \"$PYTHON_PATH\"",
            ]
        )
    )
    env = os.environ.copy()
    env["PATH"] = str(tmp_path / "bin")
    bash = shutil.which("bash") or "/bin/bash"
    return subprocess.run(
        [bash, str(harness)],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_termux_check_python_prefers_compatible_minor_over_unsupported_default(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    selected = _write_fake_python(bin_dir, "python3.11", "3.11.15")
    _write_fake_python(bin_dir, "python", "3.14.6")
    _write_pkg_stub(bin_dir)

    result = _run_check_python(tmp_path)

    assert result.returncode == 0, result.stdout
    assert f"SELECTED={selected}" in result.stdout
    assert "SUCCESS:Python found: Python 3.11.15" in result.stdout


def test_termux_check_python_rejects_post_install_unsupported_default(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_python(bin_dir, "python", "3.14.6")
    _write_pkg_stub(bin_dir)

    result = _run_check_python(tmp_path)

    assert result.returncode == 1
    assert "ERROR:Termux Python Python 3.14.6 is not supported" in result.stdout
    assert "Hermes requires Python >=3.11,<3.14" in result.stdout
    assert "Install a compatible Termux Python (for example python3.11)" in result.stdout
