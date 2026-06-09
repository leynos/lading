"""Snapshot tests for operator-facing command-failure messages.

Issue #102 collapsed the ``(stderr or stdout).strip()`` idiom into the shared
``lading.utils.process`` helpers. These snapshots pin the rendered messages
at each consuming boundary so the extraction cannot silently change text.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from lading.commands import bump_lockfiles, lockfile, publish_index_check
from lading.commands.publish_preflight import _build_cargo_error_message
from lading.workspace.metadata import CargoMetadataInvocationError

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from syrupy.assertion import SnapshotAssertion


def _failing_runner(
    stdout: str, stderr: str, exit_code: int = 101
) -> cabc.Callable[..., tuple[int, str, str]]:
    """Return a stub runner that always fails with the supplied output."""

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del command, cwd, env, echo_stdout
        return exit_code, stdout, stderr

    return runner


def test_preflight_cargo_failure_message(snapshot: SnapshotAssertion) -> None:
    """Cargo pre-flight failures render the canonical detail suffix."""
    assert snapshot == _build_cargo_error_message(
        "check", 101, "", "error: linker failed\n"
    )
    assert snapshot == _build_cargo_error_message("test", 7, "", "")


def test_publish_cargo_failure_message(snapshot: SnapshotAssertion) -> None:
    """Package and publish failures render the canonical detail suffix."""
    assert snapshot == publish_index_check._format_cargo_failure_message(
        "package", "alpha", 101, ("stdout context", "error: missing version\n")
    )
    assert snapshot == publish_index_check._format_cargo_failure_message(
        "publish", "beta", 1, ("only stdout\n", "   ")
    )


def test_lockfile_refresh_failure_message(snapshot: SnapshotAssertion) -> None:
    """Lockfile refresh failures render the canonical detail suffix."""
    manifest = Path("/ws/crates/alpha/Cargo.toml")
    with pytest.raises(lockfile.LockfileRefreshError) as excinfo:
        lockfile.refresh_lockfile(
            manifest, _failing_runner("", "error: registry offline\n")
        )

    assert snapshot == str(excinfo.value)


def test_lockfile_regeneration_failure_message(snapshot: SnapshotAssertion) -> None:
    """Lockfile regeneration failures render the canonical detail suffix."""
    manifest = Path("/ws/Cargo.toml")
    with pytest.raises(bump_lockfiles.LockfileRegenerationError) as excinfo:
        bump_lockfiles._run_workspace_lockfile_update(
            Path("/ws"),
            manifest,
            _failing_runner("", "error: dependency conflict\n"),
        )

    assert snapshot == str(excinfo.value)


def test_cargo_metadata_invocation_message(snapshot: SnapshotAssertion) -> None:
    """Metadata failures prefer stderr, then stdout, then the status."""
    assert snapshot == str(CargoMetadataInvocationError(2, "out", "err"))
    assert snapshot == str(CargoMetadataInvocationError(2, "out", "   "))
    assert snapshot == str(CargoMetadataInvocationError(2, "", ""))
