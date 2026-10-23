"""Pinned ``nose`` duplication detector used by the code-duplication gate.

This module owns everything that touches the external detector: reading the
repository's ``[tool.nose]`` settings, locating the pinned binary, verifying
its version, and running one ``nose query``. The report schema lives in
``scripts/nose_schema.py``.
"""

import dataclasses as dc
import json
import os
import shutil
import subprocess
import tomllib
from collections import abc as cabc
from pathlib import Path

from nose_schema import (
    Finding,
    GateConfigError,
    GateExecutionError,
    Location,
    normalize_findings,
    require_positive_int,
    require_string,
    require_string_tuple,
    require_table,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
DEFAULT_NOSE_RELATIVE_PATH = Path(".tools") / "nose" / "nose"
INSTALL_HINT = "run `make install-nose` to install the pinned detector"
COMMAND_TIMEOUT_SECONDS = 120

type CommandRunner = cabc.Callable[[cabc.Sequence[str]], str]


@dc.dataclass(frozen=True, slots=True)
class NoseSettings:
    """Gate settings read from ``[tool.nose]``.

    Attributes
    ----------
    version : str
        Version string the installed binary must report.
    roots : tuple[str, ...]
        Repository-relative paths handed to ``nose query``.
    mode : str
        Comma-separated detection channels, pinned so a change to nose's
        defaults cannot silently widen or narrow the gate.
    min_size : int
        Smallest unit size, in nose IL tokens, that may be reported.
    surface : str
        ``"default"`` for nose's ranked dashboard, or ``"all"`` to include
        hidden-surface families.
    top : int | None
        How many ranked families to adjudicate, or ``None`` for nose's own
        view size.
    exclude : tuple[str, ...]
        Gitignore-style globs excluded from the scan.
    """

    version: str
    roots: tuple[str, ...]
    mode: str
    min_size: int
    surface: str
    top: int | None
    exclude: tuple[str, ...]


@dc.dataclass(frozen=True, slots=True)
class NoseEnvironment:
    """Host inputs that the CLI adapter supplies to detector resolution.

    Attributes
    ----------
    repository_root : pathlib.Path
        Repository root used for relative overrides and detector execution,
        and for the missing-binary diagnostic.
    binary_override : str | None
        Value of ``NOSE_BIN`` when the caller selects a specific executable.
    search_directories : tuple[str, ...]
        Ordered directories available for finding ``nose``. The repository's
        local installation directory comes first, followed by explicit
        ``PATH`` entries when present.

    Examples
    --------
    >>> environment = NoseEnvironment(
    ...     repository_root=Path("/repo"),
    ...     binary_override=None,
    ...     search_directories=("/repo/.tools/nose", "/usr/bin"),
    ... )
    >>> environment.search_directories[0]
    '/repo/.tools/nose'
    """

    repository_root: Path
    binary_override: str | None
    search_directories: tuple[str, ...]


type BinaryFinder = cabc.Callable[[NoseEnvironment], str | None]

__all__ = [
    "BinaryFinder",
    "CommandRunner",
    "Finding",
    "GateConfigError",
    "GateExecutionError",
    "Location",
    "NoseEnvironment",
    "NoseSettings",
    "build_command",
    "discover_binary",
    "load_settings",
    "normalize_findings",
    "repository_runner",
    "resolve_binary",
    "run_detector",
]


def load_settings(pyproject_path: Path) -> NoseSettings:
    """Load the detector settings from ``[tool.nose]``.

    Parameters
    ----------
    pyproject_path : pathlib.Path
        Path to the repository ``pyproject.toml``.

    Returns
    -------
    NoseSettings
        Validated detector settings.

    Raises
    ------
    GateConfigError
        If a required key is missing or has the wrong type.
    """
    with pyproject_path.open("rb") as handle:
        data = tomllib.load(handle)
    root = require_table(data, context="pyproject")
    table = require_table(root.get("tool", {}), context="tool")
    nose = require_table(table.get("nose", {}), context="tool.nose")
    surface = require_string(nose.get("surface", "all"), context="tool.nose.surface")
    if surface not in {"default", "all"}:
        msg = "tool.nose.surface must be 'default' or 'all'"
        raise GateConfigError(msg)
    return NoseSettings(
        version=require_string(nose.get("version"), context="tool.nose.version"),
        roots=require_string_tuple(nose.get("roots"), context="tool.nose.roots"),
        mode=require_string(nose.get("mode"), context="tool.nose.mode"),
        min_size=require_positive_int(
            nose.get("min-size"), context="tool.nose.min-size"
        ),
        surface=surface,
        top=(
            None
            if nose.get("top") is None
            else require_positive_int(nose.get("top"), context="tool.nose.top")
        ),
        exclude=require_string_tuple(
            nose.get("exclude", []), context="tool.nose.exclude"
        ),
    )


def resolve_binary(
    settings: NoseSettings,
    *,
    environment: NoseEnvironment,
    binary_finder: BinaryFinder,
    runner: CommandRunner,
) -> str:
    """Locate the pinned nose binary and verify its version.

    Parameters
    ----------
    settings : NoseSettings
        Detector settings supplying the pinned version.
    environment : NoseEnvironment
        Explicit host inputs read by the CLI adapter.
    binary_finder : BinaryFinder
        Injected lookup for the repository-local or PATH-installed binary.
    runner : CommandRunner
        Injected command runner supplied by the CLI adapter.

    Returns
    -------
    str
        Path to a nose binary reporting the pinned version.

    Raises
    ------
    GateExecutionError
        If no binary is found or the reported version does not match.
    """
    # Resolve against the repository root so a relative NOSE_BIN keeps
    # working for callers that run the detector from another directory.
    if environment.binary_override:
        candidate = str(
            (environment.repository_root / environment.binary_override).resolve()
        )
    else:
        candidate = binary_finder(environment)
    if candidate is None:
        msg = (
            f"nose {settings.version} was not found at "
            f"{environment.repository_root / DEFAULT_NOSE_RELATIVE_PATH} "
            f"or on PATH: {INSTALL_HINT}"
        )
        raise GateExecutionError(msg)
    reported = runner([candidate, "--version"]).strip()
    expected = f"nose {settings.version}"
    if reported != expected:
        msg = (
            f"{candidate} reports '{reported}' but the gate pins "
            f"'{expected}': {INSTALL_HINT}"
        )
        raise GateExecutionError(msg)
    return candidate


def discover_binary(environment: NoseEnvironment) -> str | None:
    """Find the repository-local nose binary, else one on the supplied path."""
    return shutil.which("nose", path=os.pathsep.join(environment.search_directories))


def build_command(binary: str, settings: NoseSettings) -> list[str]:
    """Build the ``nose query`` command for the configured gate settings.

    Parameters
    ----------
    binary : str
        Path to the verified nose binary.
    settings : NoseSettings
        Detector settings for this repository.

    Returns
    -------
    list[str]
        Argument vector for the detector run.
    """
    command = [binary, "query"]
    for root in settings.roots:
        command.extend(("--root", root))
    if settings.surface == "all":
        # The bare `all` term unhides families nose keeps off its dashboard.
        # `top` remains a bound on the ranked surface, never on the whole scan:
        # it is deliberately not `0`, which would report every family.
        command.append("all")
    if settings.top is not None:
        command.append(f"top={settings.top}")
    command.extend(("--mode", settings.mode))
    command.extend(("--min-size", str(settings.min_size)))
    for glob in settings.exclude:
        command.extend(("--exclude", glob))
    command.extend(("--format", "json"))
    return command


def run_detector(
    settings: NoseSettings,
    *,
    environment: NoseEnvironment,
    binary_finder: BinaryFinder,
    runner: CommandRunner,
) -> list[Finding]:
    """Run the pinned detector and normalize its report.

    Parameters
    ----------
    settings : NoseSettings
        Detector settings for this repository.
    environment : NoseEnvironment
        Explicit host inputs read by the CLI adapter.
    binary_finder : BinaryFinder
        Injected lookup for the repository-local or PATH-installed binary.
    runner : CommandRunner
        Injected command runner supplied by the CLI adapter.

    Returns
    -------
    list[Finding]
        Findings ordered by descending value, then by source location.

    Raises
    ------
    GateExecutionError
        If the detector cannot be run or emits unreadable output. Report
        schema violations propagate as ``GateConfigError`` from
        :func:`normalize_findings`.
    """
    binary = resolve_binary(
        settings,
        environment=environment,
        binary_finder=binary_finder,
        runner=runner,
    )
    output = runner(build_command(binary, settings))
    try:
        report = json.loads(output)
    except json.JSONDecodeError as error:
        msg = f"nose report is not valid JSON: {error}"
        raise GateExecutionError(msg) from error
    return normalize_findings(report)


def repository_runner(repository_root: Path) -> CommandRunner:
    """Create the default command runner for one repository.

    Parameters
    ----------
    repository_root : pathlib.Path
        Working directory used for detector version checks and scans.

    Returns
    -------
    CommandRunner
        A runner that executes commands from ``repository_root``.

    Examples
    --------
    >>> runner = repository_runner(Path("/repo"))
    >>> callable(runner)
    True
    """

    def run(command: cabc.Sequence[str]) -> str:
        return _run_command(command, working_directory=repository_root)

    return run


def _run_command(command: cabc.Sequence[str], *, working_directory: Path) -> str:
    """Run one detector command from its supplied working directory."""
    try:
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed, repository-owned binary.
            list(command),
            cwd=working_directory,
            check=False,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        msg = f"{command[0]} timed out after {COMMAND_TIMEOUT_SECONDS} seconds"
        raise GateExecutionError(msg) from error
    except OSError as error:
        msg = f"cannot run {command[0]}: {error}: {INSTALL_HINT}"
        raise GateExecutionError(msg) from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        msg = f"{command[0]} exited with status {result.returncode}: {detail}"
        raise GateExecutionError(msg)
    return result.stdout
