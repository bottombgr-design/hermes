"""Deterministic file-based index for Kanban review-capture notes."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Optional

from hermes_cli import kanban_db

INDEX_VERSION = 1
CAPTURE_REL_DIR = PurePosixPath("kanban/knowledge/review-captures")
INDEX_FILENAME = "index.json"


@dataclass(frozen=True)
class KnowledgeCapture:
    task_id: str
    title: str
    decision: str
    area: Optional[str]
    files: tuple[str, ...]
    lesson: str
    what_changed: str
    captured_at: int
    path: str


def captures_dir() -> Path:
    return kanban_db.kanban_home() / str(CAPTURE_REL_DIR)


def index_path() -> Path:
    return captures_dir() / INDEX_FILENAME


def _rel_capture_path(path: Path) -> str:
    root = kanban_db.kanban_home().resolve()
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("capture note path must live under kanban home") from exc
    rel_posix = rel.as_posix()
    _validate_note_rel_path(rel_posix)
    return rel_posix


def _validate_note_rel_path(path: str) -> None:
    p = PurePosixPath(str(path).replace("\\", "/"))
    if p.is_absolute():
        raise ValueError("note path must be relative")
    if ".." in p.parts:
        raise ValueError("note path cannot contain traversal")
    if not p.is_relative_to(CAPTURE_REL_DIR):
        raise ValueError("note path must stay under kanban/knowledge/review-captures")


def _validate_file_filter(path: str) -> str:
    p = PurePosixPath(str(path or "").strip().replace("\\", "/"))
    if p.is_absolute():
        raise ValueError("file filter must be repository-relative")
    if ".." in p.parts:
        raise ValueError("file filter cannot contain traversal")
    return p.as_posix()


def _strip_ticks(value: str) -> str:
    value = value.strip()
    if value.startswith("`") and value.endswith("`") and len(value) >= 2:
        return value[1:-1].strip()
    return value


def _section(text: str, heading: str) -> str:
    pattern = rf"^##\s+{re.escape(heading)}\s*$"
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        return ""
    start = match.end()
    next_match = re.search(r"^##\s+", text[start:], flags=re.MULTILINE)
    end = start + next_match.start() if next_match else len(text)
    return text[start:end].strip()


def parse_capture_note(path: Path) -> KnowledgeCapture:
    rel_path = _rel_capture_path(path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = ""
    task_id = ""
    decision = ""
    captured_at = 0
    reviewer = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# ") and not title:
            title = stripped[2:].strip()
            if title.lower().startswith("review capture") and "—" in title:
                title = title.split("—", 1)[1].strip()
        elif stripped.startswith("- Task:"):
            task_id = _strip_ticks(stripped.split(":", 1)[1])
        elif stripped.startswith("- Decision:"):
            decision = _strip_ticks(stripped.split(":", 1)[1]).replace("-", "_")
        elif stripped.startswith("- Reviewer:"):
            reviewer = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("- Captured at:"):
            raw = stripped.split(":", 1)[1].strip()
            try:
                captured_at = int(raw)
            except ValueError:
                captured_at = 0
    what_changed = _section(text, "What changed")
    lesson = _section(text, "Key lesson / implementation note")
    affected = _section(text, "Files / area affected")
    area: Optional[str] = None
    files: list[str] = []
    for line in affected.splitlines():
        stripped = line.strip()
        if stripped.startswith("Area:"):
            value = stripped.split(":", 1)[1].strip()
            if value and value != "Not specified":
                area = value
        elif stripped.startswith("- `") and stripped.endswith("`"):
            files.append(_strip_ticks(stripped[2:].strip()))
    if not task_id:
        raise ValueError("missing Task field")
    if decision not in {"approve", "reject"}:
        raise ValueError("missing or unsupported Decision field")
    return KnowledgeCapture(
        task_id=task_id,
        title=title,
        decision=decision,
        area=area,
        files=tuple(files),
        lesson=lesson,
        what_changed=what_changed,
        captured_at=captured_at,
        path=rel_path,
    )


def capture_note_paths() -> list[Path]:
    root = captures_dir()
    if not root.exists():
        return []
    return sorted(
        p for p in root.glob("*.md")
        if p.is_file() and p.name != INDEX_FILENAME
    )


def build_index() -> dict[str, Any]:
    warnings: list[str] = []
    captures: list[KnowledgeCapture] = []
    for path in capture_note_paths():
        try:
            captures.append(parse_capture_note(path))
        except Exception as exc:
            warnings.append(f"skipped malformed capture note {path.name}: {exc}")
    captures.sort(key=lambda c: (-int(c.captured_at or 0), c.task_id, c.path))
    return {
        "version": INDEX_VERSION,
        "generated_at": int(time.time()),
        "source_dir": str(CAPTURE_REL_DIR),
        "captures": [asdict(c) | {"files": list(c.files)} for c in captures],
        "warnings": warnings,
    }


def write_index(index: dict[str, Any]) -> None:
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(index, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _index_is_stale(path: Path) -> bool:
    if not path.exists():
        return True
    notes = capture_note_paths()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return True
    if data.get("version") != INDEX_VERSION:
        return True
    if len(data.get("captures") or []) != len(notes):
        return True
    index_mtime = path.stat().st_mtime
    return any(p.stat().st_mtime > index_mtime for p in notes)


def load_index(*, refresh: bool = True) -> dict[str, Any]:
    path = index_path()
    if refresh and _index_is_stale(path):
        index = build_index()
        write_index(index)
        return index
    if not path.exists():
        return {"version": INDEX_VERSION, "generated_at": 0, "source_dir": str(CAPTURE_REL_DIR), "captures": [], "warnings": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _search_blob(capture: dict[str, Any]) -> str:
    parts: list[str] = [
        str(capture.get("task_id") or ""),
        str(capture.get("title") or ""),
        str(capture.get("area") or ""),
        str(capture.get("lesson") or ""),
        str(capture.get("what_changed") or ""),
        str(capture.get("decision") or ""),
    ]
    parts.extend(str(f) for f in (capture.get("files") or []))
    return "\n".join(parts).casefold()


def search_index(
    index: dict[str, Any],
    *,
    query: Optional[str] = None,
    file: Optional[str] = None,
    area: Optional[str] = None,
    decision: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    file_filter = _validate_file_filter(file) if file else None
    tokens = [t.casefold() for t in str(query or "").split() if t.strip()]
    area_q = area.casefold() if area else None
    decision_q = decision.replace("-", "_").casefold() if decision else None
    out: list[dict[str, Any]] = []
    for capture in index.get("captures") or []:
        if decision_q and str(capture.get("decision") or "").casefold() != decision_q:
            continue
        if area_q and area_q not in str(capture.get("area") or "").casefold():
            continue
        if file_filter and file_filter not in [str(f) for f in (capture.get("files") or [])]:
            continue
        blob = _search_blob(capture)
        if tokens and not all(token in blob for token in tokens):
            continue
        out.append(capture)
    out.sort(key=lambda c: (-int(c.get("captured_at") or 0), str(c.get("task_id") or ""), str(c.get("path") or "")))
    return out[:limit] if limit else out


def show_capture(index: dict[str, Any], task_id: str) -> dict[str, Any]:
    matches = [c for c in (index.get("captures") or []) if c.get("task_id") == task_id]
    if not matches:
        raise LookupError(f"no knowledge capture found for task {task_id}")
    if len(matches) > 1:
        raise RuntimeError(f"multiple knowledge captures found for task {task_id}")
    return matches[0]


def _print_rows(rows: Iterable[dict[str, Any]]) -> None:
    for c in rows:
        print(
            f"{c.get('captured_at', 0)}\t{c.get('decision', '')}\t"
            f"{c.get('task_id', '')}\t{c.get('area') or '-'}\t{c.get('title') or '-'}"
        )


def build_parser(parent_subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = parent_subparsers.add_parser("knowledge", help="Search local review knowledge captures")
    sub = parser.add_subparsers(dest="knowledge_action")

    p_index = sub.add_parser("index", help="Rebuild review-capture index")
    p_index.add_argument("--json", action="store_true")

    p_list = sub.add_parser("list", help="List review-capture index entries")
    p_list.add_argument("--json", action="store_true")
    p_list.add_argument("--limit", type=int, default=None)
    p_list.add_argument("--decision", choices=["approve", "reject"], default=None)

    p_recent = sub.add_parser("recent", help="Show recent review captures")
    p_recent.add_argument("--json", action="store_true")
    p_recent.add_argument("--limit", type=int, default=10)

    p_show = sub.add_parser("show", help="Show one capture by exact task id")
    p_show.add_argument("task_id")
    p_show.add_argument("--json", action="store_true")

    p_search = sub.add_parser("search", help="Deterministic substring search over captures")
    p_search.add_argument("query", nargs="?", default=None)
    p_search.add_argument("--json", action="store_true")
    p_search.add_argument("--limit", type=int, default=None)
    p_search.add_argument("--file", default=None)
    p_search.add_argument("--area", default=None)
    p_search.add_argument("--decision", choices=["approve", "reject"], default=None)

    parser.set_defaults(func=knowledge_command)
    return parser


def knowledge_command(args: argparse.Namespace) -> int:
    action = getattr(args, "knowledge_action", None)
    if not action:
        print("usage: hermes knowledge <index|list|recent|show|search>", file=sys.stderr)
        return 2
    try:
        if action == "index":
            index = build_index()
            write_index(index)
            if getattr(args, "json", False):
                print(json.dumps({"indexed": len(index["captures"]), "warnings": index.get("warnings", [])}, indent=2))
            else:
                print(f"Indexed {len(index['captures'])} review captures.")
                for w in index.get("warnings", []):
                    print(f"warning: {w}", file=sys.stderr)
            return 0

        index = load_index(refresh=True)
        if action == "list":
            rows = search_index(index, decision=getattr(args, "decision", None), limit=getattr(args, "limit", None))
        elif action == "recent":
            rows = search_index(index, limit=getattr(args, "limit", 10))
        elif action == "search":
            rows = search_index(
                index,
                query=getattr(args, "query", None),
                file=getattr(args, "file", None),
                area=getattr(args, "area", None),
                decision=getattr(args, "decision", None),
                limit=getattr(args, "limit", None),
            )
        elif action == "show":
            row = show_capture(index, args.task_id)
            if getattr(args, "json", False):
                print(json.dumps(row, indent=2, ensure_ascii=False))
            else:
                rel = row["path"]
                _validate_note_rel_path(rel)
                print((kanban_db.kanban_home() / rel).read_text(encoding="utf-8"))
            return 0
        else:
            print(f"unknown knowledge action: {action}", file=sys.stderr)
            return 2

        if getattr(args, "json", False):
            print(json.dumps({"results": rows, "warnings": index.get("warnings", [])}, indent=2, ensure_ascii=False))
        else:
            _print_rows(rows)
            for w in index.get("warnings", []):
                print(f"warning: {w}", file=sys.stderr)
        return 0
    except (ValueError, LookupError, RuntimeError) as exc:
        print(f"knowledge: {exc}", file=sys.stderr)
        return 1
