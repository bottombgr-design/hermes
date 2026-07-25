"""Lightweight review knowledge-capture notes for Kanban tasks.

This module intentionally writes only concise, explicitly supplied metadata.
It does not read task files, run metadata, environment variables, or vaults.
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable, Optional

from hermes_cli import kanban_db


_CAPTURE_ROOT = "review-captures"
_MAX_FIELD_CHARS = 1000
_MAX_AREA_CHARS = 500
_MAX_FILES = 50


@dataclass(frozen=True)
class CaptureResult:
    path: str
    warning: Optional[str] = None


@dataclass(frozen=True)
class CaptureRequest:
    what_changed: str
    lesson: str
    files: tuple[str, ...] = ()
    area: Optional[str] = None


def _redact(text: str) -> str:
    patterns = [
        r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s`]+",
        r"(?i)bearer\s+[a-z0-9._\-+/=]+",
        r"-----BEGIN [^-]+ PRIVATE KEY-----.*?-----END [^-]+ PRIVATE KEY-----",
    ]
    try:
        from agent.redact import redact_sensitive_text

        out = redact_sensitive_text(text, force=True)
    except Exception:
        out = text
    for pat in patterns:
        out = re.sub(pat, "[REDACTED]", out, flags=re.DOTALL)
    return out


def _clean_text(value: object, *, max_chars: int = _MAX_FIELD_CHARS) -> str:
    text = str(value or "").strip()
    text = _redact(text)
    # Plain Markdown only; escape raw HTML/control chars from user input.
    text = html.escape(text, quote=False)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def validate_capture_request(
    *,
    what_changed: Optional[str],
    lesson: Optional[str],
    files: Optional[Iterable[str]] = None,
    area: Optional[str] = None,
) -> CaptureRequest:
    clean_changed = _clean_text(what_changed)
    clean_lesson = _clean_text(lesson)
    clean_area = _clean_text(area, max_chars=_MAX_AREA_CHARS) if area else None
    if not clean_changed:
        raise ValueError("what_changed is required when capture is requested")
    if not clean_lesson:
        raise ValueError("lesson is required when capture is requested")

    clean_files: list[str] = []
    for raw in list(files or [])[: _MAX_FILES + 1]:
        f = str(raw or "").strip().replace("\\", "/")
        if not f:
            continue
        p = PurePosixPath(f)
        if p.is_absolute():
            raise ValueError("capture file paths must be repository-relative")
        if ".." in p.parts:
            raise ValueError("capture file paths cannot contain traversal")
        clean_files.append(_clean_text(p.as_posix(), max_chars=300))
    if len(clean_files) > _MAX_FILES:
        raise ValueError(f"capture supports at most {_MAX_FILES} file entries")
    return CaptureRequest(
        what_changed=clean_changed,
        lesson=clean_lesson,
        files=tuple(clean_files),
        area=clean_area,
    )


def capture_root() -> str:
    root = kanban_db.kanban_home() / "kanban" / "knowledge" / _CAPTURE_ROOT
    return str(root)


def write_review_capture_note(
    conn,
    task_id: str,
    *,
    decision: str,
    reviewer: str,
    comment: str,
    capture: CaptureRequest,
) -> str:
    """Write an explicit review capture note and return its generated path.

    The path is generated internally under kanban/knowledge/review-captures/.
    Listed files are metadata only: this function never opens them.
    """
    norm_decision = (decision or "").strip().replace("-", "_")
    if norm_decision == "request_changes":
        raise ValueError("knowledge capture is not allowed for request-changes")
    if norm_decision not in {"approve", "reject"}:
        raise ValueError("knowledge capture is allowed only for approve or reject")

    task = kanban_db.get_task(conn, task_id)
    if task is None:
        raise ValueError(f"unknown task {task_id}")

    now = int(time.time())
    rel_path = f"kanban/knowledge/{_CAPTURE_ROOT}/{now}-{task_id}.md"
    out_dir = kanban_db.kanban_home() / "kanban" / "knowledge" / _CAPTURE_ROOT
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{now}-{task_id}.md"

    files_block = "\n".join(f"- `{f}`" for f in capture.files) or "- _Not specified_"
    area = capture.area or "Not specified"
    body = f"""# Review Capture — {_clean_text(task.title, max_chars=200)}

- Task: `{_clean_text(task_id, max_chars=80)}`
- Decision: `{_clean_text(norm_decision, max_chars=40)}`
- Reviewer: {_clean_text(reviewer, max_chars=120)}
- Captured at: {now}

## What changed
{capture.what_changed}

## Review decision
{_clean_text(comment)}

## Key lesson / implementation note
{capture.lesson}

## Files / area affected

Area: {area}

{files_block}
"""
    out_path.write_text(body, encoding="utf-8")
    with kanban_db.write_txn(conn):
        kanban_db._append_event(
            conn,
            task_id,
            "knowledge_captured",
            {
                "path": rel_path,
                "decision": norm_decision,
                "reviewer": _clean_text(reviewer, max_chars=120),
            },
        )
    return rel_path
