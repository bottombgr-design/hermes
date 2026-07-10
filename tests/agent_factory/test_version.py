"""Version metadata / spec-release comparison."""

from __future__ import annotations

import pytest

from agent_factory.version import (
    SPEC_FORMAT_VERSION,
    VersionComparison,
    compare_spec_format,
    compare_versions,
    parse_semver,
)


def test_parse_semver_valid():
    assert parse_semver("1.2.3") == (1, 2, 3)


def test_parse_semver_invalid_raises():
    with pytest.raises(ValueError):
        parse_semver("not-a-version")


def test_compare_versions_initial_when_no_prior_release():
    result = compare_versions(None, "1.0.0")
    assert result == VersionComparison.INITIAL


def test_compare_versions_upgrade():
    assert compare_versions("1.0.0", "1.1.0") == VersionComparison.UPGRADE


def test_compare_versions_downgrade():
    assert compare_versions("1.1.0", "1.0.0") == VersionComparison.DOWNGRADE


def test_compare_versions_same():
    assert compare_versions("1.0.0", "1.0.0") == VersionComparison.SAME


def test_compare_versions_invalid_new_version():
    assert compare_versions("1.0.0", "garbage") == VersionComparison.INVALID


def test_spec_format_version_constant_matches_schema():
    from agent_factory.schema import SPEC_API_VERSION
    assert SPEC_FORMAT_VERSION == SPEC_API_VERSION


def test_compare_spec_format_compatible_when_equal():
    assert compare_spec_format(SPEC_FORMAT_VERSION, SPEC_FORMAT_VERSION) is True


def test_compare_spec_format_incompatible_when_different():
    assert compare_spec_format("agent-factory/v0", SPEC_FORMAT_VERSION) is False
