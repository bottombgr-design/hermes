"""CLI: `hermes agent-factory validate/render/test/report/deploy`.

`deploy` must always refuse through the shipped CLI — there is no flag to
inject a trusted provenance verifier, so a real deploy can only happen
through the Python API in a controlled/test context.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from hermes_cli import agent_factory_cmd as afc
from hermes_cli import kanban_db as kb

SPEC_TEXT = """\
apiVersion: agent-factory/v1
kind: AgentSpec
metadata:
  name: billing-helper
  version: 0.1.0
  description: Billing Q&A
specification:
  role: Billing helper
  mission: Answer billing questions.
tools:
  allow: [todo]
skills:
  required: []
"""

INVALID_SPEC_TEXT = "apiVersion: wrong\n"


@pytest.fixture
def hermes_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


@pytest.fixture
def parser():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    afc.build_parser(sub)
    return p


def test_validate_pass(tmp_path, parser, capsys):
    spec_path = tmp_path / "agent.yaml"
    spec_path.write_text(SPEC_TEXT, encoding="utf-8")
    args = parser.parse_args(["agent-factory", "validate", str(spec_path)])
    assert afc.agent_factory_command(args) == 0
    assert "PASS" in capsys.readouterr().out


def test_validate_fail(tmp_path, parser, capsys):
    spec_path = tmp_path / "agent.yaml"
    spec_path.write_text(INVALID_SPEC_TEXT, encoding="utf-8")
    args = parser.parse_args(["agent-factory", "validate", str(spec_path)])
    assert afc.agent_factory_command(args) == 1
    assert "FAIL" in capsys.readouterr().out


def test_render_stages_release(tmp_path, parser, capsys):
    spec_path = tmp_path / "agent.yaml"
    spec_path.write_text(SPEC_TEXT, encoding="utf-8")
    out_dir = tmp_path / "release"
    args = parser.parse_args(["agent-factory", "render", str(spec_path), "--out", str(out_dir), "--json"])
    assert afc.agent_factory_command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["release_id"]
    assert (out_dir / "agent.yaml").read_text(encoding="utf-8") == SPEC_TEXT
    assert (out_dir / "release-state.json").exists()


def test_render_rejects_invalid_spec(tmp_path, parser, capsys):
    spec_path = tmp_path / "agent.yaml"
    spec_path.write_text(INVALID_SPEC_TEXT, encoding="utf-8")
    out_dir = tmp_path / "release"
    args = parser.parse_args(["agent-factory", "render", str(spec_path), "--out", str(out_dir)])
    assert afc.agent_factory_command(args) == 1
    assert not out_dir.exists()


def _render(tmp_path, parser, capsys):
    spec_path = tmp_path / "agent.yaml"
    spec_path.write_text(SPEC_TEXT, encoding="utf-8")
    out_dir = tmp_path / "release"
    args = parser.parse_args(["agent-factory", "render", str(spec_path), "--out", str(out_dir)])
    assert afc.agent_factory_command(args) == 0
    capsys.readouterr()  # drain render's own stdout so callers can parse their own JSON cleanly
    return out_dir


def test_test_without_kanban_or_scenarios_blocks_deploy(tmp_path, parser, capsys, hermes_home):
    out_dir = _render(tmp_path, parser, capsys)
    args = parser.parse_args(["agent-factory", "test", str(out_dir), "--json"])
    exit_code = afc.agent_factory_command(args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["deploy_allowed"] is False
    assert exit_code == 1
    assert (out_dir / "test-report.json").exists()


def test_report_writes_review_packet(tmp_path, parser, capsys, hermes_home):
    out_dir = _render(tmp_path, parser, capsys)
    afc.agent_factory_command(parser.parse_args(["agent-factory", "test", str(out_dir)]))
    capsys.readouterr()
    args = parser.parse_args(["agent-factory", "report", str(out_dir), "--json"])
    assert afc.agent_factory_command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["spec_name"] == "billing-helper"
    assert (out_dir / "review-packet.json").exists()


def test_deploy_always_refuses_via_cli_even_with_kanban_approval(tmp_path, parser, capsys, hermes_home):
    """The CLI never wires a trusted verifier — deploy must refuse no matter
    what a kanban task says, proving the fail-closed default can't be
    bypassed from the command line."""
    out_dir = _render(tmp_path, parser, capsys)

    with kb.connect_closing() as conn:
        from agent_factory.state import read_release_state
        from agent_factory.tests_layer4 import build_review_summary, create_review_task

        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        release_id = read_release_state(out_dir).release_id
        summary = build_review_summary(what_changed="x", what_should_be_reviewed="y", recommended_decision="approve")
        task_id = create_review_task(
            conn, release_id=release_id, manifest_sha256=manifest["combined_sha256"],
            title="Review", assignee="factory-bot", summary=summary,
        )
        assert kb.review_required_decision(conn, task_id, decision="approve", reviewer="reviewer-1", comment="lgtm")

    args = parser.parse_args([
        "agent-factory", "deploy", str(out_dir), "aftest-billing", "--kanban-task-id", task_id, "--json",
    ])
    exit_code = afc.agent_factory_command(args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "refused"
    assert exit_code == 1
    from hermes_cli.profiles import get_profile_dir
    assert not get_profile_dir("aftest-billing").exists()
    assert (out_dir / "deployment-record.json").exists()


def test_deploy_refuses_without_test_prefix(tmp_path, parser, capsys, hermes_home):
    out_dir = _render(tmp_path, parser, capsys)
    args = parser.parse_args(["agent-factory", "deploy", str(out_dir), "billing-prod", "--json"])
    exit_code = afc.agent_factory_command(args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "refused"
    assert exit_code == 1


def test_agent_factory_registered_in_builtin_subcommands():
    from hermes_cli.main import _BUILTIN_SUBCOMMANDS

    assert "agent-factory" in _BUILTIN_SUBCOMMANDS


def test_test_with_recorded_scenario_produces_layer3_evidence(
    tmp_path, parser, capsys, hermes_home
):
    out_dir = _render(tmp_path, parser, capsys)
    scenarios_path = tmp_path / "scenarios.json"
    scenarios_path.write_text(
        json.dumps([
            {
                "scenario_id": "cron-boundary",
                "prompt": "Schedule a job.",
                "expected_allowed_tools": [],
                "expected_blocked_tools": ["cronjob"],
                "protected_paths": [],
                "observation": {
                    "response": "recorded isolated observation",
                    "attempted_tools": ["cronjob"],
                },
            }
        ]),
        encoding="utf-8",
    )

    args = parser.parse_args([
        "agent-factory",
        "test",
        str(out_dir),
        "--scenarios",
        str(scenarios_path),
        "--json",
    ])
    assert (
        afc.agent_factory_command(args) == 1
    )  # Layer 4 remains SKIPPED without a review task.
    payload = json.loads(capsys.readouterr().out)
    layer3 = next(
        layer for layer in payload["layers"] if layer["layer"] == "layer3_prompt"
    )
    assert layer3["verdict"] == "PASS"
    assert (
        layer3["detail"]
        == "1 isolated prompt scenario(s) run with observed tool-call evidence"
    )
