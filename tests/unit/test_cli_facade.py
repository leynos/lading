"""Unit tests for the ``lading.cli`` facade re-exports and helpers.

Covers the public names ``lading.cli`` re-exports, the Cyclopts parameter
helpers (``--log-level``, ``--workspace-root``), and the logging handler the
CLI installs.
"""

import collections.abc as cabc
import dataclasses as dc
import io
import logging
import typing as typ
from pathlib import Path

import pytest

from lading import cli, cli_options
from lading.utils import normalize_workspace_root
from tests.helpers.cli_workspace import preserve_root_logger
from tests.helpers.cwd import chdir_for_test

if typ.TYPE_CHECKING:
    from types import ModuleType


@dc.dataclass(frozen=True, slots=True)
class CommandDispatchCase:
    """Test case for command dispatch validation."""

    command_module: ModuleType
    command_name: str
    return_value: str
    cli_args: list[str]
    expected_version: str | None = None


def test_cli_reexports_public_annotation_aliases() -> None:
    """Public CLI annotation aliases should remain available at both paths."""
    alias_names = (
        "AllowUnpublishedWorkspaceDepsFlag",
        "DryRunFlag",
        "ForbidDirtyFlag",
        "LiveFlag",
        "PublishFlags",
        "RebuildLockfilesFlag",
        "SccacheStatsFlag",
        "SccacheStatsJsonOption",
        "SkipPreflightFlag",
        "VersionArgument",
        "WorkspaceRootOption",
    )
    for alias_name in alias_names:
        assert alias_name in cli_options.__all__, f"{alias_name} is not public"
        assert getattr(cli, alias_name) is getattr(cli_options, alias_name), (
            f"lading.cli does not re-export {alias_name}"
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, logging.INFO),
        ("", logging.INFO),
        (" info ", logging.INFO),
        ("DEBUG", logging.DEBUG),
        ("warning", logging.WARNING),
    ],
)
def test_resolve_log_level_parsing(value: str | None, expected: int) -> None:
    """``_resolve_log_level`` should normalize supported variants."""
    assert cli._resolve_log_level(value) == expected, (
        f"{value!r} should resolve to log level {expected}"
    )


def test_resolve_log_level_rejects_unknown_value() -> None:
    """Unknown log levels should raise ``SystemExit``."""
    with pytest.raises(SystemExit) as excinfo:
        cli._resolve_log_level("not-a-level")
    message = str(excinfo.value)
    assert "Invalid" in message, "the error should say the level is invalid"
    assert cli.LOG_LEVEL_ENV_VAR in message, (
        "the error should name the log level environment variable"
    )


def test_configure_logging_installs_named_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_configure_logging`` should attach a reusable root handler."""
    stream = io.StringIO()
    with preserve_root_logger() as root_logger:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
        monkeypatch.setenv(cli.LOG_LEVEL_ENV_VAR, "DEBUG")
        cli._configure_logging(stream)

        handlers = [
            handler
            for handler in root_logger.handlers
            if getattr(handler, "name", "") == cli._LADING_HANDLER_NAME
        ]
        assert len(handlers) == 1, "exactly one root handler should be installed"

        logging.getLogger("lading").debug("probe message")
        assert "probe message" in stream.getvalue(), (
            "the installed handler should write to the stream"
        )


@pytest.mark.parametrize(
    ("tokens", "expected_workspace", "expected_remaining"),
    [
        ([], None, []),
        (["bump"], None, ["bump"]),
        (["--workspace-root", "workspace", "publish"], "workspace", ["publish"]),
        (["--workspace-root=workspace", "bump"], "workspace", ["bump"]),
        (["bump", "--workspace-root", "workspace"], "workspace", ["bump"]),
        (
            [
                "--workspace-root=first",
                "--workspace-root",
                "second",
                "publish",
            ],
            "second",
            ["publish"],
        ),
    ],
)
def test_extract_workspace_override(
    tokens: cabc.Sequence[str],
    expected_workspace: str | None,
    expected_remaining: list[str],
) -> None:
    """Extract workspace overrides from CLI tokens."""
    workspace, remaining = cli._extract_workspace_override(tokens)
    assert workspace == expected_workspace, (
        f"expected workspace {expected_workspace!r}, got {workspace!r}"
    )
    assert remaining == expected_remaining, (
        f"expected remaining tokens {expected_remaining!r}, got {remaining!r}"
    )


def test_extract_workspace_override_requires_value() -> None:
    """Require a value whenever ``--workspace-root`` appears."""
    with pytest.raises(SystemExit):
        cli._extract_workspace_override(["--workspace-root"])


def test_extract_workspace_override_requires_value_equals() -> None:
    """Reject ``--workspace-root=`` when no value is supplied."""
    with pytest.raises(SystemExit):
        cli._extract_workspace_override(["--workspace-root="])


def test_normalize_workspace_root_defaults_to_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Default workspace resolution uses the current working directory."""
    chdir_for_test(monkeypatch, tmp_path)
    resolved = normalize_workspace_root(None)
    assert resolved == tmp_path.resolve(), (
        "an omitted workspace root should resolve to the current directory"
    )
