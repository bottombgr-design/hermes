import tomllib
from pathlib import Path

from packaging.version import Version


REPO_ROOT = Path(__file__).resolve().parents[1]
PILLOW_FLOOR = Version("12.3.0")


def test_pillow_security_floor_is_synchronized() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "Pillow==12.3.0" in project["project"]["dependencies"]

    lazy_deps = (REPO_ROOT / "tools/lazy_deps.py").read_text(encoding="utf-8")
    assert '"tool.vision": ("Pillow==12.3.0",)' in lazy_deps

    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions = [
        Version(package["version"])
        for package in lock["package"]
        if package["name"].lower() == "pillow"
    ]
    assert versions, "pillow not found in uv.lock"
    assert all(version >= PILLOW_FLOOR for version in versions)
