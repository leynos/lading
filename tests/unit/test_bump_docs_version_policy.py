"""Unit tests for the documentation-pass version policy (issue #368).

The documentation pass rewrites TOML fences in configured Markdown files. Before
issue #368 it assigned the target version to ``[package].version`` and
``[workspace.package].version`` in every fence that parsed, so it invented
versions for metadata-only snippets, re-versioned unrelated example manifests,
and replaced ``version.workspace = true`` with a literal.

The policy these tests pin is: replace an existing string version only when
``[package].name`` names a bumped crate, never insert a key, and leave every
inherited or non-string value alone.
"""

import collections.abc as cabc
import typing as typ

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings
from tomlkit import parse as parse_toml

from lading.commands import bump_docs_fences, bump_toml

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

_TARGET_VERSION = "2.0.0"
_TARGETS = frozenset({"alpha"})

_METADATA_ONLY = """\
[package.metadata.mytool]
mode = "warn"
"""

_UNRELATED_APP = """\
[package]
name = "my-app"
version = "0.1.0"
"""

_NAMELESS_PACKAGE = """\
[package]
version = "0.1.0"
"""

_INHERITED_DOTTED = """\
[package]
name = "alpha"
version.workspace = true
"""

_INHERITED_INLINE = """\
[package]
name = "alpha"
version = { workspace = true }
"""

_MISSING_VERSION = """\
[package]
name = "alpha"
edition = "2021"
"""

_VIRTUAL_ROOT = """\
[workspace.package]
version = "0.1.0"
edition = "2021"
"""

_MATCHING_PACKAGE = """\
[package]
name = "alpha"
version = "0.1.0"
"""

_MATCHING_WITH_WORKSPACE = """\
[package]
name = "alpha"
version = "0.1.0"

[workspace.package]
version = "0.1.0"
"""

_ALREADY_TARGET = """\
[package]
name = "alpha"
version = "2.0.0"
"""

_COMPENTED_VERSION = """\
[package]
name = "alpha"
version = "0.1.0" # keep this note
"""

_UNCHANGED_SNIPPETS = (
    pytest.param(_METADATA_ONLY, id="metadata-only"),
    pytest.param(_UNRELATED_APP, id="unrelated-package"),
    pytest.param(_NAMELESS_PACKAGE, id="nameless-package"),
    pytest.param(_INHERITED_DOTTED, id="inherited-dotted"),
    pytest.param(_INHERITED_INLINE, id="inherited-inline"),
    pytest.param(_MISSING_VERSION, id="missing-version"),
    pytest.param(_VIRTUAL_ROOT, id="virtual-root"),
)


def _rewrite(
    snippet: str, targets: cabc.Collection[str] = _TARGETS
) -> tuple[str, bool]:
    """Return ``snippet`` after the documentation pass for ``targets``."""
    return bump_docs_fences.update_toml_snippet_versions(
        snippet, targets, _TARGET_VERSION
    )


@pytest.mark.parametrize("snippet", _UNCHANGED_SNIPPETS)
def test_ineligible_snippets_are_returned_verbatim(snippet: str) -> None:
    """Ineligible package tables stay byte-for-byte identical and unchanged."""
    updated, changed = _rewrite(snippet)

    assert not changed, "an ineligible snippet must report no change"
    assert updated == snippet, (
        f"the pass must not rewrite this snippet; got {updated!r}"
    )


def test_metadata_only_snippet_gains_no_package_table() -> None:
    """A ``[package.metadata.*]`` snippet must not gain an implicit version."""
    updated, changed = _rewrite(_METADATA_ONLY)

    assert not changed, "a metadata-only snippet has no version to replace"
    assert "[package]\nversion" not in updated, (
        "the pass must not invent a [package] table for a metadata-only snippet"
    )


def test_unrelated_package_is_not_reversioned() -> None:
    """A downstream application manifest keeps its own version."""
    updated, changed = _rewrite(_UNRELATED_APP)

    assert not changed, "an unrelated package name must not be rewritten"
    assert 'version = "0.1.0"' in updated, (
        "the documented application version must survive the pass"
    )


def test_inherited_versions_are_left_alone() -> None:
    """Dotted and inline workspace inheritance keep their non-string values."""
    for snippet in (_INHERITED_DOTTED, _INHERITED_INLINE):
        updated, changed = _rewrite(snippet)

        assert not changed, f"inheritance must not be replaced in {snippet!r}"
        assert updated == snippet, (
            f"workspace inheritance must survive byte-for-byte; got {updated!r}"
        )


def test_matching_package_version_is_replaced() -> None:
    """A bumped crate's own string version is replaced."""
    updated, changed = _rewrite(_MATCHING_PACKAGE)

    assert changed, "the bumped crate's documented version must be rewritten"
    assert updated == _MATCHING_PACKAGE.replace("0.1.0", _TARGET_VERSION), (
        f"only the version value should change; got {updated!r}"
    )


def test_matching_package_keeps_its_inline_comment() -> None:
    """Replacing the value preserves the trailing comment and spacing."""
    updated, changed = _rewrite(_COMPENTED_VERSION)

    assert changed, "the differing version must be replaced"
    assert updated == _COMPENTED_VERSION.replace("0.1.0", _TARGET_VERSION), (
        f"inline trivia must survive the replacement; got {updated!r}"
    )


def test_matching_workspace_table_is_rewritten_alongside_package() -> None:
    """``[workspace.package]`` follows the package-name gate in the same fence."""
    updated, changed = _rewrite(_MATCHING_WITH_WORKSPACE)

    assert changed, "both eligible tables must be rewritten"
    assert updated.count(f'version = "{_TARGET_VERSION}"') == 2, (
        f"the package and workspace versions must both change; got {updated!r}"
    )


def test_virtual_root_workspace_table_is_not_rewritten() -> None:
    """A fence with no package name is not evidence of this workspace's root."""
    updated, changed = _rewrite(_VIRTUAL_ROOT)

    assert not changed, "a virtual-root fence has no package name to gate on"
    assert updated == _VIRTUAL_ROOT, (
        "the virtual-root workspace version must survive unchanged"
    )


def test_already_matching_version_reports_no_change() -> None:
    """A fence already at the target version is not reported as changed."""
    updated, changed = _rewrite(_ALREADY_TARGET)

    assert not changed, "an equal version must not be reported as a change"
    assert updated == _ALREADY_TARGET, "an equal version must round-trip"


def test_empty_target_set_disables_the_version_gate() -> None:
    """An empty bumped set leaves even a matching package untouched."""
    updated, changed = _rewrite(_MATCHING_PACKAGE, targets=frozenset())

    assert not changed, "no crate is being bumped, so nothing may be rewritten"
    assert updated == _MATCHING_PACKAGE, (
        "an empty target set must disable the package version gate"
    )


def test_out_of_order_package_table_is_gated_and_rewritten() -> None:
    """An ``OutOfOrderTableProxy`` package table is read through the same gate."""
    snippet = (
        '[package]\nname = "alpha"\nversion = "0.1.0"\n'
        '[dependencies]\nbeta = "1.0.0"\n'
        "[package.metadata.docs.rs]\nall-features = true\n"
    )

    updated, changed = _rewrite(snippet)

    assert changed, "the out-of-order package version must be rewritten"
    assert 'version = "2.0.0"' in updated, (
        f"the out-of-order table version must change; got {updated!r}"
    )
    assert 'beta = "1.0.0"' in updated, "an untouched dependency must keep its version"


def test_out_of_order_unrelated_package_is_not_rewritten() -> None:
    """The gate also rejects an out-of-order package with another name."""
    snippet = (
        '[package]\nname = "my-app"\nversion = "0.1.0"\n'
        '[dependencies]\nbeta = "1.0.0"\n'
        "[package.metadata.docs.rs]\nall-features = true\n"
    )

    updated, changed = _rewrite(snippet)

    assert not changed, "the gate must read the name from the proxy table"
    assert updated == snippet, "an unrelated out-of-order package must not change"


def test_eligible_dependency_fence_still_rewrites_its_dependency() -> None:
    """Dependency-reference rewriting is independent of the package gate."""
    snippet = '[dependencies]\nalpha = "0.1.0"\n'

    updated, changed = _rewrite(snippet)

    assert changed, "a bumped crate's dependency requirement must be rewritten"
    assert updated == f'[dependencies]\nalpha = "{_TARGET_VERSION}"\n', (
        f"the dependency requirement must change; got {updated!r}"
    )


_NON_MATCHING_NAME = st.text(min_size=1).filter(lambda name: name != "alpha")
_VERSION_SHAPES = st.sampled_from([
    'version = "0.1.0"',
    "version.workspace = true",
    "version = { workspace = true }",
    "",
])


@settings(max_examples=50)
@given(name=_NON_MATCHING_NAME, version_line=_VERSION_SHAPES)
def test_unrelated_package_names_are_never_rewritten(
    name: str, version_line: str
) -> None:
    """No unrelated package name and version shape is ever rewritten."""
    snippet = f'[package]\nname = "{name}"\n' + (
        f"{version_line}\n" if version_line else ""
    )

    updated, changed = _rewrite(snippet)

    assert not changed, f"an unrelated package {name!r} must not be rewritten"
    assert updated == snippet, (
        f"the snippet for {name!r} must survive unchanged; got {updated!r}"
    )


@settings(max_examples=50)
@given(version_line=_VERSION_SHAPES)
def test_a_matching_package_only_rewrites_a_string_version(
    version_line: str,
) -> None:
    """A matching package rewrites only the plain string version shape."""
    snippet = '[package]\nname = "alpha"\n' + (
        f"{version_line}\n" if version_line else ""
    )

    updated, changed = _rewrite(snippet)

    if version_line == 'version = "0.1.0"':
        assert changed, "the differing string version must be rewritten"
        assert updated == (
            f'[package]\nname = "alpha"\nversion = "{_TARGET_VERSION}"\n'
        ), f"only the version value should change; got {updated!r}"
        return
    assert not changed, (
        f"a matching package with {version_line!r} must not be rewritten"
    )
    assert updated == snippet, "non-string and absent versions must round-trip"


_MIXED_DOCUMENT = f"""\
# Guide

The policy table this project reads:

```toml
{_METADATA_ONLY}```

A downstream application looks like:

```toml
{_UNRELATED_APP}```

Depend on the crate:

```toml
[dependencies]
alpha = "0.1.0"
```

A workspace member inherits the version:

```toml
{_INHERITED_DOTTED}```
"""


def test_mixed_document_rewrites_only_the_dependency_fence(
    snapshot: SnapshotAssertion,
) -> None:
    """A mixed document changes only the eligible dependency fence."""
    updated, changed = bump_docs_fences.rewrite_markdown_toml_fences(
        _MIXED_DOCUMENT, _TARGETS, _TARGET_VERSION
    )

    assert changed, "the dependency fence is eligible and must change"
    assert updated == snapshot, "the rendered mixed document drifted"
    assert 'alpha = "2.0.0"' in updated, "the dependency requirement must change"
    assert 'version = "2.0.0"' not in updated, (
        "the pass must not write a package version anywhere in the document"
    )
    assert updated.count('"2.0.0"') == 1, (
        "only the dependency requirement may carry the target version"
    )


def test_mixed_document_without_an_eligible_fence_is_unchanged() -> None:
    """A document with no eligible fence round-trips byte-for-byte."""
    document = _MIXED_DOCUMENT.replace(
        '[dependencies]\nalpha = "0.1.0"\n', '[dependencies]\nbeta = "0.1.0"\n'
    )

    updated, changed = bump_docs_fences.rewrite_markdown_toml_fences(
        document, _TARGETS, _TARGET_VERSION
    )

    assert not changed, "no fence references a bumped crate"
    assert updated == document, "an ineligible document must round-trip"


def test_package_gate_agrees_with_the_selected_table() -> None:
    """The gate reads the name from the same table the rewrite would mutate."""
    document = parse_toml(_MATCHING_PACKAGE)
    package = bump_toml.select_table(document, ("package",))

    assert package is not None, "the package table must resolve"
    assert bump_toml.value_as_string(package.get("name")) == "alpha", (
        "the gate must read the package name through value_as_string"
    )
