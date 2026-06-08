"""Shared test helpers for workspace metadata tests."""

from __future__ import annotations

import collections.abc as cabc
import os
import typing as typ

from cmd_mox.ipc import Invocation

if typ.TYPE_CHECKING:
    import pytest
    from cmd_mox import CmdMox


def install_cargo_stub(cmd_mox: CmdMox, monkeypatch: pytest.MonkeyPatch) -> None:
    """Activate cmd-mox shims for both in-process and subprocess tests."""
    from lading.workspace import metadata as metadata_module

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: object | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del cwd, echo_stdout
        invocation = Invocation(
            command=command[0],
            args=list(command[1:]),
            stdin="",
            env=dict(os.environ) | dict(env or {}),
        )
        response = cmd_mox._handle_invocation(invocation)
        return response.exit_code, response.stdout, response.stderr

    runner_context = metadata_module.use_command_runner(runner)
    runner_context.__enter__()
    undo = monkeypatch.undo

    def undo_with_runner_context() -> None:
        runner_context.__exit__(None, None, None)
        undo()

    monkeypatch.undo = undo_with_runner_context
    monkeypatch.setenv("LADING_USE_CMD_MOX_STUB", "1")
