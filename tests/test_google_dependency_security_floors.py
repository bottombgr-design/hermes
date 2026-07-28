import tomllib
from pathlib import Path

from packaging.version import Version

from tools.lazy_deps import LAZY_DEPS


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_google_security_floors_are_synchronized() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]

    workspace_specs = {
        "google-api-python-client==2.194.0",
        "google-auth==2.55.1",
        "google-auth-oauthlib==1.3.1",
        "google-auth-httplib2==0.3.1",
        "httplib2==0.32.0",
        "pyasn1==0.6.4",
    }
    assert workspace_specs <= set(extras["google"])
    assert {"google-auth==2.55.1", "pyasn1==0.6.4"} <= set(extras["vertex"])
    assert workspace_specs <= set(LAZY_DEPS["skill.google_workspace"])
    assert {"google-auth==2.55.1", "pyasn1==0.6.4"} <= set(LAZY_DEPS["provider.vertex"])

    floors = {
        "google-auth": Version("2.55.1"),
        "httplib2": Version("0.32.0"),
        "pyasn1": Version("0.6.4"),
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
