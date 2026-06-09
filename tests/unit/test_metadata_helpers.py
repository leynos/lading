"""Tests for workspace metadata helper functions."""

from __future__ import annotations

from lading.runtime import coerce_text


def test_coerce_text_handles_bytes() -> None:
    """Byte streams should be decoded to strings."""
    assert coerce_text(b"bytes") == "bytes"
