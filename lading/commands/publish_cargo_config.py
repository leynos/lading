r"""Find the registry Cargo will publish to by default.

``lading publish --expect-digests`` can only compare against crates.io, so it
must refuse a run whose effective default registry is anything else. Cargo
decides that default from its environment and configuration files; this module
follows the documented order so the refusal comes before anything is staged,
rather than leaving ``--registry crates-io`` to override the configuration
silently:

1. the ``CARGO_REGISTRY_DEFAULT`` environment variable;
2. ``.cargo/config`` or ``.cargo/config.toml`` in the directory Cargo runs in,
   then in each ancestor, nearest first. When a directory has both, Cargo uses
   only the extensionless ``config`` (checked against Cargo 1.100, which also
   warns about it), so this does too;
3. ``config.toml`` (or ``config``) in the Cargo home directory.

Publication runs from each crate's own directory, so the search starts from each
publishable crate's root as well as the workspace root: a member's own
``.cargo/config.toml`` can set a default.

The environment value, the Cargo home and the registry token values are carried
in a :class:`CargoEnvironment` that the command-line entry point resolves once,
so nothing here reads ambient state.

Examples
--------
>>> import tempfile
>>> from pathlib import Path
>>> with tempfile.TemporaryDirectory() as directory:
...     root = Path(directory) / "ws"
...     (root / ".cargo").mkdir(parents=True)
...     _ = (root / ".cargo" / "config.toml").write_text(
...         '[registry]\ndefault = "internal"\n', encoding="utf-8"
...     )
...     effective_default_registry(root, cargo_home=None, env_default=None)
'internal'
"""

import collections.abc as cabc
import dataclasses as dc
import tomllib
import typing as typ
from pathlib import Path

from lading.commands.publish_errors import ExpectedDigestsError

if typ.TYPE_CHECKING:
    from lading.commands.publish_plan import PublishPlan

# Cargo reads the extensionless name when both exist in one directory.
_CONFIG_NAMES = ("config", "config.toml")


@dc.dataclass(frozen=True, slots=True)
class CargoEnvironment:
    """The Cargo environment a digest-checked publish was resolved against.

    Resolved by the caller (the command-line entry point reads the process
    environment and Cargo home) so that lading's own modules never read ambient
    state. A caller using ``--expect-digests`` must supply one: ``None`` means
    "not resolved", which is different from a resolved environment that names no
    alternate default.

    Attributes
    ----------
    default_registry : str | None
        The value of ``CARGO_REGISTRY_DEFAULT``, or ``None`` when unset.
    cargo_home : Path | None
        Cargo's home directory, or ``None`` when it has none to search.
    secrets : tuple[str, ...]
        Registry credential values to hide from subprocess output.
    """

    default_registry: str | None
    cargo_home: Path | None
    secrets: tuple[str, ...] = ()


def _config_in(location: Path) -> Path | None:
    """Return the one config file Cargo reads in ``location``, if any."""
    return next(
        (location / name for name in _CONFIG_NAMES if (location / name).is_file()),
        None,
    )


def _candidate_files(start: Path, cargo_home: Path | None) -> list[Path]:
    """Return Cargo's config files for a run in ``start``, highest precedence first."""
    locations = [directory / ".cargo" for directory in (start, *start.parents)]
    if cargo_home is not None:
        locations.append(cargo_home)
    return [file for location in locations if (file := _config_in(location))]


def _default_from(config_file: Path) -> str | None:
    """Return the ``[registry] default`` ``config_file`` sets, if any."""
    try:
        document = tomllib.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        message = f"Cannot read Cargo configuration {config_file}: {exc}"
        raise ExpectedDigestsError(message) from exc
    registry = document.get("registry")
    default = registry.get("default") if isinstance(registry, cabc.Mapping) else None
    return default if isinstance(default, str) else None


def effective_default_registry(
    start: Path, *, cargo_home: Path | None, env_default: str | None
) -> str | None:
    """Return the default registry Cargo would publish to, or ``None``.

    Parameters
    ----------
    start : Path
        The directory Cargo runs in; it and its ancestors are searched for
        ``.cargo/config``.
    cargo_home : Path | None
        The Cargo home directory, searched last, or ``None`` to skip it.
    env_default : str | None
        The value of ``CARGO_REGISTRY_DEFAULT``, which takes precedence.

    Returns
    -------
    str | None
        The registry name, or ``None`` when nothing configures one, which
        means crates.io.

    Raises
    ------
    ExpectedDigestsError
        If a Cargo configuration file that exists cannot be read or parsed;
        Cargo would fail on it too, and guessing past it could hide a registry.
    """  # ruff: ignore[docstring-extraneous-exception] -- raised by _default_from
    if env_default:
        return env_default
    for config_file in _candidate_files(start, cargo_home):
        if default := _default_from(config_file):
            return default
    return None


def refuse_alternate_default(
    plan: PublishPlan, workspace_root: Path, environment: CargoEnvironment
) -> None:
    """Refuse when any directory publication runs from has another default.

    Checks the workspace root and then each publishable crate's root, so a
    member's own configuration is covered, not only the workspace's.

    Raises
    ------
    ExpectedDigestsError
        Naming the registry and where it applies, for the first directory whose
        effective default is not crates.io.
    """
    starts = [("the workspace", workspace_root)]
    starts += [(f"crate {crate.name}", crate.root_path) for crate in plan.publishable]
    for label, start in starts:
        default = effective_default_registry(
            start,
            cargo_home=environment.cargo_home,
            env_default=environment.default_registry,
        )
        if default not in {None, "", "crates-io"}:
            message = (
                "--expect-digests supports crates.io only, but the default "
                f"registry for {label} is {default!r}. Remove the "
                "`[registry] default` setting or CARGO_REGISTRY_DEFAULT, or drop "
                "--expect-digests"
            )
            raise ExpectedDigestsError(message)
