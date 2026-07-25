"""Read-only Hermes profile adapter for the Control Centre agent fleet."""

from __future__ import annotations

from collections.abc import Iterable

from hermes_cli.profiles import ProfileInfo, list_profiles

from control_centre.adapters.kanban import KanbanReadResult
from control_centre.schema import AgentSummary, Freshness, SourceRef


def read_profiles(
    kanban: KanbanReadResult,
    *,
    observed_at: float,
    profile_infos: Iterable[ProfileInfo] | None = None,
) -> tuple[AgentSummary, ...]:
    """Combine configured profile metadata with current Kanban activity."""

    infos = list(list_profiles() if profile_infos is None else profile_infos)
    agents: list[AgentSummary] = []
    activities = kanban.activities or {}
    for profile in infos:
        activity = activities.get(profile.name)
        freshness = (
            Freshness.STALE
            if activity is not None and activity.heartbeat_state == "stale"
            else Freshness.LIVE
        )
        agents.append(
            AgentSummary(
                source=SourceRef(
                    source="profiles",
                    source_id=profile.name,
                    observed_at=observed_at,
                    freshness=freshness,
                    href=f"/profiles?profile={profile.name}",
                ),
                profile=profile.name,
                state=activity.state if activity is not None else "idle",
                role=profile.description or None,
                task_id=activity.task_id if activity is not None else None,
                run_id=activity.run_id if activity is not None else None,
                last_heartbeat=(
                    activity.last_heartbeat if activity is not None else None
                ),
                lease_expires_at=(
                    activity.lease_expires_at if activity is not None else None
                ),
                model=profile.model,
                provider=profile.provider,
                skill_count=profile.skill_count,
                workspace=activity.workspace if activity is not None else None,
                chat_href=f"/chat?profile={profile.name}",
            )
        )
    return tuple(sorted(agents, key=lambda item: item.profile))
