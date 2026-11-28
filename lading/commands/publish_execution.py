"""Execute publish commands through the project's subprocess boundary.

Two concerns live here, both used by :mod:`lading.commands.publish_pipeline`:

* :func:`_invoke` runs a command through the production subprocess runner
  and maps spawn and argument failures onto
  :class:`~lading.commands.publish_errors.PublishPreflightError`, so the
  publish workflow reports them through its own error boundary.
* :func:`_run_timed_cargo` runs one per-crate cargo invocation (a
  :class:`_CargoInvocation`), times it with an injectable clock, records the
  duration under :data:`CARGO_DURATION_METRIC` whatever the outcome, and
  returns a :class:`_TimedCargoResult` carrying the exit code, captured
  streams, and elapsed seconds. The pipeline uses the elapsed time for its
  per-crate progress lines.
"""

import collections.abc as cabc
import dataclasses as dc
import time
import typing as typ
from pathlib import Path

from lading.commands.publish_errors import PublishPreflightError
from lading.runtime import CommandSpawnError
from lading.runtime.redacting_runner import redacting_runner
from lading.runtime.subprocess_runner import (
    subprocess_runner as _default_subprocess_runner,
)
from lading.utils import metrics

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner
    from lading.utils.secret_scope import SecretScope

# Duration of each per-crate cargo invocation, labelled by ``subcommand``
# (``package`` or ``publish``) and ``crate``. One observation per invocation,
# so the exit summary carries one bounded record per crate and phase and a
# slow verify build can be attributed to its crate (issue #251).
CARGO_DURATION_METRIC = "publish.cargo.duration"


def _publish_error(message: str) -> PublishPreflightError:
    """Return a PublishPreflightError carrying ``message``."""
    return PublishPreflightError(message)


def _map_errors(runner: CommandRunner) -> CommandRunner:
    """Wrap ``runner`` so argument and spawn failures become publish errors."""

    def invoke(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        """Execute ``command`` and return the exit status and decoded streams."""
        try:
            return runner(command, cwd=cwd, env=env, echo_stdout=echo_stdout)
        except ValueError as exc:
            raise _publish_error(str(exc)) from exc
        except CommandSpawnError as exc:
            raise _publish_error(str(exc)) from exc

    return invoke


def _invoke(
    command: cabc.Sequence[str],
    *,
    cwd: Path | None = None,
    env: cabc.Mapping[str, str] | None = None,
    echo_stdout: bool = True,
) -> tuple[int, str, str]:
    """Execute ``command`` and return the exit status and decoded streams."""
    return _map_errors(_default_subprocess_runner)(
        command, cwd=cwd, env=env, echo_stdout=echo_stdout
    )


def redacting_invoke(
    scope: SecretScope, *, cargo_home: Path | None = None
) -> CommandRunner:
    """Return the default publish runner with ``scope``'s secrets hidden.

    Returns
    -------
    CommandRunner
        Behaves like :func:`_invoke`, with the same error mapping. The
        scope's secrets (and registry tokens a call brings) are hidden from
        what it relays to the console and logs. The captured stdout and
        stderr it returns are raw, exactly as the child wrote them, because
        lading parses them; a message built from them must go through the
        active redaction helpers (:meth:`SecretScope.redact` or
        :func:`lading.utils.secret_scope.redact_active`) before it is shown.
    """
    return _map_errors(redacting_runner(scope, cargo_home=cargo_home))


@dc.dataclass(frozen=True, slots=True)
class _TimedCargoResult:
    """Outcome of one per-crate cargo invocation, with its elapsed time."""

    exit_code: int
    stdout: str
    stderr: str
    elapsed_seconds: float

    @property
    def streams(self) -> tuple[str, str]:
        """The ``(stdout, stderr)`` pair for failure-message formatting."""
        return self.stdout, self.stderr


@dc.dataclass(frozen=True, slots=True)
class _CargoInvocation:
    """One per-crate cargo command, where it runs, and the crate it serves."""

    command: tuple[str, ...]
    cwd: Path
    crate_name: str

    @property
    def subcommand(self) -> str:
        """The cargo subcommand (``package`` or ``publish``) being run."""
        return self.command[1]


def _run_timed_cargo(
    invocation: _CargoInvocation,
    *,
    runner: CommandRunner,
    clock: cabc.Callable[[], float] = time.perf_counter,
) -> _TimedCargoResult:
    """Run ``invocation`` and time it.

    The elapsed time is recorded under :data:`CARGO_DURATION_METRIC` whatever
    the outcome, including a runner that raises, so a failed verify build is
    attributable as well as a slow successful one.

    Returns
    -------
    _TimedCargoResult
        Exit code, captured streams, and elapsed seconds of the invocation.
    """
    started_at = clock()
    try:
        exit_code, stdout, stderr = runner(
            invocation.command, cwd=invocation.cwd, env=None
        )
    finally:
        elapsed = clock() - started_at
        metrics.observe_duration(
            CARGO_DURATION_METRIC,
            elapsed,
            subcommand=invocation.subcommand,
            crate=invocation.crate_name,
        )
    return _TimedCargoResult(exit_code, stdout, stderr, elapsed)


__all__ = [
    "CARGO_DURATION_METRIC",
    "_CargoInvocation",
    "_TimedCargoResult",
    "_invoke",
    "_run_timed_cargo",
    "redacting_invoke",
]
