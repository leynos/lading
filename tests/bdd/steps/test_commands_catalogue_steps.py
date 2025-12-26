"""BDD steps for the command catalogue feature."""

from __future__ import annotations

import re
import typing as typ

from pytest_bdd import given, parsers, scenarios, then, when

from lading.utils.commands import CARGO, GIT, LADING_CATALOGUE

if typ.TYPE_CHECKING:
    from cuprum import Program, ProgramCatalogue, SafeCmd

scenarios("../features/commands_catalogue.feature")


def _parse_quoted_args(args_str: str) -> tuple[str, ...]:
    """Parse a space-separated list of quoted arguments.

    All non-whitespace content must be enclosed in double quotes. Raises
    ValueError if any unexpected unquoted content is present.

    Examples:
        '"foo" "bar baz"' -> ("foo", "bar baz")
        '"" "bar"'        -> ("", "bar")
        'foo "bar"'       -> ValueError

    """
    pattern = r'"([^"]*)"'
    matches = list(re.finditer(pattern, args_str))

    # If there are no quoted segments but there is non-whitespace content,
    # treat that as invalid.
    if not matches and args_str.strip():
        msg = (
            f"Unquoted arguments found in step text: {args_str!r}. "
            "All arguments must be enclosed in double quotes."
        )
        raise ValueError(msg)

    last_end = 0
    for match in matches:
        # Any non-whitespace between the end of the last match and the start
        # of this one is invalid (unquoted content).
        if args_str[last_end : match.start()].strip():
            msg = (
                f"Unquoted arguments found in step text: {args_str!r}. "
                "All arguments must be enclosed in double quotes."
            )
            raise ValueError(msg)
        last_end = match.end()

    # Any non-whitespace after the last match is also invalid.
    if args_str[last_end:].strip():
        msg = (
            f"Unquoted arguments found in step text: {args_str!r}. "
            "All arguments must be enclosed in double quotes."
        )
        raise ValueError(msg)

    # Empty quotes ("") are allowed; embedded spaces are preserved.
    return tuple(m.group(1) for m in matches)


def _construct_command_with_args(
    catalogue_context: ProgramCatalogue,
    program: Program,
    args: str,
) -> SafeCmd:
    """Construct a command with the given program and arguments."""
    from cuprum import scoped, sh

    parsed_args = _parse_quoted_args(args)
    with scoped(allowlist=catalogue_context.allowlist):
        cmd_builder = sh.make(program, catalogue=catalogue_context)
        return cmd_builder(*parsed_args)


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
    return _construct_command_with_args(catalogue_context, CARGO, args)


@when(
    parsers.re(r"I construct a git command with arguments (?P<args>.+)"),
    target_fixture="constructed_command",
)
def when_construct_git_command(
    catalogue_context: ProgramCatalogue,
    args: str,
) -> SafeCmd:
    """Construct a git command with the given arguments."""
    return _construct_command_with_args(catalogue_context, GIT, args)


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


# ---------------------------------------------------------------------------
# Unit tests for _parse_quoted_args helper
# ---------------------------------------------------------------------------


class TestParseQuotedArgs:
    """Unit tests for the _parse_quoted_args helper function."""

    def test_single_quoted_arg(self) -> None:
        """A single quoted argument should be parsed correctly."""
        assert _parse_quoted_args('"foo"') == ("foo",)

    def test_multiple_quoted_args(self) -> None:
        """Multiple quoted arguments should be parsed correctly."""
        assert _parse_quoted_args('"foo" "bar"') == ("foo", "bar")

    def test_quoted_arg_with_spaces(self) -> None:
        """Embedded spaces within quotes should be preserved."""
        assert _parse_quoted_args('"foo bar"') == ("foo bar",)

    def test_multiple_args_with_embedded_spaces(self) -> None:
        """Multiple args with embedded spaces should all be preserved."""
        assert _parse_quoted_args('"foo bar" "baz qux"') == ("foo bar", "baz qux")

    def test_empty_quoted_arg(self) -> None:
        """Empty quotes should produce an empty string argument."""
        assert _parse_quoted_args('""') == ("",)

    def test_empty_quotes_with_other_args(self) -> None:
        """Empty quotes mixed with non-empty args should work."""
        assert _parse_quoted_args('"" "bar"') == ("", "bar")
        assert _parse_quoted_args('"foo" ""') == ("foo", "")

    def test_empty_string_input(self) -> None:
        """An empty string input should return an empty tuple."""
        assert _parse_quoted_args("") == ()

    def test_whitespace_only_input(self) -> None:
        """Whitespace-only input should return an empty tuple."""
        assert _parse_quoted_args("   ") == ()

    def test_unquoted_arg_raises_valueerror(self) -> None:
        """Unquoted arguments should raise ValueError."""
        import pytest

        with pytest.raises(ValueError, match="Unquoted arguments"):
            _parse_quoted_args("foo")

    def test_unquoted_before_quoted_raises_valueerror(self) -> None:
        """Unquoted content before a quoted arg should raise ValueError."""
        import pytest

        with pytest.raises(ValueError, match="Unquoted arguments"):
            _parse_quoted_args('foo "bar"')

    def test_unquoted_after_quoted_raises_valueerror(self) -> None:
        """Unquoted content after a quoted arg should raise ValueError."""
        import pytest

        with pytest.raises(ValueError, match="Unquoted arguments"):
            _parse_quoted_args('"foo" bar')

    def test_unquoted_between_quoted_raises_valueerror(self) -> None:
        """Unquoted content between quoted args should raise ValueError."""
        import pytest

        with pytest.raises(ValueError, match="Unquoted arguments"):
            _parse_quoted_args('"foo" bar "baz"')

    def test_extra_whitespace_between_args_allowed(self) -> None:
        """Extra whitespace between quoted args should be allowed."""
        assert _parse_quoted_args('"foo"   "bar"') == ("foo", "bar")
        assert _parse_quoted_args('  "foo"  "bar"  ') == ("foo", "bar")
