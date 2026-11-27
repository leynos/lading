"""Shared inputs for the documentation version-policy tests."""

import collections.abc as cabc

from lading.commands import bump_docs_fences

__all__ = ["TARGETS", "TARGET_VERSION", "rewrite"]

TARGET_VERSION = "2.0.0"
TARGETS = frozenset({"alpha"})


def rewrite(snippet: str, targets: cabc.Collection[str] = TARGETS) -> tuple[str, bool]:
    """Return ``snippet`` after the documentation pass for ``targets``."""
    return bump_docs_fences.update_toml_snippet_versions(
        snippet, targets, TARGET_VERSION
    )
