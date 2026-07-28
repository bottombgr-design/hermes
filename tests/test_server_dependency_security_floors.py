import tomllib
from pathlib import Path

from packaging.version import Version

from tools.lazy_deps import LAZY_DEPS


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_server_dependency_security_floors_are_synchronized() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    core = set(project["project"]["dependencies"])
    extras = project["project"]["optional-dependencies"]

    assert "starlette==1.3.1" in core
    assert "python-multipart==0.0.32" in core
    for extra in ("dev", "mcp", "computer-use"):
        assert "mcp==1.28.1" in extras[extra]
        assert "starlette==1.3.1" in extras[extra]
    assert "starlette==1.3.1" in extras["web"]
    assert "python-multipart==0.0.32" in extras["web"]
    assert {
        "mcp==1.28.1",
        "starlette==1.3.1",
    } <= set(LAZY_DEPS["tool.computer_use"])

    floors = {
        "mcp": Version("1.28.1"),
        "python-multipart": Version("0.0.32"),
        "starlette": Version("1.3.1"),
    }
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked: dict[str, list[Version]] = {}
    for package in lock["package"]:
        locked.setdefault(package["name"].lower(), []).append(
            Version(package["version"])
        )

    for package, floor in floors.items():
        assert package in locked, f"{package} not found in uv.lock"
        assert all(version >= floor for version in locked[package])
