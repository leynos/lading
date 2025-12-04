"""Shared helpers for placeholder command implementations."""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:  # pragma: no cover - typing helper only
    from lading.workspace import WorkspaceGraph


def describe_crates(workspace: WorkspaceGraph) -> str:
    """Return a human-friendly crate count summary."""
    count = len(workspace.crates)
    label = "crate" if count == 1 else "crates"
    return f"{count} {label}"
