"""Unit tests for :mod:`lading.utils.commands`."""

import pytest

from lading.utils.commands import CARGO, GIT, LADING_CATALOGUE, SCCACHE


class TestLadingCatalogue:
    """Tests for the shared programme catalogue."""

    def test_catalogue_is_importable(self) -> None:
        """The catalogue should be importable from lading.utils.commands."""
        assert LADING_CATALOGUE is not None, (
            "importing the shared catalogue must yield a catalogue instance"
        )

    def test_catalogue_registers_cargo(self) -> None:
        """The catalogue should include cargo as an allowed programme."""
        assert LADING_CATALOGUE.is_allowed(CARGO), (
            "cargo must be an allowlisted programme in the shared catalogue"
        )

    def test_catalogue_registers_git(self) -> None:
        """The catalogue should include git as an allowed programme."""
        assert LADING_CATALOGUE.is_allowed(GIT), (
            "git must be an allowlisted programme in the shared catalogue"
        )

    def test_catalogue_registers_sccache(self) -> None:
        """The catalogue should include sccache for compiler-cache queries."""
        assert LADING_CATALOGUE.is_allowed(SCCACHE), "sccache must be allowlisted"
        assert LADING_CATALOGUE.lookup(SCCACHE).program == SCCACHE, (
            "catalogue lookup should return the sccache programme entry"
        )

    def test_catalogue_allowlist_contains_cargo_and_git(self) -> None:
        """The catalogue allowlist should contain both cargo and git."""
        allowlist = LADING_CATALOGUE.allowlist

        assert CARGO in allowlist, "cargo must appear in the catalogue's allowlist"
        assert GIT in allowlist, "git must appear in the catalogue's allowlist"

    def test_catalogue_can_lookup_cargo(self) -> None:
        """The catalogue should return a ProgramEntry for cargo."""
        entry = LADING_CATALOGUE.lookup(CARGO)

        assert entry is not None, "looking up cargo must return a catalogue entry"
        assert entry.program == CARGO, (
            "the cargo entry must name cargo as its programme"
        )

    def test_catalogue_can_lookup_git(self) -> None:
        """The catalogue should return a ProgramEntry for git."""
        entry = LADING_CATALOGUE.lookup(GIT)

        assert entry is not None, "looking up git must return a catalogue entry"
        assert entry.program == GIT, "the git entry must name git as its programme"

    def test_catalogue_rejects_unregistered_program(self) -> None:
        """Unregistered programmes should raise UnknownProgramError."""
        from cuprum import Program, UnknownProgramError

        unregistered = Program("unregistered-program-xyz")

        with pytest.raises(UnknownProgramError):
            LADING_CATALOGUE.lookup(unregistered)


class TestProgramConstants:
    """Tests for the exported program constants."""

    def test_cargo_program_name(self) -> None:
        """CARGO should represent the cargo executable."""
        assert str(CARGO) == "cargo", (
            "the CARGO constant must represent the cargo executable"
        )

    def test_git_program_name(self) -> None:
        """GIT should represent the git executable."""
        assert str(GIT) == "git", "the GIT constant must represent the git executable"

    def test_programs_exported_from_utils_package(self) -> None:
        """Program constants should be accessible from lading.utils."""
        from lading import utils

        assert utils.CARGO is CARGO, (
            "utils.CARGO must re-export the same programme constant"
        )
        assert utils.GIT is GIT, "utils.GIT must re-export the same programme constant"
        assert utils.LADING_CATALOGUE is LADING_CATALOGUE, (
            "utils.LADING_CATALOGUE must re-export the same catalogue instance"
        )


class TestScopedContext:
    """Tests for using the catalogue with cuprum's scoped context."""

    def test_catalogue_can_be_used_in_scoped_context(self) -> None:
        """The catalogue should work with scoped() context manager."""
        from cuprum import scoped, sh

        with scoped(catalogue=LADING_CATALOGUE):
            cargo_builder = sh.make(CARGO, catalogue=LADING_CATALOGUE)
            git_builder = sh.make(GIT, catalogue=LADING_CATALOGUE)

            assert cargo_builder is not None, (
                "scoped() must yield a usable cargo command builder"
            )
            assert git_builder is not None, (
                "scoped() must yield a usable git command builder"
            )

    def test_scoped_context_allows_command_construction(self) -> None:
        """Commands should be constructable within a scoped context."""
        from cuprum import scoped, sh

        with scoped(catalogue=LADING_CATALOGUE):
            cargo_builder = sh.make(CARGO, catalogue=LADING_CATALOGUE)
            cmd = cargo_builder("metadata", "--format-version", "1")

            assert cmd.argv_with_program == (
                "cargo",
                "metadata",
                "--format-version",
                "1",
            ), "argv must preserve the built cargo metadata invocation exactly"

    def test_scoped_context_rejects_unregistered_program(self) -> None:
        """Unregistered programmes should raise UnknownProgramError in scope."""
        from cuprum import Program, UnknownProgramError, scoped, sh

        unregistered = Program("unregistered-program-xyz")

        with (
            scoped(catalogue=LADING_CATALOGUE),
            pytest.raises(UnknownProgramError),
        ):
            sh.make(unregistered, catalogue=LADING_CATALOGUE)
