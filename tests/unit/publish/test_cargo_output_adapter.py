"""Unit tests for adapting cargo output into structured index failures."""

import pytest

from lading.commands.cargo_output_adapter import (
    CargoAlreadyPublishedFailure,
    CargoIndexLookupFailure,
    CargoSubprocessResult,
    parse_already_published_failure,
)

from .adapter_test_helpers import parse_publish_failure
from .index_missing_outputs import (
    INDEX_MISSING_PACKAGE_STDERR_BETA,
    INDEX_MISSING_STDERR_BETA,
    INDEX_MISSING_STDERR_UNPARSEABLE,
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
        pytest.param(
            1,
            "",
            "no matching package named `alpha` found",
            id="absent-package-without-index-marker",
        ),
        pytest.param(
            1,
            "",
            "no matching package named `alpha` found\n"
            "location searched: another registry",
            id="absent-package-on-another-registry",
        ),
    ],
)
def test_parse_index_lookup_failure_returns_none_for_non_index_errors(
    exit_code: int, stdout: str, stderr: str
) -> None:
    """Non-index failures do not produce structured lookup failures."""
    assert parse_publish_failure(exit_code, stdout, stderr) is None, (
        "a result without both index-miss markers must not classify"
    )


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        pytest.param(
            CargoSubprocessResult(101, "", "error: crate already uploaded"),
            CargoAlreadyPublishedFailure(101, "", "error: crate already uploaded"),
            id="registry-error-with-marker",
        ),
        pytest.param(
            CargoSubprocessResult(1, "", "error: crate already uploaded"),
            None,
            id="non-registry-error",
        ),
        pytest.param(
            CargoSubprocessResult(101, "", "unrelated registry error"),
            None,
            id="registry-error-without-marker",
        ),
    ],
)
def test_parse_already_published_failure_returns_typed_failure(
    result: CargoSubprocessResult,
    expected: CargoAlreadyPublishedFailure | None,
) -> None:
    """Already-published Cargo diagnostics retain their raw process output."""
    assert parse_already_published_failure(result) == expected, (
        f"Unexpected already-published classification for Cargo result {result!r}"
    )


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
            INDEX_MISSING_PACKAGE_STDERR_BETA,
            "alpha",
            id="absent-package",
        ),
        pytest.param(
            "",
            "no matching package named 'inner_crate' found\n"
            "location searched: crates.io index",
            "inner_crate",
            id="absent-package-single-quotes",
        ),
        pytest.param(
            "no matching package named `foo-bar` found\n"
            "location searched: crates.io index",
            "",
            "foo-bar",
            id="absent-package-hyphenated-on-stdout",
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
        pytest.param(
            (
                'failed to select a version for the requirement `stdout_dep = "^1"`\n'
                "location searched: crates.io index"
            ),
            "no matching package named `stderr_dep` found\n"
            "location searched: crates.io index",
            "stderr_dep",
            id="stderr-precedence-across-shapes",
        ),
    ],
)
def test_parse_index_lookup_failure_returns_structured_failure(
    stdout: str, stderr: str, expected_name: str | None
) -> None:
    """Cargo index failures retain command context and parsed dependency names."""
    failure = parse_publish_failure(101, stdout, stderr)

    assert failure == CargoIndexLookupFailure(
        crate_name="beta",
        subcommand="publish",
        exit_code=101,
        stdout=stdout,
        stderr=stderr,
        missing_dependency_name=expected_name,
    ), "the parsed failure must retain the command context and dependency name"
