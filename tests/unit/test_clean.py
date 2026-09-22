"""Unit cover for `lading clean`.

This command deletes directories, so most of what is tested here is what it
must refuse to touch. Each case names one thing a careless glob would have
taken.
"""

from __future__ import annotations

import collections.abc as cabc
import tempfile
from pathlib import Path

import pytest

from lading.commands import clean
from lading.commands.publish_staging import STAGING_PREFIX


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
    assert clean.STAGING_PREFIX is STAGING_PREFIX, (
        "clean and publish staging must read one constant, not two literals"
    )


def test_leftovers_are_found_with_their_sizes(tmp_path: Path) -> None:
    """The report has to say what removing each tree would reclaim."""
    _staging_tree(tmp_path, "aaa", contents=b"0" * 100)
    _staging_tree(tmp_path, "bbb", contents=b"0" * 20)

    found = clean.find_leftovers(tmp_path)

    assert [leftover.path.name for leftover in found] == [
        f"{STAGING_PREFIX}aaa",
        f"{STAGING_PREFIX}bbb",
    ], "the leftovers were not reported in name order"
    assert [leftover.size_bytes for leftover in found] == [100, 20], (
        "each leftover must carry the bytes removing it would reclaim"
    )


def test_a_directory_without_the_prefix_is_not_in_scope(tmp_path: Path) -> None:
    """The prefix is the whole of the command's claim on a directory.

    Anything else under the temporary directory belongs to someone else, and
    on a shared host that is everyone else.
    """
    innocent = tmp_path / "important-work"
    innocent.mkdir()
    _staging_tree(tmp_path, "mine")

    found = clean.find_leftovers(tmp_path)

    assert [leftover.path for leftover in found] == [
        tmp_path / f"{STAGING_PREFIX}mine"
    ], "a directory without the staging prefix was taken into scope"


def test_a_nested_match_is_not_in_scope(tmp_path: Path) -> None:
    """Only the immediate children are considered.

    A recursive search would reach inside a staged workspace, and inside
    unrelated trees that happen to sit under the same temporary directory.
    """
    nested = tmp_path / "someone-elses-project"
    nested.mkdir()
    _staging_tree(nested, "buried")

    assert clean.find_leftovers(tmp_path) == (), (
        "a match nested below the search directory was taken into scope"
    )


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

    assert clean.find_leftovers(tmp_path) == (), (
        "a symbolic link named like a staging tree was taken into scope"
    )

    clean.run(options=clean.CleanOptions(location=tmp_path, remove=True))

    assert (target / "keep.txt").exists(), "the link's target was followed"
    assert link.is_symlink(), "the link itself was removed"


def test_a_file_named_like_a_staging_tree_is_not_in_scope(tmp_path: Path) -> None:
    """The command removes trees; a regular file is not one."""
    (tmp_path / f"{STAGING_PREFIX}notadir").write_text("x", encoding="utf-8")

    assert clean.find_leftovers(tmp_path) == (), (
        "a regular file named like a staging tree was taken into scope"
    )


def test_reporting_is_the_default(tmp_path: Path) -> None:
    """Nothing is deleted unless the caller says so.

    A command that deleted on its bare invocation would be one mistyped
    `--location` away from taking a directory the user cared about.
    """
    tree = _staging_tree(tmp_path, "kept", contents=b"0" * 10)

    summary = clean.run(options=clean.CleanOptions(location=tmp_path))

    assert tree.is_dir(), "the default run deleted something"
    assert "Found 1 staging directory" in summary, (
        "the default run did not report what it found"
    )
    assert "--remove" in summary, "the report did not say how to delete them"


def test_removal_reports_what_it_reclaimed(tmp_path: Path) -> None:
    """The point of the command is the space, so the report names it."""
    tree = _staging_tree(tmp_path, "gone", contents=b"0" * 2048)

    summary = clean.run(options=clean.CleanOptions(location=tmp_path, remove=True))

    assert not tree.exists(), "the tree survived a removal run"
    assert "Removed 1 staging directory" in summary, (
        "the removal did not report what it took"
    )
    assert "2.0 KiB" in summary, "the removal did not report the space reclaimed"


def test_an_empty_location_says_so(tmp_path: Path) -> None:
    """Silence would leave the user wondering whether it ran."""
    summary = clean.run(options=clean.CleanOptions(location=tmp_path))

    assert "No staging directories found" in summary, (
        "an empty search reported nothing at all"
    )


def test_a_missing_location_is_not_an_error(tmp_path: Path) -> None:
    """A temporary directory that does not exist holds no leftovers."""
    assert clean.find_leftovers(tmp_path / "absent") == (), (
        "a search directory that does not exist was not treated as empty"
    )


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

    assert stubborn.is_dir(), "the tree whose removal failed was reported gone"
    assert not removable.exists(), "the sweep stopped at the first failure"
    assert "Removed 1 of 2 staging directories" in summary, (
        "a partial sweep must count both what it removed and what it found"
    )
    assert stubborn.name in summary, "the retained tree was not named"
    assert "Could not remove 1" in summary, "the failure was not reported"


def test_a_sweep_that_removes_nothing_does_not_claim_an_empty_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding trees and removing none of them is not an empty directory.

    Summarising the removed trees alone made these two cases identical, so a
    sweep in which every `shutil.rmtree` raised reported `No staging
    directories found` while every tree it found was still on disk.
    """
    trees = [_staging_tree(tmp_path, "aaa"), _staging_tree(tmp_path, "bbb")]

    def refuse(path: object, *arguments: object, **keywords: object) -> None:
        """Fail for every tree."""
        del path, arguments, keywords
        message = "device or resource busy"
        raise OSError(message)

    monkeypatch.setattr(clean.shutil, "rmtree", refuse)

    summary = clean.run(options=clean.CleanOptions(location=tmp_path, remove=True))

    assert all(tree.is_dir() for tree in trees), "a tree was removed after all"
    assert "No staging directories found" not in summary, (
        "a failed sweep claimed the search found nothing"
    )
    assert "Removed 0 of 2 staging directories" in summary, (
        "the summary did not report zero removals against two found"
    )
    assert "Could not remove 2" in summary, "the failures were not reported"


def test_the_default_location_is_where_a_publish_stages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Omitting the location must search the system temporary directory.

    Every other case supplies one, so all of them would pass against a
    default that resolved somewhere else entirely. The default is the whole
    point of the command: a user sweeping leftovers runs `lading clean` with
    no arguments, and a wrong default reports an empty directory while
    thousands of staged trees sit where it did not look.
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    tree = _staging_tree(tmp_path, "default", contents=b"x" * 32)

    summary = clean.run()

    assert tree.is_dir(), "a default report removed a tree"
    assert tree.name in summary, "the default search did not find the staged tree"
    assert "Found 1 staging directory" in summary, "the default search reported nothing"


def test_an_unreadable_location_is_a_domain_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A directory that cannot be listed must not read as an empty sweep.

    The per-file errors met while sizing a tree are counted as zero on
    purpose: an entry vanishing underfoot is the stale state this command
    clears. A search directory that cannot be read is different in kind.
    Swallowing it would report "No staging directories found" and tell the
    caller their disk was clear when nothing had been looked at.

    The refusal is injected rather than made with ``chmod``, because a mode
    that forbids reading stops neither root nor a Windows runner from
    listing the directory, so a permission-based case skips or passes
    vacuously on exactly the runners it most needs to cover.
    """
    location = tmp_path / "unreadable"
    location.mkdir()
    resolved = location.resolve()
    real_iterdir = Path.iterdir

    def refuse_location(path: Path) -> cabc.Iterator[Path]:
        if path == resolved:
            message = "directory is unreadable"
            raise PermissionError(message)
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", refuse_location)
    with pytest.raises(
        clean.CleanError, match="Cannot read the staging location"
    ) as raised:
        clean.run(options=clean.CleanOptions(location=location))
    # Carried as a value, not only inside the message: a caller deciding
    # what to do next should not have to parse prose to learn which
    # directory failed.
    assert raised.value.location == resolved, (
        "the failure did not name the directory it could not read"
    )


def test_a_windows_junction_is_not_in_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A junction named like a staging tree must not be removed.

    The symbolic-link case above cannot reach this one. A Windows junction
    answers :data:`True` to ``is_dir`` and :data:`False` to ``is_symlink``, so
    a scope rule that tested only for links would take it into scope and hand
    it to :func:`shutil.rmtree`, which removes the junction itself. What is
    lost is a link to a real directory somewhere else, which is the harm the
    link rule exists to prevent.

    Junctions cannot be created on this platform, so the predicate is driven
    directly rather than through a fixture that would silently test nothing:
    a fixture that cannot build the case cannot discriminate.
    """
    tree = _staging_tree(tmp_path, "junction")
    monkeypatch.setattr(
        Path, "is_junction", lambda self: self.name == tree.name, raising=False
    )

    assert not clean._is_leftover(tree), (
        "a junction named like a staging tree was taken into scope"
    )
    assert clean.find_leftovers(tmp_path) == (), "discovery returned a junction"
