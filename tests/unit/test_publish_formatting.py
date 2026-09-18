"""Unit tests for publish output formatting helpers."""

from __future__ import annotations

import typing as typ

from lading.commands import publish_staging

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_format_preparation_summary_reports_bump_readme_handling(
    tmp_path: Path,
) -> None:
    """Summary explains that README adoption is handled before publish."""
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    preparation = publish_staging.PublishPreparation(staging_root=staging_root)

    lines = publish_staging._format_preparation_summary(preparation, retained=True)

    assert lines == (
        f"Staged workspace at: {staging_root}",
        "Workspace READMEs are handled by lading bump.",
    )


def test_format_preparation_summary_says_a_removed_tree_has_gone(
    tmp_path: Path,
) -> None:
    """A summary naming a path must say when that path no longer exists.

    Cleanup defaults to on since issue #269, so the common case is that the
    staged tree was removed before the caller read this line. Naming its
    location with nothing else said reads as an invitation to go and look.
    """
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    preparation = publish_staging.PublishPreparation(staging_root=staging_root)

    lines = publish_staging._format_preparation_summary(preparation, retained=False)

    assert lines == (
        f"Staged workspace at: {staging_root} (removed)",
        "Workspace READMEs are handled by lading bump.",
    )
