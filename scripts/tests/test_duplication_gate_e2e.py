"""End-to-end gate tests against the real pinned nose binary.

Every other blocking test substitutes a stub report or calls the detector
without the gate, so none of them shows that a genuine duplicate reaches
``check`` and fails the build. These do, using the pinned binary; they skip
when it is not installed.

The canary workspaces are deliberately small and unsaturated, so ``top = 30``
cannot hide the planted family behind the ranking bound.
"""

import dataclasses as dc
import json
import subprocess
import textwrap
from pathlib import Path

import pytest
from duplication_gate_test_support import (
    REPOSITORY_ROOT,
    copied_gate_workspace,
    detector,
    gate_environment,
    run_gate_command,
)

#: A substantial body: it clears the gate's own size floor, and it is long
#: enough that extracting one helper would be the right answer rather than
#: noise. It is a correctness canary, not a timing benchmark.
_DUPLICATE_BODY = textwrap.dedent(
    """\
    def NAME(items):
        total = 0.0
        for item in items:
            price = item["price"] * item["quantity"]
            if item.get("taxable"):
                price *= 1.2
            if item.get("discount"):
                price -= item["discount"]
            total += price
        if total < 0:
            total = 0.0
        return round(total, 2)
    """
)

#: The canary workspace configuration: one small root, the gate's channels,
#: and a size floor low enough to report the planted family.
_PLANTED_PYPROJECT = textwrap.dedent(
    """\
    [project]
    name = "gate-test"
    version = "0"

    [tool.nose]
    version = "0.20.0"
    roots = ["planted"]
    mode = "syntax,semantic,near"
    min-size = 8
    surface = "all"
    top = 30
    """
)

#: A reasoned exception scoped to the file the canary duplicates within.
_FILED_EXCEPTION = textwrap.dedent(
    """
    [[tool.duplication_gate.allow]]
    unit = "planted/mod.py"
    reason = "Planted fixture proving the gate blocks and allows."
    """
)


def _resolved_binary() -> str:
    """Return the pinned detector path, skipping when it is unavailable."""
    settings = detector.load_settings(REPOSITORY_ROOT / "pyproject.toml")
    try:
        binary = detector.resolve_binary(settings)
    except detector.GateExecutionError as error:  # pragma: no cover
        pytest.skip(str(error))
        raise  # Unreachable; keeps the return paths symmetric for Pylint.
    return binary


def _write_planted_package(workspace: Path, bodies: dict[str, str]) -> None:
    """Write one package whose modules hold ``bodies`` verbatim."""
    package = workspace / "planted"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name, body in bodies.items():
        (package / name).write_text(body, encoding="utf-8")


def _planted_workspace(
    tmp_path: Path,
    *,
    allow: str = "",
    extra_clone: bool = False,
) -> Path:
    """Build a gate workspace whose package holds one verbatim duplicate.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Directory to build the throwaway workspace under.
    allow : str
        TOML appended to the workspace ``pyproject.toml``.
    extra_clone : bool
        Add a third copy in an unlisted file, which must reopen the family
        even when an exception already covers the first two.

    Returns
    -------
    pathlib.Path
        The workspace root.
    """
    workspace, _script = copied_gate_workspace(tmp_path)
    bodies = {
        "mod.py": _DUPLICATE_BODY.replace("NAME", "first_total")
        + "\n\n"
        + _DUPLICATE_BODY.replace("NAME", "second_total"),
    }
    if extra_clone:
        bodies["extra.py"] = _DUPLICATE_BODY.replace("NAME", "third_total")
    _write_planted_package(workspace, bodies)
    (workspace / "pyproject.toml").write_text(
        _PLANTED_PYPROJECT + allow,
        encoding="utf-8",
    )
    return workspace


def _run_check(workspace: Path) -> subprocess.CompletedProcess[str]:
    """Run the copied gate's real ``check`` against the pinned detector."""
    return run_gate_command(
        workspace / "scripts" / "duplication_gate.py",
        "check",
        environment=gate_environment(NOSE_BIN=_resolved_binary()),
    )


class TestCleanWorkspace:
    """The gate's clean case against the real detector."""

    def test_workspace_without_clones_passes(self, tmp_path: Path) -> None:
        """A small workspace with no duplicate passes through the real gate."""
        workspace, _script = copied_gate_workspace(tmp_path)
        _write_planted_package(
            workspace,
            {"mod.py": _DUPLICATE_BODY.replace("NAME", "only_total")},
        )
        (workspace / "pyproject.toml").write_text(_PLANTED_PYPROJECT, encoding="utf-8")

        result = _run_check(workspace)

        assert result.returncode == 0, (
            f"A clone-free workspace must pass.\n{result.stdout}{result.stderr}"
        )
        assert "duplication gate passed" in result.stdout, (
            "The clean case must report a passing gate."
        )


class TestPlantedDuplicate:
    """A real planted clone driven through the real gate."""

    def test_reports_a_planted_verbatim_copy(self, tmp_path: Path) -> None:
        """The pinned detector reports a planted copy through normalization."""
        settings = detector.load_settings(REPOSITORY_ROOT / "pyproject.toml")
        binary = _resolved_binary()
        (tmp_path / "mod.py").write_text(
            _DUPLICATE_BODY.replace("NAME", "first_total")
            + "\n\n"
            + _DUPLICATE_BODY.replace("NAME", "second_total"),
            encoding="utf-8",
        )
        command = detector.build_command(
            binary, dc.replace(settings, roots=(".",), min_size=8)
        )
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - pinned, repository-owned binary.
            command,
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )
        findings = detector.normalize_findings(json.loads(result.stdout))
        assert findings, "The planted copy must be reported."
        assert any(
            {location.name for location in finding.locations}
            == {"first_total", "second_total"}
            for finding in findings
        ), "The planted copy must name both duplicated functions."

    def test_planted_duplicate_blocks_the_gate(self, tmp_path: Path) -> None:
        """A genuine duplicate fails ``check`` and names both copies."""
        result = _run_check(_planted_workspace(tmp_path))

        assert result.returncode == 1, (
            f"A planted duplicate must fail the gate.\n{result.stdout}{result.stderr}"
        )
        assert "planted/mod.py" in result.stdout, (
            "The report must locate the duplicated file."
        )
        assert "make duplication-allow" in result.stdout, (
            "A blocking report must show how to record a reasoned exception."
        )

    def test_reasoned_exception_unblocks_the_planted_duplicate(
        self, tmp_path: Path
    ) -> None:
        """The same duplicate passes once a reasoned allow entry covers it."""
        result = _run_check(_planted_workspace(tmp_path, allow=_FILED_EXCEPTION))

        assert result.returncode == 0, (
            f"A covered duplicate must pass.\n{result.stdout}{result.stderr}"
        )
        assert "duplication gate passed" in result.stdout, (
            "The gate must report its successful result."
        )

    def test_a_new_member_outside_the_exception_blocks_again(
        self, tmp_path: Path
    ) -> None:
        """A third, unlisted copy reopens the family that exception covered."""
        result = _run_check(
            _planted_workspace(tmp_path, allow=_FILED_EXCEPTION, extra_clone=True)
        )

        assert result.returncode == 1, (
            "An unlisted location must reopen the family.\n"
            f"{result.stdout}{result.stderr}"
        )
        assert "planted/extra.py" in result.stdout, (
            "The report must name the newly added member."
        )

    def test_repeated_runs_report_identical_output(self, tmp_path: Path) -> None:
        """Normalized gate output is stable across repeated runs."""
        workspace = _planted_workspace(tmp_path)
        first = _run_check(workspace)
        second = _run_check(workspace)

        assert first.returncode == second.returncode == 1, (
            "Both runs must reach the same verdict."
        )
        assert first.stdout == second.stdout, (
            "The gate's normalized report must be deterministic."
        )
