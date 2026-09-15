"""Discover, upload, and report on the wheels for a GitHub release.

This is the logic behind ``scripts/upload_release_wheels.py``; that script is
the command-line edge. The release workflow previously attached wheels with
``find dist/wheels-* -name '*.whl' -print0 | xargs -0 -r gh release upload``.
A pipeline takes its exit status from the last command, so ``set -eu`` never
saw ``find`` fail when the directory did not exist, ``xargs -r`` then ran
nothing, and the step reported success having uploaded no wheel. Both v0.3.0
and v0.3.1 published without their wheel as a result, and a human attached it
afterwards.

This module is the replacement: it decides what to upload in Python, treats an
empty result as the failure it is, and invokes ``gh`` through cuprum's
allowlist rather than a shell. Its process dependencies -- the ``gh`` runner,
the clock, and the two output sinks -- are parameters with production
defaults, so tests drive the command path explicitly rather than by
intercepting the environment.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import enum
import json
import os
import stat
import sys
import time
import typing as typ
from pathlib import Path

from cuprum import Program, ProgramCatalogue, ProjectSettings, scoped, sh

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    import io

GH = Program("gh")
_RELEASE_PROJECT = ProjectSettings(
    name="lading-release",
    programs=(GH,),
    documentation_locations=("docs/developers-guide.md#release-workflow",),
    noise_rules=(),
)
RELEASE_CATALOGUE = ProgramCatalogue(projects=(_RELEASE_PROJECT,))


class Outcome(enum.StrEnum):
    """Every outcome the step can report.

    The set is closed so that a counter built from it stays bounded however
    the release fails, and so that no path, tag, or message text can reach a
    metric label. Members are strings, so they serialize as themselves.
    """

    SUCCESS = "success"
    NO_WHEEL = "no-wheel"
    MISSING_DIRECTORY = "missing-directory"
    NOT_A_DIRECTORY = "not-a-directory"
    UNREADABLE_DIRECTORY = "unreadable-directory"
    UPLOAD_FAILED = "upload-failed"


class UploadError(RuntimeError):
    """Raised when the release's wheels cannot be uploaded.

    Every cause is a failure of the release rather than a condition to report
    and continue: a build that produced no wheel, an artefact directory that
    cannot be read, and an upload the GitHub CLI rejected. The workflow step
    turns any of them into a non-zero exit.

    Parameters
    ----------
    message : str
        The diagnostic written to stderr.
    outcome : Outcome
        Which failure occurred. It is the only value reported as a metric
        label.

    Examples
    --------
    >>> raise UploadError("No wheel found under dist", outcome=Outcome.NO_WHEEL)
    Traceback (most recent call last):
        ...
    release_wheel_upload.UploadError: No wheel found under dist
    """

    def __init__(
        self, message: str, *, outcome: Outcome = Outcome.UPLOAD_FAILED
    ) -> None:
        """Record the diagnostic and the outcome it should be counted as."""
        super().__init__(message)
        self.outcome = outcome


@dc.dataclass(frozen=True, slots=True)
class CommandOutcome:
    """What a ``gh`` invocation reported back.

    This is the whole of the command dependency's return contract, so a test
    runner can satisfy it without a process.
    """

    exit_code: int
    stdout: str = ""
    stderr: str = ""


class UploadRunner(typ.Protocol):
    """Runs one ``gh`` invocation and reports how it went."""

    def __call__(self, arguments: cabc.Sequence[str]) -> CommandOutcome:
        """Run ``gh`` with ``arguments`` and return its outcome."""


@dc.dataclass(frozen=True, slots=True)
class Sinks:
    """Where the step's outcome is reported.

    Both are injected rather than read from the environment at the point of
    use, so a test states the sinks it expects to be written.
    """

    log: io.TextIOBase | typ.TextIO
    github_output: Path | None = None


@dc.dataclass(slots=True)
class Timings:
    """Seconds spent in each phase of the step.

    Discovery and upload are timed apart because they fail for unrelated
    reasons: a slow discovery is a large artefact tree, a slow upload is
    GitHub.
    """

    discovery: float = 0.0
    upload: float = 0.0


def _raise_scan_error(error: OSError) -> typ.NoReturn:
    """Re-raise a directory-scan error instead of skipping the subtree.

    ``os.walk`` reports scan failures by calling this handler and ignores them
    by default, which would make an unreadable subtree indistinguishable from
    an empty one.
    """
    raise error


def _walk_wheels(directory: Path) -> cabc.Iterator[Path]:
    """Yield every ``.whl`` file beneath ``directory``."""
    for root, _directories, files in os.walk(directory, onerror=_raise_scan_error):
        parent = Path(root)
        for name in files:
            if name.endswith(".whl"):
                yield parent / name


def _require_directory(directory: Path) -> None:
    """Fail unless ``directory`` is a readable directory.

    ``Path.exists`` reports every stat failure as absence, so the stat is made
    directly: a permission failure on the artefact path must not be reported as
    a download that never ran.

    Raises
    ------
    UploadError
        If the path is absent, unreadable, or not a directory.
    """
    try:
        status = directory.stat()
    except FileNotFoundError as error:
        message = f"Artefact directory {directory} does not exist"
        raise UploadError(message, outcome=Outcome.MISSING_DIRECTORY) from error
    except OSError as error:
        message = f"Could not read artefact directory {directory}: {error}"
        raise UploadError(message, outcome=Outcome.UNREADABLE_DIRECTORY) from error
    if not stat.S_ISDIR(status.st_mode):
        message = f"Artefact path {directory} is not a directory"
        raise UploadError(message, outcome=Outcome.NOT_A_DIRECTORY)


def discover_wheels(directory: Path) -> tuple[Path, ...]:
    """Return every wheel under ``directory``, in a stable order.

    Parameters
    ----------
    directory : Path
        Directory the release artefacts were downloaded into. The search is
        recursive because the download action's layout depends on whether the
        artefact was named, and a release must not turn on that detail.

    Returns
    -------
    tuple[Path, ...]
        The wheels found, sorted by path.

    Raises
    ------
    UploadError
        If ``directory`` is absent, is not a directory, or cannot be read.
        Absence is a different failure from emptiness -- a download step that
        did not run at all rather than a build that produced nothing -- and the
        message says so. A read failure is reported rather than swallowed: an
        unreadable subtree would otherwise look like an empty one and fail with
        the wrong reason.

    Examples
    --------
    >>> import pathlib, tempfile
    >>> directory = pathlib.Path(tempfile.mkdtemp())
    >>> _ = (directory / "one-1.0-py3-none-any.whl").write_bytes(b"")
    >>> [path.name for path in discover_wheels(directory)]
    ['one-1.0-py3-none-any.whl']
    """
    _require_directory(directory)
    try:
        return tuple(sorted(_walk_wheels(directory)))
    except OSError as error:
        message = f"Could not read artefact directory {directory}: {error}"
        raise UploadError(message, outcome=Outcome.UNREADABLE_DIRECTORY) from error


def run_gh(arguments: cabc.Sequence[str]) -> CommandOutcome:
    """Run ``gh`` through the release catalogue and report the result.

    This is the production edge: the only place the script starts a process.

    Returns
    -------
    CommandOutcome
        The exit status and captured streams.
    """
    with scoped(allowlist=RELEASE_CATALOGUE.allowlist):
        result = sh.make(GH, catalogue=RELEASE_CATALOGUE)(*arguments).run_sync()
    return CommandOutcome(
        exit_code=result.exit_code,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )


def upload_wheels(
    tag: str, wheels: cabc.Sequence[Path], *, run: UploadRunner = run_gh
) -> None:
    """Attach ``wheels`` to the release for ``tag``.

    ``--clobber`` is passed because the release is created as a draft and
    reused: a run that failed after uploading would otherwise meet its own
    asset on the retry and fail again.

    Parameters
    ----------
    tag : str
        The release tag, which must already exist.
    wheels : cabc.Sequence[Path]
        The wheels to upload; never empty by the time this is called.
    run : UploadRunner
        How to invoke ``gh``. Defaults to the cuprum-allowlisted runner.

    Raises
    ------
    UploadError
        If ``gh release upload`` exits non-zero.
    """
    arguments = (
        "release",
        "upload",
        tag,
        *(str(wheel) for wheel in wheels),
        "--clobber",
    )
    result = run(arguments)
    if result.exit_code != 0:
        # Prefer stderr and fall back so a failure never reports an empty
        # reason; both streams are optional on a command result.
        detail = result.stderr.strip() or result.stdout.strip()
        message = (
            f"gh release upload failed with exit code {result.exit_code}: {detail}"
        )
        raise UploadError(message, outcome=Outcome.UPLOAD_FAILED)


def build_summary(
    outcome: Outcome, *, wheels: int, timings: Timings
) -> dict[str, object]:
    """Return the step's outcome as a reportable mapping.

    Parameters
    ----------
    outcome : Outcome
        Which outcome to report.
    wheels : int
        How many wheels were uploaded; zero on every failure.
    timings : Timings
        The per-phase durations to report.

    Returns
    -------
    dict[str, object]
        The summary, ready to serialize.
    """
    return {
        "outcome": str(outcome),
        "wheels": wheels,
        "discovery_seconds": round(timings.discovery, 3),
        "upload_seconds": round(timings.upload, 3),
    }


def report_outcome(summary: cabc.Mapping[str, object], sinks: Sinks) -> None:
    """Write ``summary`` to the step's sinks.

    A short-lived workflow step has no collector to push to, so the signal is
    the line itself, which the job log keeps, plus ``outcome`` and ``wheels``
    on ``GITHUB_OUTPUT`` for a later step or a workflow contract to read.

    Parameters
    ----------
    summary : cabc.Mapping[str, object]
        The mapping ``build_summary`` produced.
    sinks : Sinks
        Where to write it.
    """
    print(
        f"release_wheel_upload {json.dumps(dict(summary), sort_keys=True)}",
        file=sinks.log,
    )
    if sinks.github_output is None:
        return
    with sinks.github_output.open("a", encoding="utf-8") as handle:
        handle.write(f"outcome={summary['outcome']}\nwheels={summary['wheels']}\n")


@dc.dataclass(frozen=True, slots=True)
class Dependencies:
    """What ``attach_wheels`` needs from outside itself.

    Gathering the upload, the clock, and the progress sink into one record
    keeps them injectable without giving the function a parameter list nobody
    can read. The defaults are the production bindings.
    """

    upload: cabc.Callable[[str, cabc.Sequence[Path]], None] = upload_wheels
    clock: cabc.Callable[[], float] = time.monotonic
    log: io.TextIOBase | typ.TextIO = dc.field(default_factory=lambda: sys.stdout)


def attach_wheels(
    tag: str,
    directory: Path,
    timings: Timings,
    dependencies: Dependencies | None = None,
) -> tuple[Path, ...]:
    """Upload every wheel under ``directory`` to ``tag`` and return them.

    Parameters
    ----------
    tag : str
        The release tag to attach the wheels to.
    directory : Path
        Directory to search for wheels.
    timings : Timings
        Filled in with the duration of each phase, whether or not it succeeds.
    dependencies : Dependencies | None
        The upload, clock, and progress sink to use. Defaults to production.

    Returns
    -------
    tuple[Path, ...]
        The wheels uploaded.

    Raises
    ------
    UploadError
        If the directory holds no wheel, or the upload fails.
    """
    bound = dependencies if dependencies is not None else Dependencies()
    clock = bound.clock
    started = clock()
    try:
        wheels = discover_wheels(directory)
    finally:
        timings.discovery = clock() - started
    if not wheels:
        message = f"No wheel found under {directory}; nothing to attach to {tag}"
        raise UploadError(message, outcome=Outcome.NO_WHEEL)
    for wheel in wheels:
        print(f"Uploading {wheel} to {tag}", file=bound.log)
    started = clock()
    try:
        bound.upload(tag, wheels)
    finally:
        timings.upload = clock() - started
    return wheels
