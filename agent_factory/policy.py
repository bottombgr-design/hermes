"""Factory tool-allow policy — the single source of truth for which
toolsets a factory-generated ``agent.yaml`` may request.

This is a **positive allow-list**, not a deny-list. The prior MVP denied
only ``cronjob``/``kanban`` and allowed everything else — "broad/high-
authority toolsets are still allowed" was a real gap. The list below is
closed: anything not in it is refused, whether or not it is individually
"dangerous".

The list is also constrained by a hard runtime fact (see
``docs/design/agent-factory.md``): Hermes' ``config.yaml`` can only
restrict tool exposure at **toolset** granularity via
``platform_toolsets`` — there is no config mechanism to select one tool
out of a multi-tool toolset. So ``tools.allow`` accepts only toolset
names (never individual tool names), and only toolsets that are:

* atomic — no ``includes`` (a composite/aggregate toolset like ``"all"``,
  ``"coding"``, or a platform bundle can silently pull in tools nobody
  reviewed);
* free of terminal, file-write/patch, code-execution, delegation, cron,
  Kanban-orchestration, memory-write, skill-modification, profile/config/
  deploy/approval, cross-profile, or undeclared-MCP authority.

This module has **zero dependency on ``tools.registry`` / ``model_tools``**
— importing it never triggers tool-module discovery. That is what lets
Layer 1 (which must never touch the live tool registry) use it for a
purely static membership check, while ``agent_factory.effective_tools``
separately does the live resolution.
"""

from __future__ import annotations

# web: web_search / web_extract — read-only network fetch.
# vision: vision_analyze — read-only image analysis.
# todo: todo — session-scoped task list; no cross-session or filesystem state.
# session_search: session_search — read-only search over past conversations.
# clarify: clarify — asks the user a question; no side effects at all.
ALLOWED_TOOLSETS = frozenset({"web", "vision", "todo", "session_search", "clarify"})

# Toolsets in ALLOWED_TOOLSETS whose tools depend on external credentials or
# runtime dependencies (API keys, vision-capable model, ...) that may not be
# configured in a given environment. A tool from one of these not showing
# up in a live schema-exposure check is not proof of a leak or a break —
# but it is also not proof of correct enforcement. Layer 2 treats it as
# UNKNOWN (must be verified live with real credentials before deploying),
# never as a silent PASS.
ENVIRONMENT_DEPENDENT_TOOLSETS = frozenset({"web", "vision"})
