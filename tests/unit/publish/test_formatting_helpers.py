"""Unit tests for publish formatting helpers."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import hypothesis.strategies as st
from hypothesis import given

from lading.commands import publish_plan

from .conftest import make_crate

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


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

    assert lines == ["Header:", "- alpha", "- beta"], (
        "default formatter should stringify each item under the header"
    )


def test_render_section_omits_header_for_empty_sequences() -> None:
    """Helper returns no lines when there is nothing to report."""
    assert publish_plan.render_section((), header="Header:") == [], (
        "an empty sequence renders no lines when no empty_message is given"
    )


def test_append_section_extends_list_in_place() -> None:
    """The backwards-compatible shim mutates ``lines`` like the old helper."""
    lines = ["preamble"]

    publish_plan.append_section(lines, ("alpha", "beta"), header="Header:")

    assert lines == ["preamble", "Header:", "- alpha", "- beta"]


def test_append_section_appends_nothing_when_empty() -> None:
    """An empty section leaves ``lines`` untouched, matching prior behaviour."""
    lines = ["preamble"]

    publish_plan.append_section(lines, (), header="Header:")

    assert lines == ["preamble"]


def test_append_section_matches_render_section() -> None:
    """The shim delegates to ``render_section`` so both stay in lock-step."""
    lines: list[str] = []

    publish_plan.append_section(
        lines,
        ("alpha", "beta"),
        header="Header:",
        formatter=str.upper,
    )

    assert lines == publish_plan.render_section(
        ("alpha", "beta"), header="Header:", formatter=str.upper
    )


def test_format_plan_formats_skipped_sections(tmp_path: Path) -> None:
    """``format_plan`` renders skipped crates using their names only."""
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


def test_render_section_renders_empty_message_when_empty() -> None:
    """The optional ``empty_message`` replaces an absent section."""
    lines = publish_plan.render_section(
        (), header="Header:", empty_message="Nothing to report"
    )

    assert lines == ["Nothing to report"], "empty_message replaces an absent section"


@given(
    items=st.lists(st.text(min_size=1, max_size=8), max_size=6),
    use_empty_message=st.booleans(),
)
def test_render_section_invariants(
    items: list[str],
    use_empty_message: bool,  # noqa: FBT001 - hypothesis-driven keyword
) -> None:
    """Header appears iff items exist; each item renders exactly once."""
    empty_message = "Nothing to report" if use_empty_message else None

    lines = publish_plan.render_section(
        items, header="Header:", empty_message=empty_message
    )

    if items:
        assert lines[0] == "Header:", "the header appears first when items exist"
        assert lines[1:] == [f"- {item}" for item in items], (
            "each item renders exactly once as a bullet"
        )
    elif empty_message is not None:
        assert lines == [empty_message], (
            "only the empty_message renders for an empty section"
        )
    else:
        assert lines == [], (
            "no lines render for an empty section without an empty_message"
        )


def _normalise_plan_message(message: str, root: Path) -> str:
    """Replace the absolute workspace root so snapshots stay deterministic."""
    return message.replace(str(root), "<workspace>")


def test_format_plan_snapshot_with_publishable(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """The full rendered plan with publishable and skipped crates is stable."""
    root = tmp_path.resolve()
    plan = publish_plan.PublishPlan(
        workspace_root=root,
        publishable=(make_crate(root, "alpha"), make_crate(root, "beta")),
        skipped_manifest=(make_crate(root, "gamma", publish_flag=False),),
        skipped_configuration=(make_crate(root, "delta"),),
        missing_configuration_exclusions=("missing",),
    )

    message = publish_plan.format_plan(plan, strip_patches="all")

    assert snapshot == _normalise_plan_message(message, root), (
        "rendered plan with publishable and skipped crates matches the snapshot"
    )


def test_format_plan_snapshot_without_publishable(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """An empty publish set renders the empty message instead of a header."""
    root = tmp_path.resolve()
    plan = publish_plan.PublishPlan(
        workspace_root=root,
        publishable=(),
        skipped_manifest=(),
        skipped_configuration=(),
        missing_configuration_exclusions=(),
    )

    message = publish_plan.format_plan(plan, strip_patches="per-crate")

    assert snapshot == _normalise_plan_message(message, root), (
        "empty publish set renders the empty-state plan snapshot"
    )
