"""Configuration records for publishing and publish pre-flight checks."""

import collections.abc as cabc
import dataclasses as dc
import functools
import typing as typ

from lading import toml_coerce
from lading.config_validation import (
    _boolean,
    _string_tuple,
    validate_mapping_keys,
)
from lading.exceptions import ConfigurationError

StripPatchesSetting = typ.Literal["all", "per-crate"] | bool

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


@dc.dataclass(frozen=True, slots=True)
class PublishConfig:
    """Settings for the ``publish`` command.

    Attributes
    ----------
    exclude : tuple[str, ...]
        Crate names omitted from publication.
    order : tuple[str, ...]
        Preferred ordering for publishable crates.
    strip_patches : StripPatchesSetting
        Strategy for removing workspace-only patch entries.
    """

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
        mapping = {} if mapping is None else mapping
        validate_mapping_keys(mapping, set(PUBLISH_TOML_KEYS), "publish")
        return cls(
            exclude=_string_tuple(mapping.get("exclude"), "publish.exclude"),
            order=_string_tuple(mapping.get("order"), "publish.order"),
            strip_patches=_strip_patches(mapping.get("strip_patches")),
        )


@dc.dataclass(frozen=True, slots=True)
class CompiletestExtern:
    """Describe a compiletest extern crate override.

    Attributes
    ----------
    crate : str
        Crate name used by compiletest.
    path : str
        Filesystem path to the crate's compiled library.
    """

    crate: str
    path: str


@dc.dataclass(frozen=True, slots=True)
class PreflightConfig:
    """Settings for publish pre-flight checks.

    ``skip`` suppresses only the compilation-heavy part of the pre-flight: the
    auxiliary build commands, ``cargo check``, and ``cargo test``. The
    working-tree cleanliness guard runs when ``--forbid-dirty`` is enabled,
    and the lockfile freshness guard always runs. Skipping pre-flight therefore
    does not disable either requested guard.

    Attributes
    ----------
    skip : bool
        Whether to omit compilation-heavy pre-flight commands.
    test_exclude : tuple[str, ...]
        Test package names excluded from pre-flight execution.
    unit_tests_only : bool
        Whether cargo test runs only unit tests.
    aux_build : tuple[tuple[str, ...], ...]
        Additional command argument vectors to run before Cargo checks.
    compiletest_externs : tuple[CompiletestExtern, ...]
        Compiletest extern overrides produced from the
        ``compiletest_extern`` mapping.
    env_overrides : tuple[tuple[str, str], ...]
        Environment name-value pairs supplied to pre-flight commands.
    stderr_tail_lines : int
        Maximum number of stderr lines retained in failure diagnostics.
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
        mapping = {} if mapping is None else mapping
        validate_mapping_keys(mapping, set(PREFLIGHT_TOML_KEYS), "preflight")
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


def _strip_patches(value: object) -> StripPatchesSetting:
    """Normalize the ``publish.strip_patches`` value."""
    match value:
        case None:
            return "per-crate"
        case False:
            return False
        case True:
            message = "publish.strip_patches may be 'all', 'per-crate', or false."
            raise ConfigurationError(message)
        case "all" | "per-crate":
            return typ.cast("StripPatchesSetting", value)
        case _:
            message = "publish.strip_patches must be 'all', 'per-crate', or false."
            raise ConfigurationError(message)


# Coercion helpers local to publish pre-flight configuration; the shared
# implementations live in lading.toml_coerce (issue #108).
_string_matrix = functools.partial(toml_coerce.string_matrix, error=ConfigurationError)
_string_mapping = functools.partial(
    toml_coerce.string_mapping, error=ConfigurationError
)
_non_negative_int = functools.partial(
    toml_coerce.non_negative_int, error=ConfigurationError
)
