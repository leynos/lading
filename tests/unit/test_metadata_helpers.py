"""Tests for workspace metadata helper functions."""

from __future__ import annotations

import typing as typ

from lading.workspace import metadata as metadata_module

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_build_invocation_environment_sets_pwd(tmp_path: Path) -> None:
    """The invocation environment should include PWD when cwd is provided."""
    working_dir = tmp_path / "work"
    env = metadata_module._build_invocation_environment(str(working_dir))

    assert env["PWD"] == str(working_dir)


def test_coerce_text_handles_bytes() -> None:
    """Byte streams should be decoded to strings."""
    assert metadata_module._coerce_text(b"bytes") == "bytes"


def test_error_convenience_constructors() -> None:
    """Helper constructors should expose descriptive messages."""
    assert "Invalid CMOX_IPC_TIMEOUT value" in str(
        metadata_module.CargoMetadataError.invalid_cmd_mox_timeout()
    )
    assert "must be positive" in str(
        metadata_module.CargoMetadataError.non_positive_cmd_mox_timeout()
    )
