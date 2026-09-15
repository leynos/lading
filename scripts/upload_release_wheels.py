#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cuprum>=0.1.0", "cyclopts>=3"]
# ///
"""Attach the wheels built for a release tag to that GitHub release.

The release workflow previously did this with
``find dist/wheels-* -name '*.whl' -print0 | xargs -0 -r gh release upload``.
A pipeline takes its exit status from the last command, so ``set -eu`` never
saw ``find`` fail when the directory did not exist, ``xargs -r`` then ran
nothing, and the step reported success having uploaded no wheel. Both v0.3.0
and v0.3.1 published without their wheel as a result, and a human attached it
afterwards.

This script is the replacement: it decides what to upload in Python, treats an
empty result as the failure it is, and invokes ``gh`` through cuprum's
allowlist rather than a shell.

Examples
--------
```bash
scripts/upload_release_wheels.py --tag v1.2.3 --directory dist
```
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import json
import os
import stat
import sys
import time
import typing as typ
from pathlib import Path

import cyclopts
from cuprum import Program, ProgramCatalogue, ProjectSettings, scoped, sh
from cyclopts import App, Parameter

GH = Program("gh")
_RELEASE_PROJECT = ProjectSettings(
    name="lading-release",
    programs=(GH,),
    documentation_locations=("docs/developers-guide.md#release-workflow",),
    noise_rules=(),
)
RELEASE_CATALOGUE = ProgramCatalogue(projects=(_RELEASE_PROJECT,))

app = App(
    name="upload-release-wheels",
    help="Upload the wheels in a directory to a GitHub release.",
    config=cyclopts.config.Env("INPUT_", command=False),
)


SUCCESS = "success"
NO_WHEEL = "no-wheel"
MISSING_DIRECTORY = "missing-directory"
NOT_A_DIRECTORY = "not-a-directory"
UNREADABLE_DIRECTORY = "unreadable-directory"
UPLOAD_FAILED = "upload-failed"
#: Every outcome the step can report. The set is closed so that a counter
#: built from it stays bounded however the release fails.
OUTCOMES = (
    SUCCESS,
    NO_WHEEL,
    MISSING_DIRECTORY,
    NOT_A_DIRECTORY,
    UNREADABLE_DIRECTORY,
    UPLOAD_FAILED,
)


class UploadError(RuntimeError):
    """Raised when the release's wheels cannot be uploaded.

    Both causes are failures of the release rather than conditions to report
    and continue: a build that produced no wheel, and an upload the GitHub
    CLI rejected. The workflow step turns either into a non-zero exit.

    Parameters
    ----------
    message : str
        The diagnostic written to stderr.
    outcome : str
        One of ``OUTCOMES``, naming which failure occurred. It is the only
        value reported as a metric label, so it is drawn from a fixed set and
        never carries a path, a tag, or error text.

    Examples
    --------
    >>> raise UploadError("No wheel found under dist", outcome=NO_WHEEL)
    Traceback (most recent call last):
        ...
    upload_release_wheels.UploadError: No wheel found under dist
    """

    def __init__(self, message: str, *, outcome: str = UPLOAD_FAILED) -> None:
        """Record the diagnostic and the outcome it should be counted as."""
        super().__init__(message)
        self.outcome = outcome


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
        raise UploadError(message, outcome=MISSING_DIRECTORY) from error
    except OSError as error:
        message = f"Could not read artefact directory {directory}: {error}"
        raise UploadError(message, outcome=UNREADABLE_DIRECTORY) from error
    if not stat.S_ISDIR(status.st_mode):
        message = f"Artefact path {directory} is not a directory"
        raise UploadError(message, outcome=NOT_A_DIRECTORY)


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
        raise UploadError(message, outcome=UNREADABLE_DIRECTORY) from error


def upload_wheels(tag: str, wheels: cabc.Sequence[Path]) -> None:
    """Attach ``wheels`` to the release for ``tag``.

    Parameters
    ----------
    tag : str
        The release tag, which must already exist.
    wheels : cabc.Sequence[Path]
        The wheels to upload; never empty by the time this is called.

    Raises
    ------
    UploadError
        If ``gh release upload`` exits non-zero.
    """
    arguments = ("release", "upload", tag, *(str(wheel) for wheel in wheels))
    with scoped(allowlist=RELEASE_CATALOGUE.allowlist):
        result = sh.make(GH, catalogue=RELEASE_CATALOGUE)(*arguments).run_sync()
    if result.exit_code != 0:
        # Both streams are optional on a CommandResult; prefer stderr and fall
        # back so a failure never reports an empty reason.
        detail = (result.stderr or "").strip() or (result.stdout or "").strip()
        message = (
            f"gh release upload failed with exit code {result.exit_code}: {detail}"
        )
        raise UploadError(message, outcome=UPLOAD_FAILED)


@dc.dataclass(slots=True)
class Timings:
    """Seconds spent in each phase of the step.

    Discovery and upload are timed apart because they fail for unrelated
    reasons: a slow discovery is a large artefact tree, a slow upload is
    GitHub.
    """

    discovery: float = 0.0
    upload: float = 0.0


def report_outcome(outcome: str, *, wheels: int, timings: Timings) -> None:
    """Emit the step's outcome as one machine-readable line.

    A short-lived workflow step has no collector to push to, so the signal is
    the line itself, which the job log keeps, plus ``outcome`` and ``wheels``
    on ``GITHUB_OUTPUT`` for a later step or a workflow contract to read. Only
    the bounded ``outcome`` is ever used as a label: no path, tag, or message
    text is reported.

    Parameters
    ----------
    outcome : str
        One of ``OUTCOMES``.
    wheels : int
        How many wheels were uploaded; zero on every failure.
    timings : Timings
        The per-phase durations to report.
    """
    summary = {
        "outcome": outcome,
        "wheels": wheels,
        "discovery_seconds": round(timings.discovery, 3),
        "upload_seconds": round(timings.upload, 3),
    }
    print(
        f"release_wheel_upload {json.dumps(summary, sort_keys=True)}", file=sys.stderr
    )
    destination = os.environ.get("GITHUB_OUTPUT")
    if not destination:
        return
    with Path(destination).open("a", encoding="utf-8") as handle:
        handle.write(f"outcome={outcome}\nwheels={wheels}\n")


def attach_wheels(tag: str, directory: Path, timings: Timings) -> tuple[Path, ...]:
    """Upload every wheel under ``directory`` to ``tag`` and return them.

    Parameters
    ----------
    tag : str
        The release tag to attach the wheels to.
    directory : Path
        Directory to search for wheels.
    timings : Timings
        Filled in with the duration of each phase, whether or not it succeeds.

    Returns
    -------
    tuple[Path, ...]
        The wheels uploaded.

    Raises
    ------
    UploadError
        If the directory holds no wheel, or the upload fails.
    """
    started = time.monotonic()
    try:
        wheels = discover_wheels(directory)
    finally:
        timings.discovery = time.monotonic() - started
    if not wheels:
        message = f"No wheel found under {directory}; nothing to attach to {tag}"
        raise UploadError(message, outcome=NO_WHEEL)
    for wheel in wheels:
        print(f"Uploading {wheel} to {tag}")
    started = time.monotonic()
    try:
        upload_wheels(tag, wheels)
    finally:
        timings.upload = time.monotonic() - started
    return wheels


@app.default
def main(
    *,
    tag: typ.Annotated[str, Parameter(required=True, env_var="GITHUB_REF_NAME")],
    directory: Path = Path("dist"),
) -> None:
    """Upload every wheel under ``directory`` to the release for ``tag``.

    Parameters
    ----------
    tag : str
        The release tag to attach the wheels to. Defaults to the tag the
        workflow was triggered by.
    directory : Path
        Directory to search for wheels. Defaults to ``dist``.

    Raises
    ------
    UploadError
        If the directory holds no wheel, or the upload fails. An empty result
        is an error rather than a no-op: a release that silently ships nothing
        is indistinguishable from one that shipped the right artefact. Every
        exit, successful or not, reports its outcome first.
    """
    timings = Timings()
    try:
        wheels = attach_wheels(tag, directory, timings)
    except UploadError as error:
        report_outcome(error.outcome, wheels=0, timings=timings)
        raise
    report_outcome(SUCCESS, wheels=len(wheels), timings=timings)
    print(f"Attached {len(wheels)} wheel(s) to {tag}")


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    try:
        app()
    except UploadError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
