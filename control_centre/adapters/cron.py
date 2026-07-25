"""Read-only cron jobs.json adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from control_centre.schema import Freshness, SourceError, SourceRef


@dataclass(frozen=True)
class CronReadResult:
    jobs: tuple[dict[str, object], ...] = ()
    errors: tuple[SourceError, ...] = ()


def read_cron_jobs(jobs_file: Path, *, observed_at: float) -> CronReadResult:
    path = Path(jobs_file)
    source = SourceRef(
        source="cron",
        source_id="jobs",
        observed_at=observed_at,
        freshness=Freshness.LIVE,
        href="/cron",
    )
    if not path.is_file():
        return CronReadResult()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        rows = raw.get("jobs", []) if isinstance(raw, dict) else None
        if not isinstance(rows, list):
            raise ValueError("jobs must be a list")
        jobs: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("id", "")).strip():
                raise ValueError("job rows require an id")
            job_id = str(row["id"])
            jobs.append(
                {
                    "id": job_id,
                    "name": row.get("name"),
                    "enabled": bool(row.get("enabled", True)),
                    "next_run": row.get("next_run"),
                    "last_status": row.get("last_status"),
                    "last_error": row.get("last_error"),
                    "href": f"/cron?job={job_id}",
                }
            )
        return CronReadResult(jobs=tuple(sorted(jobs, key=lambda item: str(item["id"]))))
    except Exception as exc:
        return CronReadResult(
            errors=(
                SourceError(
                    source=SourceRef(
                        source=source.source,
                        source_id=source.source_id,
                        observed_at=observed_at,
                        freshness=Freshness.UNAVAILABLE,
                        href=source.href,
                    ),
                    message=f"Cron status unavailable: {type(exc).__name__}",
                    recovery="Open Cron and validate jobs.json",
                ),
            ),
        )
