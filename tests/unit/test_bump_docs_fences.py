"""Unit tests for Markdown fence rewriting in documentation bumps.

The renderer must preserve the source when a fence is unchanged, keep a list
item's marker and continuation indentation when it changes, and align its line
splicing with MarkdownIt's own line boundaries.
"""

from lading.commands import bump_docs_fences


def _list_fence(version: str) -> str:
    """Return a list-contained TOML fence pinned to ``version``."""
    return f'- ```toml\n  [dependencies]\n  alpha = "{version}"\n  ```\n'


def _separated_fence(version: str) -> str:
    """Return a fence preceded by a U+2028 in the same Markdown line."""
    separator = chr(0x2028)
    return (
        f"Before{separator}still the same line\n"
        "```toml\n"
        f'[dependencies]\nalpha = "{version}"\n'
        "```\n"
    )


def test_an_unchanged_fence_is_copied_verbatim() -> None:
    """An unchanged fence keeps its original opener and longer closer."""
    text = '````toml\n[dependencies]\nalpha = "0.1.0"\n`````\n'

    updated, changed = bump_docs_fences.rewrite_markdown_toml_fences(
        text, ("alpha",), "0.1.0"
    )

    assert not changed, "a same-version rewrite must report no change"
    assert updated == text, (
        "an unchanged fence must be copied byte-for-byte, including its closer"
    )


def test_a_changed_list_fence_keeps_the_list_marker() -> None:
    """A list item's fence keeps its marker and continuation indentation."""
    text = '- ```toml\n  [dependencies]\n  alpha = "0.1.0"\n  ```\n'

    updated, changed = bump_docs_fences.rewrite_markdown_toml_fences(
        text, ("alpha",), "1.2.3"
    )

    assert changed, "the dependency version must be rewritten"
    assert updated == _list_fence("1.2.3"), (
        "the list marker must appear once and the body must stay in the item"
    )


def test_line_splicing_ignores_non_markdown_separators() -> None:
    """A U+2028 in prose does not shift the fence's source lines."""
    text = _separated_fence("0.1.0")

    updated, changed = bump_docs_fences.rewrite_markdown_toml_fences(
        text, ("alpha",), "1.2.3"
    )

    assert changed, "the dependency version must be rewritten"
    assert updated == _separated_fence("1.2.3"), (
        "the prose separator must not shift the rewritten fence"
    )
