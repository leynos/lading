"""Configuration loading for the :mod:`lading` toolkit."""

import collections.abc as cabc
import contextlib
import contextvars
import dataclasses as dc
import typing as typ

from cyclopts.config import Toml

from lading.config_manifest_rewrites import (
    ManifestRewriteConfig,
    parse_manifest_rewrite_configs,
)
from lading.config_manifest_rewrites import (
    StringValueRewriteConfig as StringValueRewriteConfig,
)
from lading.config_publish_preflight import (
    PREFLIGHT_TOML_KEYS as PREFLIGHT_TOML_KEYS,
)
from lading.config_publish_preflight import (
    PUBLISH_TOML_KEYS as PUBLISH_TOML_KEYS,
)
from lading.config_publish_preflight import (
    CompiletestExtern as CompiletestExtern,
)
from lading.config_publish_preflight import (
    PreflightConfig,
    PublishConfig,
)
from lading.config_publish_preflight import (
    StripPatchesSetting as StripPatchesSetting,
)
from lading.config_validation import (
    _boolean,
    _optional_mapping,
    _string_tuple,
    validate_mapping_keys,
)
from lading.exceptions import ConfigurationError, ConfigurationNotLoadedError
from lading.utils import normalize_workspace_root

if typ.TYPE_CHECKING:  # pragma: no cover - type checking only
    from pathlib import Path

CONFIG_FILENAME = "lading.toml"

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
        mapping = {} if mapping is None else mapping
        validate_mapping_keys(
            mapping, set(BUMP_DOCUMENTATION_TOML_KEYS), "bump.documentation"
        )
        return cls(
            globs=_string_tuple(mapping.get("globs"), "bump.documentation.globs"),
        )


@dc.dataclass(frozen=True, slots=True)
class BumpConfig:
    """Settings for the ``bump`` command.

    Attributes
    ----------
    exclude : tuple[str, ...]
        Workspace crate names omitted from the bump.
    lockfile_manifests : tuple[str, ...]
        Additional manifests whose tracked lockfiles may be regenerated.
    rebuild_lockfiles : bool
        Whether to regenerate selected lockfiles after manifest changes.
    documentation : DocumentationConfig
        Documentation files whose version references are updated.
    manifest_rewrites : tuple[ManifestRewriteConfig, ...]
        Allowlisted non-member manifests and rewrite rules. An empty tuple
        disables configured non-member manifest rewrites.
    """

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
        mapping = {} if mapping is None else mapping
        validate_mapping_keys(mapping, set(BUMP_TOML_KEYS), "bump")
        return cls(
            exclude=_string_tuple(mapping.get("exclude"), "bump.exclude"),
            lockfile_manifests=_string_tuple(
                mapping.get("lockfile_manifests"), "bump.lockfile_manifests"
            ),
            manifest_rewrites=parse_manifest_rewrite_configs(mapping),
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
        validate_mapping_keys(
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
def use_configuration(configuration: LadingConfig) -> cabc.Generator[None]:
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
