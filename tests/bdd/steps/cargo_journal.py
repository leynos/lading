"""Shared command-journal projections for BDD step modules."""

import typing as typ

if typ.TYPE_CHECKING:
    from cmd_mox import CmdMox


def _cargo_update_args(cmd_mox: CmdMox) -> list[tuple[str, ...]]:
    """Collect Cargo update arguments from the command journal."""
    return [
        tuple(invocation.args)
        for invocation in cmd_mox.journal
        if invocation.command == "cargo::update"
    ]
