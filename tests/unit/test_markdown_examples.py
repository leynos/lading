"""Validate the shared loader for tested Markdown examples."""

from pathlib import Path

import pytest

from tests.helpers.markdown_examples import (
    MarkdownExampleError,
    load_markdown_examples,
)


def _write_document(directory: Path, content: str) -> Path:
    """Write a Markdown sample and return its path."""
    document = directory / "guide.md"
    document.write_text(content, encoding="utf-8")
    return document


def test_loader_extracts_marked_fences_in_document_order(tmp_path: Path) -> None:
    """The loader keeps each stable identifier, language and source together."""
    document = _write_document(
        tmp_path,
        """<!-- tested-example: first -->

```python
print('first')
```

<!-- tested-example: second -->

~~~bash
echo second
~~~
""",
    )

    examples = load_markdown_examples(document)

    assert [(example.identifier, example.language) for example in examples] == [
        ("first", "python"),
        ("second", "bash"),
    ], "the loader must preserve each example's identifier and language"
    assert [example.source for example in examples] == [
        "print('first')",
        "echo second",
    ], "the loader must preserve every example's fenced source"


def test_loader_counts_physical_fence_lines_with_unicode_separator(
    tmp_path: Path,
) -> None:
    """A Unicode line separator inside JSON does not add a source line."""
    source = '{"value": "left\u2028right"}'
    document = _write_document(
        tmp_path,
        f"<!-- tested-example: unicode-separator -->\n\n```json\n{source}\n```\n",
    )

    (example,) = load_markdown_examples(document)

    assert example.source == source, (
        "the fenced source must preserve U+2028 as content on one physical line"
    )


@pytest.mark.parametrize("indent", [0, 1, 3], ids=["none", "one-space", "three-spaces"])
def test_loader_accepts_permitted_indentation_before_example_marker(
    tmp_path: Path,
    indent: int,
) -> None:
    """A marker accepts zero to three leading spaces before its comment."""
    document = _write_document(
        tmp_path,
        f"{' ' * indent}<!-- tested-example: indented-marker -->\n\n"
        "```python\nprint('indented marker')\n```\n",
    )

    (example,) = load_markdown_examples(document)

    assert (example.identifier, example.source) == (
        "indented-marker",
        "print('indented marker')",
    ), "permitted leading indentation must not hide a tested-example marker"


def test_loader_accepts_closed_fence_with_empty_body(tmp_path: Path) -> None:
    """A closed fence with no body lines remains a valid empty example."""
    document = _write_document(
        tmp_path,
        "<!-- tested-example: empty -->\n\n```json\n```\n",
    )

    (example,) = load_markdown_examples(document)

    assert not example.source, "an empty fenced body should remain empty"


def test_loader_keeps_marker_text_inside_marked_markdown_source(
    tmp_path: Path,
) -> None:
    """Marker-looking source inside a fence is not document metadata."""
    document = _write_document(
        tmp_path,
        """<!-- tested-example: markdown-source -->

```markdown
> this is literal source, not a container prefix
<!-- tested-example: inner -->
```
""",
    )

    examples = load_markdown_examples(document)

    assert [(example.identifier, example.source) for example in examples] == [
        (
            "markdown-source",
            "\n".join((
                "> this is literal source, not a container prefix",
                "<!-- tested-example: inner -->",
            )),
        )
    ], "fenced marker text must be returned as source"


def test_loader_extracts_marked_fences_inside_blockquotes(tmp_path: Path) -> None:
    """Quote prefixes are containers and do not become example source."""
    document = _write_document(
        tmp_path,
        """> <!-- tested-example: quoted -->
>
> ```python
> print('quoted')
> ```
""",
    )

    examples = load_markdown_examples(document)

    assert [(example.identifier, example.source) for example in examples] == [
        ("quoted", "print('quoted')")
    ], "a blockquote fence must retain its marker and source"


def test_loader_accepts_list_indented_blank_container_lines(tmp_path: Path) -> None:
    """A deeply indented blank container line separates marker and fence."""
    document = _write_document(
        tmp_path,
        """- Example:

  > <!-- tested-example: nested-quote -->
    >
  > ```python
  > print('nested')
  > ```
""",
    )

    (example,) = load_markdown_examples(document)

    assert (example.identifier, example.source) == (
        "nested-quote",
        "print('nested')",
    ), "an indented blank container line must not hide the marker"


def test_loader_rejects_text_after_a_blank_container_line(tmp_path: Path) -> None:
    """A container line with trailing text is not treated as blank."""
    document = _write_document(
        tmp_path,
        """- Example:

  > <!-- tested-example: nested-quote -->
    > unexpected text
  > ```python
  > print('nested')
  > ```
""",
    )

    with pytest.raises(MarkdownExampleError, match="fenced example is unmarked"):
        load_markdown_examples(document)


def test_loader_rejects_marker_from_a_different_blockquote(tmp_path: Path) -> None:
    """A separate blockquote cannot reuse an earlier quote's marker."""
    document = _write_document(
        tmp_path,
        "> <!-- tested-example: first-quote -->\n\n"
        "> ```python\n> print('second quote')\n> ```\n",
    )

    with pytest.raises(MarkdownExampleError, match="fenced example is unmarked"):
        load_markdown_examples(document)


def test_loader_extracts_marked_fences_inside_list_items(tmp_path: Path) -> None:
    """List indentation is removed as a CommonMark container prefix."""
    document = _write_document(
        tmp_path,
        """- Example:

  <!-- tested-example: list-item -->

    ~~~toml
    value = 1
    ~~~
""",
    )

    examples = load_markdown_examples(document)

    assert [
        (example.identifier, example.language, example.source) for example in examples
    ] == [("list-item", "toml", "value = 1")], (
        "the loader must return the marked code without list indentation"
    )


def test_loader_rejects_marker_from_a_different_list_item(tmp_path: Path) -> None:
    """A sibling list item's marker cannot authorize this item's fence."""
    document = _write_document(
        tmp_path,
        "- First item:\n"
        "  <!-- tested-example: previous-item -->\n"
        "- ```python\n"
        "  print('second item')\n"
        "  ```\n",
    )

    with pytest.raises(MarkdownExampleError, match="unmarked"):
        load_markdown_examples(document)


def test_loader_stops_a_quoted_fence_at_its_container_boundary(
    tmp_path: Path,
) -> None:
    """A later blockquote delimiter cannot close an earlier quote's fence."""
    document = _write_document(
        tmp_path,
        """> <!-- tested-example: quoted -->
>
> ```python
> print('quoted')
outside paragraph
> ```
""",
    )

    with pytest.raises(MarkdownExampleError, match="unterminated"):
        load_markdown_examples(document)


def test_loader_ignores_ordinary_indented_code_blocks(tmp_path: Path) -> None:
    """Indented code is not a fenced example container."""
    document = _write_document(
        tmp_path,
        """    ```python
    print('indented code')
    ```
""",
    )

    assert not load_markdown_examples(document), (
        "ordinary indented code blocks must not be treated as fences"
    )


@pytest.mark.parametrize(
    ("content", "expected_source"),
    [
        (
            (
                "<!-- tested-example: indented-open -->\n\n"
                "  ```python\n    print('indented')\n  ```\n"
            ),
            "  print('indented')",
        ),
        (
            (
                "<!-- tested-example: indented-close -->\n\n"
                "```python\nprint('indented close')\n  ```\n"
            ),
            "print('indented close')",
        ),
    ],
)
def test_loader_accepts_commonmark_fence_indentation(
    tmp_path: Path, content: str, expected_source: str
) -> None:
    """Up to three spaces may indent either fence and opening content."""
    document = _write_document(tmp_path, content)

    (example,) = load_markdown_examples(document)

    assert example.source == expected_source, (
        "opening indentation must be removed from the extracted source"
    )


@pytest.mark.parametrize(
    "fence_like_line",
    ["``", "~~~", "``` trailing text"],
    ids=["shorter-fence", "different-fence-character", "trailing-text"],
)
def test_loader_preserves_fence_like_content_inside_example(
    tmp_path: Path,
    fence_like_line: str,
) -> None:
    """Only a matching fence of sufficient length closes an example."""
    document = _write_document(
        tmp_path,
        "<!-- tested-example: fence-like-content -->\n\n"
        f"```python\nbefore\n{fence_like_line}\nafter\n```   \n",
    )

    (example,) = load_markdown_examples(document)

    assert example.source == f"before\n{fence_like_line}\nafter", (
        "shorter, different, and non-whitespace-suffixed fences stay in content"
    )


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("```python\nprint('unmarked')\n```\n", "unmarked"),
        ("  ```python\npass\n  ```\n", "unmarked"),
        (
            "> ```python\n> assert False\n> ```\n",
            "unmarked",
        ),
        (
            "- Example:\n\n    ~~~toml\n    value = 1\n    ~~~\n",
            "unmarked",
        ),
        (
            "<!-- tested-example: open -->\n\n```python\nprint('open')\n",
            "unterminated",
        ),
        (
            (
                "<!-- tested-example: separated -->\n"
                "A paragraph separates the marker from its fence.\n"
                "```python\npass\n```\n"
            ),
            "unmarked",
        ),
        (
            (
                "<!-- tested-example: open-no-final-newline -->\n\n"
                "```python\nprint('open')"
            ),
            "unterminated",
        ),
        (
            "<!-- tested-example:  -->\n\n```python\npass\n```\n",
            "needs an identifier",
        ),
        (
            (
                "<!-- tested-example: duplicate -->\n\n```python\npass\n```\n"
                "<!-- tested-example: duplicate -->\n\n```python\npass\n```\n"
            ),
            "duplicate tested-example identifier",
        ),
        (
            "<!-- tested-example: dangling -->\n",
            "has no fence",
        ),
    ],
)
def test_loader_rejects_incomplete_or_ambiguous_examples(
    tmp_path: Path, content: str, message: str
) -> None:
    """The loader fails on missing, malformed, duplicate or unclosed markers."""
    document = _write_document(tmp_path, content)

    with pytest.raises(MarkdownExampleError, match=message):
        load_markdown_examples(document)
