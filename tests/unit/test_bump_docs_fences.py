"""Unit tests for Markdown fence rewriting in documentation bumps.

The renderer must preserve the source when a fence is unchanged, keep a list
item's marker and continuation indentation when it changes, and align its line
splicing with MarkdownIt's own line boundaries.
"""

import hypothesis.strategies as st
import pytest
from hypothesis import given
from markdown_it import MarkdownIt

from lading.commands import bump_docs_fences, bump_docs_markdown


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


@pytest.mark.parametrize(
    ("text", "continuation"),
    [
        pytest.param(
            '> - ```toml\n>   [dependencies]\n>   alpha = "0.1.0"\n>   ```\n',
            "> - ```toml\n>   ",
            id="block-quoted-list",
        ),
        pytest.param(
            '- - ```toml\n    [dependencies]\n    alpha = "0.1.0"\n    ```\n',
            "- - ```toml\n    ",
            id="nested-list",
        ),
    ],
)
def test_a_container_fence_keeps_its_continuation_indentation(
    text: str,
    continuation: str,
) -> None:
    """Only the list marker becomes indentation; the container prefix stays."""
    updated, changed = bump_docs_fences.rewrite_markdown_toml_fences(
        text, ("alpha",), "1.2.3"
    )

    assert changed, "the dependency version must be rewritten"
    assert updated.startswith(continuation), (
        "the container prefix must survive and the list marker must not repeat"
    )
    parsed = MarkdownIt("commonmark").parse(updated)
    fences = [token for token in parsed if token.type == "fence"]
    assert len(fences) == 1, "the rewrite must remain one fenced block"
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


def test_a_tab_indented_list_fence_uses_column_indentation() -> None:
    """A tab after the marker expands to the CommonMark column stop."""
    text = '-\t```toml\n\t[dependencies]\n\talpha = "0.1.0"\n\t```\n'

    updated, changed = bump_docs_fences.rewrite_markdown_toml_fences(
        text, ("alpha",), "1.2.3"
    )

    assert changed, "the dependency version must be rewritten"
    assert "\n    [dependencies]" in updated, (
        "a tab after the marker must become four columns of continuation"
    )
    parsed = MarkdownIt("commonmark").parse(updated)
    fences = [token for token in parsed if token.type == "fence"]
    assert len(fences) == 1, (
        "the rewrite must remain one fenced block inside the list item"
    )
    assert 'alpha = "1.2.3"' in fences[0].content, (
        "the parsed fence must carry the rewritten TOML"
    )
    assert any(token.type == "bullet_list_open" for token in parsed), (
        "the fence must stay inside the list item"
    )


_FENCE_OPENER = st.sampled_from([
    "```toml",
    "````toml",
    "~~~toml",
    "~~~",
    "  ```toml",
    "- ```toml",
])
_FENCE_ENDING = st.sampled_from(["\n", "\r\n"])


@st.composite
def _fence_documents(draw: st.DrawFn) -> tuple[str, str]:
    """Return a one-fence document and a language-matched opener shape."""
    opener = draw(_FENCE_OPENER)
    ending = draw(_FENCE_ENDING)
    version = draw(st.sampled_from(["0.1.0", "1.2.3", "9.9.9"]))
    body = draw(
        st.lists(
            st.sampled_from([
                "[dependencies]",
                f'alpha = "{version}"',
                'beta = { version = "0.1.0" }',
                "",
            ]),
            min_size=1,
            max_size=4,
        )
    )
    closer_length = 3 if opener.strip().startswith("~") else 4
    closer_char = "~" if opener.strip().startswith("~") else "`"
    closer = closer_char * closer_length
    lines = [opener, *body, closer, ""]
    return ending.join(lines), version


@given(document=_fence_documents())
def test_an_unchanged_fence_is_byte_identical(
    document: tuple[str, str],
) -> None:
    """Rewriting to the fence's own version returns the source byte-for-byte."""
    text, version = document

    updated, changed = bump_docs_fences.rewrite_markdown_toml_fences(
        text, ("alpha",), version
    )

    assert not changed, "no dependency version differs, so nothing may change"
    assert updated == text, "an unchanged rewrite must be byte-identical"


@given(document=_fence_documents(), target=st.sampled_from(["1.2.3", "0.1.0"]))
def test_rewriting_a_fence_twice_is_idempotent(
    document: tuple[str, str],
    target: str,
) -> None:
    """A second rewrite with the same target changes nothing further."""
    text, _version = document

    once, _ = bump_docs_fences.rewrite_markdown_toml_fences(text, ("alpha",), target)
    twice, changed = bump_docs_fences.rewrite_markdown_toml_fences(
        once, ("alpha",), target
    )

    assert not changed, "the rewritten document already carries the target"
    assert twice == once, "a repeated rewrite must be idempotent"


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(("~~~toml", "\n"), id="bare-opener-at-eof"),
        pytest.param(("See:\n~~~toml", "\n"), id="opener-after-text-at-eof"),
        pytest.param(("~~~toml\r\n", "\r\n"), id="crlf-opener-keeps-its-ending"),
    ],
)
def test_an_opener_at_eof_gains_a_separator_before_its_body(
    case: tuple[str, str],
) -> None:
    """A fence opener with no line ending still separates its body lines."""
    text, ending = case
    token = MarkdownIt("commonmark").parse(text)[-1]

    rendered = bump_docs_markdown.render_fence(
        token,
        bump_docs_markdown.source_lines(text),
        "toml",
        lambda _content: "a = 1\nb = 2",
    )

    assert rendered == (f"~~~toml{ending}a = 1{ending}b = 2{ending}~~~{ending}"), (
        "the opener must be separated from the first generated body line"
    )
