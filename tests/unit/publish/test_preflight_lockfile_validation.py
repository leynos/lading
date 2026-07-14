"""Unit tests for the _validate_lockfile_freshness pre-flight helper.

These exercise the pre-flight freshness domain step through the
:class:`lading.commands.lockfile.LockfileInspectionRepository` port (issue
#82): tests inject a recording repository double instead of a command runner,
so discovery, classification, and remediation messaging are verified without
touching git or cargo.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ
from pathlib import Path

import pytest

from lading.commands import lockfile, publish, publish_preflight

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from syrupy.assertion import SnapshotAssertion


@dc.dataclass
class _RecordingLockfileRepository:
    """In-memory ``LockfileInspectionRepository`` double for pre-flight tests."""

    tracked: tuple[Path, ...]
    freshness: cabc.Mapping[Path, lockfile.LockfileFreshness] | None = None
    default_freshness: lockfile.LockfileFreshness = dc.field(
        default_factory=lambda: lockfile.LockfileFreshness(is_fresh=True)
    )
    discovered_roots: list[Path] = dc.field(default_factory=list)
    validated_manifests: list[Path] = dc.field(default_factory=list)

    def discover_tracked_lockfiles(self, workspace_root: Path) -> tuple[Path, ...]:
        """Record the discovery call and return the configured lockfiles."""
        self.discovered_roots.append(workspace_root)
        return self.tracked

    def validate_lockfile_freshness(
        self, manifest_path: Path
    ) -> lockfile.LockfileFreshness:
        """Record the validation call and return the configured freshness."""
        self.validated_manifests.append(manifest_path)
        if self.freshness is not None and manifest_path in self.freshness:
            return self.freshness[manifest_path]
        return self.default_freshness


_STALE_DETAIL = "the lock file Cargo.lock needs to be updated but --locked was passed"


def _stale_lockfiles_error_message(workspace_root: Path) -> str:
    """Return the raised error for a root and nested stale lockfile pair.

    Drives ``_validate_lockfile_freshness`` through a recording port double
    whose tracked lockfiles are all stale, and returns the resulting
    ``PublishPreflightError`` message for assertion or snapshotting.
    """
    root_lockfile = workspace_root / "Cargo.lock"
    nested_lockfile = workspace_root / "tests" / "ui_lints" / "Cargo.lock"
    repository = _RecordingLockfileRepository(
        tracked=(root_lockfile, nested_lockfile),
        default_freshness=lockfile.LockfileFreshness(
            is_fresh=False, is_stale=True, detail=_STALE_DETAIL
        ),
    )

    with pytest.raises(
        publish.PublishPreflightError,
        match="Tracked Cargo\\.lock files are stale",
    ) as excinfo:
        publish_preflight._validate_lockfile_freshness(
            workspace_root, repository=repository
        )

    return str(excinfo.value)


def test_validate_lockfile_freshness_passes_when_all_lockfiles_are_fresh(
    tmp_path: Path,
) -> None:
    """Fresh tracked lockfiles allow preflight to continue via the port."""
    root_lockfile = tmp_path / "Cargo.lock"
    nested_lockfile = tmp_path / "tests" / "ui_lints" / "Cargo.lock"
    repository = _RecordingLockfileRepository(tracked=(root_lockfile, nested_lockfile))

    publish_preflight._validate_lockfile_freshness(tmp_path, repository=repository)

    assert repository.discovered_roots == [tmp_path]
    assert repository.validated_manifests == [
        root_lockfile.parent / "Cargo.toml",
        nested_lockfile.parent / "Cargo.toml",
    ]


def test_validate_lockfile_freshness_reports_stale_lockfiles(tmp_path: Path) -> None:
    """Stale lockfiles are collected and reported with repair commands."""
    message = _stale_lockfiles_error_message(tmp_path)
    root_lockfile = tmp_path / "Cargo.lock"
    nested_lockfile = tmp_path / "tests" / "ui_lints" / "Cargo.lock"

    assert str(root_lockfile) in message
    assert str(nested_lockfile) in message
    assert "lading bump" in message
    assert (
        f"cargo generate-lockfile --manifest-path {tmp_path / 'Cargo.toml'}" in message
    )
    assert (
        "cargo generate-lockfile --manifest-path "
        f"{tmp_path / 'tests' / 'ui_lints' / 'Cargo.toml'}"
    ) in message


def test_validate_lockfile_freshness_error_snapshot(
    snapshot: SnapshotAssertion,
) -> None:
    """Stale lockfile remediation output is locked by snapshot."""
    message = _stale_lockfiles_error_message(Path("/workspace root"))

    assert message == snapshot()


def test_validate_lockfile_freshness_surfaces_cargo_failures(tmp_path: Path) -> None:
    """Cargo failures unrelated to stale lockfiles abort with cargo details."""
    root_lockfile = tmp_path / "Cargo.lock"
    repository = _RecordingLockfileRepository(
        tracked=(root_lockfile,),
        default_freshness=lockfile.LockfileFreshness(
            is_fresh=False, detail="failed to download registry index"
        ),
    )

    with pytest.raises(
        publish.PublishPreflightError,
        match="failed to download registry index",
    ):
        publish_preflight._validate_lockfile_freshness(tmp_path, repository=repository)


def test_validate_lockfile_freshness_classifies_every_lockfile(
    tmp_path: Path,
) -> None:
    """All tracked lockfiles are probed even when the first is already stale.

    Issue #83 evaluated short-circuiting on the first stale lockfile and
    rejected it: every tracked lockfile must be classified so the aggregated
    error lists each stale path for a single-pass repair. This pins the
    full-classification behaviour by asserting every tracked manifest is
    probed through the port, not just that the first stale result aborts.
    """
    lockfiles = (
        tmp_path / "Cargo.lock",
        tmp_path / "a" / "Cargo.lock",
        tmp_path / "b" / "Cargo.lock",
    )
    repository = _RecordingLockfileRepository(
        tracked=lockfiles,
        default_freshness=lockfile.LockfileFreshness(
            is_fresh=False, is_stale=True, detail=_STALE_DETAIL
        ),
    )

    with pytest.raises(
        publish.PublishPreflightError,
        match="Tracked Cargo\\.lock files are stale",
    ) as excinfo:
        publish_preflight._validate_lockfile_freshness(tmp_path, repository=repository)

    # No short-circuit: every tracked lockfile is probed despite the first
    # being stale (issue #83).
    expected_manifests = [path.parent / "Cargo.toml" for path in lockfiles]
    assert repository.validated_manifests == expected_manifests, (
        "every tracked lockfile must be probed without short-circuiting; "
        f"expected {expected_manifests}, got {repository.validated_manifests}"
    )
    message = str(excinfo.value)
    for path in lockfiles:
        assert str(path) in message, (
            f"stale lockfile {path} missing from aggregated error: {message}"
        )
