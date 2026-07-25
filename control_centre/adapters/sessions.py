"""Read-only session metadata adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hermes_state import SessionDB

from control_centre.schema import Freshness, SourceError, SourceRef


@dataclass(frozen=True)
class SessionReadResult:
    count: int
    sessions: tuple[dict[str, object], ...] = ()
    errors: tuple[SourceError, ...] = ()


def read_sessions(
    db_path: Path,
    *,
    observed_at: float,
    limit: int = 20,
) -> SessionReadResult:
    """Return session metadata only; message bodies never cross this adapter."""

    source = SourceRef(
        source="sessions",
        source_id="session-store",
        observed_at=observed_at,
        freshness=Freshness.LIVE,
        href="/sessions",
    )
    path = Path(db_path)
    if not path.is_file():
        return SessionReadResult(
            count=0,
            errors=(
                SourceError(
                    source=SourceRef(
                        source="sessions",
                        source_id="session-store",
                        observed_at=observed_at,
                        freshness=Freshness.UNAVAILABLE,
                        href="/sessions",
                    ),
                    message="Session store is unavailable",
                    recovery="Start a Hermes session to initialize the store",
                ),
            ),
        )
    db: SessionDB | None = None
    try:
        db = SessionDB(path, read_only=True)
        rows = db.list_sessions_rich(
            limit=limit,
            order_by_last_active=True,
            compact_rows=True,
        )
        sessions = tuple(
            {
                "id": str(row["id"]),
                "title": row.get("title"),
                "source": row.get("source"),
                "last_active": row.get("last_active"),
                "href": f"/sessions?session={row['id']}",
            }
            for row in rows
        )
        return SessionReadResult(count=len(sessions), sessions=sessions)
    except Exception as exc:
        return SessionReadResult(
            count=0,
            errors=(
                SourceError(
                    source=SourceRef(
                        source=source.source,
                        source_id=source.source_id,
                        observed_at=observed_at,
                        freshness=Freshness.UNAVAILABLE,
                        href=source.href,
                    ),
                    message=f"Session metadata unavailable: {type(exc).__name__}",
                    recovery="Open the Sessions view and inspect the store status",
                ),
            ),
        )
    finally:
        if db is not None:
            db.close()
