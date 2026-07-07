"""Tests for lockfile regeneration after bump operations."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import operator
import pathlib
import shlex
import tempfile
import typing as typ
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lading.commands import bump_lockfiles

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


@dc.dataclass(frozen=True, slots=True)
class _Invocation:
    """Recorded command invocation."""

    command: tuple[str, ...]
    cwd: Path | None


class _RecordingRunner:
    """Record command invocations and return a configured result."""

    def __init__(
        self,
        result: tuple[int, str, str] = (0, "", ""),
    ) -> None:
        self.result = result
        self.invocations: list[_Invocation] = []

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
    ) -> tuple[int, str, str]:
        """Record one command invocation."""
        self.invocations.append(_Invocation(command=tuple(command), cwd=cwd))
        return self.result


def test_regenerate_lockfiles_includes_workspace_manifest(tmp_path: Path) -> None:
    """The workspace root manifest should always be regenerated."""
    runner = _RecordingRunner()

    lockfiles = bump_lockfiles.regenerate_lockfiles(
        tmp_path,
        (),
        runner=runner,
    )

    assert lockfiles == (tmp_path / "Cargo.lock",)
    assert runner.invocations == [
        _Invocation(
            command=(
                "cargo",
                "update",
                "--workspace",
                "--manifest-path",
                str(tmp_path / "Cargo.toml"),
            ),
            cwd=tmp_path,
        )
    ]


def test_regenerate_lockfiles_uses_configured_manifests(tmp_path: Path) -> None:
    """Configured nested manifest paths should be passed to Cargo."""
    runner = _RecordingRunner()

    lockfiles = bump_lockfiles.regenerate_lockfiles(
        tmp_path,
        ("crates/nested/Cargo.toml",),
        runner=runner,
    )

    nested_manifest = tmp_path / "crates/nested/Cargo.toml"
    assert lockfiles == (
        tmp_path / "Cargo.lock",
        tmp_path / "crates/nested/Cargo.lock",
    )
    assert runner.invocations[-1] == _Invocation(
        command=(
            "cargo",
            "update",
            "--workspace",
            "--manifest-path",
            str(nested_manifest),
        ),
        cwd=tmp_path,
    )


def test_regenerate_lockfiles_deduplicates_root_manifest(tmp_path: Path) -> None:
    """Explicit root manifest entries should not trigger duplicate rebuilds."""
    runner = _RecordingRunner()

    lockfiles = bump_lockfiles.regenerate_lockfiles(
        tmp_path,
        ("Cargo.toml", "./Cargo.toml", "crates/nested/Cargo.toml"),
        runner=runner,
    )

    assert lockfiles == (
        tmp_path / "Cargo.lock",
        tmp_path / "crates/nested/Cargo.lock",
    )
    assert [invocation.command for invocation in runner.invocations] == [
        (
            "cargo",
            "update",
            "--workspace",
            "--manifest-path",
            str(tmp_path / "Cargo.toml"),
        ),
        (
            "cargo",
            "update",
            "--workspace",
            "--manifest-path",
            str(tmp_path / "crates/nested/Cargo.toml"),
        ),
    ]


def test_resolve_lockfile_paths_reports_dry_run_targets(tmp_path: Path) -> None:
    """Dry-run reporting can resolve lockfiles without invoking Cargo."""
    lockfiles = bump_lockfiles.resolve_lockfile_paths(
        tmp_path,
        ("Cargo.toml", "crates/nested/Cargo.toml"),
    )

    assert lockfiles == (
        tmp_path / "Cargo.lock",
        tmp_path / "crates/nested/Cargo.lock",
    )


@pytest.mark.parametrize(
    ("manifest", "expected_message"),
    [
        ("../outside/Cargo.toml", "within the workspace"),
        ("Cargo.lock", "Cargo.toml file"),
        ("crates/nested/foo.toml", "Cargo.toml file"),
    ],
)
def test_resolve_lockfile_paths_rejects_invalid_targets(
    tmp_path: Path,
    manifest: str,
    expected_message: str,
) -> None:
    """Configured manifests must stay in-workspace and name Cargo.toml."""
    with pytest.raises(
        bump_lockfiles.LockfileRegenerationError, match=expected_message
    ):
        bump_lockfiles.resolve_lockfile_paths(tmp_path, (manifest,))


@pytest.mark.parametrize(
    ("manifest", "expected_message"),
    [
        ("../outside/Cargo.toml", "within the workspace"),
        ("Cargo.lock", "Cargo.toml file"),
        ("crates/nested/foo.toml", "Cargo.toml file"),
    ],
)
def test_regenerate_lockfiles_rejects_invalid_targets_without_running_cargo(
    tmp_path: Path,
    manifest: str,
    expected_message: str,
) -> None:
    """Invalid configured manifests should fail before invoking Cargo."""
    runner = _RecordingRunner()

    with pytest.raises(
        bump_lockfiles.LockfileRegenerationError, match=expected_message
    ):
        bump_lockfiles.regenerate_lockfiles(
            tmp_path,
            (manifest,),
            runner=runner,
        )

    assert runner.invocations == []


def test_regenerate_lockfiles_surfaces_cargo_failure(tmp_path: Path) -> None:
    """A failing cargo invocation should abort the bump."""
    runner = _RecordingRunner(result=(101, "", "failed to resolve"))

    with pytest.raises(
        bump_lockfiles.LockfileRegenerationError, match="failed to resolve"
    ):
        bump_lockfiles.regenerate_lockfiles(
            tmp_path,
            (),
            runner=runner,
        )


def test_regenerate_lockfiles_partial_failure_updates_earlier_lockfiles(
    tmp_path: pathlib.Path,
) -> None:
    """First lockfile update commits to disk even when a later one fails.

    This documents the partial-update semantics: regeneration is not atomic.
    """
    nested = tmp_path / "crates" / "sub"
    nested.mkdir(parents=True)
    (nested / "Cargo.toml").write_text('[package]\nname = "sub"\nversion = "0.1.0"\n')
    invocations: list[str] = []

    def partial_runner(
        command: cabc.Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
    ) -> tuple[int, str, str]:
        manifest = next((a for a in command if a.endswith("Cargo.toml")), None)
        invocations.append(str(manifest))
        # Fail only on the nested manifest invocation.
        if manifest and "crates" in manifest:
            return (1, "", "simulated cargo failure")
        return (0, "", "")

    with pytest.raises(
        bump_lockfiles.LockfileRegenerationError,
        match="simulated cargo failure",
    ):
        bump_lockfiles.regenerate_lockfiles(
            tmp_path,
            ["crates/sub/Cargo.toml"],
            runner=partial_runner,
        )

    # Root manifest was successfully processed before the failure.
    assert len(invocations) == 2
    assert any("crates" not in inv for inv in invocations), (
        "root manifest must have been invoked first"
    )


def test_regenerate_lockfiles_wraps_runner_exceptions(tmp_path: Path) -> None:
    """Runner exceptions should retain their cause for diagnostics."""

    def failing_runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
    ) -> tuple[int, str, str]:
        del command, cwd
        message = "cargo executable not found"
        raise OSError(message)

    with pytest.raises(
        bump_lockfiles.LockfileRegenerationError,
        match="cargo executable not found",
    ) as exc_info:
        bump_lockfiles.regenerate_lockfiles(
            tmp_path,
            (),
            runner=failing_runner,
        )

    assert isinstance(exc_info.value.__cause__, OSError)


# ---------------------------------------------------------------------------
# Aggregated failure handling (issue #84)
# ---------------------------------------------------------------------------


def _selective_failure_runner(
    failing_manifests: set[Path],
) -> tuple[cabc.Callable[..., tuple[int, str, str]], list[Path]]:
    """Return a runner failing for ``failing_manifests`` and its call log."""
    attempted: list[Path] = []

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
    ) -> tuple[int, str, str]:
        del cwd
        manifest = Path(command[-1])
        attempted.append(manifest)
        if manifest in failing_manifests:
            return 101, "", "error: dependency conflict"
        return 0, "", ""

    return runner, attempted


def _prepare_manifest_fixture(
    root: Path,
    *,
    fail_root: bool,
    crates: list[tuple[str, bool]],
) -> tuple[list[Path], set[Path]]:
    """Create manifest files under root and record which should fail.

    Returns an ``(expected_order, failing)`` pair.
    """
    (root / "Cargo.toml").write_text("", encoding="utf-8")
    expected_order = [(root / "Cargo.toml").resolve()]
    failing: set[Path] = set()
    if fail_root:
        failing.add(expected_order[0])
    for name, should_fail in crates:
        (root / name).mkdir()
        (root / name / "Cargo.toml").write_text("", encoding="utf-8")
        manifest = (root / name / "Cargo.toml").resolve()
        expected_order.append(manifest)
        if should_fail:
            failing.add(manifest)
    return expected_order, failing


def _assert_failure_message_reports_each_once(message: str, failing: set[Path]) -> None:
    """Assert every failing manifest has exactly one repair line."""
    repair_lines = message.count("cargo update --workspace --manifest-path")
    assert repair_lines == len(failing), (
        f"expected one repair line per failure; got {repair_lines} for "
        f"{len(failing)} failure(s): {message}"
    )
    for manifest in failing:
        assert shlex.quote(str(manifest)) in message, (
            f"failed manifest {manifest} should appear in: {message}"
        )


@pytest.fixture
def ab_workspace(tmp_path: Path) -> Path:
    """Create a workspace with root and ``a``/``b`` member ``Cargo.toml`` files."""
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "Cargo.toml").write_text("", encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
    return tmp_path


def test_regenerate_lockfiles_attempts_all_manifests_after_failure(
    ab_workspace: Path,
) -> None:
    """A mid-list cargo failure does not skip the remaining manifests."""
    failing = (ab_workspace / "a" / "Cargo.toml").resolve()
    runner, attempted = _selective_failure_runner({failing})

    with pytest.raises(bump_lockfiles.LockfileRegenerationError) as excinfo:
        bump_lockfiles.regenerate_lockfiles(
            ab_workspace,
            ("a/Cargo.toml", "b/Cargo.toml"),
            runner=runner,
        )

    expected_order = [
        (ab_workspace / "Cargo.toml").resolve(),
        failing,
        (ab_workspace / "b" / "Cargo.toml").resolve(),
    ]
    assert attempted == expected_order, (
        f"every manifest should be attempted in order; "
        f"expected {expected_order}, got {attempted}"
    )
    message = str(excinfo.value)
    assert "failed for 1 manifest(s)" in message, (
        f"aggregate header should count one failure; got: {message}"
    )
    assert str(failing) in message, (
        f"the failed manifest should be named in the message; got: {message}"
    )
    assert f"cargo update --workspace --manifest-path {failing}" in message, (
        f"the repair command for the failed manifest should be present; got: {message}"
    )
    assert str((ab_workspace / "b" / "Cargo.toml").resolve()) not in message, (
        f"the successful manifest should not appear in the repair list; got: {message}"
    )


def test_regenerate_lockfiles_aggregates_multiple_failures(
    ab_workspace: Path, snapshot: SnapshotAssertion
) -> None:
    """Every failed manifest is listed once with its repair command."""
    failing = {
        (ab_workspace / "a" / "Cargo.toml").resolve(),
        (ab_workspace / "b" / "Cargo.toml").resolve(),
    }
    runner, _ = _selective_failure_runner(failing)

    with pytest.raises(bump_lockfiles.LockfileRegenerationError) as excinfo:
        bump_lockfiles.regenerate_lockfiles(
            ab_workspace,
            ("a/Cargo.toml", "b/Cargo.toml"),
            runner=runner,
        )

    message = str(excinfo.value)
    assert "failed for 2 manifest(s)" in message, (
        f"aggregate header should count two failures; got: {message}"
    )
    for manifest in failing:
        assert message.count(f"--manifest-path {manifest}") == 1, (
            f"each failed manifest should be listed exactly once; got: {message}"
        )
    assert snapshot == message.replace(str(ab_workspace), "<workspace>")


@given(
    spec=st.tuples(
        st.booleans(),
        st.lists(
            st.tuples(
                st.text(alphabet="abcdefghij", min_size=1, max_size=4),
                st.booleans(),
            ),
            min_size=1,
            max_size=5,
            unique_by=operator.itemgetter(0),
        ),
    ),
)
@settings(max_examples=50, deadline=None)
def test_regenerate_lockfiles_attempts_all_and_reports_each_failure_once(
    spec: tuple[bool, list[tuple[str, bool]]],
) -> None:
    """Every manifest is attempted and each failure is reported exactly once."""
    fail_root, crates = spec
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        expected_order, failing = _prepare_manifest_fixture(
            root, fail_root=fail_root, crates=crates
        )
        runner, attempted = _selective_failure_runner(failing)
        configured = tuple(f"{name}/Cargo.toml" for name, _ in crates)

        if failing:
            with pytest.raises(bump_lockfiles.LockfileRegenerationError) as excinfo:
                bump_lockfiles.regenerate_lockfiles(root, configured, runner=runner)
            _assert_failure_message_reports_each_once(str(excinfo.value), failing)
        else:
            regenerated = bump_lockfiles.regenerate_lockfiles(
                root, configured, runner=runner
            )
            assert len(regenerated) == len(expected_order), (
                "all lockfiles should be regenerated when nothing fails"
            )

        assert attempted == expected_order, (
            f"every manifest should be attempted once, in order; "
            f"expected {expected_order}, got {attempted}"
        )
