"""Temporary descriptor ownership stays with the atomic writer."""

import os
import typing as typ
from pathlib import Path

import pytest

from lading.commands import bump_toml_files

FDOPEN_FAILURE_MESSAGE = "fdopen failed"


def test_temporary_text_wrapper_does_not_own_descriptor(tmp_path: Path) -> None:
    """Closing the text wrapper leaves descriptor cleanup to the caller."""
    temporary_path = tmp_path / "temporary"
    descriptor = os.open(temporary_path, os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        handle = bump_toml_files._open_temporary_text_file(descriptor)
        handle.close()
        assert os.fstat(descriptor).st_ino == temporary_path.stat().st_ino, (
            "closing the text wrapper must leave its caller-owned descriptor open"
        )
    finally:
        os.close(descriptor)


def test_temporary_descriptor_closes_once_when_wrapper_creation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed text-wrapper construction closes the caller-owned fd once."""
    temporary_path = tmp_path / "temporary"
    descriptor = os.open(temporary_path, os.O_CREAT | os.O_WRONLY, 0o600)
    original_close = os.close
    closed_descriptors: list[int] = []

    def track_close(file_descriptor: int) -> None:
        """Record closure and still release the operating-system descriptor."""
        closed_descriptors.append(file_descriptor)
        original_close(file_descriptor)

    def fail_fdopen(*_args: object, **_kwargs: object) -> typ.NoReturn:
        """Simulate wrapper construction failing before ownership transfer."""
        raise OSError(FDOPEN_FAILURE_MESSAGE)

    monkeypatch.setattr(os, "close", track_close)
    monkeypatch.setattr(os, "fdopen", fail_fdopen)

    with pytest.raises(OSError, match=FDOPEN_FAILURE_MESSAGE):
        bump_toml_files._write_temporary_content(descriptor, "content", None)

    assert closed_descriptors == [descriptor], (
        "the caller should close its descriptor exactly once after fdopen fails"
    )
