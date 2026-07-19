"""Snapshot tests for lockfile-related CLI output (issue #81).

PR #75 introduced two text outputs that were previously verified only by
substring matching: the ``(lockfile)`` suffix in ``lading bump`` result
messages and the multi-line stale-lockfile error raised by
``lading publish``. These snapshots lock in the exact formats.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

from lading.commands import bump
from lading.commands.publish_preflight import _build_stale_lockfile_message

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

_WORKSPACE_ROOT = Path("/ws")


def _result_message(changes: bump.BumpChanges) -> str:
    """Render the bump result message against the fixed workspace root."""
    return bump._format_result_message(
        changes,
        "1.2.3",
        dry_run=False,
        workspace_root=_WORKSPACE_ROOT,
    )


def test_bump_message_with_root_lockfile(snapshot: SnapshotAssertion) -> None:
    """A workspace-root Cargo.lock is listed with the (lockfile) suffix."""
    changes = bump.BumpChanges(
        manifests=(_WORKSPACE_ROOT / "Cargo.toml",),
        lockfiles=(_WORKSPACE_ROOT / "Cargo.lock",),
    )

    assert snapshot == _result_message(changes)


def test_bump_message_with_nested_lockfile(snapshot: SnapshotAssertion) -> None:
    """A nested Cargo.lock renders relative to the workspace root."""
    changes = bump.BumpChanges(
        manifests=(_WORKSPACE_ROOT / "Cargo.toml",),
        lockfiles=(
            _WORKSPACE_ROOT / "Cargo.lock",
            _WORKSPACE_ROOT / "tests" / "ui_lints" / "Cargo.lock",
        ),
    )

    assert snapshot == _result_message(changes)


def test_stale_lockfile_error_single(snapshot: SnapshotAssertion) -> None:
    """A single stale lockfile lists one repair command."""
    message = _build_stale_lockfile_message([_WORKSPACE_ROOT / "Cargo.lock"])

    assert snapshot == message


def test_stale_lockfile_error_multiple(snapshot: SnapshotAssertion) -> None:
    """Multiple stale lockfiles each list their own repair command."""
    message = _build_stale_lockfile_message([
        _WORKSPACE_ROOT / "Cargo.lock",
        _WORKSPACE_ROOT / "tests" / "ui_lints" / "Cargo.lock",
    ])

    assert snapshot == message
