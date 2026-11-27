"""Contract tests for the ``tested-example`` documentation loader."""

from pathlib import Path

import pytest

from tests.helpers.tested_examples import load_tested_examples


def _write(tmp_path: Path, text: str) -> Path:
    """Write a small Markdown document and return its path."""
    path = tmp_path / "doc.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_loader_reads_a_marked_example(tmp_path: Path) -> None:
    """A valid marker yields its fence body and language."""
    path = _write(
        tmp_path,
        "<!-- tested-example: example-one -->\n```bash\necho one\n```\n",
    )

    examples = load_tested_examples(path)

    assert examples["example-one"].language == "bash", (
        "the fence language must be captured"
    )
    assert examples["example-one"].body == "echo one", "the fence body must be captured"


def test_loader_rejects_an_unmarked_fence(tmp_path: Path) -> None:
    """A fence without a marker fails the load."""
    path = _write(tmp_path, "```bash\necho one\n```\n")

    with pytest.raises(ValueError, match="unmarked fenced example"):
        load_tested_examples(path)


def test_loader_rejects_a_malformed_marker(tmp_path: Path) -> None:
    """A marker without an identifier fails the load."""
    path = _write(
        tmp_path,
        "<!-- tested-example: -->\n```bash\necho one\n```\n",
    )

    with pytest.raises(ValueError, match="malformed tested-example marker"):
        load_tested_examples(path)


def test_loader_rejects_a_duplicate_identifier(tmp_path: Path) -> None:
    """Two examples sharing an identifier fail the load."""
    path = _write(
        tmp_path,
        "<!-- tested-example: same -->\n```bash\necho one\n```\n\n"
        "<!-- tested-example: same -->\n```bash\necho two\n```\n",
    )

    with pytest.raises(ValueError, match="duplicate tested-example"):
        load_tested_examples(path)


def test_loader_rejects_an_unmarked_tilde_fence(tmp_path: Path) -> None:
    """A tilde fence without a marker fails the load."""
    path = _write(tmp_path, "~~~bash\necho one\n~~~\n")

    with pytest.raises(ValueError, match="unmarked fenced example"):
        load_tested_examples(path)


def test_loader_matches_a_longer_closing_fence(tmp_path: Path) -> None:
    """A four-backtick fence closes only on four or more backticks."""
    path = _write(
        tmp_path,
        "<!-- tested-example: long -->\n"
        "````bash\n"
        "echo ``` inside\n"
        "```\n"
        "still inside\n"
        "````\n",
    )

    example = load_tested_examples(path)["long"]

    assert example.language == "bash", "the info string follows the delimiter run"
    assert example.body == "echo ``` inside\n```\nstill inside", (
        "a standalone shorter backtick line must neither close the fence nor be lost"
    )


def test_loader_rejects_an_unmarked_block_quote_fence(tmp_path: Path) -> None:
    """A fenced example nested in a block quote is still a fence."""
    path = _write(
        tmp_path,
        "> ~~~python\n> print(1)\n> ~~~\n",
    )

    with pytest.raises(ValueError, match="unmarked fenced example"):
        load_tested_examples(path)


def test_loader_rejects_an_unmarked_list_item_fence(tmp_path: Path) -> None:
    """A fenced example opened inside a list item is still a fence."""
    path = _write(
        tmp_path,
        "- ~~~bash\n  echo unchecked\n",
    )

    with pytest.raises(ValueError, match="unmarked fenced example"):
        load_tested_examples(path)


def test_loader_dedents_the_opening_fence_indentation(tmp_path: Path) -> None:
    """The opening fence's indentation is removed from body lines."""
    path = _write(
        tmp_path,
        "<!-- tested-example: indented -->\n"
        "  ```python\n"
        "  print(1)\n"
        "    print(2)\n"
        "  ```\n",
    )

    example = load_tested_examples(path)["indented"]

    assert example.body == "print(1)\n  print(2)", (
        "each body line must lose the opening fence's indentation only"
    )


def test_loader_rejects_a_four_space_indented_closing_fence(
    tmp_path: Path,
) -> None:
    """A closing fence indented four spaces does not close the block."""
    path = _write(
        tmp_path,
        "<!-- tested-example: long -->\n````bash\necho one\n    ````\n",
    )

    with pytest.raises(ValueError, match="unterminated"):
        load_tested_examples(path)


def test_loader_reads_a_marked_example_in_a_block_quote(tmp_path: Path) -> None:
    """A marker and its fence may both sit inside a block quote."""
    path = _write(
        tmp_path,
        "> <!-- tested-example: quoted -->\n> ```python\n> print(1)\n> ```\n",
    )

    example = load_tested_examples(path)["quoted"]

    assert example.language == "python", "the fence info string must be captured"
    assert example.body == "print(1)", (
        "the block-quote markers must be removed from the body"
    )


def test_loader_accepts_a_fence_inside_a_list_item(tmp_path: Path) -> None:
    """A marker and fence inside a list item keep the container context."""
    path = _write(
        tmp_path,
        "- <!-- tested-example: list -->\n  ```python\n  print(1)\n  ```\n",
    )

    example = load_tested_examples(path)["list"]

    assert example.body == "print(1)", (
        "the list container indentation must not become body text"
    )


def test_loader_accepts_a_closing_fence_indented_within_the_container(
    tmp_path: Path,
) -> None:
    """A list item's closing fence may sit deeper than its opening fence."""
    path = _write(
        tmp_path,
        "- <!-- tested-example: list -->\n  ```python\n  print(1)\n    ```\n",
    )

    example = load_tested_examples(path)["list"]

    assert example.body == "print(1)", (
        "the closing fence must close the list-contained example"
    )


def test_loader_keeps_an_over_indented_delimiter_as_body_text(
    tmp_path: Path,
) -> None:
    """A four-space delimiter in a two-space top-level example is body text."""
    path = _write(
        tmp_path,
        "<!-- tested-example: top -->\n  ```python\n  print(1)\n    ```\n  ```\n",
    )

    example = load_tested_examples(path)["top"]

    assert example.body == "print(1)\n  ```", (
        "only a fence indented within the container may close the example"
    )


def test_loader_reads_a_marker_on_a_list_continuation_line(
    tmp_path: Path,
) -> None:
    """A marker indented under a list item keeps that container's indentation."""
    path = _write(
        tmp_path,
        "- Item\n"
        "    <!-- tested-example: cont -->\n"
        "    ```python\n"
        "    print(1)\n"
        "    ```\n",
    )

    example = load_tested_examples(path)["cont"]

    assert example.body == "print(1)", (
        "the continuation-line container indentation must be honoured"
    )


def test_loader_ignores_an_indented_literal_fence(tmp_path: Path) -> None:
    """A four-space delimiter is literal code, not an unmarked fence."""
    path = _write(tmp_path, "    ```python\n    print(1)\n    ```\n")

    assert not load_tested_examples(path), (
        "an indented literal delimiter must not be treated as a fence"
    )


def test_loader_rejects_a_body_line_that_ends_the_list(tmp_path: Path) -> None:
    """An outdented line ends the list container and leaves the fence unclosed."""
    path = _write(
        tmp_path,
        "- <!-- tested-example: sample -->\n  ```python\n  print(1)\noutside\n```\n",
    )

    with pytest.raises(ValueError, match="unterminated"):
        load_tested_examples(path)


def test_loader_rejects_an_unmarked_fence_inside_a_quoted_list(
    tmp_path: Path,
) -> None:
    """A fence inside a quoted ordered list is still an unmarked fence."""
    path = _write(
        tmp_path,
        "> 10. Example\n>     ~~~python\n>     print(1)\n>     ~~~\n",
    )

    with pytest.raises(ValueError, match="unmarked fenced example"):
        load_tested_examples(path)


def test_loader_uses_the_established_list_indentation(tmp_path: Path) -> None:
    """Extra marker indentation does not widen the list's content indentation."""
    path = _write(
        tmp_path,
        "- Item\n    <!-- tested-example: extra -->\n  ```python\n  print(1)\n  ```\n",
    )

    example = load_tested_examples(path)["extra"]

    assert example.body == "print(1)", (
        "the fence must be read relative to the list's content indentation"
    )


def test_loader_rejects_an_over_indented_opening_fence(tmp_path: Path) -> None:
    """A four-space opener is literal code, so the marker has no fence."""
    path = _write(
        tmp_path,
        "<!-- tested-example: demo -->\n    ~~~python\n    print(1)\n~~~\n",
    )

    with pytest.raises(ValueError, match="has no fence"):
        load_tested_examples(path)


def test_loader_restores_the_outer_list_indentation(tmp_path: Path) -> None:
    """A nested list closes before an outer-list example continues."""
    path = _write(
        tmp_path,
        "- outer\n"
        "  - inner\n"
        "\n"
        "  <!-- tested-example: outer -->\n"
        "  ~~~python\n"
        "  print(1)\n"
        "  ~~~\n",
    )

    example = load_tested_examples(path)["outer"]

    assert example.body == "print(1)", (
        "the example must continue in the outer list after the nested item"
    )


def test_inline_code_with_a_backtick_fence_lookalike_is_not_a_fence(
    tmp_path: Path,
) -> None:
    """A paragraph such as ```bash``` is inline code, not an unmarked fence."""
    path = _write(tmp_path, "Use ```bash``` to run the command.\n")

    assert not load_tested_examples(path), (
        "a backtick run inside an info string must not open a fence"
    )
