"""End-to-end contract for lading's process entry points.

The unit cases call :func:`lading.cli.main` and the Cyclopts app in process.
A user reaches the same code through either ``python -m lading.cli`` or the
installed ``lading`` script, and both hand the return value to
:func:`sys.exit`. These tests pin the half of the boundary a user observes:
a rejected invocation exits non-zero, writes its diagnostic to stderr, and
leaves stdout empty so a pipeline reading it sees nothing that looks like
output.

Cyclopts 4 moved diagnostics from stdout to its error console. Asserting the
process streams here means that change cannot silently turn a machine-read
stdout into an error message.
"""

import importlib.metadata
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from tests.helpers.cli_diagnostics import normalize_diagnostic

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_TIMEOUT_SECONDS = 120


def _console_script() -> Path | None:
    """Return the installed ``lading`` script, or :data:`None` if absent."""
    entry_points = importlib.metadata.entry_points(
        group="console_scripts", name="lading"
    )
    if not entry_points:
        return None
    script = Path(sysconfig.get_path("scripts")) / "lading"
    return script if script.is_file() else None


@pytest.fixture(params=["module", "console-script"])
def entry_point(request: pytest.FixtureRequest) -> list[str]:
    """Return the argv prefix that starts one supported entry point."""
    if request.param == "module":
        return [sys.executable, "-m", "lading.cli"]
    script = _console_script()
    if script is None:
        pytest.skip(
            "the lading console script is not installed in this environment, "
            "so only the module entry point is exercised"
        )
    return [str(script)]


def _run(
    entry_point_argv: list[str], workspace_root: Path, *arguments: str
) -> subprocess.CompletedProcess[str]:
    """Run one entry point against ``workspace_root`` and capture the result."""
    workspace_root.mkdir(exist_ok=True)
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        [*entry_point_argv, "--workspace-root", str(workspace_root), *arguments],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        timeout=_TIMEOUT_SECONDS,
    )


def test_an_unknown_subcommand_fails_on_stderr(
    entry_point: list[str], tmp_path: Path
) -> None:
    """Both entry points reject an unknown command without writing stdout."""
    result = _run(entry_point, tmp_path / "workspace", "invalid")

    assert result.returncode != 0, "an unknown command must exit non-zero"
    assert "Unknown command" in normalize_diagnostic(result.stderr), (
        f"the diagnostic must be written to stderr: {result.stderr!r}"
    )
    assert not result.stdout, f"stdout must stay empty: {result.stdout!r}"


def test_an_invalid_bump_version_fails_on_stderr(
    entry_point: list[str], tmp_path: Path
) -> None:
    """Both entry points reject an invalid version without writing stdout."""
    result = _run(entry_point, tmp_path / "workspace", "bump", "1.2")

    assert result.returncode == 1, (
        f"an invalid version must exit 1, got {result.returncode}"
    )
    assert "Invalid version argument '1.2'" in normalize_diagnostic(result.stderr), (
        f"the diagnostic must be written to stderr: {result.stderr!r}"
    )
    assert not result.stdout, f"stdout must stay empty: {result.stdout!r}"
