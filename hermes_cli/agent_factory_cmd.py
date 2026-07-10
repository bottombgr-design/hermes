"""CLI for the Hermes Agent Factory — ``hermes agent-factory …`` subcommand.

CLI edge feature, not a core model tool: nothing here is registered as an
agent-callable tool, and generation/render/test/report never deploys
anything on their own.

``deploy`` always uses :class:`agent_factory.provenance.DefaultFailClosedVerifier`
— there is no flag to inject a trusted provenance verifier from the command
line, so a real deploy can only happen through the Python API in a
controlled/test context. See ``docs/design/agent-factory.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from agent_factory import orchestrator
from agent_factory.provenance import DefaultFailClosedVerifier
from agent_factory.state import Verdict, read_release_state
from agent_factory.tests_layer3 import PromptScenario


def _generated_at() -> int:
    return int(time.time())


def _load_scenarios(path: Optional[str]) -> tuple[PromptScenario, ...]:
    if not path:
        return ()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    scenarios = []
    for item in data:
        scenarios.append(PromptScenario(
            scenario_id=item["scenario_id"],
            prompt=item.get("prompt", ""),
            expected_allowed_tools=tuple(item.get("expected_allowed_tools", [])),
            expected_blocked_tools=tuple(item.get("expected_blocked_tools", [])),
            protected_paths=tuple(Path(p) for p in item.get("protected_paths", [])),
        ))
    return tuple(scenarios)


def _resolve_release_id(release_dir: Path, explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    state_path = Path(release_dir) / "release-state.json"
    if state_path.exists():
        return read_release_state(release_dir).release_id
    raise ValueError("no --release-id given and no release-state.json found in the release directory")


def _print_evidence(label: str, evidence, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps({
            "layer": evidence.layer,
            "verdict": evidence.verdict.value,
            "checks": list(evidence.checks),
            "detail": evidence.detail,
        }, indent=2))
        return
    print(f"{label}: {evidence.verdict.value} — {evidence.detail}")
    for check in evidence.checks:
        print(f"  - {check['name']}: {check['verdict']} — {check['detail']}")


def build_parser(parent_subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = parent_subparsers.add_parser(
        "agent-factory",
        help="Author, validate, render, test, and (fail-closed) deploy factory-managed agent specs",
    )
    sub = parser.add_subparsers(dest="agent_factory_action")

    p_validate = sub.add_parser("validate", help="Layer 1 static validation of an agent.yaml spec")
    p_validate.add_argument("spec_path")
    p_validate.add_argument("--json", action="store_true")

    p_render = sub.add_parser("render", help="Validate + stage a spec into a release directory")
    p_render.add_argument("spec_path")
    p_render.add_argument("--out", required=True)
    p_render.add_argument("--release-id", default=None)
    p_render.add_argument("--json", action="store_true")

    p_test = sub.add_parser("test", help="Run Layer 1-4 tests against a staged release")
    p_test.add_argument("release_dir")
    p_test.add_argument("--release-id", default=None)
    p_test.add_argument("--kanban-task-id", default=None, help="Existing kanban review_required task id for Layer 4")
    p_test.add_argument("--scenarios", default=None, help="Path to a JSON file of Layer 3 prompt scenarios")
    p_test.add_argument("--json", action="store_true")

    p_report = sub.add_parser("report", help="Build the review packet for a staged, tested release")
    p_report.add_argument("release_dir")
    p_report.add_argument("--release-id", default=None)
    p_report.add_argument("--previous-version", default=None)
    p_report.add_argument("--json", action="store_true")

    p_deploy = sub.add_parser(
        "deploy",
        help="Guarded deploy to a NEW factory-managed TEST profile (always fail-closed via this CLI)",
    )
    p_deploy.add_argument("release_dir")
    p_deploy.add_argument("target_profile")
    p_deploy.add_argument("--release-id", default=None)
    p_deploy.add_argument("--kanban-task-id", default=None)
    p_deploy.add_argument("--json", action="store_true")

    parser.set_defaults(func=agent_factory_command)
    return parser


def _cmd_validate(args: argparse.Namespace) -> int:
    text = Path(args.spec_path).read_text(encoding="utf-8")
    evidence = orchestrator.validate(text)
    _print_evidence("validate", evidence, as_json=args.json)
    return 0 if evidence.verdict == Verdict.PASS else 1


def _cmd_render(args: argparse.Namespace) -> int:
    from agent_factory.schema import load_spec_from_yaml

    spec_path = Path(args.spec_path)
    out_dir = Path(args.out)
    spec = load_spec_from_yaml(spec_path.read_text(encoding="utf-8"))
    release_id = args.release_id or f"{spec.name}-{spec.version}"

    result = orchestrator.render(spec_path, out_dir, release_id=release_id, generated_at=_generated_at())
    if args.json:
        print(json.dumps({
            "release_id": release_id,
            "dest_dir": str(result.dest_dir),
            "manifest_combined_sha256": result.manifest["combined_sha256"],
        }, indent=2))
    else:
        print(f"Staged release {release_id!r} -> {result.dest_dir}")
        print(f"manifest combined sha256: {result.manifest['combined_sha256']}")
    return 0


def _cmd_test(args: argparse.Namespace) -> int:
    release_dir = Path(args.release_dir)
    release_id = _resolve_release_id(release_dir, args.release_id)
    scenarios = _load_scenarios(args.scenarios)

    if args.kanban_task_id:
        from hermes_cli import kanban_db as kb
        with kb.connect_closing() as conn:
            report = orchestrator.run_tests(
                release_dir, release_id=release_id, generated_at=_generated_at(),
                prompt_scenarios=scenarios, kanban_conn=conn, kanban_task_id=args.kanban_task_id,
                verifier=DefaultFailClosedVerifier(),
            )
    else:
        report = orchestrator.run_tests(
            release_dir, release_id=release_id, generated_at=_generated_at(), prompt_scenarios=scenarios,
        )

    if args.json:
        print(json.dumps({
            "overall_verdict": report.overall_verdict.value,
            "deploy_allowed": report.deploy_allowed,
            "layers": [{"layer": l.layer, "verdict": l.verdict.value, "detail": l.detail} for l in report.layers],
        }, indent=2))
    else:
        print("Layer verdicts: " + ", ".join(f"{l.layer}={l.verdict.value}" for l in report.layers))
        print(f"Overall: {report.overall_verdict.value}  deploy_allowed={report.deploy_allowed}")
    return 0 if report.overall_verdict == Verdict.PASS else 1


def _cmd_report(args: argparse.Namespace) -> int:
    release_dir = Path(args.release_dir)
    release_id = _resolve_release_id(release_dir, args.release_id)
    packet = orchestrator.build_report(
        release_dir, release_id=release_id, generated_at=_generated_at(),
        previous_version=args.previous_version,
    )
    if args.json:
        from agent_factory.review_packet import review_packet_to_dict
        print(json.dumps(review_packet_to_dict(packet), indent=2))
    else:
        print(f"{packet.spec_name} v{packet.spec_version} ({packet.version_comparison})")
        print(f"tools allowed: {', '.join(packet.effective_tools_allowed) or 'none'}")
        print(f"skills attached: {', '.join(packet.skills_attached) or 'none'}")
        print(f"test-report: {packet.test_report_overall_verdict}  deploy_allowed={packet.test_report_deploy_allowed}")
    return 0


def _cmd_deploy(args: argparse.Namespace) -> int:
    release_dir = Path(args.release_dir)
    release_id = _resolve_release_id(release_dir, args.release_id)

    from hermes_cli import kanban_db as kb
    with kb.connect_closing() as conn:
        result = orchestrator.deploy(
            release_dir, args.target_profile, release_id=release_id, generated_at=_generated_at(),
            kanban_conn=conn, kanban_task_id=args.kanban_task_id, verifier=DefaultFailClosedVerifier(),
        )

    if args.json:
        print(json.dumps({
            "outcome": result.outcome,
            "target_profile": result.target_profile,
            "failure_reason": result.failure_reason,
        }, indent=2))
    elif result.outcome == "deployed":
        print(f"Deployed factory-managed test profile {result.target_profile!r} -> {result.profile_dir}")
    else:
        print(f"Deploy refused: {result.failure_reason}", file=sys.stderr)
    return 0 if result.outcome == "deployed" else 1


def agent_factory_command(args: argparse.Namespace) -> int:
    action = getattr(args, "agent_factory_action", None)
    if not action:
        print("usage: hermes agent-factory <validate|render|test|report|deploy>", file=sys.stderr)
        return 2
    try:
        if action == "validate":
            return _cmd_validate(args)
        if action == "render":
            return _cmd_render(args)
        if action == "test":
            return _cmd_test(args)
        if action == "report":
            return _cmd_report(args)
        if action == "deploy":
            return _cmd_deploy(args)
        print(f"unknown agent-factory action: {action}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as exc:
        print(f"agent-factory: {exc}", file=sys.stderr)
        return 1
