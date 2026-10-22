"""Run a child interpreter under a one-program cuprum catalogue.

The cuprum policy tests drive real subprocesses through cuprum's public API,
so they share one catalogue, one interpreter, and one ``run_sync`` wrapper.
They live here for the reason :mod:`tests.helpers.cuprum_selection` gives: two
test modules use them, so neither should become the other's library.

The catalogue admits exactly the interpreter running the suite. A resolved
absolute path keeps every run free of ``PATH`` lookup, so a host with a
different ``python`` cannot change which executable starts, and a mistaken
lookup fails loudly instead of silently running something else.
"""

import collections.abc as cabc
import sys
import typing as typ

from cuprum import (
    ExecutionContext,
    Program,
    ProgramCatalogue,
    ProjectSettings,
    RunOutputOptions,
    scoped,
    sh,
)
from cuprum.context import EnvMode

if typ.TYPE_CHECKING:
    from cuprum import CommandResult

#: How long a child may run before the test fails. It bounds a child left
#: blocked on a handshake whose release never came, so a broken implementation
#: reports a failure rather than hanging the suite.
DEFAULT_TIMEOUT_SECONDS = 10.0

#: The interpreter running the test-suite, so the child needs no resolution
#: through ``PATH`` and the catalogue admits exactly one executable.
PYTHON = Program(sys.executable)

#: The catalogue every policy test runs under.
CATALOGUE = ProgramCatalogue(
    projects=(
        ProjectSettings(
            name="lading-cuprum-policies",
            programs=(PYTHON,),
            documentation_locations=("docs/developers-guide.md",),
            noise_rules=(),
        ),
    )
)


def run_child(
    source: str,
    *,
    output: RunOutputOptions | None = None,
    env: cabc.Mapping[str, str] | None = None,
    env_mode: EnvMode = EnvMode.OVERLAY,
) -> CommandResult:
    """Run ``source`` in a child interpreter under the test catalogue.

    Parameters
    ----------
    source : str
        The program to hand the child interpreter as ``-c``.
    output : RunOutputOptions | None
        The capture and echo options for the run. Defaults to plain capture.
    env : collections.abc.Mapping[str, str] | None
        The environment the run supplies, interpreted according to ``env_mode``.
    env_mode : EnvMode
        Whether ``env`` replaces the parent environment or overlays it.

    Returns
    -------
    CommandResult
        The child's result, carrying its captured streams and relay events.
    """
    with scoped(catalogue=CATALOGUE):
        command = sh.make(PYTHON, catalogue=CATALOGUE)("-c", source)
        return command.run_sync(
            output=output or RunOutputOptions(capture=True),
            context=ExecutionContext(env=env, env_mode=env_mode),
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
