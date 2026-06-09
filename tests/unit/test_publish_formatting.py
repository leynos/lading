"""Unit tests for publish output formatting helpers."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

from lading.commands import publish, publish_plan
from tests.unit.conftest import _CrateSpec

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.workspace import WorkspaceCrate


def test_render_section_renders_formatted_items() -> None:
    """Generic section helper applies the provided formatter."""

    class Dummy:
        def __init__(self, value: str) -> None:
            self.value = value

    items = (Dummy("alpha"), Dummy("beta"))

    lines = publish_plan.render_section(
        items,
        header="Header:",
        formatter=lambda item: item.value.upper(),
    )

    assert lines == ["Header:", "- ALPHA", "- BETA"]


def test_render_section_defaults_to_string_conversion() -> None:
    """Default formatter handles simple string values without boilerplate."""
    lines = publish_plan.render_section(("alpha", "beta"), header="Header:")

    assert lines == ["Header:", "- alpha", "- beta"]


def test_render_section_omits_header_for_empty_sequences() -> None:
    """Helper returns no lines when there is nothing to report."""
    assert publish_plan.render_section((), header="Header:") == []


def test_format_plan_formats_skipped_sections(
    tmp_path: Path,
    make_crate: cabc.Callable[[Path, str, _CrateSpec | None], WorkspaceCrate],
) -> None:
    """``_format_plan`` renders skipped crates using their names only."""
    root = tmp_path.resolve()
    manifest_skipped = make_crate(root, "beta", _CrateSpec(publish=False))
    config_skipped = make_crate(root, "gamma")
    plan = publish_plan.PublishPlan(
        workspace_root=root,
        publishable=(),
        skipped_manifest=(manifest_skipped,),
        skipped_configuration=(config_skipped,),
        missing_configuration_exclusions=("missing",),
    )

    message = publish_plan.format_plan(plan, strip_patches="all")

    lines = message.splitlines()
    manifest_index = lines.index("Skipped (publish = false):")
    configuration_index = lines.index("Skipped via publish.exclude:")
    missing_index = lines.index("Configured exclusions not found in workspace:")

    assert lines[manifest_index + 1] == "- beta"
    assert lines[configuration_index + 1] == "- gamma"
    assert lines[missing_index + 1] == "- missing"


def test_format_preparation_summary_reports_bump_readme_handling(
    tmp_path: Path,
) -> None:
    """Summary explains that README adoption is handled before publish."""
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    preparation = publish.PublishPreparation(
        staging_root=staging_root, copied_readmes=()
    )

    lines = publish._format_preparation_summary(preparation)

    assert lines == (
        f"Staged workspace at: {staging_root}",
        "Workspace READMEs are handled by lading bump.",
    )
