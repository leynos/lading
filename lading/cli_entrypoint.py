"""Process entry point for the lading command-line interface.

The module owns argument parsing for the workspace override, logging setup,
configuration loading, and the translation of domain failures into exit codes
and diagnostics. Command adapters live in :mod:`lading.cli`.
"""

import collections.abc as cabc
import sys

from . import config
from .cli_app import app
from .cli_environment import hidden_for, print_error
from .cli_helpers import (
    _configure_logging,
    _extract_workspace_override,
    _recorded_command_tokens,
    _workspace_env,
)
from .commands import publish_staging
from .commands.bump_manifest_rewrite_model import ManifestRewriteError
from .utils import metrics, normalize_workspace_root
from .utils.confinement import BumpPreflightError
from .utils.secret_scope import redact_active
from .workspace import WorkspaceModelError


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
        print_error(str(code))
        return 1
    match result:
        case None:
            status = 0
        case bool():
            # bool subclasses int, so it must be matched first. Cyclopts maps
            # a bool result to success when true and failure when false.
            status = 0 if result else 1
        case int():
            status = result
        case _:
            print(redact_active(str(result)))
            status = 0
    return status


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
    tokens = list(sys.argv[1:] if argv is None else argv)
    with hidden_for(tokens):
        return _main(tokens)


def _main(argv: cabc.Sequence[str]) -> int:
    """Run the command line for ``argv``; see :func:`main`.

    Returns
    -------
    int
        The process exit code.
    """
    try:
        _configure_logging()
        # Flush the accumulated metrics summary when this CLI process exits.
        # Registered here in bootstrap so the lifecycle is explicit rather than
        # an import-time side effect of lading.utils.metrics.
        metrics.register_summary_atexit()
        # Remove staged workspace copies if this process is terminated;
        # registered here for the same reason, and because a SIGTERM never
        # reaches an atexit hook (issue #269).
        publish_staging.install_termination_cleanup()
        workspace_override, remaining = _extract_workspace_override(list(argv))
        workspace_root = normalize_workspace_root(workspace_override)
        if not remaining:
            _dispatch_and_print(remaining)  # Print usage message
            return 2  # Standard exit code for missing subcommand
        previous_config = app.config
        config_loader = config.build_loader(workspace_root)
        try:
            configuration = config.load_from_loader(config_loader)
        except config.ConfigurationError as exc:
            print_error(f"Configuration error: {exc}")
            return 1
        app.config = (config_loader,)
        try:
            with (
                _workspace_env(workspace_root),
                config.use_configuration(configuration),
                _recorded_command_tokens(remaining),
            ):
                return _dispatch_with_diagnostics(remaining)
        finally:
            app.config = previous_config
    except KeyboardInterrupt:
        print_error("\nOperation cancelled by user.")
        return 130
    except Exception as exc:  # ruff: ignore[blind-except] - fallback guard for CLI entry point
        print_error(f"Unexpected error: {exc}")
        return 1


def _manifest_rewrite_detail(error: ManifestRewriteError) -> str:
    """Return path and write-progress context for a rewrite failure."""
    parts: list[str] = []
    if error.failed_path is not None:
        parts.append(f"path={error.failed_path}")
    if error.planned_writes:
        parts.append(
            f"completed={error.completed_writes} planned={error.planned_writes}"
        )
    return "" if not parts else f" ({', '.join(parts)})"


def _dispatch_with_diagnostics(remaining: cabc.Sequence[str]) -> int:
    """Dispatch ``remaining`` and translate expected domain failures.

    Parameters
    ----------
    remaining : cabc.Sequence[str]
        Command-line tokens after the workspace override has been removed.

    Returns
    -------
    int
        The process exit code. A rejected bump write plan exits 1 with a
        ``Preflight error:`` diagnostic naming the offending input; a workspace
        model failure keeps its established ``Workspace error:`` diagnostic.
    """
    try:
        return _dispatch_and_print(remaining)
    except ManifestRewriteError as exc:
        # A configured fixture rewrite failed before or during the commit;
        # the message names the offending manifest or pattern, and a write
        # failure also reports its path and progress.
        detail = _manifest_rewrite_detail(exc)
        print(f"Manifest rewrite error: {exc}{detail}", file=sys.stderr)
        return 1
    except BumpPreflightError as exc:
        # A rejected write plan is a validation failure, not an unexpected
        # error: it exits before any workspace file was touched (issue #409).
        print_error(f"Preflight error: {exc}")
        return 1
    except WorkspaceModelError as exc:
        print_error(f"Workspace error: {exc}")
        return 1
