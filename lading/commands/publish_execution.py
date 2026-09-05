"""Command execution helpers for publish operations.

Two concerns live here, both used by :mod:`lading.commands.publish`:

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

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import time
import typing as typ
from pathlib import Path

from lading.commands.publish_errors import PublishPreflightError
from lading.runtime import CommandSpawnError
from lading.runtime.subprocess_runner import (
    subprocess_runner as _default_subprocess_runner,
)
from lading.utils import metrics

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner

# Duration of each per-crate cargo invocation, labelled by ``subcommand``
# (``package`` or ``publish``) and ``crate``. One observation per invocation,
# so the exit summary carries one bounded record per crate and phase and a
# slow verify build can be attributed to its crate (issue #251).
CARGO_DURATION_METRIC = "publish.cargo.duration"


def _publish_error(message: str) -> PublishPreflightError:
    """Return a PublishPreflightError carrying ``message``."""
    return PublishPreflightError(message)


def _invoke(
    command: cabc.Sequence[str],
    *,
    cwd: Path | None = None,
    env: cabc.Mapping[str, str] | None = None,
    echo_stdout: bool = True,
) -> tuple[int, str, str]:
    """Execute ``command`` and return the exit status and decoded streams."""
    try:
        return _default_subprocess_runner(
            command, cwd=cwd, env=env, echo_stdout=echo_stdout
        )
    except ValueError as exc:
        raise _publish_error(str(exc)) from exc
    except CommandSpawnError as exc:
        raise _publish_error(str(exc)) from exc


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
]
