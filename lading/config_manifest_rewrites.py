"""Schema records and validation for configured manifest version rewrites."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import pathlib
import re
import string
import typing as typ

from lading import toml_coerce
from lading.config_validation import validate_mapping_keys, validate_segments
from lading.exceptions import ConfigurationError

BUMP_MANIFEST_REWRITE_TOML_KEYS: typ.Final[frozenset[str]] = frozenset({
    "paths",
    "dependencies",
    "string_values",
})
BUMP_STRING_VALUE_REWRITE_TOML_KEYS: typ.Final[frozenset[str]] = frozenset({
    "table",
    "field",
    "template",
})
_EMPTY_FORMAT_SPEC: typ.Final[re.Pattern[str]] = re.compile(
    r"(?<!\{)(?:\{\{)*\{(?:crate|version):\}"
)


def _parse_string_value_rewrite(
    mapping: cabc.Mapping[str, typ.Any], field_name: str
) -> tuple[tuple[str, ...], str | None, str]:
    """Parse fields owned by one configured string-value selector."""
    validate_mapping_keys(mapping, set(BUMP_STRING_VALUE_REWRITE_TOML_KEYS), field_name)
    table = _required_rewrite_segments(
        mapping.get("table"), f"{field_name}.table", "segment"
    )
    selected_field = _optional_rewrite_field(mapping, field_name)
    template = _rewrite_template_value(mapping, field_name)
    return table, selected_field, template


def _parse_manifest_rewrite(
    mapping: cabc.Mapping[str, typ.Any], field_name: str
) -> tuple[tuple[str, ...], bool, tuple[StringValueRewriteConfig, ...]]:
    """Parse fields owned by one configured manifest rewrite group."""
    validate_mapping_keys(mapping, set(BUMP_MANIFEST_REWRITE_TOML_KEYS), field_name)
    paths = _manifest_rewrite_paths(mapping, field_name)
    dependencies = toml_coerce.boolean(
        mapping.get("dependencies"),
        f"{field_name}.dependencies",
        error=ConfigurationError,
        default=True,
    )
    string_values = _manifest_rewrite_string_values(mapping, field_name)
    if not dependencies and not string_values:
        message = f"{field_name} must enable dependencies or define string_values."
        raise ConfigurationError(message)
    return paths, dependencies, string_values


class _MappingConfigRecord:
    """Provide one mapping constructor for immutable rewrite config records."""

    __slots__ = ()

    _mapping_parser: typ.ClassVar[
        cabc.Callable[[cabc.Mapping[str, typ.Any], str], tuple[object, ...]]
    ]
    _default_field_name: typ.ClassVar[str]

    @classmethod
    def from_mapping(
        cls,
        mapping: cabc.Mapping[str, typ.Any],
        field_name: str | None = None,
    ) -> typ.Self:
        """Create a validated config record from a TOML table mapping.

        Parameters
        ----------
        mapping : cabc.Mapping[str, typ.Any]
            Parsed settings for this record.
        field_name : str | None, default None
            Configuration path used in validation errors. The record supplies
            its standard path when this value is omitted.

        Returns
        -------
        Self
            Validated immutable configuration settings.

        Raises
        ------
        ConfigurationError
            If a key or field value is invalid.
        """  # ruff: ignore[docstring-extraneous-exception]  # delegated schema errors
        selected_field_name = (
            cls._default_field_name if field_name is None else field_name
        )
        return _construct_from_mapping(
            cls, cls._mapping_parser, mapping, selected_field_name
        )


@dc.dataclass(frozen=True, slots=True)
class StringValueRewriteConfig(_MappingConfigRecord):
    """Describe string values to rewrite in a selected TOML table.

    Attributes
    ----------
    table : tuple[str, ...]
        Literal TOML table segments selected for replacement.
    field : str | None, default None
        Sub-table field to visit, or ``None`` to visit direct string values.
    template : str, default "{crate}-{version}"
        Old and new text pattern, using only ``{crate}`` and ``{version}``.
    """

    table: tuple[str, ...]
    field: str | None = None
    template: str = "{crate}-{version}"
    _mapping_parser: typ.ClassVar[
        cabc.Callable[[cabc.Mapping[str, typ.Any], str], tuple[object, ...]]
    ] = staticmethod(_parse_string_value_rewrite)
    _default_field_name: typ.ClassVar[str] = (
        "bump.manifest_rewrites[0].string_values[0]"
    )


@dc.dataclass(frozen=True, slots=True)
class ManifestRewriteConfig(_MappingConfigRecord):
    """Allow version rewrites in selected non-member Cargo manifests.

    Attributes
    ----------
    paths : tuple[str, ...]
        Relative manifest paths or globs within the workspace.
    dependencies : bool, default True
        Whether matching dependency requirements should be updated.
    string_values : tuple[StringValueRewriteConfig, ...], default ()
        Explicit TOML string selectors and rewrite templates.
    """

    paths: tuple[str, ...]
    dependencies: bool = True
    string_values: tuple[StringValueRewriteConfig, ...] = ()
    _mapping_parser: typ.ClassVar[
        cabc.Callable[[cabc.Mapping[str, typ.Any], str], tuple[object, ...]]
    ] = staticmethod(_parse_manifest_rewrite)
    _default_field_name: typ.ClassVar[str] = "bump.manifest_rewrites[0]"


def _parse_config_mapping[ParsedValues: tuple[object, ...]](
    parser: cabc.Callable[[cabc.Mapping[str, typ.Any], str], ParsedValues],
    mapping: cabc.Mapping[str, typ.Any],
    field_name: str,
) -> ParsedValues:
    """Run a schema parser while preserving its configuration error boundary."""
    try:
        return parser(mapping, field_name)
    except ConfigurationError as exc:
        raise ConfigurationError(str(exc)) from exc


def _construct_from_mapping[Record, ParsedValues: tuple[object, ...]](
    config_type: type[Record],
    parser: cabc.Callable[[cabc.Mapping[str, typ.Any], str], ParsedValues],
    mapping: cabc.Mapping[str, typ.Any],
    field_name: str,
) -> Record:
    """Construct one config record from its validated ordered values."""
    values = _parse_config_mapping(parser, mapping, field_name)
    constructor = typ.cast("cabc.Callable[..., Record]", config_type)
    return constructor(*values)


def _manifest_rewrite_paths(
    mapping: cabc.Mapping[str, typ.Any], field_name: str
) -> tuple[str, ...]:
    """Validate required workspace-relative manifest paths or globs."""
    field = f"{field_name}.paths"
    paths = _required_rewrite_segments(mapping.get("paths"), field, "path")
    for index, path in enumerate(paths):
        candidates = (pathlib.Path(path), pathlib.PureWindowsPath(path))
        if any(candidate.anchor or ".." in candidate.parts for candidate in candidates):
            message = f"{field}[{index}] must be a safe workspace-relative path."
            raise ConfigurationError(message)
    return paths


def _required_rewrite_segments(
    value: object, field_name: str, item_name: str
) -> tuple[str, ...]:
    """Coerce a required non-empty string sequence and validate its segments."""
    raw_segments = toml_coerce.expect_sequence(
        value, field_name, error=ConfigurationError
    )
    segments = toml_coerce.validate_string_sequence(
        raw_segments, field_name, error=ConfigurationError
    )
    if not segments:
        message = f"{field_name} must contain at least one {item_name}."
        raise ConfigurationError(message)
    validate_segments(segments, field_name)
    return segments


def _optional_rewrite_field(
    mapping: cabc.Mapping[str, typ.Any], field_name: str
) -> str | None:
    """Parse an optional direct string selector without blank segments."""
    if "field" not in mapping:
        return None
    field = toml_coerce.expect_string(
        mapping["field"], f"{field_name}.field", error=ConfigurationError
    )
    validate_segments((field,), f"{field_name}.field", is_indexed=False)
    return field


def _rewrite_template_value(
    mapping: cabc.Mapping[str, typ.Any], field_name: str
) -> str:
    """Parse and validate one configured version-rewrite template."""
    template = toml_coerce.expect_string(
        mapping.get("template", "{crate}-{version}"),
        f"{field_name}.template",
        error=ConfigurationError,
    )
    _validate_rewrite_template(template, f"{field_name}.template")
    return template


def _manifest_rewrite_string_values(
    mapping: cabc.Mapping[str, typ.Any], field_name: str
) -> tuple[StringValueRewriteConfig, ...]:
    """Parse nested string-value selectors for one manifest rewrite group."""
    if "string_values" not in mapping:
        return ()
    field = f"{field_name}.string_values"
    raw_values = toml_coerce.expect_sequence(
        mapping["string_values"], field, error=ConfigurationError
    )
    return tuple(
        StringValueRewriteConfig.from_mapping(
            toml_coerce.expect_mapping(
                raw_value, f"{field}[{index}]", error=ConfigurationError
            ),
            f"{field}[{index}]",
        )
        for index, raw_value in enumerate(raw_values)
    )


def _validate_rewrite_template(template: str, field_name: str) -> None:
    """Allow only crate/version placeholders and require a version field."""
    fields = _rewrite_template_fields(template, field_name)
    if "version" not in fields:
        message = f"{field_name} must contain {{version}}."
        raise ConfigurationError(message)


def _rewrite_template_fields(template: str, field_name: str) -> tuple[str, ...]:
    """Parse template fields and report malformed braces as config errors."""
    try:
        parsed = tuple(string.Formatter().parse(template))
    except ValueError as exc:
        message = f"{field_name} is not a valid format template."
        raise ConfigurationError(message) from exc
    if _EMPTY_FORMAT_SPEC.search(template):
        message = f"{field_name} does not support format specs or conversions."
        raise ConfigurationError(message)
    return tuple(
        _validate_rewrite_template_field(field, format_spec, conversion, field_name)
        for _, field, format_spec, conversion in parsed
        if field is not None
    )


def _validate_rewrite_template_field(
    field: str, format_spec: str | None, conversion: str | None, field_name: str
) -> str:
    """Reject unsupported placeholders, format specs, and conversions."""
    if field not in {"crate", "version"}:
        message = f"{field_name} only supports {{crate}} and {{version}} fields."
        raise ConfigurationError(message)
    if format_spec or conversion is not None:
        message = f"{field_name} does not support format specs or conversions."
        raise ConfigurationError(message)
    return field
