"""Layer 3: limited isolated prompt-test harness.

The harness does not choose or judge a model.  A caller must inject a runner
that actually receives each prompt and returns the observed response and tool
calls.  Without that runner the mandatory layer is SKIPPED, which blocks
deployment.  Verdicts use tool-call policy and protected-state evidence only;
response wording is recorded but never treated as proof of refusal.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Protocol, runtime_checkable

from agent_factory.effective_tools import EffectiveToolsReport
from agent_factory.state import LayerEvidence, Verdict

LAYER_NAME = "layer3_prompt"


@dataclass(frozen=True)
class PromptScenario:
    scenario_id: str
    prompt: str
    expected_allowed_tools: tuple[str, ...]
    expected_blocked_tools: tuple[str, ...]
    protected_paths: tuple[Path, ...]


@dataclass(frozen=True)
class PromptObservation:
    response: str
    attempted_tools: tuple[str, ...]


@runtime_checkable
class PromptRunner(Protocol):
    def run(self, prompt: str) -> PromptObservation:
        """Run one prompt in an isolated environment and return observations."""
        ...


@dataclass(frozen=True)
class ToolCallOutcome:
    tool: str
    blocked: bool
    detail: str


@dataclass(frozen=True)
class PromptTestResult:
    scenario_id: str
    response: str
    tool_call_outcomes: tuple[ToolCallOutcome, ...]
    protected_state: dict
    verdict: Verdict
    detail: str


def _hash_path(path: Path) -> str:
    path = Path(path)
    if not path.exists():
        return "absent"
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    entries: list[str] = []
    for child in sorted(path.rglob("*")):
        if child.is_file():
            entries.append(
                f"{child.relative_to(path).as_posix()}:{hashlib.sha256(child.read_bytes()).hexdigest()}"
            )
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def run_prompt_scenario(
    scenario: PromptScenario,
    *,
    effective_tools: EffectiveToolsReport,
    runner: PromptRunner,
) -> PromptTestResult:
    allowed = set(effective_tools.allowed)
    before = {str(path): _hash_path(path) for path in scenario.protected_paths}
    observation = runner.run(scenario.prompt)
    after = {str(path): _hash_path(path) for path in scenario.protected_paths}

    outcomes = tuple(
        ToolCallOutcome(
            tool=tool,
            blocked=tool not in allowed,
            detail=(
                "not in effective allow-set; blocked before dispatch"
                if tool not in allowed
                else "present in effective allow-set; dispatch permitted"
            ),
        )
        for tool in observation.attempted_tools
    )
    protected_state = {
        path: {"before": before[path], "after": after[path], "unchanged": before[path] == after[path]}
        for path in before
    }

    failures: list[str] = []
    attempted = set(observation.attempted_tools)
    expected_allowed = set(scenario.expected_allowed_tools)
    expected_blocked = set(scenario.expected_blocked_tools)

    missing_allowed = sorted(expected_allowed - attempted)
    if missing_allowed:
        failures.append(f"expected allowed tool(s) not attempted: {missing_allowed}")
    missing_blocked = sorted(expected_blocked - attempted)
    if missing_blocked:
        failures.append(f"expected blocked tool(s) not attempted: {missing_blocked}")

    unexpected = sorted(attempted - expected_allowed - expected_blocked)
    if unexpected:
        failures.append(f"unexpected tool call(s): {unexpected}")

    by_tool = {outcome.tool: outcome for outcome in outcomes}
    wrongly_blocked = sorted(
        tool for tool in expected_allowed if tool in by_tool and by_tool[tool].blocked
    )
    if wrongly_blocked:
        failures.append(f"expected allowed tool(s) were blocked: {wrongly_blocked}")
    wrongly_allowed = sorted(
        tool for tool in expected_blocked if tool in by_tool and not by_tool[tool].blocked
    )
    if wrongly_allowed:
        failures.append(f"expected-blocked tool(s) were allowed by policy: {wrongly_allowed}")

    changed = sorted(path for path, state in protected_state.items() if not state["unchanged"])
    if changed:
        failures.append(f"protected state changed: {changed}")

    return PromptTestResult(
        scenario_id=scenario.scenario_id,
        response=observation.response,
        tool_call_outcomes=outcomes,
        protected_state=protected_state,
        verdict=Verdict.FAIL if failures else Verdict.PASS,
        detail="; ".join(failures) if failures else "observed tool calls and protected state verified",
    )


def run_layer3_prompt_tests(
    scenarios: Iterable[PromptScenario],
    *,
    effective_tools: EffectiveToolsReport,
    runner: Optional[PromptRunner] = None,
) -> LayerEvidence:
    scenarios = tuple(scenarios)
    if not scenarios:
        return LayerEvidence(
            layer=LAYER_NAME,
            verdict=Verdict.SKIPPED,
            checks=(),
            detail="no isolated prompt scenarios supplied",
        )
    if runner is None:
        return LayerEvidence(
            layer=LAYER_NAME,
            verdict=Verdict.SKIPPED,
            checks=(),
            detail="no isolated prompt runner supplied; scripted text is not runtime evidence",
        )
    if not any(scenario.expected_blocked_tools for scenario in scenarios):
        return LayerEvidence(
            layer=LAYER_NAME,
            verdict=Verdict.FAIL,
            checks=(),
            detail="prompt suite contains no negative boundary scenario",
        )

    results = tuple(
        run_prompt_scenario(scenario, effective_tools=effective_tools, runner=runner)
        for scenario in scenarios
    )
    checks = tuple(
        {
            "name": result.scenario_id,
            "verdict": result.verdict.value,
            "detail": result.detail,
            "response": result.response,
            "tool_calls": [
                {"tool": outcome.tool, "blocked": outcome.blocked, "detail": outcome.detail}
                for outcome in result.tool_call_outcomes
            ],
            "protected_state": result.protected_state,
        }
        for result in results
    )
    verdict = Verdict.FAIL if any(result.verdict == Verdict.FAIL for result in results) else Verdict.PASS
    return LayerEvidence(
        layer=LAYER_NAME,
        verdict=verdict,
        checks=checks,
        detail=f"{len(results)} isolated prompt scenario(s) run with observed tool-call evidence",
    )
