"""Unit tests for publish formatting helpers."""

from __future__ import annotations

import typing as typ

from lading.commands import publish_plan

from .conftest import make_crate

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_append_section_appends_formatted_items() -> None:
    """Generic section helper applies the provided formatter."""

    class Dummy:
        def __init__(self, value: str) -> None:
            self.value = value

    lines = []
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


def test_format_plan_formats_skipped_sections(tmp_path: Path) -> None:
    """``_format_plan`` renders skipped crates using their names only."""
    root = tmp_path.resolve()
    manifest_skipped = make_crate(root, "beta", publish_flag=False)
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
