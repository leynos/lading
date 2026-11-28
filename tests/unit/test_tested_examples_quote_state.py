"""Quote-scoped list-state contracts for the tested-example loader."""

from pathlib import Path

from tests.helpers.markdown_docs import write_document
from tests.helpers.tested_examples import load_tested_examples


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


def test_loader_keeps_a_list_inside_an_outer_quote(tmp_path: Path) -> None:
    """List indentation is measured after stripping the outer quote."""
    path = write_document(
        tmp_path,
        "> - item\n>   > quote\n>   <!-- tested-example: demo -->\n"
        ">     ```bash\n>     echo ok\n>     ```\n",
    )

    examples = load_tested_examples(path)

    assert set(examples) == {"demo"}, (
        "the list inside the outer quote must keep its indentation"
    )
    assert examples["demo"].body == "echo ok", "the example must be parsed"


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
