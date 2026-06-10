"""Hypothesis property tests for crate-name canonicalisation.

``_canonical_crate_name`` is documented as critical to prevent false
out-of-plan classifications: Cargo diagnostics report dependency names with
hyphens (e.g. ``my-crate``) while manifests and the publish plan store them
with underscores (e.g. ``my_crate``). These properties verify that
canonicalisation unifies the two spellings and that a publish-order index keyed
by canonical name resolves regardless of the separator styling used to query
it.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from lading.commands.publish_index_check import _canonical_crate_name

# Canonical (underscore-only) crate names, e.g. ``alpha`` or ``my_crate_core``.
_canonical_names = st.from_regex(r"[a-z][a-z0-9]*(_[a-z0-9]+)*", fullmatch=True)
# Arbitrary names spanning the characters Cargo and manifests may use.
_arbitrary_names = st.from_regex(r"[A-Za-z0-9_-]+", fullmatch=True)


@given(name=_arbitrary_names)
@settings(max_examples=100, deadline=None)
def test_canonical_crate_name_removes_all_hyphens(name: str) -> None:
    """Canonicalisation drops every hyphen, preserves length, and is idempotent."""
    canonical = _canonical_crate_name(name)
    assert "-" not in canonical
    assert len(canonical) == len(name)
    assert _canonical_crate_name(canonical) == canonical


@given(segments=st.lists(_canonical_names, min_size=1, max_size=4))
@settings(max_examples=100, deadline=None)
def test_canonical_crate_name_unifies_separators(segments: list[str]) -> None:
    """Hyphen- and underscore-joined spellings of the segments canonicalise equal."""
    hyphenated = "-".join(segments)
    underscored = "_".join(segments)
    assert _canonical_crate_name(hyphenated) == _canonical_crate_name(underscored)
    assert _canonical_crate_name(hyphenated) == underscored


@given(
    canonical_names=st.lists(_canonical_names, min_size=1, max_size=6, unique=True),
    data=st.data(),
)
@settings(max_examples=100, deadline=None)
def test_canonicalisation_enables_restyled_publish_order_lookup(
    canonical_names: list[str], data: st.DataObject
) -> None:
    """A publish-order index keyed by canonical name resolves any separator styling.

    Mirrors how ``_validate_dependency_placement`` builds
    ``publishable_name_indexes`` and looks up crate positions, confirming that a
    differently-styled (hyphenated) query for a planned crate still maps to the
    crate's true publish-order position.
    """
    index_by_canonical = {
        _canonical_crate_name(name): position
        for position, name in enumerate(canonical_names)
    }
    for position, canonical in enumerate(canonical_names):
        restyled = "".join(
            data.draw(st.sampled_from(("-", "_"))) if char == "_" else char
            for char in canonical
        )
        assert index_by_canonical[_canonical_crate_name(restyled)] == position
