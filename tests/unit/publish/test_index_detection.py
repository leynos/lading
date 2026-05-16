"""Unit tests for cargo index-missing-version detection."""

from __future__ import annotations

import pytest

from lading.commands import publish

from .conftest import INDEX_MISSING_STDERR_BETA


@pytest.mark.parametrize(
    ("exit_code", "stdout", "stderr", "expected"),
    [
        pytest.param(0, "", "", False, id="success"),
        pytest.param(1, "", "", False, id="failure-without-markers"),
        pytest.param(
            1,
            "",
            "failed to select a version for the requirement",
            False,
            id="missing-index-marker",
        ),
        pytest.param(
            1,
            "",
            "location searched: crates.io index",
            False,
            id="missing-version-marker",
        ),
        pytest.param(
            1,
            "",
            INDEX_MISSING_STDERR_BETA,
            True,
            id="full-stderr-shape",
        ),
        pytest.param(
            1,
            INDEX_MISSING_STDERR_BETA,
            "",
            True,
            id="markers-on-stdout",
        ),
    ],
)
def test_is_index_missing_version_error(
    exit_code: int, stdout: str, stderr: str, *, expected: bool
) -> None:
    """Both markers must be present and the command must have failed."""
    assert (
        publish._is_index_missing_version_error(exit_code, stdout, stderr) is expected
    )


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    [
        pytest.param("", INDEX_MISSING_STDERR_BETA, "alpha", id="stderr-backticks"),
        pytest.param(
            "",
            "failed to select a version for the requirement 'inner_crate = \"^0.8.0\"'",
            "inner_crate",
            id="single-quotes",
        ),
        pytest.param(
            'failed to select a version for the requirement "foo-bar = ^1"',
            "",
            "foo-bar",
            id="hyphenated-on-stdout",
        ),
        pytest.param("", "no match here", None, id="no-match"),
    ],
)
def test_extract_missing_dependency_name(
    stdout: str, stderr: str, expected: str | None
) -> None:
    """Regex extraction handles backticks, quotes, and hyphens."""
    assert publish._extract_missing_dependency_name(stdout, stderr) == expected
