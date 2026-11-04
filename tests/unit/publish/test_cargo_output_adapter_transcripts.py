"""Real cargo transcripts parsed through the output adapter.

These transcripts are copied verbatim as cargo emits them on a crates.io
index miss, including the full diagnostic preamble and trailing context
lines. They guard the adapter's markers and name regexes against drift from
real cargo output.
"""

import typing as typ

import pytest

from lading.commands.cargo_output_adapter import (
    CargoIndexLookupFailure,
    CargoSubprocessResult,
    parse_index_lookup_failure,
)

_REAL_CARGO_PACKAGE_STDERR = (
    "error: failed to prepare local package for uploading\n"
    "\n"
    "Caused by:\n"
    '  failed to select a version for the requirement `alpha = "^0.1.0"`\n'
    "  candidate versions found which didn't match: 0.0.1\n"
    "  location searched: crates.io index\n"
    "  required by package `beta v0.1.0`\n"
)
_REAL_CARGO_PUBLISH_STDERR = (
    "error: failed to verify package tarball\n"
    "\n"
    "Caused by:\n"
    '  failed to select a version for the requirement `alpha = "^0.1.0"`\n'
    "  candidate versions found which didn't match: 0.0.1\n"
    "  location searched: crates.io index\n"
    "  required by package `beta v0.1.0 (/work/beta)`\n"
)
# Verbatim transcript for a dependency that has never been published, as
# produced by a first-publication release train (issue #367).
_REAL_CARGO_ABSENT_PACKAGE_STDERR = (
    "error: failed to prepare local package for uploading\n"
    "\n"
    "Caused by:\n"
    "  no matching package named `ortho_l10n` found\n"
    "  location searched: crates.io index\n"
    "  required by package `ortho_config_macros v0.9.0 (/work/ortho_config_macros)`\n"
)


@pytest.mark.parametrize(
    ("subcommand", "stderr"),
    [
        pytest.param("package", _REAL_CARGO_PACKAGE_STDERR, id="cargo-package"),
        pytest.param("publish", _REAL_CARGO_PUBLISH_STDERR, id="cargo-publish"),
    ],
)
def test_parse_index_lookup_failure_parses_real_cargo_transcript(
    subcommand: typ.Literal["package", "publish"], stderr: str
) -> None:
    """Authentic full cargo index-miss transcripts parse through the adapter."""
    failure = parse_index_lookup_failure(
        crate_name="beta",
        subcommand=subcommand,
        result=CargoSubprocessResult(exit_code=101, stdout="", stderr=stderr),
    )

    assert failure == CargoIndexLookupFailure(
        crate_name="beta",
        subcommand=subcommand,
        exit_code=101,
        stdout="",
        stderr=stderr,
        missing_dependency_name="alpha",
    ), "a real cargo index-miss transcript must parse to the expected failure"


def test_parse_index_lookup_failure_parses_real_absent_package_transcript() -> None:
    """A real first-publication transcript parses through the adapter."""
    failure = parse_index_lookup_failure(
        crate_name="ortho_config_macros",
        subcommand="package",
        result=CargoSubprocessResult(
            exit_code=101,
            stdout="",
            stderr=_REAL_CARGO_ABSENT_PACKAGE_STDERR,
        ),
    )

    assert failure == CargoIndexLookupFailure(
        crate_name="ortho_config_macros",
        subcommand="package",
        exit_code=101,
        stdout="",
        stderr=_REAL_CARGO_ABSENT_PACKAGE_STDERR,
        missing_dependency_name="ortho_l10n",
    ), "a real absent-package transcript must parse to the expected failure"
