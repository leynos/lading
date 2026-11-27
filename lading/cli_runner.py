"""Select the command runner one CLI invocation uses.

The entry point is the composition root, so this is where the ambient
environment decides between the cmd-mox test runner, the plain subprocess
runner and, for ``lading publish --expect-digests``, the redacting runner.
:mod:`lading.cli` re-exports :func:`_select_runner`, and tests replace it there.
"""

import collections.abc as cabc
import importlib
import os
import typing as typ
from pathlib import Path

from .runtime import CommandRunner, subprocess_runner
from .runtime.redacting_runner import redacting_runner

_CMD_MOX_STUB_ENV = "LADING_USE_CMD_MOX_STUB"
_CMD_MOX_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


def _select_runner(
    secrets: cabc.Sequence[str] | None = None, *, cargo_home: Path | None = None
) -> CommandRunner:
    """Return the command runner selected for this CLI invocation.

    Parameters
    ----------
    secrets : cabc.Sequence[str] | None
        ``None`` selects the plain runner, so a run without
        ``--expect-digests`` is unchanged even with a token in the environment.
        A sequence, even an empty one, selects the redacting runner, which is
        what ``--expect-digests`` needs: tokens can also arrive later, through a
        call's environment overrides.
    cargo_home : Path | None
        The Cargo home the redacting runner resolves each command's own home
        from, so the credentials file a command actually uses is hidden too.

    Returns
    -------
    CommandRunner
        The cmd-mox runner under test, else a (possibly redacting) subprocess
        runner.

    Raises
    ------
    SystemExit
        If the cmd-mox runner is requested but cannot be imported.
    """
    stub_value = os.environ.get(_CMD_MOX_STUB_ENV, "")
    if stub_value.lower() in _CMD_MOX_TRUTHY_VALUES:
        try:
            module = importlib.import_module("lading.testing.cmd_mox_runner")
        except ModuleNotFoundError as exc:
            message = (
                f"{_CMD_MOX_STUB_ENV} is set, but the cmd-mox test runner "
                "could not be imported. Install the test dependencies or unset "
                f"{_CMD_MOX_STUB_ENV}."
            )
            raise SystemExit(message) from exc
        return typ.cast("CommandRunner", module.cmd_mox_runner)
    # Read here, at the composition root, so no other module touches the
    # ambient environment. Without a token the plain runner is used unchanged.
    # Without digests the plain runner is used whatever the environment holds,
    # exactly as before the option existed.
    if secrets is None:
        return subprocess_runner
    return redacting_runner(secrets, cargo_home=cargo_home)
