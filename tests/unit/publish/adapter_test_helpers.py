"""Helpers shared by the cargo output adapter test modules."""

from lading.commands.cargo_output_adapter import (
    CargoIndexLookupFailure,
    CargoSubprocessResult,
    parse_index_lookup_failure,
)


def parse_publish_failure(
    exit_code: int,
    stdout: str,
    stderr: str,
) -> CargoIndexLookupFailure | None:
    """Parse a fixture as a ``cargo publish`` failure for crate ``beta``."""
    return parse_index_lookup_failure(
        crate_name="beta",
        subcommand="publish",
        result=CargoSubprocessResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        ),
    )
