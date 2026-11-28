"""Command-line interface for the :mod:`lading` toolkit.

This module is the driving adapter between shell invocations and the command
implementations under :mod:`lading.commands`. It owns argument declarations,
environment-variable defaults, logging setup, workspace-root normalization,
configuration loading, and workspace metadata loading before dispatching to the
`bump` or `publish` command modules.

The CLI resolves user-interface concerns here before crossing into command
internals, but it does not coalesce optional flags against configuration
defaults. For example, :func:`bump` takes a ``BumpFlags`` bundle and forwards
``rebuild_lockfiles`` as ``bool | None`` exactly as received; the command layer
(``lading.commands.bump._initialize_bump_context``) owns resolving an unset
value against ``configuration.bump.rebuild_lockfiles``. This keeps the
nullable-to-concrete defaulting in a single place rather than splitting it
across the CLI adapter and the command module.
"""

import collections.abc as cabc
import importlib
import os
import typing as typ
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from cyclopts import Parameter

from . import commands, config
from .cli_app import app as _shared_app
from .cli_app import create_app
from .cli_entrypoint import _dispatch_and_print as _dispatch_and_print
from .cli_entrypoint import _dispatch_with_diagnostics as _dispatch_with_diagnostics
from .cli_entrypoint import main as main
from .cli_helpers import _LADING_HANDLER_NAME as _LADING_HANDLER_NAME
from .cli_helpers import LOG_LEVEL_ENV_VAR as LOG_LEVEL_ENV_VAR
from .cli_helpers import _configure_logging as _configure_logging
from .cli_helpers import _environment_boolean as _environment_boolean
from .cli_helpers import _extract_workspace_override as _extract_workspace_override
from .cli_helpers import (
    _resolve_allow_unpublished_workspace_deps,
    _skip_preflight_override,
)
from .cli_helpers import _resolve_log_level as _resolve_log_level
from .cli_helpers import _workspace_env as _workspace_env
from .cli_options import (
    CLEAN_LOCATION_PARAMETER,
    CLEAN_REMOVE_PARAMETER,
    VERSION_PARAMETER,
    WORKSPACE_PARAMETER,
    BumpFlags,
    PublishFlags,
)
from .cli_options import (
    WORKSPACE_ROOT_ENV_VAR as WORKSPACE_ROOT_ENV_VAR,
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
    SccacheStatsFlag as SccacheStatsFlag,
)
from .cli_options import (
    SccacheStatsJsonOption as SccacheStatsJsonOption,
)
from .cli_options import (
    SkipPreflightFlag as SkipPreflightFlag,
)
from .cli_options import (
    VersionArgument as VersionArgument,
)
from .cli_options import (
    WorkspaceRootOption as WorkspaceRootOption,
)
from .commands import publish_staging as publish_staging
from .runtime import CommandRunner, subprocess_runner
from .utils import normalize_workspace_root
from .utils.confinement import BumpPreflightError as BumpPreflightError
from .workspace import (
    WorkspaceGraph,
    load_bump_workspace,
    load_workspace,
)
from .workspace import (
    WorkspaceModelError as WorkspaceModelError,
)
from .workspace import metadata as metadata_module

_CMD_MOX_STUB_ENV = "LADING_USE_CMD_MOX_STUB"
_CMD_MOX_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

# ``python -m lading.cli`` executes this module a second time as ``__main__``
# after ``lading/__init__`` imported it. Registering the command decorators on
# the shared application twice raises a collision, so the second execution
# registers on a throwaway application; dispatch still uses the shared one.
app = _shared_app if __name__ == "lading.cli" else create_app()


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


type WorkspaceLoader = cabc.Callable[[Path], WorkspaceGraph]


def _run_with_context(
    workspace_root: Path,
    runner: cabc.Callable[
        [Path, config.LadingConfig, WorkspaceGraph, CommandRunner],
        str,
    ],
    *,
    command_runner: CommandRunner | None = None,
    workspace_loader: WorkspaceLoader | None = None,
) -> str:
    """Execute ``runner`` with configuration and workspace data.

    Parameters
    ----------
    workspace_root : Path
        Canonical workspace root.
    runner : cabc.Callable[[Path, LadingConfig, WorkspaceGraph, CommandRunner], str]
        Command body invoked with the loaded configuration and workspace.
    command_runner : CommandRunner | None
        Explicit runner; ``None`` selects the CLI runner.
    workspace_loader : WorkspaceLoader | None
        Workspace discovery function. ``None`` uses :func:`load_workspace`;
        bump passes the confined, read-only loader.

    Returns
    -------
    str
        The command's rendered summary.
    """
    active_runner = command_runner or _select_runner()
    configuration_scope: AbstractContextManager[object] = nullcontext()
    try:
        configuration = config.current_configuration()
    except config.ConfigurationNotLoadedError:
        # Freshly loaded configuration must be installed for the duration of
        # the command; an already-active configuration needs no new scope.
        configuration = config.load_configuration(workspace_root)
        configuration_scope = config.use_configuration(configuration)
    loader = load_workspace if workspace_loader is None else workspace_loader
    with configuration_scope, metadata_module.use_command_runner(active_runner):
        workspace_model = loader(workspace_root)
        return runner(workspace_root, configuration, workspace_model, active_runner)


_DEFAULT_BUMP_FLAGS = BumpFlags()


@app.command
def bump(
    version: typ.Annotated[str, VERSION_PARAMETER],
    workspace_root: typ.Annotated[Path | None, WORKSPACE_PARAMETER] = None,
    *,
    flags: typ.Annotated[BumpFlags, Parameter(name="*")] = _DEFAULT_BUMP_FLAGS,
) -> str:
    """Update workspace manifests to ``version``.

    Parameters
    ----------
    version : str
        Target semantic version to write across workspace manifests.
    workspace_root : Path | None
        Optional path to the workspace root; resolved to the current
        directory when :data:`None`.
    flags : BumpFlags
        Flag bundle: ``dry_run`` previews without writing, ``diff`` appends a
        unified diff of each documentation file that would change (requires
        ``dry_run``), and ``rebuild_lockfiles`` is the nullable lockfile
        override the bump command resolves against the configuration.

    Returns
    -------
    str
        The rendered summary of the bump operation.

    Examples
    --------
    >>> from lading.cli import BumpFlags, bump
    >>> summary = bump("1.2.3", flags=BumpFlags(dry_run=True))  # doctest: +SKIP
    >>> "Dry run; would update version to 1.2.3 in" in summary  # doctest: +SKIP
    True
    """
    # Reject an impossible flag combination before discovery, so a directory
    # that is not a workspace still reports the flag diagnostic.
    commands.bump._validate_bump_options(
        commands.bump.BumpOptions(dry_run=flags.dry_run, diff=flags.diff)
    )
    resolved = normalize_workspace_root(workspace_root)
    return _run_with_context(
        resolved,
        lambda root, configuration, workspace, command_runner: commands.bump.run(
            root,
            version,
            options=commands.bump.BumpOptions(
                dry_run=flags.dry_run,
                diff=flags.diff,
                # Forwarded unresolved: default-resolution against the
                # configuration is the bump command's responsibility
                # (_initialize_bump_context), not the CLI adapter's.
                rebuild_lockfiles=flags.rebuild_lockfiles,
                configuration=configuration,
                workspace=workspace,
                lockfile_repository=commands.bump_lockfiles.CargoLockfileRepository(
                    runner=command_runner
                ),
            ),
        ),
        workspace_loader=load_bump_workspace,
    )


@app.command
def clean(
    *,
    location: typ.Annotated[Path | None, CLEAN_LOCATION_PARAMETER] = None,
    remove: typ.Annotated[bool, CLEAN_REMOVE_PARAMETER] = False,
) -> str:
    """Report, and optionally remove, staging copies left by earlier publishes.

    Publishes before issue #269 never removed their staged workspace copy.
    This finds what they left and, when asked, deletes it. Reporting is the
    default, and only directories named with the staging prefix, directly
    under the searched location, are ever considered.

    Run it when no publish is in progress: a staging copy belonging to a
    running publish looks exactly like one that was abandoned.

    Parameters
    ----------
    location : Path | None
        Directory to search; defaults to the system temporary directory.
    remove : bool
        Whether to delete what is found rather than only report it.

    Returns
    -------
    str
        The rendered summary of what was found, and of what was removed.

    Examples
    --------
    >>> from lading.cli import clean
    >>> summary = clean()  # doctest: +SKIP
    >>> summary.startswith(("Found", "No staging"))  # doctest: +SKIP
    True
    """
    return commands.clean.run(
        options=commands.clean.CleanOptions(location=location, remove=remove)
    )


_DEFAULT_PUBLISH_FLAGS = PublishFlags()


@app.command
def publish(
    workspace_root: typ.Annotated[Path | None, WORKSPACE_PARAMETER] = None,
    *,
    flags: typ.Annotated[PublishFlags, Parameter(name="*")] = _DEFAULT_PUBLISH_FLAGS,
) -> str:
    """Run pre-flight checks, package crates, and execute cargo publish.

    The command performs pre-flight validation, stages the workspace, runs
    ``cargo package`` for each publishable crate, and then executes ``cargo
    publish`` (dry-run by default, live when ``--live`` is supplied).

    Parameters
    ----------
    workspace_root : Path | None
        Optional path to the workspace root; resolved to the current
        directory when :data:`None`.
    flags : PublishFlags
        The publish flags, each surfaced by Cyclopts as its own option:
        ``--forbid-dirty``, ``--live``,
        ``--allow-unpublished-workspace-deps`` (tri-state, resolved against
        the publish mode when omitted), ``--skip-preflight`` (tri-state,
        labelled with its source here and resolved against
        ``[preflight] skip`` by the publish command), ``--sccache-stats``,
        ``--sccache-stats-json`` (issue #252; a report path implies the
        measurement, resolved by the publish command), and
        ``--keep-staging``, which retains the staged workspace copy and logs
        where it was left. The copy is removed when the publish ends unless
        that flag is given (issue #269).

    Returns
    -------
    str
        The rendered summary of the publish operation.

    Examples
    --------
    >>> from lading.cli import publish
    >>> summary = publish(flags=PublishFlags(live=False))  # doctest: +SKIP
    >>> "Staged workspace at:" in summary  # doctest: +SKIP
    True
    """
    resolved = normalize_workspace_root(workspace_root)
    return _run_with_context(
        resolved,
        lambda root, configuration, workspace, command_runner: commands.publish.run(
            root,
            configuration,
            workspace,
            options=_publish_options(flags, command_runner),
        ),
    )


def _publish_options(
    flags: PublishFlags, command_runner: CommandRunner
) -> commands.publish.PublishOptions:
    """Translate the CLI flag bundle into the publish command's options."""
    return commands.publish.PublishOptions(
        allow_dirty=not flags.forbid_dirty,
        live=flags.live,
        allow_unpublished_workspace_deps=_resolve_allow_unpublished_workspace_deps(
            live=flags.live,
            allow_unpublished_workspace_deps=flags.allow_unpublished_workspace_deps,
        ),
        # Labelled, not resolved: only the CLI can tell the flag from its
        # environment variable, while resolving an absent value against
        # `[preflight] skip` stays with the publish command.
        skip_preflight=_skip_preflight_override(
            skip_preflight=flags.skip_preflight, environment=os.environ
        ),
        # Forwarded unresolved: a report path implying the measurement is the
        # publish command's decision, so library callers behave the same.
        sccache_stats=flags.sccache_stats,
        sccache_stats_json=flags.sccache_stats_json,
        # --keep-staging is the negative of the option it sets: retaining the
        # staged copy is the exception, so the flag names the exception.
        cleanup=not flags.keep_staging,
        command_runner=command_runner,
    )


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    raise SystemExit(main())
