"""Unit tests for the low-level _run_cargo_preflight helper."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from pathlib import Path

import pytest

from lading.commands import publish

from .conftest import ORIGINAL_PREFLIGHT


def test_run_cargo_preflight_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-zero command results are converted into preflight errors."""

    def failing_runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        assert cwd == tmp_path
        assert command[0] == "cargo"
        return 1, "", "boom"

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._run_cargo_preflight(
            tmp_path,
            "check",
            runner=failing_runner,
            options=publish._CargoPreflightOptions(extra_args=("--workspace",)),
        )

    message = str(excinfo.value)
    assert "cargo check" in message
    assert "boom" in message


def _run_and_record_cargo_preflight(
    workspace_root: Path,
    subcommand: typ.Literal["check", "test"],
    options: publish._CargoPreflightOptions,
) -> tuple[str, ...]:
    """Run cargo preflight with a recording runner and return the command.

    Returns
    -------
        The recorded cargo command as a tuple of strings.

    """
    recorded: list[tuple[str, ...]] = []

    def recording_runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        recorded.append(command)
        return 0, "", ""

    publish._run_cargo_preflight(
        workspace_root,
        subcommand,
        runner=recording_runner,
        options=options,
    )

    assert len(recorded) == 1, f"Expected 1 recorded command, got {len(recorded)}"
    return recorded.pop()


def test_run_cargo_preflight_honours_test_excludes(tmp_path: Path) -> None:
    """Configured test exclusions append ``--exclude`` arguments."""
    command = _run_and_record_cargo_preflight(
        tmp_path,
        "test",
        publish._CargoPreflightOptions(
            extra_args=("--workspace", "--all-targets"),
            test_excludes=(" alpha ", "", "beta"),
        ),
    )
    assert command[:2] == ("cargo", "test")
    assert command[2:4] == ("--workspace", "--all-targets")
    assert command[4:] == ("--exclude", "alpha", "--exclude", "beta")


def test_run_cargo_preflight_excludes_blank_entries(tmp_path: Path) -> None:
    """Blank test exclude entries do not emit ``--exclude`` arguments."""
    command = _run_and_record_cargo_preflight(
        tmp_path,
        "test",
        publish._CargoPreflightOptions(
            extra_args=("--workspace", "--all-targets"),
            test_excludes=["", "   ", "\t", "\n"],
        ),
    )
    assert "--exclude" not in command


def test_run_cargo_preflight_honours_unit_tests_only(tmp_path: Path) -> None:
    """The unit test flag narrows cargo test targets to lib and bins."""
    command = _run_and_record_cargo_preflight(
        tmp_path,
        "test",
        publish._CargoPreflightOptions(
            extra_args=("--workspace", "--all-targets"), unit_tests_only=True
        ),
    )
    assert command[:2] == ("cargo", "test")
    assert command[2:4] == ("--workspace", "--all-targets")
    assert command[4:6] == ("--lib", "--bins")


def test_run_cargo_preflight_defaults_when_unit_tests_only_false(
    tmp_path: Path,
) -> None:
    """When unit-tests-only is disabled, no target narrowing arguments are added."""
    command = _run_and_record_cargo_preflight(
        tmp_path,
        "test",
        publish._CargoPreflightOptions(
            extra_args=("--workspace", "--all-targets"), unit_tests_only=False
        ),
    )
    assert command[:2] == ("cargo", "test")
    assert command[2:4] == ("--workspace", "--all-targets")
    assert "--lib" not in command
    assert "--bins" not in command


def test_compiletest_diagnostic_details(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Failing cargo test pre-flight lists stderr artifacts with tail output."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    artifact = tmp_path / "ui.stderr"
    artifact.write_text("line1\nline2\nline3\n", encoding="utf-8")

    def failing_runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        return 1, f"diff at {artifact}", ""

    options = publish._CargoPreflightOptions(
        extra_args=("--workspace",),
        env={},
        diagnostics_tail_lines=2,
    )
    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._run_cargo_preflight(
            tmp_path,
            "test",
            runner=failing_runner,
            options=options,
        )

    message = str(excinfo.value)
    assert str(artifact) in message
    assert "line2" in message
    assert "line3" in message
