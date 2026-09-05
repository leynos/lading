"""Tests for working-directory test helpers."""

from __future__ import annotations

import typing as typ
from pathlib import Path

from tests.helpers.cwd import chdir_for_test

if typ.TYPE_CHECKING:
    import pytest


def test_chdir_for_test_creates_mutmut_source_path_before_chdir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The mutmut source path exists before the helper changes directory."""
    original_chdir = monkeypatch.chdir

    def assert_source_path_exists(path: Path) -> None:
        assert (path / "lading").is_dir()
        original_chdir(path)

    monkeypatch.setattr(monkeypatch, "chdir", assert_source_path_exists)

    chdir_for_test(monkeypatch, tmp_path)

    assert Path.cwd() == tmp_path
