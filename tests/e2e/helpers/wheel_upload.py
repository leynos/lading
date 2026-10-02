"""Run the release uploader in repository mode and build wheel fixtures.

Two end-to-end modules exercise the repository path -- one for the workflow
contract, one for the span record the process edge emits -- so the run helper
lives here rather than in either of them. The same reasoning applies as for
the selection readers: neither test module should become the other's library,
because a change made for one would then silently re-scope the other.

The no-publication guarantees are the stub helper's, in
:mod:`tests.helpers.gh_stub`; this module only supplies the two conveniences
both callers share.
"""

import typing as typ
from pathlib import Path

from tests.helpers.gh_stub import GhStub, isolated_environment, run_uploader

if typ.TYPE_CHECKING:
    import subprocess


def run_repository_upload(
    stub: GhStub,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the script the way the workflow does and capture its result.

    Parameters
    ----------
    stub : GhStub
        The stub the child must resolve ``gh`` to.
    *arguments : str
        Arguments for the uploader.
    environment : dict[str, str] | None
        Overrides applied to the isolated environment; the tag goes here.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed run.
    """
    child = isolated_environment(stub, extra=environment or {})
    return run_uploader("repository", child, *arguments)


def make_wheel(directory: Path, name: str) -> Path:
    """Create an empty file named like a wheel and return its path.

    Parameters
    ----------
    directory : Path
        Where to create it; parents are created as needed.
    name : str
        The file name, which the callers write to look like a wheel.

    Returns
    -------
    Path
        The created file.
    """
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / name
    wheel.write_bytes(b"")
    return wheel
