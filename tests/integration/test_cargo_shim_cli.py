"""Integration tests for the publish-check cargo shim CLI."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "publish-check" / "bin" / "cargo"
)


def _make_fake_cargo(tmp_path: Path) -> Path:
    """Create an executable stub that echoes its arguments as JSON."""
    executable = tmp_path / "cargo"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "json.dump(sys.argv[1:], sys.stdout)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def test_cli_inserts_flag_before_separator(tmp_path: Path) -> None:
    """Cargo shim should add the feature flag before a separator."""
    _make_fake_cargo(tmp_path)
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(tmp_path), env.get("PATH", "")])
    process = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - test executes a fixed shell-free argv list
        [str(SCRIPT_PATH), "test", "--", "--test-threads", "1"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert process.returncode == 0, process.stderr
    stdout = process.stdout.strip()
    assert stdout, "cargo shim produced no output"
    args = json.loads(stdout)
    assert args == ["test", "--all-features", "--", "--test-threads", "1"], args


def test_cli_forwards_additional_arguments(tmp_path: Path) -> None:
    """Cargo shim should retain extra toolchain and command arguments."""
    _make_fake_cargo(tmp_path)
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(tmp_path), env.get("PATH", "")])
    process = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - test executes a fixed shell-free argv list
        [str(SCRIPT_PATH), "+nightly", "bench", "--", "foo"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert process.returncode == 0, process.stderr
    args = json.loads(process.stdout.strip())
    assert args == ["+nightly", "bench", "--all-features", "--", "foo"], args


def test_cli_preserves_global_option_values(tmp_path: Path) -> None:
    """Cargo shim should preserve global option values while adding features."""
    _make_fake_cargo(tmp_path)
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(tmp_path), env.get("PATH", "")])
    process = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - test executes a fixed shell-free argv list
        [str(SCRIPT_PATH), "--target-dir", "ci-target", "test"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert process.returncode == 0, process.stderr
    stdout = process.stdout.strip()
    assert stdout, "cargo shim produced no output"
    args = json.loads(stdout)
    assert args == ["--target-dir", "ci-target", "test", "--all-features"], args
