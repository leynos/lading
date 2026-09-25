"""Discovery tests for the release wheel upload script.

The script replaced a shell glob that assumed one layout for the downloaded
artefact, so discovery is the part of it that must not be trusting: it searches
recursively, and it distinguishes an absent directory from an empty one and an
unreadable one from either.
"""

from __future__ import annotations

import os
import string
import typing as typ
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.helpers.script_imports import SCRIPT_DIRECTORY, import_script_module

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    import types


@pytest.fixture(name="upload_module")
def upload_module_fixture(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Import the uploader's logic the way the script imports it.

    Returns
    -------
    types.ModuleType
        The imported ``release_wheel_upload`` module.
    """
    return import_script_module(monkeypatch, "release_wheel_upload")


def _make_wheel(directory: Path, name: str) -> Path:
    """Create an empty file named like a wheel and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / name
    wheel.write_bytes(b"")
    return wheel


def test_discovery_finds_wheels_in_nested_directories(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """Wheels are found wherever the download action places them.

    The previous shell glob assumed one layout and silently matched nothing
    when the action changed it, so the search is deliberately recursive.
    """
    nested = _make_wheel(tmp_path / "wheels-pure", "b-1.0-py3-none-any.whl")
    top_level = _make_wheel(tmp_path, "a-1.0-py3-none-any.whl")
    _make_wheel(tmp_path, "notes.txt")

    found = upload_module.discover_wheels(tmp_path)

    assert found == (top_level, nested), f"expected both wheels in order: {found}"


def test_discovery_returns_nothing_for_an_empty_directory(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """An empty directory yields no wheels rather than raising."""
    assert upload_module.discover_wheels(tmp_path) == ()


def test_missing_directory_is_a_distinct_failure(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """An absent directory reports itself rather than looking empty.

    A download step that never ran and a build that produced nothing are
    different failures, and the operator reading the log needs to tell them
    apart.
    """
    with pytest.raises(upload_module.UploadError, match="does not exist"):
        upload_module.discover_wheels(tmp_path / "absent")


def test_a_file_in_place_of_the_directory_is_rejected(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """A path that is not a directory fails rather than yielding nothing."""
    path = tmp_path / "dist"
    path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(upload_module.UploadError, match="not a directory"):
        upload_module.discover_wheels(path)


requires_unprivileged = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="permission bits do not restrain this user",
)


@requires_unprivileged
def test_an_unreadable_subtree_is_an_error_not_an_empty_result(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """A subtree the job cannot read fails loudly instead of reading as empty.

    ``Path.rglob`` skips directories it cannot open, so an artefact tree with
    one unreadable branch would otherwise discover nothing and fail with "no
    wheel found" -- the wrong diagnosis of a permissions problem.
    """
    locked = tmp_path / "wheels-pure"
    _make_wheel(locked, "a-1.0-py3-none-any.whl")
    locked.chmod(0o000)
    try:
        with pytest.raises(upload_module.UploadError) as raised:
            upload_module.discover_wheels(tmp_path)
    finally:
        locked.chmod(0o755)

    assert raised.value.outcome == upload_module.Outcome.UNREADABLE_DIRECTORY
    assert "Could not read" in str(raised.value), str(raised.value)


@requires_unprivileged
def test_an_unreadable_artefact_path_is_not_reported_as_absent(
    upload_module: types.ModuleType, tmp_path: Path
) -> None:
    """A stat the job is not permitted to make is a read failure, not absence.

    ``Path.exists`` answers False for a permission error, which would blame the
    download step for a problem it did not cause.
    """
    parent = tmp_path / "locked"
    (parent / "dist").mkdir(parents=True)
    parent.chmod(0o000)
    try:
        with pytest.raises(upload_module.UploadError) as raised:
            upload_module.discover_wheels(parent / "dist")
    finally:
        parent.chmod(0o755)

    assert raised.value.outcome == upload_module.Outcome.UNREADABLE_DIRECTORY


@given(
    names=st.lists(
        st.lists(
            st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=4),
            min_size=1,
            max_size=3,
        ),
        min_size=0,
        max_size=6,
        unique_by=lambda parts: "/".join(parts),
    ),
    suffixes=st.lists(st.sampled_from([".whl", ".txt", ".tar.gz"]), max_size=6),
)
@settings(max_examples=40, deadline=None)
def test_discovery_returns_exactly_the_wheels(
    names: list[list[str]], suffixes: list[str]
) -> None:
    """Every wheel is found, nothing else is, and the order is by path.

    The layout the download action produces is not fixed, so the property is
    stated over arbitrary nesting rather than the one tree an example can show.
    """
    import importlib
    import sys
    import tempfile

    if str(SCRIPT_DIRECTORY) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIRECTORY))
    module = importlib.import_module("release_wheel_upload")

    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root)
        expected: list[Path] = []
        for index, parts in enumerate(names):
            suffix = suffixes[index] if index < len(suffixes) else ".whl"
            path = root.joinpath(*parts).with_suffix(suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_dir():
                continue
            path.write_bytes(b"")
            if suffix == ".whl":
                expected.append(path)

        found = module.discover_wheels(root)

    assert found == tuple(sorted(expected)), (
        f"expected exactly the wheels in path order: {found} != {sorted(expected)}"
    )
