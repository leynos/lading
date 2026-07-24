"""Configuration loading for the :mod:`lading` toolkit."""

from __future__ import annotations

import collections.abc as cabc
import contextlib
import contextvars
import dataclasses as dc
import functools
import typing as typ

from cyclopts.config import Toml

from lading import toml_coerce
from lading.exceptions import LadingError
from lading.utils import normalise_workspace_root

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
    "rebuild_lockfiles",
})
BUMP_DOCUMENTATION_TOML_KEYS: typ.Final[frozenset[str]] = frozenset({"globs"})
PUBLISH_TOML_KEYS: typ.Final[frozenset[str]] = frozenset({
    "exclude",
    "order",
    "strip_patches",
})
PREFLIGHT_TOML_KEYS: typ.Final[frozenset[str]] = frozenset({
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
        _validate_mapping_keys(
            mapping, set(BUMP_DOCUMENTATION_TOML_KEYS), "bump.documentation"
        )
        return cls(
            globs=_string_tuple(mapping.get("globs"), "bump.documentation.globs"),
        )


@dc.dataclass(frozen=True, slots=True)
class BumpConfig:
    """Settings for the ``bump`` command."""

    exclude: tuple[str, ...] = ()
    lockfile_manifests: tuple[str, ...] = ()
    rebuild_lockfiles: bool = True
    documentation: DocumentationConfig = dc.field(default_factory=DocumentationConfig)

    @classmethod
    def from_mapping(cls, mapping: cabc.Mapping[str, typ.Any] | None) -> BumpConfig:
        """Create a :class:`BumpConfig` from a TOML table mapping."""
        if mapping is None:
            return cls()
        _validate_mapping_keys(mapping, set(BUMP_TOML_KEYS), "bump")
        return cls(
            exclude=_string_tuple(mapping.get("exclude"), "bump.exclude"),
            lockfile_manifests=_string_tuple(
                mapping.get("lockfile_manifests"), "bump.lockfile_manifests"
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
        """Create a :class:`PublishConfig` from a TOML table mapping."""
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
    """Settings for publish pre-flight checks."""

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
        """Create a :class:`PreflightConfig` from a TOML table mapping."""
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
        """Create a :class:`LadingConfig` from a parsed configuration mapping."""
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
    """Validate that mapping contains only allowed keys."""
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
        must_exist=False,
        search_parents=False,
        allow_unknown=True,
        use_commands_as_keys=True,
    )


def load_from_loader(loader: Toml) -> LadingConfig:
    """Load and validate configuration using ``loader``."""
    try:
        raw = loader.config
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
def use_configuration(configuration: LadingConfig) -> cabc.Iterator[None]:
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
