#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cuprum==0.2.0b1", "cyclopts>=3"]
# ///
"""Attach the wheels built for a release tag to that GitHub release.

The step this replaced could not fail: it searched for wheels through a shell
pipeline, whose exit status is its last command's, so a release published with
nothing attached while the job stayed green (issue #266). This file is the
command-line edge and the composition root; ``release_wheel_upload`` beside it
holds the logic and records the defect in full.

Examples
--------
```bash
scripts/upload_release_wheels.py --tag v1.2.3 --directory dist
```
"""

from __future__ import annotations

import os
import sys
import typing as typ
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter
from release_wheel_upload import (
    Outcome,
    Sinks,
    Timings,
    UploadError,
    attach_wheels,
    build_summary,
    report_outcome,
)

app = App(
    name="upload-release-wheels",
    help="Upload the wheels in a directory to a GitHub release.",
    config=cyclopts.config.Env("INPUT_", command=False),
)


def _environment_sinks() -> Sinks:
    """Return the sinks the workflow provides."""
    destination = os.environ.get("GITHUB_OUTPUT")
    return Sinks(
        log=sys.stderr, github_output=Path(destination) if destination else None
    )


@app.default
def main(
    *,
    tag: typ.Annotated[str, Parameter(required=True, env_var="GITHUB_REF_NAME")],
    directory: Path = Path("dist"),
) -> None:
    """Upload every wheel under ``directory`` to the release for ``tag``.

    This is the composition root: it binds the production clock, runner, and
    sinks, and every other function takes them as parameters.

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
    sinks = _environment_sinks()
    try:
        wheels = attach_wheels(tag, directory, timings)
    except UploadError as error:
        report_outcome(build_summary(error.outcome, wheels=0, timings=timings), sinks)
        raise
    report_outcome(
        build_summary(Outcome.SUCCESS, wheels=len(wheels), timings=timings), sinks
    )
    print(f"Attached {len(wheels)} wheel(s) to {tag}")


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    try:
        app()
    except UploadError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
