"""Snapshot tests for lockfile-related CLI output (issue #81).

PR #75 introduced two text outputs that were previously verified only by
substring matching: the ``(lockfile)`` suffix in ``lading bump`` result
messages and the multi-line stale-lockfile error raised by
``lading publish``. These snapshots lock in the exact formats.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

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


class TestBumpLockfileMessages:
    """Snapshot bump messages for root and nested lockfiles."""

    @pytest.mark.parametrize(
        ("lockfiles", "scenario"),
        [
            pytest.param(
                (_WORKSPACE_ROOT / "Cargo.lock",),
                "root",
                id="root",
            ),
            pytest.param(
                (
                    _WORKSPACE_ROOT / "Cargo.lock",
                    _WORKSPACE_ROOT / "tests" / "ui_lints" / "Cargo.lock",
                ),
                "nested",
                id="nested",
            ),
        ],
    )
    def test_message(
        self,
        snapshot: SnapshotAssertion,
        lockfiles: tuple[Path, ...],
        scenario: str,
    ) -> None:
        """Lockfiles render relative to the workspace root."""
        changes = bump.BumpChanges(
            manifests=(_WORKSPACE_ROOT / "Cargo.toml",),
            lockfiles=lockfiles,
        )

        assert snapshot == _result_message(changes), (
            f"{scenario} lockfile bump message changed"
        )


class TestStaleLockfileMessages:
    """Snapshot stale-lockfile errors for one and multiple lockfiles."""

    @pytest.mark.parametrize(
        ("lockfiles", "scenario"),
        [
            pytest.param(
                [_WORKSPACE_ROOT / "Cargo.lock"],
                "single",
                id="single",
            ),
            pytest.param(
                [
                    _WORKSPACE_ROOT / "Cargo.lock",
                    _WORKSPACE_ROOT / "tests" / "ui_lints" / "Cargo.lock",
                ],
                "multiple",
                id="multiple",
            ),
        ],
    )
    def test_message(
        self,
        snapshot: SnapshotAssertion,
        lockfiles: list[Path],
        scenario: str,
    ) -> None:
        """Stale lockfiles each list their own repair command."""
        message = _build_stale_lockfile_message(lockfiles)

        assert snapshot == message, f"{scenario} stale-lockfile message changed"
