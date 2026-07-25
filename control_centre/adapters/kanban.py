"""Read-only Kanban adapter for attention and active-agent state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_diagnostics as kd

from control_centre.attention import sort_attention
from control_centre.schema import AttentionItem, Freshness, SourceError, SourceRef


@dataclass(frozen=True)
class TaskActivity:
    profile: str
    task_id: str
    run_id: str | None
    state: str
    last_heartbeat: float | None
    heartbeat_state: str
    lease_expires_at: float | None
    workspace: str | None
    task_href: str


@dataclass(frozen=True)
class KanbanReadResult:
    attention: tuple[AttentionItem, ...] = ()
    activities: dict[str, TaskActivity] | None = None
    errors: tuple[SourceError, ...] = ()

    def __post_init__(self) -> None:
        if self.activities is None:
            object.__setattr__(self, "activities", {})


def _task_source(
    task_id: str,
    observed_at: float,
    *,
    freshness: Freshness = Freshness.LIVE,
) -> SourceRef:
    return SourceRef(
        source="kanban",
        source_id=task_id,
        observed_at=observed_at,
        freshness=freshness,
        href=f"/kanban?task={task_id}",
    )


def diagnostic_to_attention(
    diagnostic: kd.Diagnostic,
    *,
    task_id: str,
    observed_at: float,
) -> AttentionItem:
    return AttentionItem(
        source=SourceRef(
            source="kanban",
            source_id=f"{task_id}:diagnostic:{diagnostic.kind}",
            observed_at=observed_at,
            freshness=Freshness.LIVE,
            href=f"/kanban?task={task_id}",
        ),
        severity=diagnostic.severity,
        title=diagnostic.title,
        detail=diagnostic.detail,
    )


def _heartbeat_state(
    heartbeat: int | None,
    *,
    observed_at: float,
    stale_after_seconds: int,
) -> str:
    if heartbeat is None:
        return "unknown"
    if observed_at - heartbeat > stale_after_seconds:
        return "stale"
    return "fresh"


def _activity_rank(state: str) -> int:
    return {"working": 0, "blocked": 1, "review_required": 2, "assigned": 3}.get(
        state, 4
    )


def read_kanban(
    conn: Any,
    *,
    observed_at: float,
    stale_after_seconds: int,
) -> KanbanReadResult:
    """Read task/run/event state without opening a write transaction."""

    attention: list[AttentionItem] = []
    activities: dict[str, TaskActivity] = {}
    errors: list[SourceError] = []
    tasks = kb.list_tasks(conn, include_archived=False, order_by="priority")
    for task in tasks:
        lifecycle = kb.effective_lifecycle_state(task)
        runs = kb.list_runs(conn, task.id)
        events = kb.list_events(conn, task.id)
        current_run = (
            kb.get_run(conn, task.current_run_id) if task.current_run_id is not None else None
        )
        if task.status == "running" and task.current_run_id is not None and current_run is None:
            errors.append(
                SourceError(
                    source=_task_source(task.id, observed_at, freshness=Freshness.UNAVAILABLE),
                    message="Running task references a missing run",
                    recovery="Inspect the task run history in Kanban",
                    severity="critical",
                )
            )

        try:
            diagnostics = kd.compute_task_diagnostics(
                task,
                events,
                runs,
                now=int(observed_at),
            )
        except Exception as exc:
            errors.append(
                SourceError(
                    source=_task_source(task.id, observed_at, freshness=Freshness.UNAVAILABLE),
                    message=f"Kanban diagnostics unavailable: {type(exc).__name__}",
                    recovery="Open the Kanban task and inspect its event history",
                )
            )
            diagnostics = []
        attention.extend(
            diagnostic_to_attention(item, task_id=task.id, observed_at=observed_at)
            for item in diagnostics
        )

        if lifecycle == "blocked":
            attention.append(
                AttentionItem(
                    source=_task_source(task.id, observed_at),
                    severity="error",
                    title=f"Blocked: {task.title}",
                    detail=task.last_failure_error or "Task requires human attention",
                    product_id=task.tenant,
                    agent_id=task.assignee,
                )
            )
        elif lifecycle == "review_required":
            attention.append(
                AttentionItem(
                    source=_task_source(task.id, observed_at),
                    severity="warning",
                    title=f"Review required: {task.title}",
                    detail="Implementation is waiting for independent review",
                    product_id=task.tenant,
                    agent_id=task.assignee,
                )
            )
        elif lifecycle == "in_progress":
            attention.append(
                AttentionItem(
                    source=_task_source(task.id, observed_at),
                    severity="info",
                    title=f"Running: {task.title}",
                    detail="Task is actively assigned to a worker",
                    product_id=task.tenant,
                    agent_id=task.assignee,
                )
            )

        heartbeat_state = _heartbeat_state(
            task.last_heartbeat_at,
            observed_at=observed_at,
            stale_after_seconds=stale_after_seconds,
        )
        if lifecycle == "in_progress" and heartbeat_state == "stale":
            attention.append(
                AttentionItem(
                    source=_task_source(task.id, observed_at, freshness=Freshness.STALE),
                    severity="warning",
                    title=f"Stale worker: {task.title}",
                    detail=(
                        f"Last heartbeat is older than {stale_after_seconds} seconds"
                    ),
                    product_id=task.tenant,
                    agent_id=task.assignee,
                )
            )

        if not task.assignee or lifecycle not in {
            "in_progress",
            "blocked",
            "review_required",
            "assigned",
        }:
            continue
        state = {
            "in_progress": "working",
            "blocked": "blocked",
            "review_required": "blocked",
            "assigned": "assigned",
        }[lifecycle]
        candidate = TaskActivity(
            profile=task.assignee,
            task_id=task.id,
            run_id=(str(task.current_run_id) if task.current_run_id is not None else None),
            state=state,
            last_heartbeat=task.last_heartbeat_at,
            heartbeat_state=heartbeat_state,
            lease_expires_at=task.claim_expires,
            workspace=task.workspace_path,
            task_href=f"/kanban?task={task.id}",
        )
        existing = activities.get(task.assignee)
        if existing is None or _activity_rank(candidate.state) < _activity_rank(
            existing.state
        ):
            activities[task.assignee] = candidate

    return KanbanReadResult(
        attention=sort_attention(attention),
        activities=activities,
        errors=tuple(sorted(errors, key=lambda item: item.source.source_id)),
    )
