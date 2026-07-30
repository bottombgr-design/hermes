"""Parity guard between COMMAND_REGISTRY and the classic CLI dispatcher.

``COMMAND_REGISTRY`` (hermes_cli/commands.py) is the single source of truth for
``/help``, tab completion, and the Telegram/Discord command menus. The classic
CLI, however, dispatches slash commands from a hand-written if/elif chain inside
``HermesCLI.process_command``. Nothing keeps the two in sync, so a command can be
registered -- and therefore advertised in ``/help`` and offered by autocomplete --
while the CLI has no branch for it. The user types it and nothing happens.

This test walks the registry and asserts every CLI-reachable command is at least
mentioned inside ``process_command``.

Deliberately permissive: it collects string constants from the function body via
AST rather than trying to recognise branch shapes. A command referenced only in,
say, a help string would slip through. That is the intended trade-off -- the
failure mode this guards against is a command being *entirely absent*, and a
zero-false-positive check is one nobody has to fight.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from cli import HermesCLI
from hermes_cli.commands import COMMAND_REGISTRY

# Commands that are registered and CLI-reachable but have no dispatch branch
# today. Each is a live bug with a fix already in flight; drop the entry when
# its PR lands and this test will hold the line from then on.
KNOWN_MISSING_DISPATCH = {
    # https://github.com/NousResearch/hermes-agent/pull/26047
    "whoami",
    # https://github.com/NousResearch/hermes-agent/pull/41869
    "indicator",
}


def _string_constants(func) -> set[str]:
    """Every string literal appearing anywhere inside ``func``'s body."""

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _dispatchable_in_cli(cmd) -> bool:
    """True when the classic CLI is expected to handle ``cmd`` itself."""

    return not cmd.gateway_only


class TestCommandRegistryDispatchParity:
    def test_every_cli_command_is_mentioned_in_process_command(self):
        literals = _string_constants(HermesCLI.process_command)

        undispatched = []
        for cmd in COMMAND_REGISTRY:
            if not _dispatchable_in_cli(cmd):
                continue
            if cmd.name in KNOWN_MISSING_DISPATCH:
                continue
            names = (cmd.name, *cmd.aliases)
            if any(
                name in literals or f"/{name}" in literals for name in names
            ):
                continue
            undispatched.append(cmd.name)

        assert not undispatched, (
            "These commands are in COMMAND_REGISTRY and reachable from the "
            "classic CLI, but process_command has no branch for them -- they "
            "will appear in /help and tab completion and then do nothing: "
            f"{sorted(undispatched)}. Add a dispatch branch, or mark the "
            "command gateway_only=True if the CLI should not handle it."
        )

    def test_known_missing_dispatch_entries_are_still_missing(self):
        """Keep the allowlist honest: prune entries once they are fixed."""

        literals = _string_constants(HermesCLI.process_command)

        fixed = [
            name
            for name in sorted(KNOWN_MISSING_DISPATCH)
            if name in literals or f"/{name}" in literals
        ]

        assert not fixed, (
            f"{fixed} now has a dispatch branch in process_command. Remove it "
            "from KNOWN_MISSING_DISPATCH so the parity check covers it."
        )

    def test_allowlisted_commands_still_exist_in_the_registry(self):
        """Guard against the allowlist outliving the commands it names."""

        registered = {cmd.name for cmd in COMMAND_REGISTRY}
        stale = sorted(KNOWN_MISSING_DISPATCH - registered)

        assert not stale, (
            f"KNOWN_MISSING_DISPATCH names commands that are no longer in "
            f"COMMAND_REGISTRY: {stale}. Remove them."
        )
