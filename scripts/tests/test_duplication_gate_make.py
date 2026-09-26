"""Make-target contract tests for recording duplication exceptions."""

import dataclasses as dc
import shutil
import subprocess  # noqa: S404 - tests exercise the real Make target.
import sys
import typing as typ

import pytest
from duplication_gate_test_support import (
    REPOSITORY_ROOT,
    allowlist,
    copied_gate_workspace,
    gate_environment,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


@dc.dataclass(frozen=True, slots=True)
class _MakeAllowRequest:
    """The ``FIRST``, ``SECOND``, and ``REASON`` inputs for one Make target run."""

    first: str | None
    second: str | None
    reason: str | None


def _make_allow(
    workspace: Path,
    *,
    request: _MakeAllowRequest,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the real Make target against a copied, writable gate workspace."""
    make = shutil.which("make")
    assert make is not None, "Expected make to be available for contract tests."
    command = [
        make,
        "--no-print-directory",
        "-f",
        str(REPOSITORY_ROOT / "Makefile"),
        "duplication-allow",
        f"DUPLICATION_GATE={sys.executable} scripts/duplication_gate.py",
    ]
    if request.first is not None:
        command.append(f"FIRST={request.first}")
    if request.second is not None:
        command.append(f"SECOND={request.second}")
    if request.reason is not None:
        command.append(f"REASON={request.reason}")
    return subprocess.run(  # noqa: S603 - fixed Make target and copied workspace.
        command,
        cwd=workspace,
        env=gate_environment() if environment is None else environment,
        check=False,
        capture_output=True,
        text=True,
    )


class TestMakeDuplicationAllow:
    """The ``make duplication-allow`` command-line contract."""

    @pytest.mark.parametrize(
        ("first", "reason", "expected_error"),
        [
            pytest.param(None, "reviewed exception", "FIRST is required", id="first"),
            pytest.param("lading/a.py", None, "REASON is required", id="reason"),
        ],
    )
    def test_make_duplication_allow_rejects_missing_and_ambient_values(
        self,
        tmp_path: Path,
        first: str | None,
        reason: str | None,
        expected_error: str,
    ) -> None:
        """Only command-line values satisfy the Make target's required inputs."""
        workspace, _script = copied_gate_workspace(tmp_path)
        environment = {
            **gate_environment(),
            "FIRST": "lading/ambient.py",
            "SECOND": "lading/ambient_second.py",
            "REASON": "ambient reason",
        }
        result = _make_allow(
            workspace,
            request=_MakeAllowRequest(first=first, second=None, reason=reason),
            environment=environment,
        )
        assert result.returncode == 2, result.stderr
        assert expected_error in result.stderr, (
            "Make must reject ambient values for required arguments."
        )

    @pytest.mark.parametrize(
        ("second", "expected_keys"),
        [
            pytest.param(None, ("lading/a.py",), id="unit"),
            pytest.param(
                "lading/b.py::beta",
                ("lading/a.py", "lading/b.py::beta"),
                id="members",
            ),
        ],
    )
    def test_make_duplication_allow_round_trips_quoted_values(
        self,
        tmp_path: Path,
        second: str | None,
        expected_keys: tuple[str, ...],
    ) -> None:
        """The Make target forwards unit and member inputs as literal arguments."""
        workspace, _script = copied_gate_workspace(tmp_path)
        marker = tmp_path / "injected-command"
        reason = f'kept literally: "$(touch {marker})"; $HOME; `id`; \\'
        result = _make_allow(
            workspace,
            request=_MakeAllowRequest(
                first="lading/a.py", second=second, reason=reason
            ),
        )
        assert result.returncode == 0, result.stderr
        entries = allowlist.load_allowlist(workspace / "pyproject.toml")
        assert entries[0].keys == expected_keys, (
            "Make must forward the requested keys exactly."
        )
        assert entries[0].reason == reason, "Make must preserve quoted reasons."
        assert not marker.exists(), "Quoted values must not execute shell fragments."

    def test_make_duplication_allow_preserves_spaces_and_dollar_signs(
        self,
        tmp_path: Path,
    ) -> None:
        """A reason containing spaces and dollars survives the Make boundary."""
        workspace, _script = copied_gate_workspace(tmp_path)
        reason = "costs $5 for two  spaced  keys; $(not_a_variable)"
        result = _make_allow(
            workspace,
            request=_MakeAllowRequest(
                first="lading/a.py", second="lading/b.py", reason=reason
            ),
        )

        assert result.returncode == 0, result.stderr
        entries = allowlist.load_allowlist(workspace / "pyproject.toml")
        assert entries[0].reason == reason, (
            "Make must not interpolate or re-split the supplied reason."
        )


def _make_dry_run(target: str) -> subprocess.CompletedProcess[str]:
    """Expand a Make target's recipe without running it."""
    make = shutil.which("make")
    assert make is not None, "Expected make to be available for contract tests."
    return subprocess.run(  # noqa: S603 - fixed Make target in the repository root.
        [
            make,
            "--dry-run",
            "--no-print-directory",
            "-f",
            str(REPOSITORY_ROOT / "Makefile"),
            target,
        ],
        cwd=REPOSITORY_ROOT,
        env=gate_environment(),
        check=False,
        capture_output=True,
        text=True,
    )


class TestMakeGateWiring:
    """The Make targets that must run the blocking duplication gate.

    Mocked gate tests cannot show that ``make lint`` still reaches the gate, so
    these expand the real recipes and assert the gate command survives.
    """

    @pytest.mark.parametrize("target", ["lint", "duplication"])
    def test_target_runs_the_duplication_gate_check(self, target: str) -> None:
        """Both targets invoke the gate's ``check`` subcommand."""
        result = _make_dry_run(target)

        assert result.returncode == 0, result.stderr
        assert "scripts/duplication_gate.py check" in result.stdout, (
            f"`make {target}` must invoke the duplication gate's check command."
        )

    @pytest.mark.parametrize("target", ["lint", "duplication"])
    def test_target_pins_the_detector_binary(self, target: str) -> None:
        """Both targets pass the pinned detector location to the gate."""
        result = _make_dry_run(target)

        assert "NOSE_BIN=" in result.stdout, (
            f"`make {target}` must pin the detector binary for the gate."
        )

    def test_duplication_installs_the_pinned_detector_first(self) -> None:
        """The standalone target ensures the pinned detector before gating."""
        result = _make_dry_run("duplication")
        install = result.stdout.find("nose-cli@")
        check = result.stdout.find("scripts/duplication_gate.py check")

        assert install != -1, "`make duplication` must ensure the pinned detector."
        assert install < check, "The detector must be installed before the gate runs."

    def test_install_nose_refuses_to_compile_from_source(self) -> None:
        """The installer never falls back to a local source build."""
        result = _make_dry_run("install-nose")

        assert "--disable-strategies compile,quick-install" in result.stdout, (
            "The installer must disable the compile and quick-install strategies."
        )
        assert "cargo install" not in result.stdout, (
            "A source build must not be an installation strategy."
        )

    def test_install_nose_is_idempotent_by_version(self) -> None:
        """The installer compares the installed version before reinstalling."""
        result = _make_dry_run("install-nose")

        assert "--version" in result.stdout, (
            "The installer must check the installed detector's version."
        )

    @pytest.mark.parametrize("target", ["duplication", "duplication-test"])
    def test_help_lists_the_duplication_targets(self, target: str) -> None:
        """The documented targets are discoverable through `make help`."""
        # `help` is a recipe that prints; it has to run rather than dry-run.
        make = shutil.which("make")
        assert make is not None, "Expected make to be available for contract tests."
        result = subprocess.run(  # noqa: S603 - fixed target in the repository root.
            [make, "--no-print-directory", "-f", str(REPOSITORY_ROOT / "Makefile"),
             "help"],
            cwd=REPOSITORY_ROOT,
            env=gate_environment(),
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert target in result.stdout, f"`make help` must list the {target} target."
        assert "-- Run the duplication-gate helper tests" not in result.stdout, (
            "The help output must show the description, not the recipe body."
        )
