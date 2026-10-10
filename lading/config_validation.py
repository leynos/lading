"""Shared configuration schema validators."""

from __future__ import annotations

import collections.abc as cabc

from lading.exceptions import ConfigurationError


def validate_mapping_keys(
    mapping: cabc.Mapping[str, object] | None,
    allowed_keys: set[str],
    context: str,
) -> None:
    """Reject options outside the allowlist for one configuration record."""
    if mapping is None:
        return
    unknown = set(mapping) - allowed_keys
    if unknown:
        joined = ", ".join(sorted(unknown))
        suffix = "(s)" if context.endswith(" section") else " option(s)"
        message = f"Unknown {context}{suffix}: {joined}."
        raise ConfigurationError(message)


def validate_segments(
    segments: tuple[str, ...], field_name: str, *, is_indexed: bool = True
) -> None:
    """Reject blank or whitespace-padded path and selector segments."""
    for index, segment in enumerate(segments):
        if not segment.strip() or segment != segment.strip():
            location = f"{field_name}[{index}]" if is_indexed else field_name
            message = (
                f"{location} must be non-blank and have no leading or "
                "trailing whitespace."
            )
            raise ConfigurationError(message)
