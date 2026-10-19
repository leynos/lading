#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.14"
# dependencies = ["cuprum==0.2.0b2", "cyclopts>=3"]
# ///
"""Attach the sdist and wheels built for a release tag to GitHub.

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

import os
import sys
import typing as typ
from functools import partial
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter
from release_gh import GitHubReleaseAssets
from release_wheel_upload import (
    Dependencies,
    Outcome,
    Sinks,
    Timings,
    UploadError,
    attach_distributions,
    build_summary,
    report_outcome,
    upload_distributions,
)

#: The production wiring of the domain port is bound here, at the one place
#: that knows both the policy and its GitHub adapter.
_ASSETS = GitHubReleaseAssets()
_PRODUCTION = Dependencies(upload=partial(upload_distributions, assets=_ASSETS))

app = App(
    name="upload-release-wheels",
    help="Upload the sdist and wheels in a directory to a GitHub release.",
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
    """Attach each built distribution not already on the release for ``tag``.

    This is the composition root: it binds the GitHub asset adapter, clock,
    and sinks, and every other function takes them as parameters.

    Parameters
    ----------
    tag : str
        The release tag to attach the distributions to. Defaults to the tag the
        workflow was triggered by.
    directory : Path
        Directory to search for wheels and the source archive. Defaults to
        ``dist``.

    Raises
    ------
    UploadError
        If the directory holds no distribution, or release inspection or
        upload fails. An empty local result is an error rather than a no-op;
        every exit reports its outcome first.
    """
    timings = Timings()
    sinks = _environment_sinks()
    try:
        distributions = attach_distributions(tag, directory, timings, _PRODUCTION)
    except UploadError as error:
        report_outcome(
            build_summary(error.outcome, distributions=0, timings=timings), sinks
        )
        raise
    report_outcome(
        build_summary(
            Outcome.SUCCESS, distributions=len(distributions), timings=timings
        ),
        sinks,
    )
    print(f"Attached {len(distributions)} distribution(s) to {tag}")


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    try:
        app()
    except UploadError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
