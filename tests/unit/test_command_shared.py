"""Tests for shared command helpers."""

from __future__ import annotations

from types import SimpleNamespace

from lading.commands import _shared


def test_describe_crates_handles_pluralisation() -> None:
    """Pluralisation should reflect the number of crates."""
    workspace = SimpleNamespace(crates=(1,))
    assert _shared.describe_crates(workspace) == "1 crate"

    workspace_many = SimpleNamespace(crates=(1, 2, 3))
    assert _shared.describe_crates(workspace_many) == "3 crates"
