"""Tests for the canonical Cargo dependency-section vocabulary.

Issue #103: the section names live once in
:data:`lading.commands.bump_toml_dependencies.DEPENDENCY_SECTIONS`; the kind
mapping in ``bump`` and the snippet rewriting in ``bump_docs`` must stay in
agreement with it.

Issue #369: a documented dependency is identified by its ``package`` field
when present and by its key otherwise, matching the manifest pass.

Issue #370: the documentation pass must reach the same dependency locations as
the manifest pass, so the property below also generates ``[workspace.*]`` and
``[target.<spec>.*]`` tables.
"""

import collections.abc as cabc
import typing as typ

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings
from tomlkit import parse as parse_toml

from lading.commands import bump_docs, bump_manifests, bump_toml, bump_toml_dependencies

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

# A table location is the key path that leads to a dependency table: the
# section name is appended to the prefix, so ``()`` is a top-level
# ``[dependencies]`` and ``("target", "'cfg(unix)'")`` is
# ``[target.'cfg(unix)'.dependencies]``.
type _Prefix = tuple[str, ...]

_DECOY_SECTIONS = ("decoy-dependencies", "tooling")
# The parsed key of each target selector, mapped to the spelling the rendered
# manifest uses. Quoting is a source-level concern only, so the property
# generates a quoted ``cfg(...)`` selector in both quote styles and a bare
# triple, while resolving the tables by their unquoted key.
_TARGET_SELECTOR_SPELLINGS: dict[str, str] = {
    "cfg(unix)": "'cfg(unix)'",
    "cfg(windows)": '"cfg(windows)"',
    "x86_64-unknown-linux-gnu": "x86_64-unknown-linux-gnu",
}
_CANONICAL_PREFIXES: tuple[_Prefix, ...] = (
    (),
    ("workspace",),
    *(("target", selector) for selector in _TARGET_SELECTOR_SPELLINGS),
)
_DECOY_PREFIXES: tuple[_Prefix, ...] = (
    ("workspace", "tooling"),
    ("target", "cfg(unix)", "decoy-dependencies"),
)
_CANONICAL_LOCATIONS: tuple[_Prefix, ...] = tuple(
    (*prefix, section)
    for prefix in _CANONICAL_PREFIXES
    for section in bump_toml_dependencies.DEPENDENCY_SECTIONS
)
_DECOY_LOCATIONS: tuple[_Prefix, ...] = (
    *((section,) for section in _DECOY_SECTIONS),
    *_DECOY_PREFIXES,
)


def test_kind_mapping_agrees_with_canonical_sections() -> None:
    """Every mapped section is canonical and every canonical section is mapped."""
    mapped = set(bump_manifests._DEPENDENCY_SECTION_BY_KIND.values())

    assert mapped == set(bump_toml_dependencies.DEPENDENCY_SECTIONS), (
        "the kind mapping and the canonical section list must agree exactly"
    )


def test_workspace_dependency_sections_use_canonical_vocabulary() -> None:
    """The workspace manifest update targets exactly the canonical sections."""
    sections = bump_manifests._workspace_dependency_sections(["alpha", "beta"])

    assert tuple(sections) == bump_toml_dependencies.DEPENDENCY_SECTIONS, (
        "workspace updates must target the canonical sections in order"
    )
    assert all(names == {"alpha", "beta"} for names in sections.values()), (
        "every canonical section must receive the same dependency names"
    )


def _render_key(key: str) -> str:
    """Return ``key`` spelled for a table header."""
    return _TARGET_SELECTOR_SPELLINGS.get(key, key)


def _render_manifest(locations: cabc.Iterable[_Prefix]) -> str:
    """Render one pinned dependency block per requested table location."""
    blocks = [
        f"[{('.'.join(map(_render_key, location)))}]\n"
        'alpha = "1.0.0"\nother = "0.9.0"\n'
        for location in locations
    ]
    return "\n".join(blocks)


@settings(max_examples=40)
@given(
    present=st.sets(st.sampled_from(_CANONICAL_LOCATIONS)),
    decoys=st.sets(st.sampled_from(_DECOY_LOCATIONS)),
)
def test_snippet_rewrite_visits_exactly_canonical_locations(
    present: set[_Prefix], decoys: set[_Prefix]
) -> None:
    """Rewriting updates targets in canonical locations and nowhere else."""
    ordered = tuple(
        location
        for location in (*_CANONICAL_LOCATIONS, *_DECOY_LOCATIONS)
        if location in present | decoys
    )
    document = parse_toml(_render_manifest(ordered))

    changed = bump_docs.update_toml_snippet_dependencies(document, ("alpha",), "2.0.0")

    assert changed is bool(present), (
        "the rewrite must report a change exactly when a canonical location is "
        "present, including when that location is nested under workspace or target"
    )
    rendered = document.as_string()
    for location in ordered:
        table = bump_toml.select_table(document, location)
        assert table is not None, f"expected a table at {location!r}"
        expected_alpha = "2.0.0" if location in present else "1.0.0"
        assert table["alpha"] == expected_alpha, rendered
        # Non-target crates are never rewritten, canonical location or not.
        assert table["other"] == "0.9.0", rendered


def test_multi_section_manifest_rewrite_snapshot(
    snapshot: SnapshotAssertion,
) -> None:
    """The rewritten multi-location manifest output is locked by snapshot."""
    manifest = _render_manifest((*_CANONICAL_LOCATIONS, *_DECOY_LOCATIONS))
    document = parse_toml(manifest)

    assert bump_docs.update_toml_snippet_dependencies(document, ("alpha",), "2.0.0"), (
        "the multi-location manifest rewrite must report a change"
    )
    assert snapshot == document.as_string(), (
        "the rewritten manifest must match the recorded snapshot"
    )


# Each case names the dependency header a fence carries and the exact text the
# bump to 2.0.0 must produce. The header is the whole entry, so the expected
# text differs only in the version it pins.
_IDENTITY_CASES: typ.Final[tuple[tuple[str, str, str, bool], ...]] = (
    (
        "inline-alias",
        '[dependencies]\nalias = { package = "alpha", version = "1.0.0" }\n',
        '[dependencies]\nalias = { package = "alpha", version = "2.0.0" }\n',
        True,
    ),
    (
        "nested-table-alias",
        '[dependencies.alias]\npackage = "alpha"\nversion = "1.0.0"\n',
        '[dependencies.alias]\npackage = "alpha"\nversion = "2.0.0"\n',
        True,
    ),
    (
        "dotted-alias",
        '[dependencies]\nalias.package = "alpha"\nalias.version = "1.0.0"\n',
        '[dependencies]\nalias.package = "alpha"\nalias.version = "2.0.0"\n',
        True,
    ),
    (
        "key-collides-with-other-package",
        '[dependencies]\nalpha = { package = "unrelated", version = "3.0.0" }\n',
        '[dependencies]\nalpha = { package = "unrelated", version = "3.0.0" }\n',
        False,
    ),
    (
        "dotted-fields-interleaved",
        (
            '[dependencies]\nalias.package = "alpha"\n'
            'other = "1.0.0"\n'
            'alias.version = "1.0.0"\n'
        ),
        (
            '[dependencies]\nalias.package = "alpha"\n'
            'other = "1.0.0"\n'
            'alias.version = "2.0.0"\n'
        ),
        True,
    ),
    (
        "key-based-string",
        '[dependencies]\nalpha = "1.0.0"\n',
        '[dependencies]\nalpha = "2.0.0"\n',
        True,
    ),
    (
        "key-based-inline-table",
        '[dependencies]\nalpha = { version = "1.0.0", features = ["default"] }\n',
        '[dependencies]\nalpha = { version = "2.0.0", features = ["default"] }\n',
        True,
    ),
    (
        "key-matches-its-own-package",
        '[dependencies]\nalpha = { package = "alpha", version = "1.0.0" }\n',
        '[dependencies]\nalpha = { package = "alpha", version = "2.0.0" }\n',
        True,
    ),
    (
        "multiple-aliases",
        (
            '[dependencies]\nfirst = { package = "alpha", version = "1.0.0" }\n'
            'second = { package = "alpha", version = "1.0.0" }\n'
        ),
        (
            '[dependencies]\nfirst = { package = "alpha", version = "2.0.0" }\n'
            'second = { package = "alpha", version = "2.0.0" }\n'
        ),
        True,
    ),
    (
        "empty-package-is-unidentifiable",
        '[dependencies]\nalias = { package = "", version = "1.0.0" }\n',
        '[dependencies]\nalias = { package = "", version = "1.0.0" }\n',
        False,
    ),
    (
        "non-string-package-is-unidentifiable",
        '[dependencies]\nalias = { package = 1, version = "1.0.0" }\n',
        '[dependencies]\nalias = { package = 1, version = "1.0.0" }\n',
        False,
    ),
    (
        "alias-already-current",
        '[dependencies]\nalias = { package = "alpha", version = "2.0.0" }\n',
        '[dependencies]\nalias = { package = "alpha", version = "2.0.0" }\n',
        False,
    ),
    (
        "inline-comment-preserved",
        '[dependencies]\nalias = { package = "alpha", version = "1.0.0" }  # pinned\n',
        '[dependencies]\nalias = { package = "alpha", version = "2.0.0" }  # pinned\n',
        True,
    ),
    (
        "dotted-comment-preserved",
        '[dependencies]\nalias.package = "alpha"\nalias.version = "1.0.0"  # pinned\n',
        '[dependencies]\nalias.package = "alpha"\nalias.version = "2.0.0"  # pinned\n',
        True,
    ),
)


@pytest.mark.parametrize(
    ("test_id", "snippet", "expected", "expected_changed"),
    _IDENTITY_CASES,
    ids=[case[0] for case in _IDENTITY_CASES],
)
def test_dependency_identity_uses_package_then_key(
    test_id: str,
    snippet: str,
    expected: str,
    *,
    expected_changed: bool,
) -> None:
    """A documented dependency is identified by ``package`` then by its key."""
    document = parse_toml(snippet)

    changed = bump_docs.update_toml_snippet_dependencies(document, ("alpha",), "2.0.0")

    assert changed is expected_changed, (
        f"{test_id}: the rewrite changed flag must be {expected_changed}"
    )
    assert document.as_string() == expected, (
        f"{test_id}: the rewritten fence must match the expected text"
    )


def test_out_of_order_section_alias_is_updated() -> None:
    """An out-of-order dependency section is traversed for aliases."""
    snippet = (
        '[dependencies.alias]\npackage = "alpha"\nversion = "1.0.0"\n'
        "[other]\nx = 1\n"
        '[dependencies.beta]\npackage = "beta"\nversion = "1.0.0"\n'
    )
    document = parse_toml(snippet)

    changed = bump_docs.update_toml_snippet_dependencies(document, ("alpha",), "2.0.0")

    assert changed, "the out-of-order section must yield the aliased entry"
    rendered = document.as_string()
    assert 'version = "2.0.0"' in rendered, "the aliased entry must reach the target"
    assert 'package = "beta"' in rendered, "the unrelated entry must stay untouched"
    assert rendered.count('version = "1.0.0"') == 1, (
        "only the unrelated entry may keep its version"
    )


def test_decoy_section_alias_is_not_updated() -> None:
    """Sections outside the canonical vocabulary are never rewritten."""
    snippet = (
        '[decoy-dependencies]\nalias = { package = "alpha", version = "1.0.0" }\n'
        '[tooling]\nalias = { package = "alpha", version = "1.0.0" }\n'
    )
    document = parse_toml(snippet)

    changed = bump_docs.update_toml_snippet_dependencies(document, ("alpha",), "2.0.0")

    assert changed is False, "a decoy section must not report a change"
    assert document.as_string() == snippet, "the decoy sections must stay verbatim"
