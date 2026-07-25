"""Version metadata and spec-release comparison helpers.

Two distinct version concepts:

* ``SPEC_FORMAT_VERSION`` — the ``agent.yaml`` schema/API version
  (``agent_factory.schema.SPEC_API_VERSION``). Changes when the *shape* of
  the spec format changes.
* an agent's own semantic ``metadata.version`` — changes with each release
  of a *given* agent, independent of the spec format it's written against.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from agent_factory.schema import SPEC_API_VERSION, _VERSION_RE

SPEC_FORMAT_VERSION = SPEC_API_VERSION


class VersionComparison(str, Enum):
    INITIAL = "initial"
    UPGRADE = "upgrade"
    DOWNGRADE = "downgrade"
    SAME = "same"
    INVALID = "invalid"


def parse_semver(version: str) -> tuple[int, int, int]:
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise ValueError(f"invalid semantic version: {version!r}")
    major, minor, patch = version.split(".")
    return (int(major), int(minor), int(patch))


def compare_versions(old_version: Optional[str], new_version: str) -> VersionComparison:
    """Compare a prior release's version to a new one for the same agent."""
    try:
        new_tuple = parse_semver(new_version)
    except ValueError:
        return VersionComparison.INVALID

    if old_version is None:
        return VersionComparison.INITIAL

    try:
        old_tuple = parse_semver(old_version)
    except ValueError:
        return VersionComparison.INVALID

    if new_tuple > old_tuple:
        return VersionComparison.UPGRADE
    if new_tuple < old_tuple:
        return VersionComparison.DOWNGRADE
    return VersionComparison.SAME


def compare_spec_format(old_format: str, new_format: str) -> bool:
    """Return True if two spec format (apiVersion) strings are compatible (i.e. equal)."""
    return old_format == new_format
