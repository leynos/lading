"""Unit cover for `lading clean` at the command-line boundary.

These live outside `test_clean.py` because they cover something that file
cannot: `test_clean.py` calls `lading.commands.clean.run` directly, so it
would pass unchanged against a command that was never registered, or one
whose Cyclopts options reached `CleanOptions` with the wrong values. The
command deletes directories, so a `--remove` that did not map is a run that
deletes when nobody asked, and a `--location` that did not map is a run that
deletes somewhere else.
"""

from __future__ import annotations

import tempfile
import typing as typ

from lading import cli
from lading.commands.publish_staging import STAGING_PREFIX

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path

    import pytest


def _staging_tree(location: Path, suffix: str) -> Path:
    """Create a staging directory the way a publish would have left one."""
    tree = location / f"{STAGING_PREFIX}{suffix}"
    tree.mkdir()
    (tree / "Cargo.toml").write_bytes(b"")
    return tree


def test_the_clean_command_is_registered(tmp_path: Path) -> None:
    """The application must dispatch `clean` and honour `--location`.

    Reaching the command through `cli.app` is the only way to show that the
    decorator registered it; a command defined but never registered fails
    here and nowhere else.
    """
    summary = cli.app(["clean", "--location", str(tmp_path)])

    assert "No staging directories found" in summary, (
        "the clean command did not run through the application"
    )
    assert str(tmp_path) in summary, "--location did not reach the command"


def test_the_remove_flag_reaches_the_command(tmp_path: Path) -> None:
    """`--remove` must map onto `CleanOptions.remove`.

    A flag that did not map would leave the tree in place while the caller
    believed the space had been reclaimed.
    """
    tree = _staging_tree(tmp_path, "gone")

    summary = cli.app(["clean", "--location", str(tmp_path), "--remove"])

    assert not tree.exists(), "--remove did not reach the command"
    assert "Removed 1 staging directory" in summary, (
        "the removal was not reported through the application"
    )


def test_the_command_reports_rather_than_removes_by_default(tmp_path: Path) -> None:
    """Omitting `--remove` must leave the tree alone.

    This is what stops the previous case passing against a command that
    ignored the flag and always removed. Reporting is the default precisely
    because the alternative deletes.
    """
    tree = _staging_tree(tmp_path, "kept")

    summary = cli.app(["clean", "--location", str(tmp_path)])

    assert tree.is_dir(), "a bare clean deleted a staging tree"
    assert "Found 1 staging directory" in summary, (
        "the default run did not report what it found"
    )


def test_the_command_defaults_to_the_staging_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bare `lading clean` must search where a publish actually stages.

    Every other case here passes `--location`, so all of them would pass
    against a command whose default never reached `CleanOptions`, and the
    form the guide documents is the one nobody would have run.
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    tree = _staging_tree(tmp_path, "bare")

    summary = cli.app(["clean"])

    assert tree.is_dir(), "a bare clean removed a staging tree"
    assert tree.name in summary, (
        "the default location did not reach the command's search"
    )
