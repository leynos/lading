"""Tests for the canonical Cargo dependency-section vocabulary.

Issue #103: the section names live once in
:data:`lading.commands.bump_toml.DEPENDENCY_SECTIONS`; the kind mapping in
``bump`` and the snippet rewriting in ``bump_docs`` must stay in agreement
with it.
"""

from __future__ import annotations

import typing as typ

import hypothesis.strategies as st
from hypothesis import given, settings
from tomlkit import parse as parse_toml

from lading.commands import bump, bump_docs, bump_toml

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

_DECOY_SECTIONS = ("decoy-dependencies", "tooling")


def test_kind_mapping_agrees_with_canonical_sections() -> None:
    """Every mapped section is canonical and every canonical section is mapped."""
    mapped = set(bump._DEPENDENCY_SECTION_BY_KIND.values())

    assert mapped == set(bump_toml.DEPENDENCY_SECTIONS)


def test_workspace_dependency_sections_use_canonical_vocabulary() -> None:
    """The workspace manifest update targets exactly the canonical sections."""
    sections = bump._workspace_dependency_sections(["alpha", "beta"])

    assert tuple(sections) == bump_toml.DEPENDENCY_SECTIONS
    assert all(names == {"alpha", "beta"} for names in sections.values())


def _render_manifest(sections: tuple[str, ...]) -> str:
    """Render a manifest with one pinned dependency per requested section."""
    blocks = [
        f'[{section}]\nalpha = "1.0.0"\nother = "0.9.0"\n' for section in sections
    ]
    return "\n".join(blocks)


@settings(max_examples=40, deadline=None)
@given(
    present=st.sets(st.sampled_from(bump_toml.DEPENDENCY_SECTIONS)),
    decoys=st.sets(st.sampled_from(_DECOY_SECTIONS)),
)
def test_snippet_rewrite_visits_exactly_canonical_sections(
    present: set[str], decoys: set[str]
) -> None:
    """Rewriting updates targets in canonical sections and nothing else."""
    ordered = tuple(
        section
        for section in (*bump_toml.DEPENDENCY_SECTIONS, *_DECOY_SECTIONS)
        if section in present | decoys
    )
    document = parse_toml(_render_manifest(ordered))

    changed = bump_docs.update_toml_snippet_dependencies(document, ("alpha",), "2.0.0")

    assert changed is bool(present)
    rendered = document.as_string()
    for section in ordered:
        table = document[section]
        expected_alpha = "2.0.0" if section in present else "1.0.0"
        assert table["alpha"] == expected_alpha, rendered
        # Non-target crates are never rewritten, canonical section or not.
        assert table["other"] == "0.9.0", rendered


def test_multi_section_manifest_rewrite_snapshot(
    snapshot: SnapshotAssertion,
) -> None:
    """The rewritten multi-section manifest output is locked by snapshot."""
    manifest = _render_manifest(bump_toml.DEPENDENCY_SECTIONS + _DECOY_SECTIONS)
    document = parse_toml(manifest)

    assert bump_docs.update_toml_snippet_dependencies(document, ("alpha",), "2.0.0")
    assert snapshot == document.as_string()
