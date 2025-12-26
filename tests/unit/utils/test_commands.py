"""Unit tests for :mod:`lading.utils.commands`."""

from __future__ import annotations

import pytest

from lading.utils.commands import CARGO, GIT, LADING_CATALOGUE


class TestLadingCatalogue:
    """Tests for the shared programme catalogue."""

    def test_catalogue_is_importable(self) -> None:
        """The catalogue should be importable from lading.utils.commands."""
        from lading.utils.commands import LADING_CATALOGUE

        assert LADING_CATALOGUE is not None

    def test_catalogue_registers_cargo(self) -> None:
        """The catalogue should include cargo as an allowed programme."""
        assert LADING_CATALOGUE.is_allowed(CARGO)

    def test_catalogue_registers_git(self) -> None:
        """The catalogue should include git as an allowed programme."""
        assert LADING_CATALOGUE.is_allowed(GIT)

    def test_catalogue_allowlist_contains_cargo_and_git(self) -> None:
        """The catalogue allowlist should contain both cargo and git."""
        allowlist = LADING_CATALOGUE.allowlist

        assert CARGO in allowlist
        assert GIT in allowlist
        assert len(allowlist) == 2

    def test_catalogue_can_lookup_cargo(self) -> None:
        """The catalogue should return a ProgramEntry for cargo."""
        entry = LADING_CATALOGUE.lookup(CARGO)

        assert entry is not None
        assert entry.program == CARGO

    def test_catalogue_can_lookup_git(self) -> None:
        """The catalogue should return a ProgramEntry for git."""
        entry = LADING_CATALOGUE.lookup(GIT)

        assert entry is not None
        assert entry.program == GIT

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
        assert str(CARGO) == "cargo"

    def test_git_program_name(self) -> None:
        """GIT should represent the git executable."""
        assert str(GIT) == "git"

    def test_programs_exported_from_utils_package(self) -> None:
        """Program constants should be accessible from lading.utils."""
        from lading import utils

        assert utils.CARGO is CARGO
        assert utils.GIT is GIT


class TestScopedContext:
    """Tests for using the catalogue with cuprum's scoped context."""

    def test_catalogue_can_be_used_in_scoped_context(self) -> None:
        """The catalogue should work with scoped() context manager."""
        from cuprum import scoped, sh

        with scoped(allowlist=LADING_CATALOGUE.allowlist):
            cargo_builder = sh.make(CARGO, catalogue=LADING_CATALOGUE)
            git_builder = sh.make(GIT, catalogue=LADING_CATALOGUE)

            assert cargo_builder is not None
            assert git_builder is not None

    def test_scoped_context_allows_command_construction(self) -> None:
        """Commands should be constructable within a scoped context."""
        from cuprum import scoped, sh

        with scoped(allowlist=LADING_CATALOGUE.allowlist):
            cargo_builder = sh.make(CARGO, catalogue=LADING_CATALOGUE)
            cmd = cargo_builder("metadata", "--format-version", "1")

            assert cmd.argv_with_program == (
                "cargo",
                "metadata",
                "--format-version",
                "1",
            )

    def test_scoped_context_rejects_unregistered_program(self) -> None:
        """Unregistered programmes should raise UnknownProgramError in scope."""
        from cuprum import Program, UnknownProgramError, scoped, sh

        unregistered = Program("unregistered-program-xyz")

        with (
            scoped(allowlist=LADING_CATALOGUE.allowlist),
            pytest.raises(UnknownProgramError),
        ):
            sh.make(unregistered, catalogue=LADING_CATALOGUE)
