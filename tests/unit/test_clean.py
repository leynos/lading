"""Unit cover for `lading clean`.

This command deletes directories, so most of what is tested here is what it
must refuse to touch. Each case names one thing a careless glob would have
taken.
"""

from __future__ import annotations

import typing as typ

from lading.commands import clean
from lading.commands.publish_staging import STAGING_PREFIX

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path

    import pytest


def _staging_tree(location: Path, suffix: str, *, contents: bytes = b"") -> Path:
    """Create a staging directory the way a publish would have left one.

    Returns
    -------
    Path
        The directory created.
    """
    tree = location / f"{STAGING_PREFIX}{suffix}"
    tree.mkdir()
    (tree / "Cargo.toml").write_bytes(contents)
    return tree


def test_the_prefix_is_the_one_a_publish_stages_with() -> None:
    """Both sides must read the same constant, not two equal literals.

    A publish that changed its prefix would leak trees no clean could find,
    and a clean with its own copy of the string would not notice.
    """
    assert clean.STAGING_PREFIX is STAGING_PREFIX


def test_leftovers_are_found_with_their_sizes(tmp_path: Path) -> None:
    """The report has to say what removing each tree would reclaim."""
    _staging_tree(tmp_path, "aaa", contents=b"0" * 100)
    _staging_tree(tmp_path, "bbb", contents=b"0" * 20)

    found = clean.find_leftovers(tmp_path)

    assert [leftover.path.name for leftover in found] == [
        f"{STAGING_PREFIX}aaa",
        f"{STAGING_PREFIX}bbb",
    ]
    assert [leftover.size_bytes for leftover in found] == [100, 20]


def test_a_directory_without_the_prefix_is_not_in_scope(tmp_path: Path) -> None:
    """The prefix is the whole of the command's claim on a directory.

    Anything else under the temporary directory belongs to someone else, and
    on a shared host that is everyone else.
    """
    innocent = tmp_path / "important-work"
    innocent.mkdir()
    _staging_tree(tmp_path, "mine")

    found = clean.find_leftovers(tmp_path)

    assert [leftover.path for leftover in found] == [tmp_path / f"{STAGING_PREFIX}mine"]


def test_a_nested_match_is_not_in_scope(tmp_path: Path) -> None:
    """Only the immediate children are considered.

    A recursive search would reach inside a staged workspace, and inside
    unrelated trees that happen to sit under the same temporary directory.
    """
    nested = tmp_path / "someone-elses-project"
    nested.mkdir()
    _staging_tree(nested, "buried")

    assert clean.find_leftovers(tmp_path) == ()


def test_a_symlink_is_never_followed(tmp_path: Path) -> None:
    """A link named like a staging tree must not lead the removal elsewhere.

    Following one would delete whatever it points at, which is the worst
    thing this command could do.
    """
    target = tmp_path / "real-data"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    link = tmp_path / f"{STAGING_PREFIX}link"
    link.symlink_to(target, target_is_directory=True)

    assert clean.find_leftovers(tmp_path) == ()

    clean.run(options=clean.CleanOptions(location=tmp_path, remove=True))

    assert (target / "keep.txt").exists(), "the link's target was followed"
    assert link.is_symlink(), "the link itself was removed"


def test_a_file_named_like_a_staging_tree_is_not_in_scope(tmp_path: Path) -> None:
    """The command removes trees; a regular file is not one."""
    (tmp_path / f"{STAGING_PREFIX}notadir").write_text("x", encoding="utf-8")

    assert clean.find_leftovers(tmp_path) == ()


def test_reporting_is_the_default(tmp_path: Path) -> None:
    """Nothing is deleted unless the caller says so.

    A command that deleted on its bare invocation would be one mistyped
    `--location` away from taking a directory the user cared about.
    """
    tree = _staging_tree(tmp_path, "kept", contents=b"0" * 10)

    summary = clean.run(options=clean.CleanOptions(location=tmp_path))

    assert tree.is_dir(), "the default run deleted something"
    assert "Found 1 staging directory" in summary
    assert "--remove" in summary


def test_removal_reports_what_it_reclaimed(tmp_path: Path) -> None:
    """The point of the command is the space, so the report names it."""
    tree = _staging_tree(tmp_path, "gone", contents=b"0" * 2048)

    summary = clean.run(options=clean.CleanOptions(location=tmp_path, remove=True))

    assert not tree.exists()
    assert "Removed 1 staging directory" in summary
    assert "2.0 KiB" in summary


def test_an_empty_location_says_so(tmp_path: Path) -> None:
    """Silence would leave the user wondering whether it ran."""
    summary = clean.run(options=clean.CleanOptions(location=tmp_path))

    assert "No staging directories found" in summary


def test_a_missing_location_is_not_an_error(tmp_path: Path) -> None:
    """A temporary directory that does not exist holds no leftovers."""
    assert clean.find_leftovers(tmp_path / "absent") == ()


def test_a_tree_that_cannot_be_removed_is_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One unremovable tree must not abandon the rest of the sweep."""
    stubborn = _staging_tree(tmp_path, "aaa")
    removable = _staging_tree(tmp_path, "bbb")
    real_rmtree = clean.shutil.rmtree

    def refuse(path: object, *arguments: object, **keywords: object) -> None:
        """Fail for the first tree only."""
        if path == stubborn:
            message = "device or resource busy"
            raise OSError(message)
        real_rmtree(path, *arguments, **keywords)

    monkeypatch.setattr(clean.shutil, "rmtree", refuse)

    summary = clean.run(options=clean.CleanOptions(location=tmp_path, remove=True))

    assert stubborn.is_dir()
    assert not removable.exists()
    assert "Removed 1 staging directory" in summary
