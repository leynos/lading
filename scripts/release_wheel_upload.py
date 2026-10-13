"""Reconcile and report the distributions attached to a GitHub release.

This module is the policy behind ``scripts/upload_release_wheels.py``. It
discovers the sdist and wheels, compares their filenames with the release
assets, and uploads only missing names. An empty local directory is a release
failure, while a run with every name already attached succeeds without an
upload. The process still runs through cuprum's allowlist.

The ``gh`` runner, clock, and output sinks are injected, so tests drive the
release decision without intercepting the environment.

This module imports no cuprum and does not import the adapter that does. The
port it depends on, ``UploadRunner``, and the record that crosses it,
``CommandOutcome``, live in ``release_port``; the cuprum process edge lives in
``release_gh``; and the two are bound together at the composition root in
``upload_release_wheels``. That direction is deliberate -- a policy module that
imported its adapter would drag the process technology into every test that
exercises it -- and is worth preserving when the upload path changes.
"""

import collections
import collections.abc as cabc
import dataclasses as dc
import enum
import io
import json
import os
import stat
import sys
import time
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    # Only the port protocol is named here. The record that crosses it is part
    # of the protocol's signature, not of this module's vocabulary: importing
    # it would be an import this module does not use, and the port is the one
    # thing it genuinely depends on.
    from release_port import UploadRunner


class Outcome(enum.StrEnum):
    """Every outcome the step can report.

    The set is closed so that a counter built from it stays bounded however
    the release fails, and so that no path, tag, or message text can reach a
    metric label. Members are strings, so they serialize as themselves.
    """

    SUCCESS = "success"
    NO_DISTRIBUTIONS = "no-distributions"
    MISSING_DIRECTORY = "missing-directory"
    NOT_A_DIRECTORY = "not-a-directory"
    UNREADABLE_DIRECTORY = "unreadable-directory"
    UPLOAD_FAILED = "upload-failed"


class UploadError(RuntimeError):
    """Raised when the release's distributions cannot be reconciled.

    Every cause is a failure of the release rather than a condition to report
    and continue: a build that produced no distribution, an artefact directory
    that cannot be read, or a GitHub CLI request that failed. The workflow
    step turns any of them into a non-zero exit.

    Parameters
    ----------
    message : str
        The diagnostic written to stderr.
    outcome : Outcome
        Which failure occurred. It is the only value reported as a metric
        label.

    Examples
    --------
    >>> raise UploadError(
    ...     "No distributions found under dist",
    ...     outcome=Outcome.NO_DISTRIBUTIONS,
    ... )
    Traceback (most recent call last):
        ...
    release_wheel_upload.UploadError: No distributions found under dist
    """

    def __init__(
        self, message: str, *, outcome: Outcome = Outcome.UPLOAD_FAILED
    ) -> None:
        """Record the diagnostic and the outcome it should be counted as."""
        super().__init__(message)
        self.outcome = outcome


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


def _walk_distributions(directory: Path) -> cabc.Iterator[Path]:
    """Yield every wheel and source archive beneath ``directory``."""
    for root, _directories, files in os.walk(directory, onerror=_raise_scan_error):
        parent = Path(root)
        for name in files:
            if name.endswith((".whl", ".tar.gz")):
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


def discover_distributions(directory: Path) -> tuple[Path, ...]:
    """Return every wheel and source distribution under ``directory``.

    Parameters
    ----------
    directory : Path
        Directory the release artefacts were downloaded into. The search is
        recursive because the download action's layout depends on whether the
        artefact was named, and a release must not turn on that detail.

    Returns
    -------
    tuple[Path, ...]
        The distributions found, sorted by path.

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
    >>> [path.name for path in discover_distributions(directory)]
    ['one-1.0-py3-none-any.whl']
    """
    _require_directory(directory)
    try:
        return tuple(sorted(_walk_distributions(directory)))
    except OSError as error:
        message = f"Could not read artefact directory {directory}: {error}"
        raise UploadError(message, outcome=Outcome.UNREADABLE_DIRECTORY) from error


def upload_distributions(
    tag: str,
    distributions: cabc.Sequence[Path],
    log: io.TextIOBase | typ.TextIO,
    *,
    run: UploadRunner,
) -> tuple[Path, ...]:
    """Attach distributions not already present on the release for ``tag``.

    Parameters
    ----------
    tag : str
        The release tag, which must already exist.
    distributions : cabc.Sequence[Path]
        Wheels and source archives to reconcile with the release assets.
    run : UploadRunner
        How to invoke ``gh``. Bound to the production adapter at the
        composition root rather than defaulted here, so this module never
        imports the module that imports cuprum.
    log : io.TextIOBase | typ.TextIO
        Where per-file skip notices are written.

    Returns
    -------
    tuple[Path, ...]
        The distributions uploaded during this invocation.

    Raises
    ------
    UploadError
        If release inspection or upload fails.
    """
    filename_counts = collections.Counter(
        distribution.name for distribution in distributions
    )
    duplicate_names = sorted(
        name for name, count in filename_counts.items() if count > 1
    )
    if duplicate_names:
        message = f"Duplicate distribution filenames: {', '.join(duplicate_names)}"
        raise UploadError(message, outcome=Outcome.UPLOAD_FAILED)

    result = run(("release", "view", tag, "--json", "assets", "--jq", ".assets[].name"))
    if result.exit_code != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        message = f"gh release view failed with exit code {result.exit_code}: {detail}"
        raise UploadError(message, outcome=Outcome.UPLOAD_FAILED)
    existing = set(result.stdout.splitlines())
    pending: list[Path] = []
    for distribution in distributions:
        if distribution.name in existing:
            print(
                f"::notice::Skipping already-attached distribution {distribution.name}",
                file=log,
            )
        else:
            pending.append(distribution)
    if not pending:
        return ()

    arguments = ("release", "upload", tag, *(str(path) for path in pending))
    result = run(arguments)
    if result.exit_code != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        message = (
            f"gh release upload failed with exit code {result.exit_code}: {detail}"
        )
        raise UploadError(message, outcome=Outcome.UPLOAD_FAILED)
    return tuple(pending)


def build_summary(
    outcome: Outcome, *, distributions: int, timings: Timings
) -> dict[str, object]:
    """Return the step's outcome as a reportable mapping.

    Parameters
    ----------
    outcome : Outcome
        Which outcome to report.
    distributions : int
        How many distributions were uploaded during this invocation. This is
        zero when no upload was needed or when the run failed.
    timings : Timings
        The per-phase durations to report.

    Returns
    -------
    dict[str, object]
        The summary, ready to serialize.
    """
    return {
        "outcome": str(outcome),
        "distributions": distributions,
        "discovery_seconds": round(timings.discovery, 3),
        "upload_seconds": round(timings.upload, 3),
    }


def report_outcome(summary: cabc.Mapping[str, object], sinks: Sinks) -> None:
    """Write ``summary`` to the step's sinks.

    A short-lived workflow step has no collector to push to, so the signal is
    the line itself, which the job log keeps, plus ``outcome`` and
    ``distributions`` on ``GITHUB_OUTPUT`` for a later step or contract.

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
        handle.write(
            f"outcome={summary['outcome']}\ndistributions={summary['distributions']}\n"
        )


@dc.dataclass(frozen=True, slots=True)
class Dependencies:
    """What ``attach_distributions`` needs from outside itself.

    Gathering the upload, the clock, and the progress sink into one record
    keeps them injectable without giving the function a parameter list nobody
    can read.

    The clock and the sink are defaulted, because production and test want the
    same ones. ``upload`` is not: it carries the process port, and defaulting
    it here would mean naming a runner in this module, which is the dependency
    this module exists not to have. The composition root supplies it.
    """

    upload: cabc.Callable[
        [str, cabc.Sequence[Path], io.TextIOBase | typ.TextIO],
        cabc.Sequence[Path],
    ]
    clock: cabc.Callable[[], float] = time.monotonic
    log: io.TextIOBase | typ.TextIO = dc.field(default_factory=lambda: sys.stdout)


def attach_distributions(
    tag: str,
    directory: Path,
    timings: Timings,
    dependencies: Dependencies,
) -> tuple[Path, ...]:
    """Reconcile every wheel and source archive under ``directory`` with ``tag``.

    Parameters
    ----------
    tag : str
        The release tag to attach the wheels to.
    directory : Path
        Directory to search for distributions.
    timings : Timings
        Filled in with the duration of each phase, whether or not it succeeds.
    dependencies : Dependencies
        The upload, clock, and progress sink to use. There is no default: the
        upload carries the process port, and only the composition root knows
        which runner to bind to it.

    Returns
    -------
    tuple[Path, ...]
        The distributions uploaded during this invocation.

    Raises
    ------
    UploadError
        If the directory holds no distribution, or inspection/upload fails.
    """
    bound = dependencies
    clock = bound.clock
    started = clock()
    try:
        distributions = discover_distributions(directory)
    finally:
        timings.discovery = clock() - started
    if not distributions:
        message = (
            f"No distributions found under {directory}; nothing to attach to {tag}"
        )
        raise UploadError(message, outcome=Outcome.NO_DISTRIBUTIONS)
    for distribution in distributions:
        print(f"Reconciling {distribution} with {tag}", file=bound.log)
    started = clock()
    try:
        uploaded = bound.upload(tag, distributions, bound.log)
    finally:
        timings.upload = clock() - started
    return tuple(uploaded)
