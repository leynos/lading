"""Command-line interface for the :mod:`lading` toolkit.

This module is the driving adapter between shell invocations and the command
implementations under :mod:`lading.commands`. It owns argument declarations,
environment-variable defaults, logging setup, workspace-root normalization,
configuration loading, and workspace metadata loading before dispatching to the
`bump` or `publish` command modules.

The CLI resolves user-interface concerns here before crossing into command
internals, but it does not coalesce optional flags against configuration
defaults. For example, :func:`bump` forwards its ``rebuild_lockfiles``
parameter as ``bool | None`` exactly as received; the command layer
(``lading.commands.bump._initialize_bump_context``) owns resolving an unset
value against ``configuration.bump.rebuild_lockfiles``. This keeps the
nullable-to-concrete defaulting in a single place rather than splitting it
across the CLI adapter and the command module.
"""

from __future__ import annotations

import collections.abc as cabc
import importlib
import logging
import os
import sys
import typing as typ
from contextlib import AbstractContextManager, contextmanager, nullcontext
from logging import INFO as _DEFAULT_LOG_LEVEL
from pathlib import Path

from cyclopts import App

from . import commands, config
from .cli_options import (
    ALLOW_UNPUBLISHED_WORKSPACE_DEPS_PARAMETER,
    DRY_RUN_PARAMETER,
    FORBID_DIRTY_PARAMETER,
    LIVE_PARAMETER,
    REBUILD_LOCKFILES_PARAMETER,
    VERSION_PARAMETER,
    WORKSPACE_PARAMETER,
    WORKSPACE_ROOT_ENV_VAR,
    WORKSPACE_ROOT_REQUIRED_MESSAGE,
)
from .cli_options import (
    AllowUnpublishedWorkspaceDepsFlag as AllowUnpublishedWorkspaceDepsFlag,
)
from .cli_options import (
    DryRunFlag as DryRunFlag,
)
from .cli_options import (
    ForbidDirtyFlag as ForbidDirtyFlag,
)
from .cli_options import (
    LiveFlag as LiveFlag,
)
from .cli_options import (
    RebuildLockfilesFlag as RebuildLockfilesFlag,
)
from .cli_options import (
    VersionArgument as VersionArgument,
)
from .cli_options import (
    WorkspaceRootOption as WorkspaceRootOption,
)
from .runtime import CommandRunner, subprocess_runner
from .utils import metrics, normalise_workspace_root
from .workspace import WorkspaceGraph, WorkspaceModelError, load_workspace
from .workspace import metadata as metadata_module

LOG_LEVEL_ENV_VAR = "LADING_LOG_LEVEL"
_LOG_FORMAT = "%(levelname)s: %(message)s"
_LADING_HANDLER_NAME = "lading-cli-handler"
_CMD_MOX_STUB_ENV = "LADING_USE_CMD_MOX_STUB"
_CMD_MOX_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})
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
LOGGER = logging.getLogger(__name__)


def _select_runner() -> CommandRunner:
    """Return the command runner selected for this CLI invocation."""
    stub_value = os.environ.get(_CMD_MOX_STUB_ENV, "")
    if stub_value.lower() in _CMD_MOX_TRUTHY_VALUES:
        try:
            module = importlib.import_module("lading.testing.cmd_mox_runner")
        except ModuleNotFoundError as exc:
            message = (
                f"{_CMD_MOX_STUB_ENV} is set, but the cmd-mox test runner "
                "could not be imported. Install the test dependencies or unset "
                f"{_CMD_MOX_STUB_ENV}."
            )
            raise SystemExit(message) from exc
        return typ.cast("CommandRunner", module.cmd_mox_runner)
    return subprocess_runner


def _validate_workspace_value(value: str) -> str:
    """Ensure ``value`` is usable as a workspace path."""
    if not value or value.startswith("-"):
        raise SystemExit(WORKSPACE_ROOT_REQUIRED_MESSAGE)
    return value


def _parse_workspace_flag(tokens: cabc.Sequence[str], index: int) -> tuple[str, int]:
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


def _resolve_allow_unpublished_workspace_deps(
    *,
    live: bool,
    allow_unpublished_workspace_deps: bool | None,
) -> bool:
    """Resolve the tri-state ``--allow-unpublished-workspace-deps`` flag."""
    if allow_unpublished_workspace_deps is not None:
        resolved_value = allow_unpublished_workspace_deps
        reason = "explicit flag"
    elif live:
        resolved_value = False
        reason = "live mode suppresses default"
    else:
        # Dry runs default to permissive so unpublished workspace members do
        # not abort a rehearsal; operators should see that decision at INFO.
        LOGGER.info(
            "Defaulting to allow unpublished workspace dependencies "
            "during dry-run publish"
        )
        resolved_value = True
        reason = "dry-run default"
    LOGGER.debug(
        "_resolve_allow_unpublished_workspace_deps: raw=%r live=%r -> resolved=%r (%s)",
        allow_unpublished_workspace_deps,
        live,
        resolved_value,
        reason,
    )
    return resolved_value


def _extract_workspace_override(
    tokens: cabc.Sequence[str],
) -> tuple[str | None, list[str]]:
    """Split ``--workspace-root`` from CLI tokens."""
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
    existing_handler = next(
        (
            existing
            for existing in root_logger.handlers
            if getattr(existing, "name", "") == _LADING_HANDLER_NAME
        ),
        None,
    )
    if existing_handler is None:
        handler = logging.StreamHandler(stream)
        handler.name = _LADING_HANDLER_NAME
        root_logger.addHandler(handler)
    else:
        handler = existing_handler
    if stream is not None and isinstance(handler, logging.StreamHandler):
        handler.stream = stream
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))


@contextmanager
def _workspace_env(value: Path) -> cabc.Iterator[None]:
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


def _dispatch_and_print(tokens: cabc.Sequence[str]) -> int:
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


def main(argv: cabc.Sequence[str] | None = None) -> int:
    """Entry point for ``python -m lading.cli``.

    Parameters
    ----------
    argv : cabc.Sequence[str] | None
        Command-line arguments to parse; defaults to :data:`sys.argv`
        without the program name when :data:`None`.

    Returns
    -------
    int
        The process exit code.

    Examples
    --------
    >>> from lading.cli import main
    >>> main(["bump", "1.2.3", "--dry-run"])  # doctest: +SKIP
    0
    """
    try:
        if argv is None:
            argv = sys.argv[1:]
        _configure_logging()
        # Flush the accumulated metrics summary when this CLI process exits.
        # Registered here in bootstrap so the lifecycle is explicit rather than
        # an import-time side effect of lading.utils.metrics.
        metrics.register_summary_atexit()
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
    except Exception as exc:  # ruff: ignore[blind-except] - fallback guard for CLI entry point
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


def _run_with_context(
    workspace_root: Path,
    runner: cabc.Callable[
        [Path, config.LadingConfig, WorkspaceGraph, CommandRunner],
        str,
    ],
    *,
    command_runner: CommandRunner | None = None,
) -> str:
    """Execute ``runner`` with configuration and workspace data."""
    active_runner = command_runner or _select_runner()
    configuration_scope: AbstractContextManager[object] = nullcontext()
    try:
        configuration = config.current_configuration()
    except config.ConfigurationNotLoadedError:
        # Freshly loaded configuration must be installed for the duration of
        # the command; an already-active configuration needs no new scope.
        configuration = config.load_configuration(workspace_root)
        configuration_scope = config.use_configuration(configuration)
    with configuration_scope, metadata_module.use_command_runner(active_runner):
        workspace_model = load_workspace(workspace_root)
        return runner(workspace_root, configuration, workspace_model, active_runner)


@app.command
def bump(
    version: typ.Annotated[str, VERSION_PARAMETER],
    workspace_root: typ.Annotated[Path | None, WORKSPACE_PARAMETER] = None,
    *,
    dry_run: typ.Annotated[bool, DRY_RUN_PARAMETER] = False,
    rebuild_lockfiles: typ.Annotated[bool | None, REBUILD_LOCKFILES_PARAMETER] = None,
) -> str:
    """Update workspace manifests to ``version``.

    Parameters
    ----------
    version : VersionArgument
        Target semantic version to write across workspace manifests.
    workspace_root : WorkspaceRootOption | None
        Optional path to the workspace root; resolved to the current
        directory when :data:`None`.
    dry_run : DryRunFlag
        When ``True``, preview manifest changes without writing files.
    rebuild_lockfiles : RebuildLockfilesFlag | None
        Tri-state flag forwarded unresolved to the bump command, which owns
        defaulting an unset value against the configuration.

    Returns
    -------
    str
        The rendered summary of the bump operation.

    Examples
    --------
    >>> from lading.cli import bump
    >>> summary = bump("1.2.3", dry_run=True)  # doctest: +SKIP
    >>> "Dry run; would update version to 1.2.3 in" in summary  # doctest: +SKIP
    True
    """
    resolved = normalise_workspace_root(workspace_root)
    return _run_with_context(
        resolved,
        lambda root, configuration, workspace, command_runner: commands.bump.run(
            root,
            version,
            options=commands.bump.BumpOptions(
                dry_run=dry_run,
                # Forwarded unresolved: default-resolution against the
                # configuration is the bump command's responsibility
                # (_initialize_bump_context), not the CLI adapter's.
                rebuild_lockfiles=rebuild_lockfiles,
                configuration=configuration,
                workspace=workspace,
                lockfile_repository=commands.bump_lockfiles.CargoLockfileRepository(
                    runner=command_runner
                ),
            ),
        ),
    )


@app.command
def publish(
    workspace_root: typ.Annotated[Path | None, WORKSPACE_PARAMETER] = None,
    *,
    forbid_dirty: typ.Annotated[bool, FORBID_DIRTY_PARAMETER] = False,
    live: typ.Annotated[bool, LIVE_PARAMETER] = False,
    allow_unpublished_workspace_deps: typ.Annotated[
        bool | None, ALLOW_UNPUBLISHED_WORKSPACE_DEPS_PARAMETER
    ] = None,
) -> str:
    """Run pre-flight checks, package crates, and execute cargo publish.

    The command performs pre-flight validation, stages the workspace, runs
    ``cargo package`` for each publishable crate, and then executes ``cargo
    publish`` (dry-run by default, live when ``--live`` is supplied).

    Parameters
    ----------
    workspace_root : WorkspaceRootOption | None
        Optional path to the workspace root; resolved to the current
        directory when :data:`None`.
    forbid_dirty : ForbidDirtyFlag
        When ``True``, require a clean working tree before pre-flight checks.
    live : LiveFlag
        When ``True``, run ``cargo publish`` without ``--dry-run``.
    allow_unpublished_workspace_deps : AllowUnpublishedWorkspaceDepsFlag
        Tri-state override for unpublished sibling workspace dependencies;
        resolved against the publish mode when omitted.

    Returns
    -------
    str
        The rendered summary of the publish operation.

    Examples
    --------
    The rendered summary is a formatted publication plan followed by a
    blank line and staging-summary lines.

    >>> from lading.cli import publish
    >>> summary = publish(live=False)  # doctest: +SKIP
    >>> "Staged workspace at:" in summary  # doctest: +SKIP
    True
    >>> readmes = "Workspace READMEs are handled by lading bump."
    >>> readmes in summary  # doctest: +SKIP
    True
    """
    resolved = normalise_workspace_root(workspace_root)
    return _run_with_context(
        resolved,
        lambda root, configuration, workspace, command_runner: commands.publish.run(
            root,
            configuration,
            workspace,
            options=commands.publish.PublishOptions(
                allow_dirty=not forbid_dirty,
                live=live,
                allow_unpublished_workspace_deps=(
                    _resolve_allow_unpublished_workspace_deps(
                        live=live,
                        allow_unpublished_workspace_deps=(
                            allow_unpublished_workspace_deps
                        ),
                    )
                ),
                command_runner=command_runner,
            ),
        ),
    )


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    raise SystemExit(main())
