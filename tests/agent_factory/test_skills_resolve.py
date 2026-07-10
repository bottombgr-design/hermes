"""Local-only required-skill resolution + explicit per-skill copy attachment.

Resolution must only ever read from the repo-local bundled/optional skills
catalogs (never a live profile home, never the network), and attachment must
copy exactly the named skill directories — never the whole catalog root.
"""

from __future__ import annotations

import pytest

from agent_factory.skills_resolve import (
    SkillResolutionReport,
    attach_required_skills,
    find_skill,
    resolve_required_skills,
)


@pytest.fixture
def catalog(tmp_path):
    bundled = tmp_path / "bundled-skills"
    optional = tmp_path / "optional-skills"
    bundled.mkdir()
    optional.mkdir()

    (bundled / "alpha-skill").mkdir()
    (bundled / "alpha-skill" / "SKILL.md").write_text("# Alpha\n", encoding="utf-8")
    (bundled / "alpha-skill" / "references").mkdir()
    (bundled / "alpha-skill" / "references" / "notes.md").write_text("notes", encoding="utf-8")

    (bundled / "beta-skill").mkdir()
    (bundled / "beta-skill" / "SKILL.md").write_text("# Beta\n", encoding="utf-8")

    (optional / "gamma-skill").mkdir()
    (optional / "gamma-skill" / "SKILL.md").write_text("# Gamma\n", encoding="utf-8")

    return bundled, optional


def test_find_skill_in_bundled_root(catalog):
    bundled, optional = catalog
    path = find_skill("alpha-skill", bundled_root=bundled, optional_root=optional)
    assert path == bundled / "alpha-skill"


def test_find_skill_in_optional_root(catalog):
    bundled, optional = catalog
    path = find_skill("gamma-skill", bundled_root=bundled, optional_root=optional)
    assert path == optional / "gamma-skill"


def test_find_skill_missing_returns_none(catalog):
    bundled, optional = catalog
    assert find_skill("nonexistent", bundled_root=bundled, optional_root=optional) is None


def test_resolve_required_skills_reports_resolved_and_missing(catalog):
    bundled, optional = catalog
    report = resolve_required_skills(
        ["alpha-skill", "gamma-skill", "missing-skill"],
        bundled_root=bundled,
        optional_root=optional,
    )
    assert isinstance(report, SkillResolutionReport)
    assert dict(report.resolved) == {
        "alpha-skill": bundled / "alpha-skill",
        "gamma-skill": optional / "gamma-skill",
    }
    assert report.missing == ("missing-skill",)


def test_attach_required_skills_copies_only_named_skills(catalog, tmp_path):
    bundled, optional = catalog
    dest = tmp_path / "staged" / "skills"
    report = attach_required_skills(
        ["alpha-skill"],
        dest_root=dest,
        bundled_root=bundled,
        optional_root=optional,
    )
    assert (dest / "alpha-skill" / "SKILL.md").read_text(encoding="utf-8") == "# Alpha\n"
    assert (dest / "alpha-skill" / "references" / "notes.md").exists()
    # beta-skill lives in the same bundled root but was never requested —
    # no broad inheritance of the catalog.
    assert not (dest / "beta-skill").exists()
    assert report.attached == ("alpha-skill",)
    assert report.missing == ()


def test_attach_required_skills_raises_on_missing_skill(catalog, tmp_path):
    bundled, optional = catalog
    dest = tmp_path / "staged" / "skills"
    with pytest.raises(ValueError, match="missing-skill"):
        attach_required_skills(
            ["missing-skill"],
            dest_root=dest,
            bundled_root=bundled,
            optional_root=optional,
        )
    assert not dest.exists()


def test_attach_required_skills_rejects_path_traversal_name(catalog, tmp_path):
    bundled, optional = catalog
    dest = tmp_path / "staged" / "skills"
    with pytest.raises(ValueError):
        attach_required_skills(
            ["../escape"],
            dest_root=dest,
            bundled_root=bundled,
            optional_root=optional,
        )


def test_attach_required_skills_is_deterministic(catalog, tmp_path):
    bundled, optional = catalog
    dest_a = tmp_path / "staged-a" / "skills"
    dest_b = tmp_path / "staged-b" / "skills"
    attach_required_skills(["alpha-skill", "beta-skill"], dest_root=dest_a, bundled_root=bundled, optional_root=optional)
    attach_required_skills(["alpha-skill", "beta-skill"], dest_root=dest_b, bundled_root=bundled, optional_root=optional)
    a_files = sorted(p.relative_to(dest_a).as_posix() for p in dest_a.rglob("*") if p.is_file())
    b_files = sorted(p.relative_to(dest_b).as_posix() for p in dest_b.rglob("*") if p.is_file())
    assert a_files == b_files
