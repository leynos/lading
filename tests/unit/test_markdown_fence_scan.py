"""Contract tests for the Markdown fence scanning helpers.

The scanner is shared test support: it decides whether a line opens a fence,
where a list-contained fence ends, and how leading whitespace is measured. The
cases here pin the CommonMark rules that the tested-example loader relies on.
"""

from tests.helpers.markdown_fence_scan import (
    indentation,
    unmarked_fence_delimiter,
    unmarked_fence_end,
)


def test_unmarked_fence_end_stops_at_a_list_item_outdent() -> None:
    """An outdented line ends a list-contained fence before any later closer."""
    lines = ["- ```text", "  literal", "", "outside", "```python", "pass", "```"]

    assert unmarked_fence_end(lines, 0, 2) == 3, (
        "the fence must end with its list item, not at the later closer"
    )


def test_five_space_list_padding_leaves_indented_code() -> None:
    """More than four spaces after a marker leaves indented code, not a fence."""
    assert unmarked_fence_delimiter("-     ```python", 0) is None, (
        "one space is padding and the remaining four indentation"
    )


def test_one_to_four_space_list_padding_opens_a_fence() -> None:
    """One to four spaces after a marker are valid fence padding."""
    for padding in range(1, 5):
        line = "-" + " " * padding + "```python"

        assert unmarked_fence_delimiter(line, 0) == ("`", 3), (
            f"{padding} space(s) of padding must still open a fence"
        )


def test_tab_indentation_counts_four_columns() -> None:
    """A tab reaches the next four-column stop."""
    assert indentation("\t```python") == 4, (
        "a leading tab must count as four indentation columns"
    )
    assert unmarked_fence_delimiter("\t```python", 0) is None, (
        "a tab-indented fence is literal code, not a fence"
    )


def test_tab_indented_closing_fence_does_not_close() -> None:
    """A tab-indented line is too far indented to close a top-level fence."""
    lines = ["```python", "\t```", "pass", "```"]

    assert unmarked_fence_end(lines, 0, 0) == 4, (
        "the tab-indented line must not close the fence"
    )
