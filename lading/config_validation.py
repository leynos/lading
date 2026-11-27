"""Share TOML coercion and validation across configuration records.

The private coercers bind shared TOML conversions to
:class:`lading.exceptions.ConfigurationError` for :mod:`lading.config` and
:mod:`lading.config_publish_preflight`. :func:`validate_mapping_keys` serves
those parsers and :mod:`lading.config_manifest_rewrites`; :func:`validate_segments`
validates literal selectors in the rewrite schema. Validation errors retain
the relevant field path.
"""

import collections.abc as cabc
import functools

from lading import toml_coerce
from lading.exceptions import ConfigurationError

_boolean = functools.partial(toml_coerce.boolean, error=ConfigurationError)
_optional_mapping = functools.partial(
    toml_coerce.optional_mapping, error=ConfigurationError
)
_string_tuple = functools.partial(toml_coerce.string_tuple, error=ConfigurationError)


def validate_mapping_keys(
    mapping: cabc.Mapping[str, object] | None,
    allowed_keys: set[str],
    context: str,
) -> None:
    """Validate that a configuration mapping contains only allowed keys.

    Parameters
    ----------
    mapping : collections.abc.Mapping[str, object] | None
        Configuration values to validate. ``None`` represents an omitted
        optional table and is accepted.
    allowed_keys : set[str]
        Keys accepted by the configuration record.
    context : str
        Configuration path used to identify unknown keys in an error message.

    Raises
    ------
    ConfigurationError
        If ``mapping`` contains keys outside ``allowed_keys``. Unknown keys
        are sorted in the message for deterministic diagnostics.

    Examples
    --------
    >>> validate_mapping_keys({"version": "1.0"}, {"version"}, "release")
    >>> validate_mapping_keys(None, {"version"}, "release")
    """
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
    """Validate that path or selector segments have no surrounding spaces.

    Parameters
    ----------
    segments : tuple[str, ...]
        Path or selector segments to validate.
    field_name : str
        Configuration field name used in an error message.
    is_indexed : bool, default=True
        Whether to append the invalid segment's zero-based index to
        ``field_name`` in an error message.

    Raises
    ------
    ConfigurationError
        If a segment is blank or has leading or trailing whitespace.

    Examples
    --------
    >>> validate_segments(("target", "cfg(unix)"), "selectors")
    >>> validate_segments(("target",), "selector", is_indexed=False)
    """
    for index, segment in enumerate(segments):
        if not segment.strip() or segment != segment.strip():
            location = f"{field_name}[{index}]" if is_indexed else field_name
            message = (
                f"{location} must be non-blank and have no leading or "
                "trailing whitespace."
            )
            raise ConfigurationError(message)
