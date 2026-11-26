"""Pure helpers for the lading command-line interface.

These helpers parse the workspace override, label tri-state flags, record the
dispatch tokens, and configure logging. They hold no command state and are
imported by the CLI adapter and its entry point.
"""

import collections.abc as cabc
import contextvars
import logging
import os
import typing as typ
from contextlib import contextmanager
from logging import INFO as _DEFAULT_LOG_LEVEL
from pathlib import Path

from .cli_options import (
    SKIP_PREFLIGHT_ENV_VAR,
    WORKSPACE_ROOT_ENV_VAR,
    WORKSPACE_ROOT_REQUIRED_MESSAGE,
)
from .commands.publish_skip import SkipPreflightDecision, SkipPreflightSource
from .utils.log_redaction import protect_handler

# Keep the established log channel: operators and tests filter on it.
LOGGER = logging.getLogger("lading.cli")

LOG_LEVEL_ENV_VAR = "LADING_LOG_LEVEL"
_LOG_FORMAT = "%(levelname)s: %(message)s"
_LADING_HANDLER_NAME = "lading-cli-handler"
# Mirrors the literals cyclopts coerces into a bool for an env_var-backed
# option. Only consulted when the dispatch tokens are unavailable, which
# happens when the app is driven in-process rather than through main().
_ENVIRONMENT_TRUTHY_VALUES = frozenset({"1", "true", "t", "yes", "y"})
_ENVIRONMENT_FALSY_VALUES = frozenset({"0", "false", "f", "no", "n"})
_SKIP_PREFLIGHT_TOKENS = frozenset({"--skip-preflight", "--no-skip-preflight"})
_command_tokens: contextvars.ContextVar[tuple[str, ...] | None] = (
    contextvars.ContextVar("lading_cli_command_tokens", default=None)
)
_LOG_LEVEL_ALIASES: dict[str, int] = {
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}


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


def _environment_boolean(raw: str | None) -> bool | None:
    """Return the boolean ``raw`` spells, or ``None`` when it spells neither.

    Cyclopts matches these spellings case-insensitively and does not trim, so
    neither does this; a value it would reject cannot have produced the
    resolved flag.

    Returns
    -------
    bool | None
        The boolean ``raw`` spells, or ``None`` when it spells neither.
    """
    if raw is None:
        return None
    normalized = raw.lower()
    if normalized in _ENVIRONMENT_TRUTHY_VALUES:
        return True
    if normalized in _ENVIRONMENT_FALSY_VALUES:
        return False
    return None


@contextmanager
def _recorded_command_tokens(
    tokens: cabc.Sequence[str],
) -> cabc.Generator[None]:
    """Publish ``tokens`` for the duration of one dispatch.

    Cyclopts reports the value it resolved for an option but not the input it
    came from, and the command line beats ``env_var``. The tokens this
    invocation was dispatched with are the only reliable way to tell the two
    apart.
    """
    reset_token = _command_tokens.set(tuple(tokens))
    try:
        yield
    finally:
        _command_tokens.reset(reset_token)


def _mentions_skip_preflight(tokens: cabc.Sequence[str]) -> bool:
    """Return whether ``tokens`` contain either form of the skip flag."""
    return any(token.split("=", 1)[0] in _SKIP_PREFLIGHT_TOKENS for token in tokens)


def _skip_preflight_source(
    *,
    environment: cabc.Mapping[str, str],
    skip_preflight: bool,
) -> SkipPreflightSource:
    """Return the input the resolved skip value came from.

    With the dispatch tokens available, an explicit flag beats the variable,
    exactly as cyclopts resolves them. Without them the app was driven
    in-process: the variable is credited only when it spells the value
    cyclopts resolved, and anything else came from the calling code rather
    than from a command line that was never parsed.

    Returns
    -------
    SkipPreflightSource
        The command line, the environment variable, or an in-process caller.
    """
    tokens = _command_tokens.get()
    if tokens is not None:
        return (
            SkipPreflightSource.COMMAND_LINE
            if _mentions_skip_preflight(tokens)
            else SkipPreflightSource.ENVIRONMENT
        )
    from_environment = _environment_boolean(environment.get(SKIP_PREFLIGHT_ENV_VAR))
    if from_environment is skip_preflight:
        return SkipPreflightSource.ENVIRONMENT
    return SkipPreflightSource.IN_PROCESS


def _skip_preflight_override(
    *,
    skip_preflight: bool | None,
    environment: cabc.Mapping[str, str],
) -> SkipPreflightDecision | None:
    """Label an explicit skip decision with the input that supplied it.

    Returning ``None`` leaves the decision to the ``[preflight] skip``
    configuration setting, which the publish command resolves.

    Parameters
    ----------
    skip_preflight : bool | None
        The value cyclopts resolved for ``--skip-preflight``.
    environment : cabc.Mapping[str, str]
        The process environment to inspect for the backing variable.

    Returns
    -------
    SkipPreflightDecision | None
        The labelled decision, or ``None`` when no caller expressed one.

    Examples
    --------
    >>> _skip_preflight_override(skip_preflight=None, environment={}) is None
    True
    >>> _skip_preflight_override(skip_preflight=True, environment={}).source
    <SkipPreflightSource.IN_PROCESS: 'in-process'>
    """
    if skip_preflight is None:
        return None
    return SkipPreflightDecision(
        skip=skip_preflight,
        source=_skip_preflight_source(
            environment=environment, skip_preflight=skip_preflight
        ),
    )


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
        protect_handler(handler)
    else:
        handler = existing_handler
    if stream is not None and isinstance(handler, logging.StreamHandler):
        handler.stream = stream
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))


@contextmanager
def _workspace_env(value: Path) -> cabc.Generator[None]:
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
