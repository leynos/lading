"""Run the release uploader against a recording ``gh`` stub, never a real one.

Tests that exercise the uploader start a child process that runs ``gh``. On a
development machine that ``gh`` can be authenticated, and the lading
repository is a real GitHub repository, so a test that accidentally let the
real binary through could attach assets to -- or see the state of -- a real
release. The stub is therefore the guarantee, and the environment isolation
below is defence in depth for the case where a refactor moves the stub off the
front of ``PATH``.

The stub records one JSON line per invocation, so a test can assert the whole
call list rather than that "something was called": the previous stub
overwrote its record, which meant a second call was invisible and a stray
``release edit`` could never be observed.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import json
import os
import shutil
import subprocess
import sys
import typing as typ
from pathlib import Path

from tests.helpers.script_imports import REPOSITORY_ROOT

UPLOAD_SCRIPT = REPOSITORY_ROOT / "scripts" / "upload_release_wheels.py"

#: The tag every stub-driven run uses. It cannot exist, so a real ``gh``
#: reached by mistake would still have nothing to modify.
STUB_TAG = "v0.0.0-stub"

#: The variable a test sets in the parent to prove the child inherited the
#: environment. Production needs that inheritance for ``GITHUB_TOKEN``, so a
#: run that dropped the environment would fail the release for the wrong
#: reason.
SENTINEL_VARIABLE = "LADING_STUB_SENTINEL"

#: How long the uploader child process may run before the test fails. The
#: standalone mode may resolve dependencies through uv, which is why this is
#: generous compared with the repository mode's work.
UPLOAD_TIMEOUT_SECONDS = 45

#: Environment variables that could let a child ``gh`` reach a real account.
_CREDENTIAL_VARIABLES = ("GH_TOKEN", "GITHUB_TOKEN")

#: Variables uv reads to locate the project it is (or is not) working on. The
#: standalone mode must build its environment from the script's inline
#: metadata, so a leaked project variable would make the test prove nothing.
_UV_VARIABLES = (
    "UV_PROJECT",
    "UV_PROJECT_ENVIRONMENT",
    "UV_WORKING_DIRECTORY",
    "VIRTUAL_ENV",
)

_STUB_SOURCE = """#!{python}
import json
import os
import pathlib
import sys

record = pathlib.Path({record!r})
call = {{
    "argv": sys.argv[1:],
    "saw_gh_token": "GH_TOKEN" in os.environ,
    "saw_github_token": "GITHUB_TOKEN" in os.environ,
    "sentinel": os.environ.get({sentinel!r}),
    "gh_config_dir": os.environ.get("GH_CONFIG_DIR"),
    "virtual_env": os.environ.get("VIRTUAL_ENV"),
}}
with record.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(call) + "\\n")
sys.stdout.write({stdout!r})
sys.stderr.write({stderr!r})
sys.exit({exit_code})
"""


@dc.dataclass(frozen=True, slots=True)
class GhCall:
    """One recorded ``gh`` invocation."""

    argv: tuple[str, ...]
    saw_gh_token: bool
    saw_github_token: bool
    sentinel: str | None
    gh_config_dir: str | None
    virtual_env: str | None

    @property
    def is_release_mutation(self) -> bool:
        """Whether the call would change a release rather than read one."""
        return self.argv[:2] in {("release", "create"), ("release", "edit")}


@dc.dataclass(frozen=True, slots=True)
class GhStub:
    """A recording ``gh`` on disk, and the record of what it saw."""

    bin_directory: Path
    record: Path

    def calls(self) -> tuple[GhCall, ...]:
        """Return every call the stub has recorded, oldest first.

        Returns
        -------
        tuple[GhCall, ...]
            The recorded calls. Empty if the stub was never invoked.

        Examples
        --------
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     stub = install_gh_stub(Path(tmp))
        ...     stub.calls()
        ()
        """
        if not self.record.exists():
            return ()
        lines = self.record.read_text(encoding="utf-8").splitlines()
        return tuple(
            GhCall(
                argv=tuple(entry["argv"]),
                saw_gh_token=entry["saw_gh_token"],
                saw_github_token=entry["saw_github_token"],
                sentinel=entry["sentinel"],
                gh_config_dir=entry["gh_config_dir"],
                virtual_env=entry["virtual_env"],
            )
            for entry in map(json.loads, lines)
        )


def install_gh_stub(
    directory: Path,
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> GhStub:
    """Write a recording ``gh`` into ``directory`` and return it.

    Parameters
    ----------
    directory : Path
        Scratch directory to create the stub tree under.
    exit_code : int
        The status the stub exits with.
    stdout : str
        Text the stub writes to stdout.
    stderr : str
        Text the stub writes to stderr, which is where ``gh`` puts its
        diagnostic for an upload it rejected.

    Returns
    -------
    GhStub
        The stub, whose ``calls()`` reports what it saw.
    """
    bin_directory = directory / "bin"
    bin_directory.mkdir(parents=True, exist_ok=True)
    record = directory / "gh-calls.jsonl"
    stub = bin_directory / "gh"
    stub.write_text(
        _STUB_SOURCE.format(
            python=sys.executable,
            record=str(record),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            sentinel=SENTINEL_VARIABLE,
        ),
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return GhStub(bin_directory=bin_directory, record=record)


def isolated_environment(
    stub: GhStub,
    *,
    sentinel: str | None = None,
    extra: cabc.Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a child environment in which ``gh`` can only be the stub.

    Parameters
    ----------
    stub : GhStub
        The stub whose directory goes first on ``PATH``.
    sentinel : str | None
        Value to set for ``LADING_STUB_SENTINEL``, proving inheritance. Omit
        to leave the variable unset.
    extra : cabc.Mapping[str, str] | None
        Further variables to set, applied last.

    Returns
    -------
    dict[str, str]
        The environment for the child process.

    Raises
    ------
    AssertionError
        If the stub is not the ``gh`` the child would resolve. A later ``PATH``
        entry that still shadows the stub would defeat the whole arrangement,
        so the check is made here rather than trusted.
    """
    environment = dict(os.environ)
    environment["PATH"] = f"{stub.bin_directory}{os.pathsep}{environment['PATH']}"
    for variable in _CREDENTIAL_VARIABLES + _UV_VARIABLES:
        environment.pop(variable, None)
    if sentinel is not None:
        environment[SENTINEL_VARIABLE] = sentinel
    environment["GH_CONFIG_DIR"] = str(_empty_config_directory(stub))
    environment["GH_HOST"] = "stub.invalid"
    environment["GH_PROMPT_DISABLED"] = "1"
    if extra is not None:
        environment.update(extra)
    resolved = shutil.which("gh", path=environment["PATH"])
    expected = stub.bin_directory / "gh"
    if resolved is None or Path(resolved) != expected:
        message = f"the stub must be the gh on PATH, but gh resolves to {resolved}"
        raise AssertionError(message)
    return environment


def _empty_config_directory(stub: GhStub) -> Path:
    """Return the empty directory that replaces a stored ``gh`` login."""
    directory = stub.bin_directory.parent / "gh-config"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def run_uploader(
    mode: typ.Literal["repository", "standalone"],
    environment: cabc.Mapping[str, str],
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    """Run the uploader the way the release workflow does.

    The repository mode runs the script with the interpreter that is already
    installed; the standalone mode runs it through ``uv run --script``, which
    builds the environment from the script's own metadata and lockfile and
    ignores the project entirely.

    Parameters
    ----------
    mode : typ.Literal["repository", "standalone"]
        Which dependency path to exercise.
    environment : cabc.Mapping[str, str]
        The child environment, normally from ``isolated_environment``.
    *arguments : str
        Arguments for the uploader.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed run.

    Raises
    ------
    subprocess.TimeoutExpired
        If the uploader does not finish within ``UPLOAD_TIMEOUT_SECONDS``. The
        timeout is enforced here rather than left to pytest's, so the failure
        names the command that hung.
    """  # ruff: ignore[docstring-extraneous-exception]  # TimeoutExpired comes from subprocess.run, not a raise
    command = (
        ["uv", "run", "--script", str(UPLOAD_SCRIPT)]
        if mode == "standalone"
        else [sys.executable, str(UPLOAD_SCRIPT)]
    )
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        [*command, *arguments],
        capture_output=True,
        text=True,
        check=False,
        env=dict(environment),
        cwd=REPOSITORY_ROOT,
        timeout=UPLOAD_TIMEOUT_SECONDS,
    )
