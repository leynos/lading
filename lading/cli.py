"""Command-line interface for the :mod:`lading` toolkit."""

from __future__ import annotations

import logging
import os
import re
import sys
import typing as typ
from contextlib import contextmanager
from pathlib import Path

from cyclopts import App, Parameter

from . import commands, config
from .utils import normalise_workspace_root
from .workspace import WorkspaceGraph, WorkspaceModelError, load_workspace

WORKSPACE_ROOT_ENV_VAR = "LADING_WORKSPACE_ROOT"
WORKSPACE_ROOT_REQUIRED_MESSAGE = "--workspace-root requires a value"
_WORKSPACE_PARAMETER = Parameter(
    name="workspace-root",
    env_var=WORKSPACE_ROOT_ENV_VAR,
    help="Path to the Rust workspace root.",
)
WorkspaceRootOption = typ.Annotated[Path, _WORKSPACE_PARAMETER]

_VERSION_PARAMETER = Parameter(
    help="Target semantic version (e.g., 1.2.3) to set across workspace manifests.",
)
VersionArgument = typ.Annotated[str, _VERSION_PARAMETER]

_DRY_RUN_PARAMETER = Parameter(
    name="dry-run",
    help="Preview manifest changes without writing files.",
)
DryRunFlag = typ.Annotated[bool, _DRY_RUN_PARAMETER]

_LIVE_PARAMETER = Parameter(
    name="live",
    help="Run cargo publish without --dry-run; default behaviour is dry-run.",
)
LiveFlag = typ.Annotated[bool, _LIVE_PARAMETER]

_FORBID_DIRTY_PARAMETER = Parameter(
    name="forbid-dirty",
    help=("Require a clean working tree before running publish pre-flight checks."),
)
ForbidDirtyFlag = typ.Annotated[bool, _FORBID_DIRTY_PARAMETER]

LOG_LEVEL_ENV_VAR = "LADING_LOG_LEVEL"
_DEFAULT_LOG_LEVEL = logging.INFO
_LOG_FORMAT = "%(levelname)s: %(message)s"
_LADING_HANDLER_NAME = "lading-cli-handler"
_LOG_LEVEL_ALIASES: dict[str, int] = {
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

app = App(help="Manage Rust workspaces with the lading toolkit.")


def _validate_workspace_value(value: str) -> str:
    """Ensure ``value`` is usable as a workspace path."""
    if not value or value.startswith("-"):
        raise SystemExit(WORKSPACE_ROOT_REQUIRED_MESSAGE)
    return value


def _parse_workspace_flag(tokens: typ.Sequence[str], index: int) -> tuple[str, int]:
    """Parse ``--workspace-root <path>`` form starting at ``index``."""
    try:
        candidate = tokens[index + 1]
    except IndexError as err:
        raise SystemExit(WORKSPACE_ROOT_REQUIRED_MESSAGE) from err
    workspace = _validate_workspace_value(candidate)
    return workspace, index + 2


def _parse_workspace_equals(argument: str, index: int) -> tuple[str, int]:
    """Parse ``--workspace-root=<path>`` form for ``argument``."""
    candidate = argument.partition("=")[2]
    workspace = _validate_workspace_value(candidate)
    return workspace, index + 1


def _extract_workspace_override(
    tokens: typ.Sequence[str],
) -> tuple[str | None, list[str]]:
    """Split ``--workspace-root`` from CLI tokens.

    The flag can appear in either ``--workspace-root <path>`` or
    ``--workspace-root=<path>`` form. The last occurrence wins, matching
    common CLI conventions. The returned token list can be passed directly
    to :func:`cyclopts.App.__call__`.
    """
    workspace: str | None = None
    remainder: list[str] = []
    index = 0
    while index < len(tokens):
        current_argument = tokens[index]
        if current_argument == "--workspace-root":
            workspace, index = _parse_workspace_flag(tokens, index)
            continue
        if current_argument.startswith("--workspace-root="):
            workspace, index = _parse_workspace_equals(current_argument, index)
            continue
        remainder.append(current_argument)
        index += 1
    return workspace, remainder


def _resolve_log_level(value: str | None) -> int:
    """Return the configured log level or :data:`_DEFAULT_LOG_LEVEL`."""
    if value is None:
        return _DEFAULT_LOG_LEVEL
    candidate = value.strip()
    if not candidate:
        return _DEFAULT_LOG_LEVEL
    level = _LOG_LEVEL_ALIASES.get(candidate.upper())
    if level is None:
        choices = ", ".join(sorted(_LOG_LEVEL_ALIASES))
        message = (
            f"Invalid {LOG_LEVEL_ENV_VAR} value {value!r}; expected one of: {choices}"
        )
        raise SystemExit(message)
    return level


def _configure_logging(stream: typ.TextIO | None = None) -> None:
    """Configure root logging so command execution is visible."""
    level = _resolve_log_level(os.environ.get(LOG_LEVEL_ENV_VAR))
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    handler = next(
        (
            existing
            for existing in root_logger.handlers
            if getattr(existing, "name", "") == _LADING_HANDLER_NAME
        ),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler(stream)
        handler.name = _LADING_HANDLER_NAME
        root_logger.addHandler(handler)
    elif stream is not None:
        handler.stream = stream
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))


@contextmanager
def _workspace_env(value: Path) -> typ.Iterator[None]:
    """Temporarily set :data:`WORKSPACE_ROOT_ENV_VAR` to ``value``."""
    previous = os.environ.get(WORKSPACE_ROOT_ENV_VAR)
    os.environ[WORKSPACE_ROOT_ENV_VAR] = str(value)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(WORKSPACE_ROOT_ENV_VAR, None)
        else:
            os.environ[WORKSPACE_ROOT_ENV_VAR] = previous


def _dispatch_and_print(tokens: typ.Sequence[str]) -> int:
    """Execute the Cyclopts app and print command results."""
    try:
        result = app(tokens)
    except SystemExit as err:
        code = err.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    if isinstance(result, int):
        return result
    if result is not None:
        print(result)
    return 0


def main(argv: typ.Sequence[str] | None = None) -> int:
    """Entry point for ``python -m lading.cli``."""
    try:
        if argv is None:
            argv = sys.argv[1:]
        _configure_logging()
        workspace_override, remaining = _extract_workspace_override(list(argv))
        workspace_root = normalise_workspace_root(workspace_override)
        if not remaining:
            _dispatch_and_print(remaining)  # Print usage message
            return 2  # Standard exit code for missing subcommand
        previous_config = app.config
        config_loader = config.build_loader(workspace_root)
        try:
            configuration = config.load_from_loader(config_loader)
        except config.ConfigurationError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 1
        app.config = (config_loader,)
        try:
            with (
                _workspace_env(workspace_root),
                config.use_configuration(configuration),
            ):
                try:
                    return _dispatch_and_print(remaining)
                except WorkspaceModelError as exc:
                    print(f"Workspace error: {exc}", file=sys.stderr)
                    return 1
        finally:
            app.config = previous_config
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - fallback guard for CLI entry point
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


def _run_with_context(
    workspace_root: Path,
    runner: typ.Callable[
        [Path, config.LadingConfig | None, WorkspaceGraph | None],
        str,
    ],
) -> str:
    """Execute ``runner`` with configuration and workspace data."""
    try:
        configuration = config.current_configuration()
    except config.ConfigurationNotLoadedError:
        configuration = config.load_configuration(workspace_root)
        workspace_model = load_workspace(workspace_root)
        with config.use_configuration(configuration):
            return runner(workspace_root, configuration, workspace_model)
    workspace_model = load_workspace(workspace_root)
    return runner(workspace_root, configuration, workspace_model)


_VERSION_PATTERN = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def _validate_version_argument(version: str) -> None:
    """Ensure ``version`` matches the semantic version pattern."""
    if not _VERSION_PATTERN.fullmatch(version):
        message = (
            "Invalid version argument "
            f"{version!r}. Expected semantic version in the form "
            "<major>.<minor>.<patch> with optional pre-release/build segments."
        )
        raise SystemExit(message)


@app.command
def bump(
    version: VersionArgument,
    workspace_root: WorkspaceRootOption | None = None,
    *,
    dry_run: DryRunFlag = False,
) -> str:
    """Update workspace manifests to ``version``."""
    _validate_version_argument(version)
    resolved = normalise_workspace_root(workspace_root)
    return _run_with_context(
        resolved,
        lambda root, configuration, workspace: commands.bump.run(
            root,
            version,
            options=commands.bump.BumpOptions(
                dry_run=dry_run,
                configuration=configuration,
                workspace=workspace,
            ),
        ),
    )


@app.command
def publish(
    workspace_root: WorkspaceRootOption | None = None,
    *,
    forbid_dirty: ForbidDirtyFlag = False,
    live: LiveFlag = False,
) -> str:
    """Execute publish planning with pre-flight checks."""
    resolved = normalise_workspace_root(workspace_root)
    return _run_with_context(
        resolved,
        lambda root, configuration, workspace: commands.publish.run(
            root,
            configuration,
            workspace,
            options=commands.publish.PublishOptions(
                allow_dirty=not forbid_dirty,
                live=live,
            ),
        ),
    )


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    raise SystemExit(main())
