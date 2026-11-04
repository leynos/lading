"""Schema records and validation for configured manifest version rewrites."""

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
DEFAULT_REWRITE_TEMPLATE: typ.Final[str] = "{crate}-{version}"
_EMPTY_FORMAT_SPEC: typ.Final[re.Pattern[str]] = re.compile(
    r"(?<!\{)(?:\{\{)*\{(?:crate|version):\}"
)


@dc.dataclass(frozen=True, slots=True)
class StringValueRewriteConfig:
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
    template: str = DEFAULT_REWRITE_TEMPLATE

    @classmethod
    def from_mapping(
        cls,
        mapping: cabc.Mapping[str, typ.Any],
        field_name: str = "bump.manifest_rewrites[0].string_values[0]",
    ) -> typ.Self:
        """Create a validated TOML string selector.

        Parameters
        ----------
        mapping : cabc.Mapping[str, typ.Any]
            Parsed settings for this selector.
        field_name : str, default "bump.manifest_rewrites[0].string_values[0]"
            Configuration path used to identify validation errors.

        Returns
        -------
        StringValueRewriteConfig
            Validated table path, optional field, and replacement template.

        Raises
        ------
        ConfigurationError
            If a key, table segment, field, or template is invalid.

        Examples
        --------
        >>> StringValueRewriteConfig.from_mapping({"table": ["metadata"]}).table
        ('metadata',)
        """  # ruff: ignore[docstring-extraneous-exception]  # schema validators raise
        validate_mapping_keys(
            mapping, set(BUMP_STRING_VALUE_REWRITE_TOML_KEYS), field_name
        )
        return cls(
            table=_required_rewrite_segments(
                mapping.get("table"), f"{field_name}.table", "segment"
            ),
            field=_optional_rewrite_field(mapping, field_name),
            template=_rewrite_template_value(mapping, field_name),
        )


@dc.dataclass(frozen=True, slots=True)
class ManifestRewriteConfig:
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

    @classmethod
    def from_mapping(
        cls,
        mapping: cabc.Mapping[str, typ.Any],
        field_name: str = "bump.manifest_rewrites[0]",
    ) -> typ.Self:
        """Create an allowlisted manifest rewrite group.

        Parameters
        ----------
        mapping : cabc.Mapping[str, typ.Any]
            Parsed settings for this group.
        field_name : str, default "bump.manifest_rewrites[0]"
            Configuration path used to identify validation errors.

        Returns
        -------
        ManifestRewriteConfig
            Validated manifest paths, dependency setting, and string selectors.

        Raises
        ------
        ConfigurationError
            If a key or field value is invalid, or if both rewrite kinds are
            disabled.

        Examples
        --------
        >>> ManifestRewriteConfig.from_mapping({"paths": ["fixture"]}).dependencies
        True
        """
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
        return cls(
            paths=paths,
            dependencies=dependencies,
            string_values=string_values,
        )


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
        mapping.get("template", DEFAULT_REWRITE_TEMPLATE),
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
