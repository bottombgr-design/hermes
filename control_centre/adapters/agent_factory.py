"""Read and integrity-check Agent Factory release evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agent_factory.staging import MANIFEST_FILENAME, recompute_manifest
from agent_factory.state import (
    APPROVAL_FILENAME,
    DEPLOYMENT_RECORD_FILENAME,
    RELEASE_STATE_FILENAME,
    REVIEW_PACKET_FILENAME,
    TEST_REPORT_FILENAME,
    read_approval,
    read_deployment_record,
    read_release_state,
    read_test_report,
)

from control_centre.schema import BuildSummary, Freshness, SourceError, SourceRef


@dataclass(frozen=True)
class FactoryReadResult:
    builds: tuple[BuildSummary, ...] = ()
    errors: tuple[SourceError, ...] = ()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def read_agent_factory_release(
    release_dir: Path,
    *,
    product_id: str,
    configured_root: Path,
    observed_at: float,
) -> FactoryReadResult:
    release = Path(release_dir).resolve()
    root = Path(configured_root).resolve()
    source_id = release.name or "unknown-release"
    href = f"/control-centre?release={source_id}"

    def source(freshness: Freshness = Freshness.LIVE) -> SourceRef:
        return SourceRef(
            source="agent_factory",
            source_id=source_id,
            observed_at=observed_at,
            freshness=freshness,
            href=href,
        )

    def error(
        message: str,
        *,
        severity: str = "warning",
        recovery: str = "Inspect the staged Agent Factory evidence",
    ) -> SourceError:
        return SourceError(
            source=source(Freshness.UNAVAILABLE),
            message=message,
            recovery=recovery,
            severity=severity,
        )

    if not _inside(release, root):
        return FactoryReadResult(
            errors=(
                error(
                    "Release path is outside the configured Agent Factory root",
                    severity="critical",
                    recovery="Correct the configured release root",
                ),
            )
        )

    errors: list[SourceError] = []
    release_state = None
    report = None
    approval = None
    deployment = None
    review_packet = None
    manifest: dict | None = None
    integrity_failed = False

    readers = (
        (RELEASE_STATE_FILENAME, read_release_state, "release state"),
        (TEST_REPORT_FILENAME, read_test_report, "test report"),
        (APPROVAL_FILENAME, read_approval, "approval"),
        (DEPLOYMENT_RECORD_FILENAME, read_deployment_record, "deployment record"),
    )
    values: dict[str, object] = {}
    for filename, reader, label in readers:
        try:
            values[filename] = reader(release)
        except FileNotFoundError:
            errors.append(error(f"Agent Factory {label} is unavailable"))
        except Exception as exc:
            errors.append(
                error(
                    f"Agent Factory {label} is invalid: {type(exc).__name__}",
                    severity="critical",
                )
            )
            integrity_failed = True
    release_state = values.get(RELEASE_STATE_FILENAME)
    report = values.get(TEST_REPORT_FILENAME)
    approval = values.get(APPROVAL_FILENAME)
    deployment = values.get(DEPLOYMENT_RECORD_FILENAME)

    try:
        raw_manifest = json.loads((release / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        if not isinstance(raw_manifest, dict) or not isinstance(
            raw_manifest.get("combined_sha256"), str
        ):
            raise ValueError("manifest shape")
        manifest = raw_manifest
        recomputed = recompute_manifest(release)
        if recomputed["combined_sha256"] != manifest["combined_sha256"]:
            raise ValueError("manifest mismatch")
    except FileNotFoundError:
        errors.append(error("Agent Factory manifest is unavailable", severity="critical"))
        integrity_failed = True
    except Exception as exc:
        errors.append(
            error(
                f"Agent Factory manifest integrity failed: {type(exc).__name__}",
                severity="critical",
            )
        )
        integrity_failed = True

    try:
        loaded = json.loads((release / REVIEW_PACKET_FILENAME).read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("review packet shape")
        review_packet = loaded
    except FileNotFoundError:
        errors.append(error("Agent Factory review packet is unavailable"))
    except Exception as exc:
        errors.append(
            error(
                f"Agent Factory review packet is invalid: {type(exc).__name__}",
                severity="critical",
            )
        )
        integrity_failed = True

    manifest_hash = manifest.get("combined_sha256") if manifest is not None else None
    release_id = getattr(release_state, "release_id", source_id)
    bindings = (
        (getattr(release_state, "manifest_combined_sha256", None), "release state"),
        (getattr(report, "manifest_combined_sha256", None), "test report"),
        (
            review_packet.get("manifest_combined_sha256")
            if isinstance(review_packet, dict)
            else None,
            "review packet",
        ),
    )
    if manifest_hash is not None:
        for bound_hash, label in bindings:
            if bound_hash is not None and bound_hash != manifest_hash:
                errors.append(
                    error(
                        f"Agent Factory {label} manifest binding failed",
                        severity="critical",
                    )
                )
                integrity_failed = True
    for candidate, label in (
        (getattr(report, "release_id", None), "test report"),
        (getattr(approval, "release_id", None), "approval"),
        (getattr(deployment, "release_id", None), "deployment record"),
        (
            review_packet.get("release_id") if isinstance(review_packet, dict) else None,
            "review packet",
        ),
    ):
        if candidate is not None and candidate != release_id:
            errors.append(error(f"Agent Factory {label} release binding failed", severity="critical"))
            integrity_failed = True

    state = "UNKNOWN"
    layer_verdicts: tuple[tuple[str, str], ...] = ()
    if report is not None:
        state = report.overall_verdict.value
        layer_verdicts = tuple(
            (layer.layer, layer.verdict.value) for layer in report.layers
        )
    if integrity_failed:
        state = "FAIL"
    review_state = (
        getattr(approval, "decision", None)
        or ("pending" if review_packet is not None else "missing")
    )
    deployment_outcome = getattr(deployment, "outcome", None)
    build = BuildSummary(
        source=source(Freshness.UNAVAILABLE if errors else Freshness.LIVE),
        product_id=product_id,
        release_id=release_id,
        state=state,
        manifest_hash=manifest_hash,
        layer_verdicts=layer_verdicts,
        review_state=review_state,
        deployment_outcome=deployment_outcome,
    )
    return FactoryReadResult(
        builds=(build,),
        errors=tuple(errors),
    )
