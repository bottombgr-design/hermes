"""Default-deny effective tool calculation for factory-generated agent specs.

``tools.allow`` accepts only toolset names from the narrow, positive
``agent_factory.policy.ALLOWED_TOOLSETS`` list — never individual tool
names, never composite/high-authority toolsets (see HIGH 3 / CRITICAL 1:
Hermes config.yaml can only restrict tool exposure at toolset granularity,
so anything else would be unenforceable or misleading).
"""

from __future__ import annotations

from agent_factory.effective_tools import EffectiveToolsReport, compute_effective_tools
from agent_factory.policy import ALLOWED_TOOLSETS


def test_allowed_toolset_expands_to_its_tools():
    report = compute_effective_tools(["web"])
    assert isinstance(report, EffectiveToolsReport)
    assert set(report.allowed) == {"web_search", "web_extract"}
    assert report.allowed_toolsets == ("web",)
    assert report.denied_forbidden == ()
    assert report.denied_unknown == ()
    assert report.default_deny is True


def test_single_tool_toolset_expands_correctly():
    report = compute_effective_tools(["todo"])
    assert set(report.allowed) == {"todo"}
    assert report.allowed_toolsets == ("todo",)


def test_multiple_allowed_toolsets_combine():
    report = compute_effective_tools(["todo", "clarify", "session_search"])
    assert set(report.allowed) == {"todo", "clarify", "session_search"}
    assert set(report.allowed_toolsets) == {"todo", "clarify", "session_search"}


def test_individual_tool_name_is_denied_not_accepted():
    report = compute_effective_tools(["todo_tool_or_whatever_read_file"])
    assert report.allowed == ()
    assert report.allowed_toolsets == ()


def test_individual_tool_name_belonging_to_a_real_toolset_is_denied():
    """'read_file' names a real tool but is not itself an allowed toolset —
    it must be denied (as forbidden, with a clear reason), never silently
    treated as if it granted access."""
    report = compute_effective_tools(["read_file"])
    assert report.allowed == ()
    assert any(entry == "read_file" for entry, _reason in report.denied_forbidden)


def test_unknown_entry_is_denied_as_unknown():
    report = compute_effective_tools(["totally_fake_toolset_xyz"])
    assert report.allowed == ()
    assert "totally_fake_toolset_xyz" in report.denied_unknown


def test_high_authority_toolsets_are_denied_even_though_they_are_real_toolsets():
    for toolset in ("cronjob", "kanban", "terminal", "file", "code_execution", "delegation", "memory", "skills"):
        report = compute_effective_tools([toolset])
        assert report.allowed == (), f"expected {toolset!r} to grant nothing"
        assert any(entry == toolset for entry, _reason in report.denied_forbidden), f"expected {toolset!r} denied_forbidden"


def test_composite_toolset_is_denied():
    report = compute_effective_tools(["coding"])
    assert report.allowed == ()
    assert any(entry == "coding" for entry, _reason in report.denied_forbidden)


def test_wildcard_toolset_is_denied():
    report = compute_effective_tools(["all"])
    assert report.allowed == ()


def test_mixed_allowed_forbidden_and_unknown_entries():
    report = compute_effective_tools(["todo", "cronjob", "nonexistent_toolset"])
    assert set(report.allowed) == {"todo"}
    assert any(entry == "cronjob" for entry, _reason in report.denied_forbidden)
    assert "nonexistent_toolset" in report.denied_unknown


def test_everything_not_requested_is_denied_by_default():
    report = compute_effective_tools(["todo"])
    assert "clarify" not in report.allowed
    assert report.default_deny is True


def test_report_is_deterministic_and_sorted():
    report = compute_effective_tools(["web", "todo"])
    assert list(report.allowed) == sorted(report.allowed)
    assert list(report.allowed_toolsets) == sorted(report.allowed_toolsets)


def test_every_policy_allowed_toolset_actually_resolves_to_at_least_one_tool_or_is_intentionally_empty():
    for toolset in sorted(ALLOWED_TOOLSETS):
        report = compute_effective_tools([toolset])
        assert report.allowed_toolsets == (toolset,)
        assert toolset not in dict(report.denied_forbidden)
        assert toolset not in report.denied_unknown
