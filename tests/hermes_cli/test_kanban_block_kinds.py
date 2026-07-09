"""Tests for typed block reasons + the unblock-loop breaker.

Covers the built-in fix for the kanban "blocked loop" — a worker blocks a
task, a cron unblocks it, the worker re-blocks for the same reason, repeat
forever. The fix gives ``block_task`` a typed ``kind`` and a persistent
``block_recurrences`` counter:

* ``dependency`` blocks route to ``todo`` (parent-gated, auto-resumed) and
  never enter the human ``blocked`` bucket a cron would keep unblocking.
* ``needs_input`` / ``capability`` / un-typed blocks land in ``blocked``;
  each same-cause re-block after an unblock increments ``block_recurrences``,
  and at ``BLOCK_RECURRENCE_LIMIT`` the task routes to ``triage`` for a human.
* ``unblock_task`` deliberately does NOT reset ``block_recurrences`` (the
  amnesia that let the loop run unbounded).
* A successful ``complete_task`` resets the loop memory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _running_task(conn, title="t"):
    """Create a task and drive it to ``running`` so block_task can act."""
    tid = kb.create_task(conn, title=title, assignee="worker")
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
    claimed = kb.claim_task(conn, tid, claimer="worker")
    assert claimed is not None
    return tid


def _make_running_again(conn, tid):
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
    assert kb.claim_task(conn, tid, claimer="worker") is not None


# ---------------------------------------------------------------------------
# Loop breaker
# ---------------------------------------------------------------------------


def test_first_typed_block_lands_in_blocked(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="which key?", kind="needs_input")
        t = kb.get_task(conn, tid)
        assert t.status == "blocked"
        assert t.block_kind == "needs_input"
        assert t.block_recurrences == 1


def _review_summary() -> str:
    return (
        "What changed: implementation updates are ready. "
        "What should be reviewed: changed files and tests. "
        "Recommended decision: approve if checks match the report."
    )


def test_review_required_block_lands_in_blocked_with_review_lifecycle(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(
            conn,
            tid,
            reason=_review_summary(),
            kind="review_required",
        )
        t = kb.get_task(conn, tid)
        assert t is not None
        assert t.status == "blocked"
        assert t.block_kind == "review_required"
        assert kb.effective_lifecycle_state(t) == "review_required"


def test_review_required_block_requires_structured_review_summary(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        with pytest.raises(ValueError, match="what should be reviewed"):
            kb.block_task(
                conn,
                tid,
                reason="What changed: only a partial summary",
                kind="review_required",
            )
        after_reject = kb.get_task(conn, tid)
        assert after_reject is not None
        assert after_reject.status == "running"


def test_legacy_review_required_prefix_is_still_recognised() -> None:
    assert kb.is_review_required_reason("review-required: please inspect")
    assert kb.is_review_required_reason("  REVIEW-REQUIRED: please inspect")
    assert not kb.is_review_required_reason("needs input: please inspect")


def test_request_changes_unblock_resumes_same_task_without_duplicate(kanban_home: Path) -> None:
    """Review Required → Request Changes → Ready → worker resumes same task."""
    with kb.connect_closing() as conn:
        tid = _running_task(conn, title="implementation needs review")
        assert kb.block_task(
            conn,
            tid,
            reason=_review_summary(),
            kind="review_required",
        )
        blocked = kb.get_task(conn, tid)
        assert blocked is not None
        assert blocked.status == "blocked"
        before_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

        kb.add_comment(conn, tid, author="reviewer", body="Request changes: update tests")
        assert kb.unblock_task(conn, tid)
        ready = kb.get_task(conn, tid)
        assert ready is not None
        assert ready.status == "ready"

        resumed = kb.claim_task(conn, tid, claimer="worker")
        assert resumed is not None
        assert resumed.id == tid
        running = kb.get_task(conn, tid)
        assert running is not None
        assert running.status == "running"
        after_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        assert after_count == before_count


def test_review_decision_approve_completes_original_task(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason=_review_summary(), kind="review_required")
        before_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

        assert kb.review_required_decision(
            conn,
            tid,
            decision="approve",
            reviewer="reviewer",
            comment="Looks good",
        )

        task = kb.get_task(conn, tid)
        assert task is not None
        assert task.status == "done"
        assert task.block_kind is None
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == before_count
        assert any("REVIEW APPROVED" in c.body for c in kb.list_comments(conn, tid))
        assert any(
            e.kind == "review_decision"
            and e.payload is not None
            and e.payload["decision"] == "approve"
            for e in kb.list_events(conn, tid)
        )


def test_review_decision_request_changes_returns_original_task_ready(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason=_review_summary(), kind="review_required")
        before_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

        assert kb.review_required_decision(
            conn,
            tid,
            decision="request-changes",
            reviewer="reviewer",
            comment="Fix tests",
        )

        task = kb.get_task(conn, tid)
        assert task is not None
        assert task.status == "ready"
        assert task.block_kind == "review_required"
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == before_count
        assert any("REVIEW REQUEST CHANGES" in c.body for c in kb.list_comments(conn, tid))


def test_review_decision_reject_archives_original_task(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason=_review_summary(), kind="review_required")
        before_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

        assert kb.review_required_decision(
            conn,
            tid,
            decision="reject",
            reviewer="reviewer",
            comment="Close without merge",
        )

        task = kb.get_task(conn, tid)
        assert task is not None
        assert task.status == "archived"
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == before_count
        assert any("REVIEW REJECTED" in c.body for c in kb.list_comments(conn, tid))


def test_review_decision_rejects_non_review_required_and_repeated_decision(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        with pytest.raises(ValueError, match="review decisions apply only"):
            kb.review_required_decision(
                conn, tid, decision="approve", reviewer="reviewer", comment="ok"
            )
        assert kb.block_task(conn, tid, reason=_review_summary(), kind="review_required")
        assert kb.review_required_decision(
            conn, tid, decision="approve", reviewer="reviewer", comment="ok"
        )
        with pytest.raises(ValueError, match="review decisions apply only"):
            kb.review_required_decision(
                conn, tid, decision="approve", reviewer="reviewer", comment="again"
            )


def test_review_decision_rejects_self_approval(kanban_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason=_review_summary(), kind="review_required")
        with pytest.raises(ValueError, match="assignee cannot review"):
            kb.review_required_decision(
                conn, tid, decision="approve", reviewer="worker", comment="ok"
            )
        monkeypatch.setattr(kb, "_current_profile_name_for_review", lambda: "worker")
        with pytest.raises(ValueError, match="active profile cannot review"):
            kb.review_required_decision(
                conn, tid, decision="approve", reviewer="reviewer", comment="ok"
            )


def test_review_decision_does_not_apply_to_physical_review_status(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="physical review", assignee="worker")
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='review' WHERE id=?", (tid,))
        with pytest.raises(ValueError, match="review decisions apply only"):
            kb.review_required_decision(
                conn, tid, decision="approve", reviewer="reviewer", comment="ok"
            )


def test_unblock_does_not_reset_recurrence_counter(kanban_home: Path) -> None:
    """The crux of the fix: unblock must preserve the loop counter."""
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        kb.block_task(conn, tid, reason="x", kind="needs_input")
        assert kb.get_task(conn, tid).block_recurrences == 1
        assert kb.unblock_task(conn, tid)
        t = kb.get_task(conn, tid)
        assert t.status == "ready"
        assert t.block_recurrences == 1  # NOT reset to 0
        assert t.block_kind == "needs_input"  # kind preserved for comparison


def test_same_cause_reblock_routes_to_triage(kanban_home: Path) -> None:
    """Dale's loop: block → unblock → re-block same kind → triage."""
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        kb.block_task(conn, tid, reason="need creds", kind="needs_input")
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        kb.block_task(conn, tid, reason="still need creds", kind="needs_input")
        t = kb.get_task(conn, tid)
        assert t.status == "triage"
        assert t.block_recurrences == 2


def test_untyped_block_loop_also_protected(kanban_home: Path) -> None:
    """Legacy un-typed blocks (kind=None) still trip the breaker."""
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        kb.block_task(conn, tid, reason="a")
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        kb.block_task(conn, tid, reason="a again")
        assert kb.get_task(conn, tid).status == "triage"


def test_different_kinds_do_not_compound(kanban_home: Path) -> None:
    """A re-block for a DIFFERENT reason resets the counter to 1."""
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        kb.block_task(conn, tid, reason="a", kind="needs_input")
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        kb.block_task(conn, tid, reason="b", kind="capability")
        t = kb.get_task(conn, tid)
        assert t.status == "blocked"
        assert t.block_recurrences == 1


def test_block_loop_detected_event_emitted(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        kb.block_task(conn, tid, reason="x", kind="capability")
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        kb.block_task(conn, tid, reason="x", kind="capability")
        events = [e for e in kb.list_events(conn, tid)
                  if e.kind == "block_loop_detected"]
        assert events, "expected a block_loop_detected event"
        payload = events[-1].payload or {}
        assert payload.get("recurrences") == 2
        assert payload.get("kind") == "capability"


# ---------------------------------------------------------------------------
# Dependency routing
# ---------------------------------------------------------------------------


def test_dependency_block_routes_to_todo(kanban_home: Path) -> None:
    """Dependency waits never enter the human 'blocked' bucket."""
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="need X first", kind="dependency")
        t = kb.get_task(conn, tid)
        assert t.status == "todo"
        assert t.block_kind == "dependency"


def test_dependency_then_parent_done_promotes(kanban_home: Path) -> None:
    """A dependency-parked child becomes ready once its parent completes."""
    with kb.connect_closing() as conn:
        parent = kb.create_task(conn, title="parent", assignee="worker")
        child = _running_task(conn, title="child")
        kb.link_tasks(conn, parent_id=parent, child_id=child)
        kb.block_task(conn, child, reason="wait", kind="dependency")
        assert kb.get_task(conn, child).status == "todo"
        # Finish the parent, then let recompute_ready run.
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (parent,))
        kb.claim_task(conn, parent, claimer="worker")
        kb.complete_task(conn, parent, result="done")
        kb.recompute_ready(conn)
        assert kb.get_task(conn, child).status == "ready"


# ---------------------------------------------------------------------------
# Completion resets loop memory
# ---------------------------------------------------------------------------


def test_completion_clears_block_memory(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        kb.block_task(conn, tid, reason="x", kind="capability")
        kb.unblock_task(conn, tid)
        assert kb.get_task(conn, tid).block_recurrences == 1
        kb.complete_task(conn, tid, result="done")
        t = kb.get_task(conn, tid)
        assert t.status == "done"
        assert t.block_recurrences == 0
        assert t.block_kind is None


# ---------------------------------------------------------------------------
# Validation + back-compat
# ---------------------------------------------------------------------------


def test_invalid_kind_rejected(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        with pytest.raises(ValueError):
            kb.block_task(conn, tid, reason="x", kind="bogus")


def test_block_without_kind_is_backward_compatible(kanban_home: Path) -> None:
    """Existing callers that pass no kind keep the old single-block behaviour."""
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="legacy")
        t = kb.get_task(conn, tid)
        assert t.status == "blocked"
        assert t.block_kind is None
