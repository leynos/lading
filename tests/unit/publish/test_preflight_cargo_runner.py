"""Unit tests for the low-level _run_cargo_preflight helper."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
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


@dc.dataclass(frozen=True)
class _RunCargoPreflightCase:
    """Parameters for a single cargo-preflight argument-construction scenario."""

    options: publish._CargoPreflightOptions
    expected_tail: tuple[str, ...]


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


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            _RunCargoPreflightCase(
                options=publish._CargoPreflightOptions(
                    extra_args=("--workspace", "--all-targets"),
                    test_excludes=(" alpha ", "", "beta"),
                ),
                expected_tail=("--exclude", "alpha", "--exclude", "beta"),
            ),
            id="test_excludes",
        ),
        pytest.param(
            _RunCargoPreflightCase(
                options=publish._CargoPreflightOptions(
                    extra_args=("--workspace", "--all-targets"),
                    test_excludes=["", "   ", "\t", "\n"],
                ),
                expected_tail=(),
            ),
            id="blank_test_excludes",
        ),
        pytest.param(
            _RunCargoPreflightCase(
                options=publish._CargoPreflightOptions(
                    extra_args=("--workspace", "--all-targets"),
                    unit_tests_only=True,
                ),
                expected_tail=("--lib", "--bins"),
            ),
            id="unit_tests_only",
        ),
        pytest.param(
            _RunCargoPreflightCase(
                options=publish._CargoPreflightOptions(
                    extra_args=("--workspace", "--all-targets"),
                    unit_tests_only=False,
                ),
                expected_tail=(),
            ),
            id="unit_tests_only_false",
        ),
    ],
)
def test_run_cargo_preflight_command_arguments(
    tmp_path: Path, scenario: _RunCargoPreflightCase
) -> None:
    """Cargo preflight constructs correct arguments for each option combination."""
    command = _run_and_record_cargo_preflight(tmp_path, "test", scenario.options)
    assert command[:4] == ("cargo", "test", "--workspace", "--all-targets"), (
        f"Unexpected command prefix: {command[:4]}"
    )
    assert command[4:] == scenario.expected_tail, (
        f"Unexpected command tail: {command[4:]!r}, expected {scenario.expected_tail!r}"
    )


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
