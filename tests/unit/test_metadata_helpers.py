"""Tests for workspace metadata helper functions."""

from __future__ import annotations

from lading.workspace import metadata as metadata_module


def test_coerce_text_handles_bytes() -> None:
    """Byte streams should be decoded to strings."""
    assert metadata_module.coerce_text(b"bytes") == "bytes"


def test_error_convenience_constructors() -> None:
    """Helper constructors should expose descriptive messages."""
    assert "Invalid CMOX_IPC_TIMEOUT value" in str(
        metadata_module.CargoMetadataError.invalid_ipc_timeout()
    )
    assert "must be positive" in str(
        metadata_module.CargoMetadataError.non_positive_ipc_timeout()
    )
