"""BDD steps for the command catalogue feature."""

from __future__ import annotations

import re
import typing as typ

from pytest_bdd import given, parsers, scenarios, then, when

from lading.utils.commands import CARGO, GIT, LADING_CATALOGUE

if typ.TYPE_CHECKING:
    from cuprum import ProgramCatalogue, SafeCmd

scenarios("../features/commands_catalogue.feature")


def _parse_quoted_args(args_str: str) -> tuple[str, ...]:
    """Parse a space-separated list of quoted arguments."""
    return tuple(re.findall(r'"([^"]*)"', args_str))


@given("the lading catalogue", target_fixture="catalogue")
def given_lading_catalogue() -> ProgramCatalogue:
    """Provide the shared lading catalogue."""
    return LADING_CATALOGUE


@given("the lading catalogue is active", target_fixture="catalogue_context")
def given_catalogue_is_active() -> ProgramCatalogue:
    """Provide the catalogue for use in a scoped context."""
    return LADING_CATALOGUE


@then("cargo should be in the allowlist")
def then_cargo_in_allowlist(catalogue: ProgramCatalogue) -> None:
    """Assert that cargo is registered in the catalogue."""
    assert catalogue.is_allowed(CARGO)
    assert CARGO in catalogue.allowlist


@then("git should be in the allowlist")
def then_git_in_allowlist(catalogue: ProgramCatalogue) -> None:
    """Assert that git is registered in the catalogue."""
    assert catalogue.is_allowed(GIT)
    assert GIT in catalogue.allowlist


@when(
    parsers.re(r"I construct a cargo command with arguments (?P<args>.+)"),
    target_fixture="constructed_command",
)
def when_construct_cargo_command(
    catalogue_context: ProgramCatalogue,
    args: str,
) -> SafeCmd:
    """Construct a cargo command with the given arguments."""
    from cuprum import scoped, sh

    parsed_args = _parse_quoted_args(args)
    with scoped(allowlist=catalogue_context.allowlist):
        cargo_builder = sh.make(CARGO, catalogue=catalogue_context)
        return cargo_builder(*parsed_args)


@when(
    parsers.re(r"I construct a git command with arguments (?P<args>.+)"),
    target_fixture="constructed_command",
)
def when_construct_git_command(
    catalogue_context: ProgramCatalogue,
    args: str,
) -> SafeCmd:
    """Construct a git command with the given arguments."""
    from cuprum import scoped, sh

    parsed_args = _parse_quoted_args(args)
    with scoped(allowlist=catalogue_context.allowlist):
        git_builder = sh.make(GIT, catalogue=catalogue_context)
        return git_builder(*parsed_args)


@when(
    parsers.parse(
        'I attempt to construct a command for unregistered program "{program_name}"',
    ),
    target_fixture="unregistered_error",
)
def when_construct_unregistered_command(
    catalogue_context: ProgramCatalogue,
    program_name: str,
) -> Exception | None:
    """Attempt to construct a command for an unregistered program."""
    from cuprum import Program, UnknownProgramError, scoped, sh

    unregistered = Program(program_name)

    with scoped(allowlist=catalogue_context.allowlist):
        try:
            sh.make(unregistered, catalogue=catalogue_context)
        except UnknownProgramError as exc:
            return exc
        else:
            return None


@then(parsers.re(r"the command argv should be (?P<expected_argv>.+)"))
def then_command_argv_matches(
    constructed_command: SafeCmd,
    expected_argv: str,
) -> None:
    """Assert that the constructed command has the expected argv."""
    expected = _parse_quoted_args(expected_argv)
    assert constructed_command.argv_with_program == expected


@then("an UnknownProgramError should be raised")
def then_unknown_program_error_raised(unregistered_error: Exception | None) -> None:
    """Assert that an UnknownProgramError was raised."""
    from cuprum import UnknownProgramError

    assert isinstance(unregistered_error, UnknownProgramError)
