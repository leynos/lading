"""Contract tests for the blocking Skylos dead-code lint gate."""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _skylos_configuration() -> dict[str, object]:
    """Return the repository's Skylos configuration."""
    configuration = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    tool = configuration.get("tool")
    assert isinstance(tool, dict), "Expected pyproject.toml to define [tool]."
    skylos = tool.get("skylos")
    assert isinstance(skylos, dict), "Expected pyproject.toml to define [tool.skylos]."
    return skylos


def test_skylos_configuration_is_strict_and_reasoned() -> None:
    """Keep every Skylos false-positive exception precise and documented."""
    skylos = _skylos_configuration()
    assert skylos.get("gate") == {"strict": True}

    dead_code = skylos.get("dead_code")
    assert isinstance(dead_code, dict), "Expected Skylos dead-code configuration."
    entrypoints = dead_code.get("entrypoints")
    assert isinstance(entrypoints, list), "Expected Skylos dead-code entry points."
    assert entrypoints, "Expected at least one Skylos dead-code entry point."
    assert all(
        isinstance(entrypoint, dict)
        and entrypoint.get("type") in {"function", "method"}
        and isinstance(entrypoint.get("full_name"), list)
        and entrypoint["full_name"]
        and isinstance(entrypoint.get("reason"), str)
        and entrypoint["reason"].strip()
        for entrypoint in entrypoints
    ), "Expected typed Skylos entry points with reasons."


def test_make_lint_runs_production_only_skylos_scan() -> None:
    """Keep the Skylos lint command deterministic and source-scoped."""
    make_executable = shutil.which("make")
    assert make_executable is not None, "Expected make to be available."

    result = subprocess.run(  # noqa: S603 - test invokes make without a shell
        [make_executable, "--no-print-directory", "--dry-run", "lint"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, "Expected make lint dry run to succeed."
    commands = result.stdout.splitlines()
    skylos_indexes = [
        index
        for index, line in enumerate(commands)
        if "skylos --config-file pyproject.toml" in line
    ]
    assert len(skylos_indexes) == 1, "Expected exactly one Skylos lint command."
    skylos_index = skylos_indexes[0]
    command = " ".join(commands[skylos_index : skylos_index + 2])
    assert " lading --category dead_code --gate " in command
    assert " tests" not in command
    assert "--no-upload --no-provenance --no-grep-verify" in command


def test_skylos_allow_requires_a_name() -> None:
    """Reject unnamed Skylos allow-list exceptions."""
    make_executable = shutil.which("make")
    assert make_executable is not None, "Expected make to be available."

    result = subprocess.run(  # noqa: S603 - test invokes make without a shell
        [make_executable, "--no-print-directory", "skylos-allow"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "NAME is required" in result.stderr


def test_skylos_allow_invokes_the_whitelist_subcommand_before_its_name(
    tmp_path: Path,
) -> None:
    """Keep Skylos's standalone whitelist subcommand free of scan options."""
    make_executable = shutil.which("make")
    assert make_executable is not None, "Expected make to be available."
    log_file = tmp_path / "skylos-arguments"
    recorder = tmp_path / "record-skylos-arguments"
    recorder.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$SKYLOS_ALLOW_LOG"\n',
        encoding="utf-8",
    )
    recorder.chmod(0o755)

    result = subprocess.run(  # noqa: S603 - test invokes make without a shell
        [
            make_executable,
            "--no-print-directory",
            "skylos-allow",
            "NAME=handler; touch must-not-exist",
            f"SKYLOS_COMMAND={recorder}",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "SKYLOS_ALLOW_LOG": str(log_file)},
    )

    assert result.returncode == 0, result.stderr
    assert log_file.read_text(encoding="utf-8").splitlines() == [
        "whitelist",
        "handler; touch must-not-exist",
    ]
    assert not (REPOSITORY_ROOT / "must-not-exist").exists()
