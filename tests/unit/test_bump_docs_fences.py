"""Unit tests for Markdown fence rewriting in documentation bumps.

The renderer must preserve the source when a fence is unchanged, keep a list
item's marker and continuation indentation when it changes, and align its line
splicing with MarkdownIt's own line boundaries.
"""

from markdown_it import MarkdownIt

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


def test_a_block_quoted_list_fence_keeps_its_quote() -> None:
    """Only the list marker becomes indentation; the quote stays."""
    text = '> - ```toml\n>   [dependencies]\n>   alpha = "0.1.0"\n>   ```\n'

    updated, changed = bump_docs_fences.rewrite_markdown_toml_fences(
        text, ("alpha",), "1.2.3"
    )

    assert changed, "the dependency version must be rewritten"
    assert updated.startswith("> - ```toml\n>   "), (
        "the quote must survive and the list marker must not repeat"
    )
    parsed = MarkdownIt("commonmark").parse(updated)
    fences = [token for token in parsed if token.type == "fence"]
    assert fences, "the rewritten TOML must remain a fenced block in the quote"
    assert 'alpha = "1.2.3"' in fences[0].content, (
        "the fence must carry the rewritten dependency version"
    )


def test_an_unclosed_fence_does_not_reuse_a_body_line_as_its_closer() -> None:
    """A body line that mentions the marker is emitted once and closed."""
    text = '```toml\n[dependencies]\nalpha = "0.1.0" # ```rust\n'

    updated, changed = bump_docs_fences.rewrite_markdown_toml_fences(
        text, ("alpha",), "1.2.3"
    )

    assert changed, "the dependency version must be rewritten"
    assert updated.count("alpha = ") == 1, (
        "the last body line must not be duplicated as a closing fence"
    )
    assert " # ```rust\n" in updated, "the body line must keep its text"
    assert updated.endswith("```\n"), "a closing fence must be added once"


def test_a_quoted_list_fence_keeps_its_closing_fence() -> None:
    """A quoted list item's closer uses the continuation prefix."""
    text = '> - ```toml\n>   [dependencies]\n>   alpha = "0.1.0"\n>   ```\n'

    updated, changed = bump_docs_fences.rewrite_markdown_toml_fences(
        text, ("alpha",), "1.2.3"
    )

    assert changed, "the dependency version must be rewritten"
    assert updated.endswith(">   ```\n"), (
        "the closer must keep its container prefix and close the fence"
    )
    parsed = MarkdownIt("commonmark").parse(updated)
    assert [token.type for token in parsed].count("fence") == 1, (
        "the rewrite must leave exactly one fenced block"
    )


def test_a_quote_after_a_list_marker_is_preserved() -> None:
    """A list marker followed by a block quote keeps the quote markers."""
    text = '- > ```toml\n  > [dependencies]\n  > alpha = "0.1.0"\n  > ```\n'

    updated, changed = bump_docs_fences.rewrite_markdown_toml_fences(
        text, ("alpha",), "1.2.3"
    )

    assert changed, "the dependency version must be rewritten"
    assert "  > [dependencies]" in updated, (
        "the block quote after the list marker must be preserved"
    )
