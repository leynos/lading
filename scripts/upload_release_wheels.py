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
import sys
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


class UploadError(RuntimeError):
    """Raised when the release's wheels cannot be uploaded.

    Both causes are failures of the release rather than conditions to report
    and continue: a build that produced no wheel, and an upload the GitHub
    CLI rejected. The workflow step turns either into a non-zero exit.

    Examples
    --------
    >>> raise UploadError("No wheel found under dist")
    Traceback (most recent call last):
        ...
    upload_release_wheels.UploadError: No wheel found under dist
    """


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

    Examples
    --------
    >>> import pathlib, tempfile
    >>> directory = pathlib.Path(tempfile.mkdtemp())
    >>> _ = (directory / "one-1.0-py3-none-any.whl").write_bytes(b"")
    >>> [path.name for path in discover_wheels(directory)]
    ['one-1.0-py3-none-any.whl']
    """
    return tuple(sorted(directory.rglob("*.whl")))


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
        raise UploadError(message)


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
        is indistinguishable from one that shipped the right artefact.
    """
    wheels = discover_wheels(directory)
    if not wheels:
        message = f"No wheel found under {directory}; nothing to attach to {tag}"
        raise UploadError(message)
    for wheel in wheels:
        print(f"Uploading {wheel} to {tag}")
    upload_wheels(tag, wheels)


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    try:
        app()
    except UploadError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
