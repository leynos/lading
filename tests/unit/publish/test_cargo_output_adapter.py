"""Unit tests for adapting cargo output into structured index failures."""

from __future__ import annotations

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lading.commands.cargo_output_adapter import (
    CargoIndexLookupFailure,
    CargoSubprocessResult,
    parse_index_lookup_failure,
)

from .conftest import (
    INDEX_MISSING_STDERR_BETA,
    INDEX_MISSING_STDERR_UNPARSEABLE,
)

_MARKER_VERSION = "failed to select a version for the requirement"
_MARKER_INDEX = "location searched: crates.io index"
_VALID_CRATE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

_crate_names = st.from_regex(r"[A-Za-z_][A-Za-z0-9_-]*", fullmatch=True)
_quote_pairs = st.sampled_from([
    ("backtick", "`", "`"),
    ("single", "'", "'"),
    ("double", '"', '"'),
])


@st.composite
def _both_markers_stderr(
    draw: st.DrawFn,
) -> tuple[str, str]:
    """Generate ``(crate_name, stderr)`` with both index-miss markers present."""
    name = draw(_crate_names)
    _label, open_q, close_q = draw(_quote_pairs)
    version = draw(st.from_regex(r"\^[0-9]+(\.[0-9]+){0,2}", fullmatch=True))
    prefix = draw(st.text(max_size=40))
    suffix = draw(st.text(max_size=40))
    stderr = (
        f"{prefix}"
        f'{_MARKER_VERSION} {open_q}{name} = "{version}"{close_q}\n'
        f"{_MARKER_INDEX}\n"
        f"{suffix}"
    )
    return name, stderr


def _randomly_cased(text: str) -> st.SearchStrategy[str]:
    """Return a strategy yielding ``text`` with each letter's case toggled."""
    return st.lists(st.booleans(), min_size=len(text), max_size=len(text)).map(
        lambda flags: "".join(
            char.upper() if flag else char.lower()
            for char, flag in zip(text, flags, strict=True)
        )
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
        result=CargoSubprocessResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        ),
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


@given(stdout=st.text(), stderr=st.text())
@settings(max_examples=100, deadline=None)
def test_parse_index_lookup_failure_success_always_returns_none(
    stdout: str, stderr: str
) -> None:
    """A zero exit code always produces None regardless of output content."""
    assert _parse_index_lookup_failure(0, stdout, stderr) is None


@given(case=_both_markers_stderr(), exit_code=st.integers(min_value=1, max_value=255))
@settings(max_examples=80, deadline=None)
def test_parse_index_lookup_failure_both_markers_nonzero_returns_failure(
    case: tuple[str, str], exit_code: int
) -> None:
    """Both index-miss markers with a non-zero exit code produce a failure."""
    _name, stderr = case
    result = _parse_index_lookup_failure(exit_code, "", stderr)
    assert result is not None


@given(
    stdout=st.text().filter(lambda s: _MARKER_INDEX.lower() not in s.lower()),
    stderr=st.text().filter(lambda s: _MARKER_INDEX.lower() not in s.lower()),
    exit_code=st.integers(min_value=1, max_value=255),
)
@settings(max_examples=80, deadline=None)
def test_parse_index_lookup_failure_missing_index_marker_returns_none(
    stdout: str, stderr: str, exit_code: int
) -> None:
    """Absence of the crates.io index marker always produces None."""
    assert _parse_index_lookup_failure(exit_code, stdout, stderr) is None


@given(case=_both_markers_stderr(), exit_code=st.integers(min_value=1, max_value=255))
@settings(max_examples=80, deadline=None)
def test_parse_index_lookup_failure_extracted_name_matches_crate_name_pattern(
    case: tuple[str, str], exit_code: int
) -> None:
    """When a name is extracted it always matches the valid crate-name pattern."""
    _name, stderr = case
    result = _parse_index_lookup_failure(exit_code, "", stderr)
    if result is not None and result.missing_dependency_name is not None:
        assert _VALID_CRATE_NAME_RE.match(result.missing_dependency_name)


@given(
    version_marker=_randomly_cased(_MARKER_VERSION),
    index_marker=_randomly_cased(_MARKER_INDEX),
    name=_crate_names,
    exit_code=st.integers(min_value=1, max_value=255),
)
@settings(max_examples=80, deadline=None)
def test_parse_index_lookup_failure_matches_markers_case_insensitively(
    version_marker: str, index_marker: str, name: str, exit_code: int
) -> None:
    """Marker matching and name extraction honour the re.IGNORECASE contract.

    The production markers are matched case-insensitively, so cargo output with
    arbitrarily cased markers must still classify as an index-lookup failure and
    yield the parsed dependency name.
    """
    stderr = f'{version_marker} `{name} = "^1.0"`\n{index_marker}'
    result = _parse_index_lookup_failure(exit_code, "", stderr)
    assert result is not None
    assert result.missing_dependency_name == name
