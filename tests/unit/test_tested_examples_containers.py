"""Container and indentation contracts for the tested-example loader."""

from pathlib import Path

import pytest

from tests.helpers.markdown_docs import write_document
from tests.helpers.tested_examples import load_tested_examples


def test_loader_skips_a_literal_marker_inside_an_unmarked_fence(
    tmp_path: Path,
) -> None:
    """A literal marker inside an unmarked fence is not an example."""
    path = write_document(
        tmp_path,
        "````markdown\n"
        "<!-- tested-example: literal -->\n"
        "```bash\necho inner\n```\n"
        "````\n\n"
        "<!-- tested-example: real -->\n"
        "```bash\necho outer\n```\n",
    )

    examples = load_tested_examples(path, require_markers=False)

    assert set(examples) == {"real"}, (
        "only the marked example outside the unmarked fence should be returned"
    )
    assert examples["real"].body == "echo outer", (
        "the real example must still be parsed"
    )


def test_loader_ignores_a_malformed_marker_inside_an_unmarked_fence(
    tmp_path: Path,
) -> None:
    """A malformed marker inside an unmarked fence does not fail a permissive load."""
    path = write_document(
        tmp_path,
        "````markdown\n<!-- tested-example: -->\n````\n",
    )

    examples = load_tested_examples(path, require_markers=False)

    assert not examples, (
        "a permissive load must ignore a literal malformed marker in a fence body"
    )


def test_loader_reads_a_marked_example_after_a_list_fence_outdent(
    tmp_path: Path,
) -> None:
    """A list-item fence ends at its outdent, so a later example is read."""
    path = write_document(
        tmp_path,
        "- ```text\n  ignored\n\n"
        "<!-- tested-example: real -->\n```bash\necho real\n```\n",
    )

    examples = load_tested_examples(path, require_markers=False)

    assert set(examples) == {"real"}, (
        "the marked example after the outdented list fence must be read"
    )
    assert examples["real"].body == "echo real", (
        "the marked example must still be parsed"
    )


def test_loader_keeps_an_enclosing_list_when_a_quote_ends(
    tmp_path: Path,
) -> None:
    """A quote indented into a list preserves the list for later lines."""
    path = write_document(
        tmp_path,
        "- item\n  > quote\n  <!-- tested-example: demo -->\n"
        "    ```bash\n    echo ok\n    ```\n",
    )

    examples = load_tested_examples(path)

    assert set(examples) == {"demo"}, (
        "the list indentation must survive the nested quote"
    )
    assert examples["demo"].body == "echo ok", "the example must be parsed"


@pytest.mark.parametrize(
    ("document", "identifier"),
    [
        pytest.param(
            "- outer\n  - inner\n    - <!-- tested-example: nested -->\n"
            "      ```bash\n      echo ok\n      ```\n",
            "nested",
            id="nested-list-item",
        ),
        pytest.param(
            "- - <!-- tested-example: deep -->\n    ```bash\n    echo ok\n    ```\n",
            "deep",
            id="nested-markers-one-line",
        ),
    ],
)
def test_loader_reads_a_marker_in_nested_containers(
    tmp_path: Path, document: str, identifier: str
) -> None:
    """A marker inside nested list containers is recognised."""
    path = write_document(tmp_path, document)

    examples = load_tested_examples(path)

    assert set(examples) == {identifier}, (
        "the marker inside the nested containers must be recognised"
    )
    assert examples[identifier].body == "echo ok", "the nested example must be parsed"


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("> ~~~python\n> print(1)\n> ~~~\n", id="block-quote"),
        pytest.param(
            "> 10. Example\n>     ~~~python\n>     print(1)\n>     ~~~\n",
            id="quoted-ordered-list",
        ),
    ],
)
def test_loader_rejects_an_unmarked_fence_in_a_container(
    tmp_path: Path, document: str
) -> None:
    """A fenced example in a block-quote container is still a fence."""
    path = write_document(tmp_path, document)

    with pytest.raises(ValueError, match="unmarked fenced example"):
        load_tested_examples(path)


def test_loader_does_not_read_a_quote_indented_literal_fence(
    tmp_path: Path,
) -> None:
    """Four content spaces inside a quote are indented code, not a fence."""
    path = write_document(
        tmp_path,
        "- item\n\n>     ```text\n>     literal\n\n"
        "<!-- tested-example: real -->\n```bash\necho real\n```\n",
    )

    examples = load_tested_examples(path)

    assert set(examples) == {"real"}, (
        "the literal fence must not be reported and the real example must load"
    )


def test_loader_rejects_an_unmarked_tilde_fence(tmp_path: Path) -> None:
    """A tilde fence without a marker fails the load."""
    path = write_document(tmp_path, "~~~bash\necho one\n~~~\n")

    with pytest.raises(ValueError, match="unmarked fenced example"):
        load_tested_examples(path)


def test_loader_rejects_an_unmarked_list_item_fence(tmp_path: Path) -> None:
    """A fenced example opened inside a list item is still a fence."""
    path = write_document(
        tmp_path,
        "- ~~~bash\n  echo unchecked\n",
    )

    with pytest.raises(ValueError, match="unmarked fenced example"):
        load_tested_examples(path)


def test_loader_dedents_the_opening_fence_indentation(tmp_path: Path) -> None:
    """The opening fence's indentation is removed from body lines."""
    path = write_document(
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
    path = write_document(
        tmp_path,
        "<!-- tested-example: long -->\n````bash\necho one\n    ````\n",
    )

    with pytest.raises(ValueError, match="unterminated"):
        load_tested_examples(path)


def test_loader_reads_a_marked_example_in_a_block_quote(tmp_path: Path) -> None:
    """A marker and its fence may both sit inside a block quote."""
    path = write_document(
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
    path = write_document(
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
    path = write_document(
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
    path = write_document(
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
    path = write_document(
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
    path = write_document(tmp_path, "    ```python\n    print(1)\n    ```\n")

    assert not load_tested_examples(path), (
        "an indented literal delimiter must not be treated as a fence"
    )


def test_loader_rejects_a_body_line_that_ends_the_list(tmp_path: Path) -> None:
    """An outdented line ends the list container and leaves the fence unclosed."""
    path = write_document(
        tmp_path,
        "- <!-- tested-example: sample -->\n  ```python\n  print(1)\noutside\n```\n",
    )

    with pytest.raises(ValueError, match="unterminated"):
        load_tested_examples(path)


def test_loader_uses_the_established_list_indentation(tmp_path: Path) -> None:
    """Extra marker indentation does not widen the list's content indentation."""
    path = write_document(
        tmp_path,
        "- Item\n    <!-- tested-example: extra -->\n  ```python\n  print(1)\n  ```\n",
    )

    example = load_tested_examples(path)["extra"]

    assert example.body == "print(1)", (
        "the fence must be read relative to the list's content indentation"
    )


def test_loader_rejects_an_over_indented_opening_fence(tmp_path: Path) -> None:
    """A four-space opener is literal code, so the marker has no fence."""
    path = write_document(
        tmp_path,
        "<!-- tested-example: demo -->\n    ~~~python\n    print(1)\n~~~\n",
    )

    with pytest.raises(ValueError, match="has no fence"):
        load_tested_examples(path)


def test_loader_restores_the_outer_list_indentation(tmp_path: Path) -> None:
    """A nested list closes before an outer-list example continues."""
    path = write_document(
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


def test_loader_clears_list_state_when_a_quote_starts_outside_it(
    tmp_path: Path,
) -> None:
    """An outdented quote after a list ends the list for later literals."""
    path = write_document(
        tmp_path,
        "- item\n\n> quote\n\n    ```text\n    literal\n    ```\n",
    )

    examples = load_tested_examples(path)

    assert not examples, (
        "four spaces inside the quote are indented code, not an unmarked fence"
    )


def test_loader_reads_an_example_outer_to_a_nested_fence(tmp_path: Path) -> None:
    """A line returning to the outer list indentation ends the skipped fence."""
    path = write_document(
        tmp_path,
        "- - ```text\n    ignored\n"
        "  <!-- tested-example: real -->\n  ```bash\n  echo real\n  ```\n",
    )

    examples = load_tested_examples(path, require_markers=False)

    assert set(examples) == {"real"}, (
        "the marked example after the outer-level line must be read"
    )
    assert examples["real"].body == "echo real", (
        "the marked example must still be parsed"
    )


def test_loader_clears_list_state_between_separate_quotes(
    tmp_path: Path,
) -> None:
    """A later quote at the same depth does not inherit an earlier list level."""
    path = write_document(
        tmp_path,
        "> - item\n\nOutside the quote.\n\n>     ```text\n>     literal\n",
    )

    examples = load_tested_examples(path)

    assert not examples, (
        "indented code inside the second quote is not an unmarked fence"
    )
