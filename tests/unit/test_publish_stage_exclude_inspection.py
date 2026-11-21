"""Inspection-failure tests for staging exclusion validation.

A workspace that cannot be read must be reported as an inspection failure
rather than read as absence, so an exclusion cannot silently remove something
staging needed.
"""

import errno
import os
import typing as typ
from pathlib import Path

import pytest

from lading.commands import publish_stage_exclude
from tests.unit.publish_staging_plan_fixtures import _plan_with_roles

if typ.TYPE_CHECKING:
    from tests.unit.conftest import PreparationFixtures


def test_stage_exclude_reports_a_link_that_cannot_be_inspected(
    preparation_fixtures: PreparationFixtures,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A link that cannot be stat-ed is an inspection failure, not a skip."""
    plan = _plan_with_roles(preparation_fixtures)
    manifest = plan.workspace_root / "alpha" / "Cargo.toml"
    real_lstat = Path.lstat

    def deny_link(self: Path) -> os.stat_result:
        """Fail the no-follow inspection of the crate manifest."""
        if self == manifest:
            raise OSError(errno.EIO, "I/O error", str(self))
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", deny_link)

    with pytest.raises(publish_stage_exclude.StageExcludeInspectionError) as excinfo:
        publish_stage_exclude._validate_stage_exclude(
            plan, ("shared",), preserve_symlinks=True
        )

    assert "could not inspect the workspace" in str(excinfo.value), (
        "a failed link inspection must not be read as 'not a link'"
    )


def test_stage_exclude_reports_an_unreadable_manifest(
    preparation_fixtures: PreparationFixtures,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A permission failure is an inspection failure, not a silent absence."""
    plan = _plan_with_roles(preparation_fixtures)
    manifest = plan.workspace_root / "Cargo.toml"
    real_stat = Path.stat

    def deny_unreadable(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        """Deny ``stat`` on the workspace manifest and delegate every other path."""
        if self == manifest:
            raise PermissionError(errno.EACCES, "permission denied", str(self))
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", deny_unreadable)

    with pytest.raises(publish_stage_exclude.StageExcludeInspectionError) as excinfo:
        publish_stage_exclude._validate_stage_exclude(
            plan, ("shared",), preserve_symlinks=True
        )

    assert "could not inspect the workspace" in str(excinfo.value), (
        "an unreadable manifest must surface as an inspection failure"
    )
