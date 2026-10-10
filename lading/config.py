"""Configuration loading for the :mod:`lading` toolkit."""

from __future__ import annotations

import collections.abc as cabc
import contextlib
import contextvars
import dataclasses as dc
import functools
import pathlib
import string
import typing as typ

from cyclopts.config import Toml

from lading import toml_coerce
from lading.exceptions import LadingError
from lading.utils import normalize_workspace_root

if typ.TYPE_CHECKING:  # pragma: no cover - type checking only
    from pathlib import Path

CONFIG_FILENAME = "lading.toml"

StripPatchesSetting = typ.Literal["all", "per-crate"] | bool

CONFIG_ROOT_TOML_KEYS: typ.Final[frozenset[str]] = frozenset({
    "bump",
    "publish",
    "preflight",
})
BUMP_TOML_KEYS: typ.Final[frozenset[str]] = frozenset({
    "exclude",
    "documentation",
    "lockfile_manifests",
    "manifest_rewrites",
    "rebuild_lockfiles",
})
BUMP_DOCUMENTATION_TOML_KEYS: typ.Final[frozenset[str]] = frozenset({"globs"})
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
PUBLISH_TOML_KEYS: typ.Final[frozenset[str]] = frozenset({
    "exclude",
    "order",
    "strip_patches",
})
PREFLIGHT_TOML_KEYS: typ.Final[frozenset[str]] = frozenset({
    "skip",
    "test_exclude",
    "unit_tests_only",
    "aux_build",
    "compiletest_extern",
    "env",
    "stderr_tail_lines",
})


class ConfigurationError(LadingError):
    """Raised when the :mod:`lading` configuration is invalid."""


class ConfigurationNotLoadedError(ConfigurationError):
    """Raised when code accesses the configuration before it is loaded."""


@dc.dataclass(frozen=True, slots=True)
class DocumentationConfig:
    """Configuration for documentation updates triggered by ``bump``."""

    globs: tuple[str, ...] = ()

    @classmethod
    def from_mapping(
        cls, mapping: cabc.Mapping[str, typ.Any] | None
    ) -> DocumentationConfig:
        """Create a :class:`DocumentationConfig` from a TOML table mapping.

        Parameters
        ----------
        mapping : cabc.Mapping[str, typ.Any] | None
            The parsed ``bump.documentation`` table, or ``None`` for defaults.

        Returns
        -------
        DocumentationConfig
            Documentation settings parsed from ``mapping``, or defaults when
            ``mapping`` is ``None``.

        Raises
        ------
        ConfigurationError
            If ``mapping`` contains an unknown key or an invalid setting
            value, propagated from the shared mapping validators/coercers.

        Examples
        --------
        >>> DocumentationConfig.from_mapping({"globs": ["docs/*.md"]})
        DocumentationConfig(globs=('docs/*.md',))
        >>> DocumentationConfig.from_mapping(None)
        DocumentationConfig(globs=())
        """  # ruff: ignore[docstring-extraneous-exception]  # propagated from the shared mapping validators
        if mapping is None:
            return cls()
        _validate_mapping_keys(
            mapping, set(BUMP_DOCUMENTATION_TOML_KEYS), "bump.documentation"
        )
        return cls(
            globs=_string_tuple(mapping.get("globs"), "bump.documentation.globs"),
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
    template: str = "{crate}-{version}"

    @classmethod
    def from_mapping(
        cls,
        mapping: cabc.Mapping[str, typ.Any],
        field_name: str = "bump.manifest_rewrites[0].string_values[0]",
    ) -> StringValueRewriteConfig:
        """Create a validated string rewrite from a TOML table mapping.

        Parameters
        ----------
        mapping : cabc.Mapping[str, typ.Any]
            Parsed selector settings.
        field_name : str, default "bump.manifest_rewrites[0].string_values[0]"
            Configuration path used in validation errors.

        Returns
        -------
        StringValueRewriteConfig
            Validated immutable selector settings.

        Raises
        ------
        ConfigurationError
            If a key, selector, or template is invalid.
        """
        _validate_mapping_keys(
            mapping, set(BUMP_STRING_VALUE_REWRITE_TOML_KEYS), field_name
        )
        table_name = f"{field_name}.table"
        raw_table = toml_coerce.expect_sequence(
            mapping.get("table"), table_name, error=ConfigurationError
        )
        table = toml_coerce.validate_string_sequence(
            raw_table, table_name, error=ConfigurationError
        )
        _validate_segments(table, table_name)
        if not table:
            message = f"{table_name} must contain at least one segment."
            raise ConfigurationError(message)

        selected_field = (
            None
            if "field" not in mapping
            else toml_coerce.expect_string(
                mapping["field"], f"{field_name}.field", error=ConfigurationError
            )
        )
        if selected_field is not None:
            _validate_segments(
                (selected_field,), f"{field_name}.field", is_indexed=False
            )

        raw_template = mapping.get("template", "{crate}-{version}")
        template = toml_coerce.expect_string(
            raw_template, f"{field_name}.template", error=ConfigurationError
        )
        _validate_rewrite_template(template, f"{field_name}.template")
        return cls(table=table, field=selected_field, template=template)


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
    ) -> ManifestRewriteConfig:
        """Create a validated manifest rewrite group from a TOML mapping.

        Parameters
        ----------
        mapping : cabc.Mapping[str, typ.Any]
            Parsed manifest rewrite settings.
        field_name : str, default "bump.manifest_rewrites[0]"
            Configuration path used in validation errors.

        Returns
        -------
        ManifestRewriteConfig
            Validated immutable manifest rewrite settings.

        Raises
        ------
        ConfigurationError
            If the allowlist, rewrite rules, or dependency flag is invalid.
        """
        _validate_mapping_keys(
            mapping, set(BUMP_MANIFEST_REWRITE_TOML_KEYS), field_name
        )
        paths_name = f"{field_name}.paths"
        raw_paths = toml_coerce.expect_sequence(
            mapping.get("paths"), paths_name, error=ConfigurationError
        )
        paths = toml_coerce.validate_string_sequence(
            raw_paths, paths_name, error=ConfigurationError
        )
        if not paths:
            message = f"{paths_name} must contain at least one path."
            raise ConfigurationError(message)
        _validate_segments(paths, paths_name)
        for index, path in enumerate(paths):
            if any(
                candidate.anchor or ".." in candidate.parts
                for candidate in (pathlib.Path(path), pathlib.PureWindowsPath(path))
            ):
                message = (
                    f"{paths_name}[{index}] must be a safe workspace-relative path."
                )
                raise ConfigurationError(message)

        dependencies = _boolean(
            mapping.get("dependencies"), f"{field_name}.dependencies", default=True
        )
        raw_values: cabc.Sequence[object] = ()
        if "string_values" in mapping:
            raw_values = toml_coerce.expect_sequence(
                mapping["string_values"],
                f"{field_name}.string_values",
                error=ConfigurationError,
            )
        string_values = tuple(
            StringValueRewriteConfig.from_mapping(
                toml_coerce.expect_mapping(
                    raw_value,
                    f"{field_name}.string_values[{index}]",
                    error=ConfigurationError,
                ),
                f"{field_name}.string_values[{index}]",
            )
            for index, raw_value in enumerate(raw_values)
        )
        if not dependencies and not string_values:
            message = f"{field_name} must enable dependencies or define string_values."
            raise ConfigurationError(message)
        return cls(paths=paths, dependencies=dependencies, string_values=string_values)


@dc.dataclass(frozen=True, slots=True)
class BumpConfig:
    """Settings for the ``bump`` command."""

    exclude: tuple[str, ...] = ()
    lockfile_manifests: tuple[str, ...] = ()
    rebuild_lockfiles: bool = True
    documentation: DocumentationConfig = dc.field(default_factory=DocumentationConfig)
    manifest_rewrites: tuple[ManifestRewriteConfig, ...] = ()

    @classmethod
    def from_mapping(cls, mapping: cabc.Mapping[str, typ.Any] | None) -> BumpConfig:
        """Create a :class:`BumpConfig` from a TOML table mapping.

        Parameters
        ----------
        mapping : cabc.Mapping[str, typ.Any] | None
            The parsed ``bump`` table, or ``None`` for defaults.

        Returns
        -------
        BumpConfig
            Bump settings parsed from ``mapping``, or defaults when ``mapping``
            is ``None``.

        Raises
        ------
        ConfigurationError
            If ``mapping`` contains an unknown key or an invalid setting
            value, propagated from the shared mapping validators/coercers.

        Examples
        --------
        >>> BumpConfig.from_mapping({"exclude": ["crate-a"]}).exclude
        ('crate-a',)
        >>> BumpConfig.from_mapping(None).rebuild_lockfiles
        True
        """  # ruff: ignore[docstring-extraneous-exception]  # propagated from the shared mapping validators
        if mapping is None:
            return cls()
        _validate_mapping_keys(mapping, set(BUMP_TOML_KEYS), "bump")
        raw_rewrites: cabc.Sequence[object] = ()
        if "manifest_rewrites" in mapping:
            raw_rewrites = toml_coerce.expect_sequence(
                mapping["manifest_rewrites"],
                "bump.manifest_rewrites",
                error=ConfigurationError,
            )
        return cls(
            exclude=_string_tuple(mapping.get("exclude"), "bump.exclude"),
            lockfile_manifests=_string_tuple(
                mapping.get("lockfile_manifests"), "bump.lockfile_manifests"
            ),
            manifest_rewrites=tuple(
                ManifestRewriteConfig.from_mapping(
                    toml_coerce.expect_mapping(
                        raw_rewrite,
                        f"bump.manifest_rewrites[{index}]",
                        error=ConfigurationError,
                    ),
                    f"bump.manifest_rewrites[{index}]",
                )
                for index, raw_rewrite in enumerate(raw_rewrites)
            ),
            rebuild_lockfiles=_boolean(
                mapping.get("rebuild_lockfiles"),
                "bump.rebuild_lockfiles",
                default=True,
            ),
            documentation=DocumentationConfig.from_mapping(
                _optional_mapping(mapping.get("documentation"), "bump.documentation")
            ),
        )


@dc.dataclass(frozen=True, slots=True)
class PublishConfig:
    """Settings for the ``publish`` command."""

    exclude: tuple[str, ...] = ()
    order: tuple[str, ...] = ()
    strip_patches: StripPatchesSetting = "per-crate"

    @classmethod
    def from_mapping(cls, mapping: cabc.Mapping[str, typ.Any] | None) -> PublishConfig:
        """Create a :class:`PublishConfig` from a TOML table mapping.

        Parameters
        ----------
        mapping : cabc.Mapping[str, typ.Any] | None
            The parsed ``publish`` table, or ``None`` for defaults.

        Returns
        -------
        PublishConfig
            Publish settings parsed from ``mapping``, or defaults when
            ``mapping`` is ``None``.

        Raises
        ------
        ConfigurationError
            If ``mapping`` contains an unknown key or an invalid setting
            value, propagated from the shared mapping validators/coercers.

        Examples
        --------
        >>> PublishConfig.from_mapping({"order": ["a", "b"]}).order
        ('a', 'b')
        >>> PublishConfig.from_mapping(None).strip_patches
        'per-crate'
        """  # ruff: ignore[docstring-extraneous-exception]  # propagated from the shared mapping validators
        if mapping is None:
            return cls()
        _validate_mapping_keys(mapping, set(PUBLISH_TOML_KEYS), "publish")
        return cls(
            exclude=_string_tuple(mapping.get("exclude"), "publish.exclude"),
            order=_string_tuple(mapping.get("order"), "publish.order"),
            strip_patches=_strip_patches(mapping.get("strip_patches")),
        )


@dc.dataclass(frozen=True, slots=True)
class CompiletestExtern:
    """Describe a compiletest extern crate override."""

    crate: str
    path: str


@dc.dataclass(frozen=True, slots=True)
class PreflightConfig:
    """Settings for publish pre-flight checks.

    ``skip`` suppresses only the compilation-heavy part of the pre-flight: the
    auxiliary build commands, ``cargo check``, and ``cargo test``. The
    working-tree cleanliness guard and the lockfile freshness guard cost
    nothing and always run, so a skipped pre-flight never weakens the
    publication guarantees those two checks provide.
    """

    skip: bool = False
    test_exclude: tuple[str, ...] = ()
    unit_tests_only: bool = False
    aux_build: tuple[tuple[str, ...], ...] = ()
    compiletest_externs: tuple[CompiletestExtern, ...] = ()
    env_overrides: tuple[tuple[str, str], ...] = ()
    stderr_tail_lines: int = 40

    @classmethod
    def from_mapping(
        cls, mapping: cabc.Mapping[str, typ.Any] | None
    ) -> PreflightConfig:
        """Create a :class:`PreflightConfig` from a TOML table mapping.

        Parameters
        ----------
        mapping : cabc.Mapping[str, typ.Any] | None
            The parsed ``preflight`` table, or ``None`` for defaults.

        Returns
        -------
        PreflightConfig
            Pre-flight settings parsed from ``mapping``, or defaults when
            ``mapping`` is ``None``.

        Raises
        ------
        ConfigurationError
            If ``mapping`` contains an unknown key or an invalid setting
            value, propagated from the shared mapping validators/coercers.

        Examples
        --------
        >>> PreflightConfig.from_mapping({"unit_tests_only": True}).unit_tests_only
        True
        >>> PreflightConfig.from_mapping(None).stderr_tail_lines
        40
        >>> PreflightConfig.from_mapping({"skip": True}).skip
        True
        """  # ruff: ignore[docstring-extraneous-exception]  # propagated from the shared mapping validators
        if mapping is None:
            return cls()
        _validate_mapping_keys(mapping, set(PREFLIGHT_TOML_KEYS), "preflight")
        raw_excludes = _string_tuple(
            mapping.get("test_exclude"), "preflight.test_exclude"
        )
        filtered_excludes = tuple(
            dict.fromkeys(
                trimmed for entry in raw_excludes if (trimmed := entry.strip())
            )
        )
        aux_build_commands = _string_matrix(
            mapping.get("aux_build"), "preflight.aux_build"
        )
        extern_entries = _string_mapping(
            mapping.get("compiletest_extern"), "preflight.compiletest_extern"
        )
        env_overrides = _string_mapping(mapping.get("env"), "preflight.env")
        return cls(
            skip=_boolean(mapping.get("skip"), "preflight.skip"),
            test_exclude=filtered_excludes,
            unit_tests_only=_boolean(
                mapping.get("unit_tests_only"), "preflight.unit_tests_only"
            ),
            aux_build=aux_build_commands,
            compiletest_externs=tuple(
                CompiletestExtern(crate=name, path=path)
                for name, path in extern_entries
            ),
            env_overrides=env_overrides,
            stderr_tail_lines=_non_negative_int(
                mapping.get("stderr_tail_lines"), "preflight.stderr_tail_lines", 40
            ),
        )


@dc.dataclass(frozen=True, slots=True)
class LadingConfig:
    """Strongly-typed representation of ``lading.toml``."""

    bump: BumpConfig = dc.field(default_factory=BumpConfig)
    publish: PublishConfig = dc.field(default_factory=PublishConfig)
    preflight: PreflightConfig = dc.field(default_factory=PreflightConfig)

    @classmethod
    def from_mapping(cls, mapping: cabc.Mapping[str, typ.Any]) -> LadingConfig:
        """Create a :class:`LadingConfig` from a parsed configuration mapping.

        Parameters
        ----------
        mapping : cabc.Mapping[str, typ.Any]
            The parsed root configuration table.

        Returns
        -------
        LadingConfig
            Fully populated configuration with defaults for absent sections.

        Raises
        ------
        ConfigurationError
            If ``mapping`` contains an unknown key or an invalid setting
            value, propagated from the shared mapping validators/coercers.

        Examples
        --------
        >>> config = LadingConfig.from_mapping({"bump": {"exclude": ["crate-a"]}})
        >>> config.bump.exclude
        ('crate-a',)
        """  # ruff: ignore[docstring-extraneous-exception]  # propagated from the shared mapping validators
        _validate_mapping_keys(
            mapping, set(CONFIG_ROOT_TOML_KEYS), "configuration section"
        )
        return cls(
            bump=BumpConfig.from_mapping(
                _optional_mapping(mapping.get("bump"), "bump")
            ),
            publish=PublishConfig.from_mapping(
                _optional_mapping(mapping.get("publish"), "publish")
            ),
            preflight=PreflightConfig.from_mapping(
                _optional_mapping(mapping.get("preflight"), "preflight")
            ),
        )


_active_config: contextvars.ContextVar[LadingConfig] = contextvars.ContextVar(
    "lading_active_config"
)


def _validate_mapping_keys(
    mapping: cabc.Mapping[str, typ.Any] | None,
    allowed_keys: set[str],
    context: str,
) -> None:
    """Validate that ``mapping`` contains only ``allowed_keys``."""
    if mapping is None:
        return
    unknown = set(mapping) - allowed_keys
    if unknown:
        joined = ", ".join(sorted(unknown))
        if context.endswith(" section"):
            message = f"Unknown {context}(s): {joined}."
        else:
            message = f"Unknown {context} option(s): {joined}."
        raise ConfigurationError(message)


def _validate_segments(
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


def _validate_rewrite_template(template: str, field_name: str) -> None:
    """Allow only crate/version placeholders and require a version field."""
    formatter = string.Formatter()
    has_version = False
    try:
        parsed = formatter.parse(template)
        for _, field, format_spec, conversion in parsed:
            if field is None:
                continue
            if field not in {"crate", "version"}:
                message = (
                    f"{field_name} only supports {{crate}} and {{version}} fields."
                )
                raise ConfigurationError(message)
            if format_spec or conversion is not None:
                message = f"{field_name} does not support format specs or conversions."
                raise ConfigurationError(message)
            has_version |= field == "version"
    except ValueError as exc:
        message = f"{field_name} is not a valid format template."
        raise ConfigurationError(message) from exc
    if not has_version:
        message = f"{field_name} must contain {{version}}."
        raise ConfigurationError(message)


def build_loader(workspace_root: Path) -> Toml:
    """Return a Cyclopts loader for ``lading.toml`` in ``workspace_root``.

    Parameters
    ----------
    workspace_root : Path
        The workspace root whose ``lading.toml`` the loader targets.

    Returns
    -------
    Toml
        Loader targeting ``lading.toml`` within the resolved workspace root.

    Examples
    --------
    >>> from pathlib import Path
    >>> build_loader(Path("workspace")).path.name
    'lading.toml'
    """
    resolved = normalize_workspace_root(workspace_root)
    return Toml(
        path=resolved / CONFIG_FILENAME,
        must_exist=False,
        search_parents=False,
        allow_unknown=True,
        use_commands_as_keys=True,
    )


def load_from_loader(loader: Toml) -> LadingConfig:
    """Load and validate configuration using ``loader``.

    Parameters
    ----------
    loader : Toml
        The Cyclopts loader providing the parsed TOML table.

    Returns
    -------
    LadingConfig
        Validated configuration parsed from the loader's TOML table.

    Raises
    ------
    ConfigurationError
        If reading ``loader.config`` raises :class:`ValueError`, the parsed
        configuration root is not a TOML table, or validation fails while
        propagating from :meth:`LadingConfig.from_mapping` (unknown keys or
        invalid setting values).

    Examples
    --------
    >>> from pathlib import Path
    >>> load_from_loader(build_loader(Path("workspace")))  # doctest: +SKIP
    LadingConfig(...)
    """
    try:
        raw = loader.config
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    if not isinstance(raw, cabc.Mapping):
        message = "Configuration root must be a TOML table."
        raise ConfigurationError(message)
    return LadingConfig.from_mapping(raw)


def load_configuration(workspace_root: Path) -> LadingConfig:
    """Load configuration for ``workspace_root`` using Cyclopts.

    Parameters
    ----------
    workspace_root : Path
        The workspace root whose ``lading.toml`` is loaded.

    Returns
    -------
    LadingConfig
        Validated configuration for the given workspace root.

    Raises
    ------
    ConfigurationError
        If loading or validation fails, propagated from
        :func:`load_from_loader` (parse failures or validation failures).

    Examples
    --------
    >>> from pathlib import Path
    >>> load_configuration(Path("workspace"))  # doctest: +SKIP
    LadingConfig(...)
    """  # ruff: ignore[docstring-extraneous-exception]  # propagated from load_from_loader, not raised here
    return load_from_loader(build_loader(workspace_root))


@contextlib.contextmanager
def use_configuration(configuration: LadingConfig) -> cabc.Iterator[None]:
    """Set ``configuration`` as the active configuration for the current context.

    Parameters
    ----------
    configuration : LadingConfig
        Configuration to make active for the duration of the ``with`` block.

    Yields
    ------
    None
        Control, with ``configuration`` active until the ``with`` block exits.

    Examples
    --------
    >>> config = LadingConfig()
    >>> with use_configuration(config):
    ...     current_configuration() is config
    True
    """
    token = _active_config.set(configuration)
    try:
        yield
    finally:
        _active_config.reset(token)


def current_configuration() -> LadingConfig:
    """Return the active configuration or raise if none has been set.

    Returns
    -------
    LadingConfig
        The configuration set by the enclosing :func:`use_configuration`.

    Raises
    ------
    ConfigurationNotLoadedError
        If no configuration is active in the current context.

    Examples
    --------
    >>> config = LadingConfig()
    >>> with use_configuration(config):
    ...     current_configuration() is config
    True
    """
    try:
        return _active_config.get()
    except LookupError as exc:  # pragma: no cover - defensive guard
        message = "Configuration has not been loaded yet."
        raise ConfigurationNotLoadedError(message) from exc


def _strip_patches(value: object) -> StripPatchesSetting:
    """Normalize the ``publish.strip_patches`` value."""
    if value is None:
        return "per-crate"
    if value in {"all", "per-crate"}:
        return typ.cast("StripPatchesSetting", value)
    if value is False:
        return False
    if value is True:
        message = "publish.strip_patches may be 'all', 'per-crate', or false."
        raise ConfigurationError(message)
    message = "publish.strip_patches must be 'all', 'per-crate', or false."
    raise ConfigurationError(message)


# Coercion helpers bound to the configuration error type; the shared
# implementations live in lading.toml_coerce (issue #108).
_string_tuple = functools.partial(toml_coerce.string_tuple, error=ConfigurationError)
_string_matrix = functools.partial(toml_coerce.string_matrix, error=ConfigurationError)
_string_mapping = functools.partial(
    toml_coerce.string_mapping, error=ConfigurationError
)
_boolean = functools.partial(toml_coerce.boolean, error=ConfigurationError)
_non_negative_int = functools.partial(
    toml_coerce.non_negative_int, error=ConfigurationError
)
_optional_mapping = functools.partial(
    toml_coerce.optional_mapping, error=ConfigurationError
)
