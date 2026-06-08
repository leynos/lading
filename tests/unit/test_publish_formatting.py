"""Unit tests for publish output formatting helpers."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

from lading.commands import publish, publish_plan
from tests.unit.conftest import _CrateSpec

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.workspace import WorkspaceCrate


def test_append_section_appends_formatted_items() -> None:
    """Generic section helper applies the provided formatter."""

    class Dummy:
        def __init__(self, value: str) -> None:
            self.value = value

    lines: list[str] = []
    items = (Dummy("alpha"), Dummy("beta"))

    publish_plan.append_section(
        lines,
        items,
        header="Header:",
        formatter=lambda item: item.value.upper(),
    )

    assert lines == ["Header:", "- ALPHA", "- BETA"]


def test_append_section_defaults_to_string_conversion() -> None:
    """Default formatter handles simple string values without boilerplate."""
    lines: list[str] = []

    publish_plan.append_section(lines, ("alpha", "beta"), header="Header:")

    assert lines == ["Header:", "- alpha", "- beta"]


def test_append_section_omits_header_for_empty_sequences() -> None:
    """Helper leaves ``lines`` unchanged when there is nothing to report."""
    lines = ["prefix"]

    publish_plan.append_section(lines, (), header="Header:")

    assert lines == ["prefix"]


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


def test_format_preparation_summary_lists_copied_readmes(
    tmp_path: Path,
) -> None:
    """Summary includes relative README paths when copies exist."""
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    readme_alpha = staging_root / "crates" / "alpha" / "README.md"
    readme_beta = staging_root / "crates" / "beta" / "README.md"
    for path in (readme_alpha, readme_beta):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("workspace", encoding="utf-8")
    preparation = publish.PublishPreparation(
        staging_root=staging_root, copied_readmes=(readme_alpha, readme_beta)
    )

    lines = publish._format_preparation_summary(preparation)

    assert lines[0] == f"Staged workspace at: {staging_root}"
    assert "Copied workspace README to:" in lines[1]
    assert "- crates/alpha/README.md" in lines
    assert "- crates/beta/README.md" in lines


def test_format_preparation_summary_handles_external_paths(
    tmp_path: Path,
) -> None:
    """Summary falls back to absolute paths when not under the staging root."""
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    external_readme = tmp_path.parent / "external-readme.md"
    external_readme.write_text("workspace", encoding="utf-8")
    preparation = publish.PublishPreparation(
        staging_root=staging_root, copied_readmes=(external_readme,)
    )

    lines = publish._format_preparation_summary(preparation)

    assert lines[0] == f"Staged workspace at: {staging_root}"
    assert lines[1] == "Copied workspace README to:"
    assert f"- {external_readme}" in lines


def test_format_preparation_summary_reports_absence(
    tmp_path: Path,
) -> None:
    """Summary highlights when no README copies were required."""
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    preparation = publish.PublishPreparation(
        staging_root=staging_root, copied_readmes=()
    )

    lines = publish._format_preparation_summary(preparation)

    assert lines == (
        f"Staged workspace at: {staging_root}",
        "Copied workspace README to: none required",
    )
