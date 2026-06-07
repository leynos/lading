"""Unit tests for adapting cargo output into structured index failures."""

from __future__ import annotations

import pytest

from lading.commands.cargo_output_adapter import (
    CargoIndexLookupFailure,
    parse_index_lookup_failure,
)

from .conftest import (
    INDEX_MISSING_STDERR_BETA,
    INDEX_MISSING_STDERR_UNPARSEABLE,
)


def _parse_index_lookup_failure(
    exit_code: int,
    stdout: str,
    stderr: str,
) -> CargoIndexLookupFailure | None:
    """Parse a fixed publish failure fixture through the adapter."""
    return parse_index_lookup_failure(
        crate_name="beta",
        subcommand="publish",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


@pytest.mark.parametrize(
    ("exit_code", "stdout", "stderr"),
    [
        pytest.param(0, "", "", id="success"),
        pytest.param(1, "", "", id="failure-without-markers"),
        pytest.param(
            1,
            "",
            "failed to select a version for the requirement",
            id="missing-index-marker",
        ),
        pytest.param(
            1,
            "",
            "location searched: crates.io index",
            id="missing-version-marker",
        ),
    ],
)
def test_parse_index_lookup_failure_returns_none_for_non_index_errors(
    exit_code: int, stdout: str, stderr: str
) -> None:
    """Non-index failures do not produce structured lookup failures."""
    assert _parse_index_lookup_failure(exit_code, stdout, stderr) is None


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected_name"),
    [
        pytest.param("", INDEX_MISSING_STDERR_BETA, "alpha", id="stderr-backticks"),
        pytest.param(
            "",
            "failed to select a version for the requirement "
            "'inner_crate = \"^0.8.0\"'\n"
            "location searched: crates.io index",
            "inner_crate",
            id="single-quotes",
        ),
        pytest.param(
            'failed to select a version for the requirement "foo-bar = ^1"\n'
            "location searched: crates.io index",
            "",
            "foo-bar",
            id="hyphenated-on-stdout",
        ),
        pytest.param(
            "",
            (
                "error: failed to prepare local package for uploading\n"
                "Caused by:\n"
                '  failed to select a version for the requirement `my-crate = "^1"`\n'
                "  location searched: crates.io index\n"
            ),
            "my-crate",
            id="hyphenated-name",
        ),
        pytest.param(
            "",
            INDEX_MISSING_STDERR_UNPARSEABLE,
            None,
            id="unparseable-name",
        ),
        pytest.param(
            (
                'failed to select a version for the requirement `stdout_dep = "^1"`\n'
                "location searched: crates.io index"
            ),
            (
                'failed to select a version for the requirement `stderr_dep = "^1"`\n'
                "location searched: crates.io index"
            ),
            "stderr_dep",
            id="stderr-precedence",
        ),
    ],
)
def test_parse_index_lookup_failure_returns_structured_failure(
    stdout: str, stderr: str, expected_name: str | None
) -> None:
    """Cargo index failures retain command context and parsed dependency names."""
    failure = _parse_index_lookup_failure(101, stdout, stderr)

    assert failure == CargoIndexLookupFailure(
        crate_name="beta",
        subcommand="publish",
        exit_code=101,
        stdout=stdout,
        stderr=stderr,
        missing_dependency_name=expected_name,
    )
