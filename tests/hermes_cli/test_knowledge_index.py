from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pytest

from hermes_cli import knowledge_index as ki


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    return home


def _write_note(home: Path, name: str, *, task="t_aaa111", title="Kanban Review", decision="approve", area="dashboard", files=None, lesson="Keep it small", changed="Changed kanban", captured=100) -> Path:
    root = home / "kanban" / "knowledge" / "review-captures"
    root.mkdir(parents=True, exist_ok=True)
    files = files if files is not None else ["hermes_cli/kanban.py"]
    file_lines = "\n".join(f"- `{f}`" for f in files)
    text = f"""# Review Capture — {title}

- Task: `{task}`
- Decision: `{decision}`
- Reviewer: reviewer
- Captured at: {captured}

## What changed
{changed}

## Review decision
ok

## Key lesson / implementation note
{lesson}

## Files / area affected

Area: {area}

{file_lines}
"""
    path = root / name
    path.write_text(text, encoding="utf-8")
    return path


def test_index_generation_from_valid_phase5_note(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _write_note(home, "100-t_aaa111.md")

    index = ki.build_index()

    assert index["version"] == 1
    assert len(index["captures"]) == 1
    c = index["captures"][0]
    assert c["task_id"] == "t_aaa111"
    assert c["title"] == "Kanban Review"
    assert c["decision"] == "approve"
    assert c["area"] == "dashboard"
    assert c["files"] == ["hermes_cli/kanban.py"]
    assert c["lesson"] == "Keep it small"
    assert c["what_changed"] == "Changed kanban"
    assert c["captured_at"] == 100
    assert c["path"] == "kanban/knowledge/review-captures/100-t_aaa111.md"


def test_missing_optional_fields_allowed(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _write_note(home, "100-t_aaa111.md", area="Not specified", files=[])

    c = ki.build_index()["captures"][0]

    assert c["area"] is None
    assert c["files"] == []


def test_malformed_note_skipped_with_warning_and_index_excludes_index_json(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    root = home / "kanban" / "knowledge" / "review-captures"
    _write_note(home, "100-t_aaa111.md")
    (root / "bad.md").write_text("# nope\n", encoding="utf-8")
    (root / "index.json").write_text("{}", encoding="utf-8")

    index = ki.build_index()

    assert len(index["captures"]) == 1
    assert index["warnings"]
    assert all(c["path"].endswith(".md") for c in index["captures"])


def test_auto_rebuild_when_missing_stale_or_note_count_changes(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    first = _write_note(home, "100-t_aaa111.md", captured=100)

    index = ki.load_index(refresh=True)
    assert len(index["captures"]) == 1
    index_file = ki.index_path()
    assert index_file.exists()

    time.sleep(0.01)
    first.write_text(first.read_text(encoding="utf-8").replace("Changed kanban", "Changed again"), encoding="utf-8")
    index = ki.load_index(refresh=True)
    assert index["captures"][0]["what_changed"] == "Changed again"

    _write_note(home, "200-t_bbb222.md", task="t_bbb222", captured=200)
    index = ki.load_index(refresh=True)
    assert len(index["captures"]) == 2


def test_write_index_is_atomic(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _write_note(home, "100-t_aaa111.md")
    calls = []

    def fake_replace(src, dst):
        calls.append((Path(src).name, Path(dst).name))
        Path(dst).write_text(Path(src).read_text(encoding="utf-8"), encoding="utf-8")
        Path(src).unlink()

    monkeypatch.setattr(ki.os, "replace", fake_replace)
    ki.write_index(ki.build_index())

    assert calls and calls[0][1] == "index.json"
    assert not list(ki.captures_dir().glob(".index.json.tmp.*"))


def test_deterministic_ordering_and_case_insensitive_searches(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _write_note(home, "100-t_aaa111.md", task="t_aaa111", title="Old", decision="approve", area="core", files=["a.py"], lesson="Alpha lesson", changed="First change", captured=100)
    _write_note(home, "200-t_bbb222.md", task="t_bbb222", title="New", decision="reject", area="Dashboard", files=["hermes_cli/kanban.py"], lesson="Beta lesson", changed="Kanban changed", captured=200)
    index = ki.build_index()

    assert [c["task_id"] for c in index["captures"]] == ["t_bbb222", "t_aaa111"]
    assert [c["task_id"] for c in ki.search_index(index, query="KANBAN")] == ["t_bbb222"]
    assert [c["task_id"] for c in ki.search_index(index, file="hermes_cli/kanban.py")] == ["t_bbb222"]
    assert [c["task_id"] for c in ki.search_index(index, area="dash")] == ["t_bbb222"]
    assert [c["task_id"] for c in ki.search_index(index, query="t_aaa111")] == ["t_aaa111"]
    assert [c["task_id"] for c in ki.search_index(index, query="alpha")] == ["t_aaa111"]
    assert [c["task_id"] for c in ki.search_index(index, decision="reject")] == ["t_bbb222"]
    assert [c["task_id"] for c in ki.search_index(index, query="first")] == ["t_aaa111"]


def test_show_exact_task_id_missing_and_duplicate(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _write_note(home, "100-t_aaa111.md", task="t_aaa111", captured=100)
    index = ki.build_index()
    assert ki.show_capture(index, "t_aaa111")["task_id"] == "t_aaa111"
    with pytest.raises(LookupError):
        ki.show_capture(index, "t_missing")

    _write_note(home, "200-t_aaa111.md", task="t_aaa111", captured=200)
    with pytest.raises(RuntimeError):
        ki.show_capture(ki.build_index(), "t_aaa111")


def test_path_validation_rejects_traversal_and_absolute(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="repository-relative"):
        ki.search_index({"captures": []}, file="/abs.py")
    with pytest.raises(ValueError, match="traversal"):
        ki.search_index({"captures": []}, file="../x.py")
    with pytest.raises(ValueError, match="relative"):
        ki._validate_note_rel_path("/tmp/x.md")
    with pytest.raises(ValueError, match="traversal"):
        ki._validate_note_rel_path("kanban/knowledge/review-captures/../x.md")


def test_indexing_does_not_modify_source_markdown(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    note = _write_note(home, "100-t_aaa111.md")
    before = note.read_text(encoding="utf-8")
    ki.write_index(ki.build_index())
    ki.load_index(refresh=True)
    assert note.read_text(encoding="utf-8") == before


def test_cli_index_search_recent_list_and_show(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    _write_note(home, "100-t_aaa111.md", task="t_aaa111", captured=100)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    ki.build_parser(sub)

    assert ki.knowledge_command(parser.parse_args(["knowledge", "index"])) == 0
    assert "Indexed 1" in capsys.readouterr().out
    assert ki.knowledge_command(parser.parse_args(["knowledge", "list"])) == 0
    assert "t_aaa111" in capsys.readouterr().out
    assert ki.knowledge_command(parser.parse_args(["knowledge", "recent"])) == 0
    assert "t_aaa111" in capsys.readouterr().out
    assert ki.knowledge_command(parser.parse_args(["knowledge", "search", "kanban"])) == 0
    assert "t_aaa111" in capsys.readouterr().out
    assert ki.knowledge_command(parser.parse_args(["knowledge", "show", "t_aaa111"])) == 0
    assert "Review Capture" in capsys.readouterr().out
