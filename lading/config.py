"""Configuration loading for the :mod:`lading` toolkit."""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses as dc
import typing as typ
from collections import abc as cabc

from cyclopts.config import Toml

from lading.utils import normalise_workspace_root

if typ.TYPE_CHECKING:
    from pathlib import Path

CONFIG_FILENAME = "lading.toml"

StripPatchesSetting = typ.Literal["all", "per-crate"] | bool


class ConfigurationError(RuntimeError):
    """Raised when the :mod:`lading` configuration is invalid."""


class ConfigurationNotLoadedError(ConfigurationError):
    """Raised when code accesses the configuration before it is loaded."""


class MissingConfigurationError(ConfigurationError):
    """Raised when the configuration file cannot be located."""


@dc.dataclass(frozen=True, slots=True)
class DocumentationConfig:
    """Configuration for documentation updates triggered by ``bump``."""

    globs: tuple[str, ...] = ()

    @classmethod
    def from_mapping(
        cls, mapping: cabc.Mapping[str, typ.Any] | None
    ) -> DocumentationConfig:
        """Create a :class:`DocumentationConfig` from a TOML table mapping."""
        if mapping is None:
            return cls()
        _validate_mapping_keys(mapping, {"globs"}, "bump.documentation")
        return cls(
            globs=_string_tuple(mapping.get("globs"), "bump.documentation.globs"),
        )


@dc.dataclass(frozen=True, slots=True)
class BumpConfig:
    """Settings for the ``bump`` command."""

    exclude: tuple[str, ...] = ()
    documentation: DocumentationConfig = dc.field(default_factory=DocumentationConfig)

    @classmethod
    def from_mapping(cls, mapping: cabc.Mapping[str, typ.Any] | None) -> BumpConfig:
        """Create a :class:`BumpConfig` from a TOML table mapping."""
        if mapping is None:
            return cls()
        _validate_mapping_keys(mapping, {"exclude", "documentation"}, "bump")
        return cls(
            exclude=_string_tuple(mapping.get("exclude"), "bump.exclude"),
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
        """Create a :class:`PublishConfig` from a TOML table mapping."""
        if mapping is None:
            return cls()
        _validate_mapping_keys(
            mapping,
            {"exclude", "order", "strip_patches"},
            "publish",
        )
        return cls(
            exclude=_string_tuple(mapping.get("exclude"), "publish.exclude"),
            order=_string_tuple(mapping.get("order"), "publish.order"),
            strip_patches=_strip_patches(mapping.get("strip_patches")),
        )


@dc.dataclass(frozen=True, slots=True)
class PreflightConfig:
    """Settings for publish pre-flight checks."""

    test_exclude: tuple[str, ...] = ()
    unit_tests_only: bool = False

    @classmethod
    def from_mapping(
        cls, mapping: cabc.Mapping[str, typ.Any] | None
    ) -> PreflightConfig:
        """Create a :class:`PreflightConfig` from a TOML table mapping."""
        if mapping is None:
            return cls()
        _validate_mapping_keys(
            mapping, {"test_exclude", "unit_tests_only"}, "preflight"
        )
        raw_excludes = _string_tuple(
            mapping.get("test_exclude"), "preflight.test_exclude"
        )
        filtered_excludes = [
            trimmed for entry in raw_excludes if (trimmed := entry.strip())
        ]
        return cls(
            test_exclude=tuple(filtered_excludes),
            unit_tests_only=_boolean(
                mapping.get("unit_tests_only"), "preflight.unit_tests_only"
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
        """Create a :class:`LadingConfig` from a parsed configuration mapping."""
        _validate_mapping_keys(
            mapping, {"bump", "publish", "preflight"}, "configuration section"
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
    """Validate that mapping contains only allowed keys.

    Args:
        mapping: The mapping to validate (may be None).
        allowed_keys: Set of permitted key names.
        context: Context for error message (e.g., "bump", "publish").

    Raises:
        ConfigurationError: If mapping contains unknown keys.

    """
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


def build_loader(workspace_root: Path) -> Toml:
    """Return a Cyclopts loader for ``lading.toml`` in ``workspace_root``."""
    resolved = normalise_workspace_root(workspace_root)
    return Toml(
        path=resolved / CONFIG_FILENAME,
        must_exist=True,
        search_parents=False,
        allow_unknown=True,
        use_commands_as_keys=True,
    )


def load_from_loader(loader: Toml) -> LadingConfig:
    """Load and validate configuration using ``loader``."""
    try:
        raw = loader.config
    except FileNotFoundError as exc:
        missing_path = exc.filename or str(loader.path)
        message = f"Configuration file not found: {missing_path}"
        raise MissingConfigurationError(message) from exc
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    if not isinstance(raw, cabc.Mapping):
        message = "Configuration root must be a TOML table."
        raise ConfigurationError(message)
    return LadingConfig.from_mapping(raw)


def load_configuration(workspace_root: Path) -> LadingConfig:
    """Load configuration for ``workspace_root`` using Cyclopts."""
    loader = build_loader(workspace_root)
    return load_from_loader(loader)


@contextlib.contextmanager
def use_configuration(configuration: LadingConfig) -> typ.Iterator[None]:
    """Set ``configuration`` as the active configuration for the current context."""
    token = _active_config.set(configuration)
    try:
        yield
    finally:
        _active_config.reset(token)


def current_configuration() -> LadingConfig:
    """Return the active configuration or raise if none has been set."""
    try:
        return _active_config.get()
    except LookupError as exc:  # pragma: no cover - defensive guard
        message = "Configuration has not been loaded yet."
        raise ConfigurationNotLoadedError(message) from exc


def _validate_string_sequence(
    sequence: cabc.Sequence[typ.Any], field_name: str
) -> tuple[str, ...]:
    """Validate that ``sequence`` contains only strings and return them."""
    items: list[str] = []
    for index, entry in enumerate(sequence):
        if not isinstance(entry, str):
            message = (
                f"{field_name}[{index}] must be a string, got {type(entry).__name__}."
            )
            raise ConfigurationError(message)
        items.append(entry)
    return tuple(items)


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    """Return a tuple of strings derived from ``value``."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, cabc.Sequence) and not isinstance(value, str | bytes):
        return _validate_string_sequence(value, field_name)
    message = (
        f"{field_name} must be a string or a sequence of strings; "
        f"received {type(value).__name__}."
    )
    raise ConfigurationError(message)


def _boolean(value: object, field_name: str) -> bool:
    """Return a boolean parsed from ``value``."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    message = f"{field_name} must be a boolean; received {type(value).__name__}."
    raise ConfigurationError(message)


def _strip_patches(value: object) -> StripPatchesSetting:
    """Normalise the ``publish.strip_patches`` value."""
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


def _optional_mapping(
    value: object, field_name: str
) -> cabc.Mapping[str, typ.Any] | None:
    """Ensure ``value`` is a mapping if provided."""
    if value is None:
        return None
    if isinstance(value, cabc.Mapping):
        return value
    message = f"{field_name} must be a TOML table; received {type(value).__name__}."
    raise ConfigurationError(message)
