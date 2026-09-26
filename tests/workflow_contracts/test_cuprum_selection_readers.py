"""Self-tests for the cuprum selection readers.

Each test feeds a reader a document that must be rejected, so a reader that
silently agreed with everything cannot pass this module. They are kept apart
from the alignment and freshness assertions in ``test_cuprum_selection.py`` so
that a broken reader is reported against the reader itself, rather than against
whichever assertion it happened to break.
"""

from __future__ import annotations

import pytest

from tests.helpers.cuprum_pin import SelectionError, declared_pin, names_cuprum
from tests.helpers.cuprum_selection import (
    lock_pin,
    lock_specifier,
    script_requirements,
)

pytestmark = pytest.mark.timeout(60)

#: The two lock shapes, as ``uv lock`` writes them. A script lock resolves only
#: the script's dependencies, so it carries no package for the script itself
#: and its requirement sits in ``[manifest]``; a project lock resolves the
#: project, so the requirement is in that package's metadata. Reading either
#: with the other's rule reports a correct lock as broken, which is why the
#: shape is a parameter rather than something guessed.
_SCRIPT_LOCK_SHAPE = (
    "version = 1\n\n[manifest]\nrequirements = [\n"
    '    { name = "cuprum", specifier = "==0.2.0b1" },\n]\n'
)
_PROJECT_LOCK_SHAPE = (
    '[[package]]\nname = "lading"\nversion = "0.3.1"\n\n[package.metadata]\n'
    'requires-dist = [\n    { name = "cuprum", specifier = "==0.2.0b1" },\n]\n'
)


def _pyproject_with(dependency: str) -> str:
    """Return a ``pyproject.toml`` body whose only dependency is ``dependency``."""
    return f'[project]\nname = "lading"\ndependencies = [{dependency!r}]\n'


def test_a_metadata_block_pinning_another_version_is_reported() -> None:
    """The script's own pin is compared, not merely parsed."""
    other = declared_pin(_pyproject_with("cuprum==0.1.0"))

    assert other == "0.1.0", f"the pin was mis-parsed as {other!r}"
    assert other != declared_pin(_pyproject_with("cuprum==0.2.0b1")), (
        "two different pins must not compare equal"
    )


@pytest.mark.parametrize(
    "requirement",
    ["cuprum>=0.2.0b1", "cuprum==0.2.0b1,<0.3", "cuprum[extra]==0.2.0b1"],
    ids=["range", "bounded", "extras"],
)
def test_a_requirement_that_is_not_an_exact_pin_is_reported(requirement: str) -> None:
    """A range, a bound, or an extra each defeats cross-path agreement."""
    text = _pyproject_with(requirement)

    with pytest.raises(SelectionError):
        declared_pin(text)


def test_a_requirement_with_a_marker_is_reported() -> None:
    """A marker would let the two paths resolve differently per environment."""
    text = _pyproject_with('cuprum==0.2.0b1; python_version >= "3.13"')

    with pytest.raises(SelectionError):
        declared_pin(text)


def test_a_script_without_a_metadata_block_is_reported() -> None:
    """No block means no standalone requirement, which must not read as none."""
    with pytest.raises(SelectionError, match="exactly one PEP 723 block"):
        script_requirements('"""A script with no inline metadata."""\n')


def test_a_script_with_two_metadata_blocks_is_reported() -> None:
    """Two blocks leave the resolved requirement ambiguous."""
    doubled = (
        "# /// script\n"
        '# dependencies = ["cuprum==0.2.0b1"]\n'
        "# ///\n"
        "# /// script\n"
        '# dependencies = ["cuprum==0.1.0"]\n'
        "# ///\n"
    )

    with pytest.raises(SelectionError, match="exactly one PEP 723 block"):
        script_requirements(doubled)


def test_a_lock_with_two_cuprum_entries_is_reported() -> None:
    """One name resolving twice means the paths could diverge undetected."""
    lock = (
        '[[package]]\nname = "cuprum"\nversion = "0.1.0"\n'
        '[[package]]\nname = "cuprum"\nversion = "0.2.0b1"\n'
    )

    with pytest.raises(SelectionError, match="exactly one cuprum package entry"):
        lock_pin(lock, site="a doubled lock")


def test_the_name_matcher_agrees_with_pep_503_on_what_is_cuprum() -> None:
    """Normalization is PEP 503's, and ``cup-rum`` is a *different* project.

    Two claims live here, and the second is the one that is easy to get
    backwards. PEP 503 collapses separator runs, so ``cup_rum``, ``cup.rum``,
    and ``cup--rum`` are one name -- but that name is ``cup-rum``, not
    ``cuprum``. The index does not equate them, so a site spelling the
    requirement ``cup-rum`` genuinely selects another distribution and must
    not be counted as a cuprum site here.
    """
    assert names_cuprum("Cuprum==0.2.0b1"), "the name is case-insensitive"
    assert names_cuprum("CUPRUM==0.2.0b1"), "upper case must still match"

    for spelling in ("cup_rum", "cup.rum", "cup--rum", "cup-rum"):
        assert not names_cuprum(f"{spelling}==0.2.0b1"), (
            f"{spelling!r} normalises to 'cup-rum', which is not cuprum"
        )


def test_a_project_lock_entry_without_a_requirement_is_reported() -> None:
    """A lock that recorded no requirement cannot be checked for staleness."""
    lock = '[[package]]\nname = "lading"\nversion = "0.3.1"\n'

    with pytest.raises(SelectionError, match="exactly one cuprum requirement"):
        lock_specifier(lock, site="a lock with no requirement", origin="project")


def test_each_lock_shape_is_read_from_its_own_requirement_site() -> None:
    """A script lock's pin comes from ``[manifest]``, a project lock's from metadata.

    Reading a script lock the project way is not a hypothetical: it reports
    the lock as carrying no lading entry, so a correctly locked script reads
    as broken, and the mistake is invisible while the caller is only ever
    handed a document it happens to know the shape of.
    """
    from_script = lock_specifier(
        _SCRIPT_LOCK_SHAPE, site="a script lock", origin="script"
    )
    assert from_script == "0.2.0b1", f"the script lock's pin read as {from_script!r}"
    from_project = lock_specifier(
        _PROJECT_LOCK_SHAPE, site="a project lock", origin="project"
    )
    assert from_project == "0.2.0b1", f"the project lock's pin read as {from_project!r}"


def test_a_script_lock_losing_its_manifest_is_reported() -> None:
    """The manifest is the script path's only requirement record; its absence fails.

    Without this, a script lock that dropped its manifest would be read as a
    lock that names no cuprum, and the ``exactly one`` assertion would be the
    thing that failed -- a message pointing at the symptom rather than at the
    missing record.
    """
    with pytest.raises(SelectionError, match="exactly one cuprum requirement"):
        lock_specifier(
            'version = 1\n\n[[package]]\nname = "attrs"\nversion = "26.1.0"\n',
            site="a script lock with no manifest",
            origin="script",
        )


def test_a_project_lock_read_as_a_script_lock_is_reported() -> None:
    """The shape argument is load-bearing, not decorative.

    This is the inverse of the defect the shape parameter exists to prevent:
    it pins down that the two readings are genuinely different, so a later
    "simplification" that tries project first and falls back to the manifest
    cannot pass both this test and the one above.
    """
    with pytest.raises(SelectionError, match="exactly one cuprum requirement"):
        lock_specifier(_PROJECT_LOCK_SHAPE, site="a project lock", origin="script")
