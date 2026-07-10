"""Default-deny effective tool calculation for factory-generated agent specs.

The declared ``tools.allow`` list in an ``agent.yaml`` spec names toolsets
from the narrow, positive ``agent_factory.policy.ALLOWED_TOOLSETS`` list —
never individual tool names, never composite/high-authority toolsets.
Everything not resolved from that list is denied by default. This is a
positive allow-list, not a deny-list: an entry is only ever granted
anything if it is *itself* in ``ALLOWED_TOOLSETS``, regardless of whether
it also happens to be a real, individually "safe-looking" tool or toolset
name — see ``agent_factory.policy`` for the full rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import model_tools  # noqa: F401  side effect: populates tools.registry.registry
from tools.registry import registry
from toolsets import TOOLSETS, resolve_toolset

from agent_factory.policy import ALLOWED_TOOLSETS


@dataclass(frozen=True)
class EffectiveToolsReport:
    requested: tuple[str, ...]
    allowed_toolsets: tuple[str, ...] = ()
    allowed: tuple[str, ...] = ()
    denied_forbidden: tuple[tuple[str, str], ...] = ()
    denied_unknown: tuple[str, ...] = ()
    default_deny: bool = True


def compute_effective_tools(tools_allow: Iterable[str]) -> EffectiveToolsReport:
    requested = tuple(tools_allow)
    all_tool_names = set(registry.get_all_tool_names())

    allowed_toolsets: set[str] = set()
    allowed: set[str] = set()
    denied_forbidden: list[tuple[str, str]] = []
    denied_unknown: list[str] = []

    for entry in requested:
        if entry in ALLOWED_TOOLSETS:
            allowed_toolsets.add(entry)
            for name in resolve_toolset(entry):
                if name not in all_tool_names:
                    denied_unknown.append(name)
                else:
                    allowed.add(name)
        elif entry in TOOLSETS:
            denied_forbidden.append((
                entry,
                f"toolset '{entry}' is not in the factory's narrow allowed-toolset list "
                f"{sorted(ALLOWED_TOOLSETS)} — composite/high-authority toolsets are never permitted",
            ))
        elif entry in all_tool_names:
            denied_forbidden.append((
                entry,
                f"'{entry}' is an individual tool name, not a toolset — individual tool names are "
                "never accepted in tools.allow (config.yaml can only restrict tool exposure at "
                "toolset granularity, so this could never be enforced as declared)",
            ))
        else:
            denied_unknown.append(entry)

    return EffectiveToolsReport(
        requested=requested,
        allowed_toolsets=tuple(sorted(allowed_toolsets)),
        allowed=tuple(sorted(allowed)),
        denied_forbidden=tuple(denied_forbidden),
        denied_unknown=tuple(sorted(set(denied_unknown))),
    )
