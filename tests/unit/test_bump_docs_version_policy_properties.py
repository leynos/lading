"""Property tests for the documentation version policy.

These invariants span generated package names and version shapes. The named
examples live in :mod:`tests.unit.test_bump_docs_version_policy`.
"""

import hypothesis.strategies as st
from hypothesis import given, settings

from tests.unit.bump_docs_policy_support import TARGET_VERSION, rewrite

_NON_MATCHING_NAME = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_"
    ),
    min_size=1,
    max_size=20,
).filter(lambda name: name != "alpha")
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

    updated, changed = rewrite(snippet)

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

    updated, changed = rewrite(snippet)

    if version_line == 'version = "0.1.0"':
        assert changed, "the differing string version must be rewritten"
        assert updated == (
            f'[package]\nname = "alpha"\nversion = "{TARGET_VERSION}"\n'
        ), f"only the version value should change; got {updated!r}"
        return
    assert not changed, (
        f"a matching package with {version_line!r} must not be rewritten"
    )
    assert updated == snippet, "non-string and absent versions must round-trip"
