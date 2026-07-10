"""Guarded deploy: create exactly one NEW factory-managed TEST profile.

Fails closed unless *every* precondition holds:

* the target profile name carries :data:`TEST_PROFILE_PREFIX` and does not
  already exist (collision refusal — an existing profile is never touched,
  so there is no rollback logic to write);
* the supplied test-report says ``deploy_allowed`` (no FAIL/SKIPPED/UNKNOWN
  layer — see ``agent_factory.state``);
* a kanban review task exists whose *live* decision is "approve", whose
  hash binding still matches this exact release, and whose provenance
  verifier independently returns trusted (see ``agent_factory.tests_layer4``
  and ``agent_factory.provenance`` — "No manual approval.json or identity
  string is trusted").

Profile contents are always assembled in a temporary destination first;
on any failure — precondition or build-time — the temp directory is
removed and nothing under the real profiles root is left behind.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from hermes_cli import profiles as hermes_profiles
from hermes_cli.default_soul import DEFAULT_SOUL_MD

from agent_factory.provenance import ProvenanceVerifier
from agent_factory.state import TestReport
from agent_factory.tests_layer4 import evaluate_deployment_gate

TEST_PROFILE_PREFIX = "aftest-"

# Mirrors hermes_cli.profiles._PROFILE_DIRS. Kept as a local copy rather
# than importing the private symbol — agent-factory profiles only need the
# standard skeleton, not that module's other internals.
_PROFILE_SKELETON_DIRS = (
    "memories", "sessions", "skills", "skins", "logs", "plans", "workspace", "cron", "home",
)


class DeploymentRefused(Exception):
    """A precondition was not met; deploy stops before (or during) building."""


@dataclass(frozen=True)
class DeployResult:
    outcome: str  # "deployed" | "refused"
    target_profile: str
    profile_dir: Optional[Path]
    failure_reason: Optional[str]
    verified_identity: Optional[str]


def _require_test_profile_name(target_profile_name: str) -> str:
    canon = hermes_profiles.normalize_profile_name(target_profile_name)
    hermes_profiles.validate_profile_name(canon)
    if not canon.startswith(TEST_PROFILE_PREFIX):
        raise DeploymentRefused(
            f"target profile name must start with {TEST_PROFILE_PREFIX!r} — "
            "agent-factory deploy only ever creates a new factory-managed TEST profile"
        )
    return canon


def _build_profile_contents(temp_dir: Path, release_dir: Path) -> None:
    for subdir in _PROFILE_SKELETON_DIRS:
        (temp_dir / subdir).mkdir(parents=True, exist_ok=True)
    (temp_dir / "SOUL.md").write_text(DEFAULT_SOUL_MD, encoding="utf-8")
    (temp_dir / ".env").write_text(
        "# Per-profile secrets for this Hermes profile.\n"
        "# API keys and tokens set here override the shell environment.\n",
        encoding="utf-8",
    )
    shutil.copy2(release_dir / "agent.yaml", temp_dir / "agent.yaml")
    shutil.copy2(release_dir / "rendered-config.json", temp_dir / "rendered-config.json")
    shutil.copy2(release_dir / "manifest.json", temp_dir / "manifest.json")
    release_skills = release_dir / "skills"
    if release_skills.is_dir():
        shutil.copytree(release_skills, temp_dir / "skills", dirs_exist_ok=True)
    (temp_dir / hermes_profiles.NO_BUNDLED_SKILLS_MARKER).write_text(
        "Factory-managed test profile — no bundled-skill seeding.\n", encoding="utf-8",
    )


def _build_and_commit(release_dir: Path, profile_dir: Path, canon: str) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix=f".agent-factory-deploy-{canon}-"))
    try:
        _build_profile_contents(temp_dir, release_dir)
        if profile_dir.exists():
            raise DeploymentRefused(f"profile {canon!r} was created concurrently at {profile_dir}")
        profile_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temp_dir), str(profile_dir))
        return profile_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def deploy_release(
    release_dir: Path,
    target_profile_name: str,
    *,
    release_id: str,
    manifest: dict,
    kanban_conn,
    kanban_task_id: Optional[str],
    verifier: ProvenanceVerifier,
    test_report: Optional[TestReport],
    claimed_identity: Optional[str] = None,
) -> DeployResult:
    try:
        canon = _require_test_profile_name(target_profile_name)

        profile_dir = hermes_profiles.get_profile_dir(canon)
        if profile_dir.exists():
            raise DeploymentRefused(f"profile {canon!r} already exists at {profile_dir} — refusing to overwrite")

        if test_report is None or not test_report.deploy_allowed:
            raise DeploymentRefused(
                "test-report does not permit deployment (missing, or a mandatory layer "
                "is FAIL/SKIPPED/UNKNOWN)"
            )

        if kanban_task_id is None:
            raise DeploymentRefused("no kanban review task associated with this release")

        gate = evaluate_deployment_gate(
            kanban_conn, kanban_task_id, release_id=release_id,
            manifest_sha256=manifest["combined_sha256"], verifier=verifier,
            claimed_identity=claimed_identity,
        )
        if not gate.passes:
            raise DeploymentRefused(f"deployment gate refused: {gate.detail}")

        committed_dir = _build_and_commit(Path(release_dir), profile_dir, canon)
        return DeployResult(
            outcome="deployed",
            target_profile=canon,
            profile_dir=committed_dir,
            failure_reason=None,
            verified_identity=gate.provenance.verified_identity,
        )
    except DeploymentRefused as exc:
        return DeployResult(
            outcome="refused",
            target_profile=target_profile_name,
            profile_dir=None,
            failure_reason=str(exc),
            verified_identity=None,
        )
    except Exception as exc:
        return DeployResult(
            outcome="refused",
            target_profile=target_profile_name,
            profile_dir=None,
            failure_reason=f"unexpected error during deploy: {exc}",
            verified_identity=None,
        )
