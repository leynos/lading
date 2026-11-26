"""CommonMark fence suffix and line-ending validation."""

import typing as typ

import pytest

from lading.commands import bump_docs_fence_lines


@pytest.mark.parametrize(
    ("closing_line", "expected_kind"),
    [
        ("~~~ \n", "closing"),
        ("~~~\t\r\n", "closing"),
        ("~~~\u00a0\n", "content"),
    ],
)
def test_closing_fence_suffix_allows_only_commonmark_spacing(
    closing_line: str,
    expected_kind: typ.Literal["closing", "content"],
) -> None:
    """Only spaces and tabs may follow a closing fence marker."""
    should_close = expected_kind == "closing"
    assert (
        bump_docs_fence_lines._is_closing_fence(closing_line, "~~~") is should_close
    ), "a closing fence must reject non-CommonMark whitespace suffixes"
