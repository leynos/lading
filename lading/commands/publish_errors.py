"""Error types raised by publish command workflows."""

from __future__ import annotations


class PublishPreflightError(RuntimeError):
    """Raised when required pre-publication checks fail."""


class PublishError(PublishPreflightError):
    """Raised when publishing crates fails after pre-flight checks."""


__all__ = [
    "PublishError",
    "PublishPreflightError",
]
