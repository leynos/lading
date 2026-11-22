"""Shared test helpers for workspace metadata tests."""

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
        """Send a metadata command to cmd-mox and return (exit, stdout, stderr)."""
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
        """Exit the runner context, then run the original monkeypatch undo."""
        runner_context.__exit__(None, None, None)
        undo()

    # `setattr` rather than direct assignment: pytest's `undo` is typed as a
    # bound method, which no callable we can supply satisfies, and `setattr` is
    # the API that also restores the previous value at teardown. The first
    # entry it pushes onto `_setattr` is `undo` itself, so the wrapper runs
    # once and the original is restored before the remaining attributes are
    # unwound.
    monkeypatch.setattr(monkeypatch, "undo", undo_with_runner_context)
    monkeypatch.setenv("LADING_USE_CMD_MOX_STUB", "1")


#: ``cargo metadata`` spellings a fixture may need to serve. Bump discovery
#: passes ``--no-deps`` so the metadata phase stays read-only; publish still
#: asks for the full dependency graph.
METADATA_SPELLINGS: typ.Final[tuple[tuple[str, ...], ...]] = (
    ("metadata", "--format-version", "1"),
    ("metadata", "--format-version", "1", "--no-deps"),
)


def stub_cargo_metadata(
    cmd_mox: CmdMox,
    payload: str,
) -> None:
    """Stub ``cargo metadata`` for both the bump and publish spellings.

    cmd-mox replaces a double when a second stub is registered for the same
    program, so two ``with_args`` registrations would leave only the last one.
    A handler accepts whichever spelling the command under test issues.

    Parameters
    ----------
    cmd_mox : CmdMox
        Command-double fixture.
    payload : str
        JSON text every ``cargo metadata`` invocation should return.
    """
    from cmd_mox import UnexpectedCommandError

    def handler(invocation: Invocation) -> tuple[str, str, int]:
        """Return ``payload`` for a recognised metadata spelling, else raise."""
        if tuple(invocation.args) in METADATA_SPELLINGS:
            return payload, "", 0
        message = f"unexpected cargo invocation: {invocation.args!r}"
        raise UnexpectedCommandError(message)

    cmd_mox.stub("cargo").runs(handler)
