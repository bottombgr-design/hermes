"""Local-only required-skill resolution + explicit per-skill copy attachment.

Only the repo-local bundled (``skills/``) and optional (``optional-skills/``)
catalogs are ever consulted — never a live profile home, never the network.
Attachment copies exactly the named skill directories one at a time; it
never copies a catalog root wholesale (no broad inheritance).
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from agent.skill_utils import is_excluded_skill_path
from hermes_constants import get_bundled_skills_dir, get_optional_skills_dir

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def default_bundled_root() -> Path:
    return get_bundled_skills_dir(_REPO_ROOT / "skills")


def default_optional_root() -> Path:
    return get_optional_skills_dir(_REPO_ROOT / "optional-skills")


def _search_root(name: str, root: Path) -> Optional[Path]:
    if not root.is_dir():
        return None
    for skill_md in root.rglob("SKILL.md"):
        if is_excluded_skill_path(skill_md):
            continue
        skill_dir = skill_md.parent
        if skill_dir.name == name:
            return skill_dir
    return None


def find_skill(
    name: str,
    *,
    bundled_root: Optional[Path] = None,
    optional_root: Optional[Path] = None,
) -> Optional[Path]:
    """Resolve *name* to a skill directory, searching bundled then optional. Local-only."""
    bundled_root = bundled_root if bundled_root is not None else default_bundled_root()
    optional_root = optional_root if optional_root is not None else default_optional_root()
    return _search_root(name, bundled_root) or _search_root(name, optional_root)


@dataclass(frozen=True)
class SkillResolutionReport:
    resolved: tuple[tuple[str, Path], ...]
    missing: tuple[str, ...]


def resolve_required_skills(
    names: Iterable[str],
    *,
    bundled_root: Optional[Path] = None,
    optional_root: Optional[Path] = None,
) -> SkillResolutionReport:
    resolved: list[tuple[str, Path]] = []
    missing: list[str] = []
    for name in names:
        path = find_skill(name, bundled_root=bundled_root, optional_root=optional_root)
        if path is None:
            missing.append(name)
        else:
            resolved.append((name, path))
    return SkillResolutionReport(resolved=tuple(resolved), missing=tuple(missing))


@dataclass(frozen=True)
class SkillAttachmentReport:
    attached: tuple[str, ...]
    missing: tuple[str, ...]


def _validate_skill_name(name: str) -> None:
    if not _SKILL_NAME_RE.match(name):
        raise ValueError(f"invalid skill name {name!r}: must match ^[a-z0-9][a-z0-9_-]{{0,63}}$")


def _assert_within(path: Path, root: Path) -> None:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    if resolved_root != resolved_path and resolved_root not in resolved_path.parents:
        raise ValueError(f"resolved skill path {path} escapes its catalog root {root}")


def attach_required_skills(
    names: Iterable[str],
    *,
    dest_root: Path,
    bundled_root: Optional[Path] = None,
    optional_root: Optional[Path] = None,
) -> SkillAttachmentReport:
    """Copy each named skill's directory, individually, into ``dest_root/<name>``.

    Raises ``ValueError`` (leaving ``dest_root`` untouched) if any requested
    skill name is invalid or cannot be resolved locally.
    """
    names = list(names)
    for name in names:
        _validate_skill_name(name)

    bundled_root = bundled_root if bundled_root is not None else default_bundled_root()
    optional_root = optional_root if optional_root is not None else default_optional_root()

    report = resolve_required_skills(names, bundled_root=bundled_root, optional_root=optional_root)
    if report.missing:
        raise ValueError(f"required skill(s) not found in local catalog: {', '.join(report.missing)}")

    dest_root.mkdir(parents=True, exist_ok=True)
    attached: list[str] = []
    for name, skill_dir in report.resolved:
        source_root = bundled_root if bundled_root.resolve() in skill_dir.resolve().parents or bundled_root.resolve() == skill_dir.resolve() else optional_root
        _assert_within(skill_dir, source_root)
        dest = dest_root / name
        shutil.copytree(skill_dir, dest, symlinks=False)
        attached.append(name)

    return SkillAttachmentReport(attached=tuple(sorted(attached)), missing=())
