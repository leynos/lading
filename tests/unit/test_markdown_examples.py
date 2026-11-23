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


def test_loader_keeps_marker_text_inside_marked_markdown_source(
    tmp_path: Path,
) -> None:
    """Marker-looking source inside a fence is not document metadata."""
    document = _write_document(
        tmp_path,
        """<!-- tested-example: markdown-source -->

```markdown
<!-- tested-example: inner -->
```
""",
    )

    examples = load_markdown_examples(document)

    assert [(example.identifier, example.source) for example in examples] == [
        ("markdown-source", "<!-- tested-example: inner -->")
    ], "fenced marker text must be returned as source"


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
    ("content", "message"),
    [
        ("```python\nprint('unmarked')\n```\n", "unmarked"),
        ("  ```python\npass\n  ```\n", "unmarked"),
        (
            "<!-- tested-example: open -->\n\n```python\nprint('open')\n",
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
