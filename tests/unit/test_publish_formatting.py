"""Unit tests for publish output formatting helpers."""
from __future__ import annotations

import typing as typ

from lading.commands import publish
from lading.commands import publish_plan, publish_staging

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_format_preparation_summary_reports_bump_readme_handling(
    tmp_path: Path,
) -> None:
    """Summary explains that README adoption is handled before publish."""
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    preparation = publish_staging.PublishPreparation(staging_root=staging_root)

    lines = publish_staging._format_preparation_summary(preparation)

    assert lines == (
        f"Staged workspace at: {staging_root}",
        "Workspace READMEs are handled by lading bump.",
    )
