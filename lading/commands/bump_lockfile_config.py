"""Locate a Cargo lockfile-path override in the configuration Cargo layers.

Cargo accepts ``resolver.lockfile-path`` from a layered configuration file as
well as from ``CARGO_RESOLVER_LOCKFILE_PATH``. A configuration file may pull in
more files through its top-level ``include`` key, so planning must follow those
includes before it decides whether Cargo could write a lockfile the validated
plan does not name.
"""

import collections.abc as cabc
import dataclasses as dc
import tomllib
import typing as typ
from pathlib import Path

from lading.utils.confinement import BumpPreflightError

__all__ = ["configured_lockfile_override"]

_CONFIG_NAME = "config"
_CONFIG_TOML_NAME = "config.toml"
_INCLUDE_KEY = "include"
_LOCKFILE_PATH_KEY = "lockfile-path"
_RESOLVER_TABLE = "resolver"
_TOML_SUFFIX = ".toml"


@dc.dataclass(frozen=True, slots=True)
class _PathSetting:
    """One declared ``resolver.lockfile-path`` value and its source file."""

    value: str
    source: Path


def configured_lockfile_override(
    workspace_root: Path,
    environ: cabc.Mapping[str, str],
) -> tuple[Path, str] | None:
    """Return the effective configured lockfile path and the file setting it.

    Parameters
    ----------
    workspace_root : Path
        Canonical workspace root, used to locate layered configuration files.
    environ : cabc.Mapping[str, str]
        Environment used to resolve ``CARGO_HOME``.

    Returns
    -------
    tuple[Path, str] | None
        The file that supplied the effective non-empty value and the value
        itself, or ``None`` when no layer sets one.

    Raises
    ------
    BumpPreflightError
        If a configuration file or an included file cannot be read or parsed,
        an ``include`` entry is malformed, or includes form a cycle.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by helpers
    setting: _PathSetting | None = None
    # Cargo loads ancestors first and lets the deepest file win, so this
    # iteration runs from the outermost layer to the workspace root.
    for config_path in reversed(_cargo_config_paths(workspace_root, environ)):
        declared = _effective_setting(config_path, frozenset())
        if declared is not None:
            setting = declared
    if setting is None or not setting.value:
        return None
    return setting.source, setting.value


def _cargo_config_paths(
    workspace_root: Path,
    environ: cabc.Mapping[str, str],
) -> tuple[Path, ...]:
    """Return the Cargo configuration files that could layer over the root."""
    paths: list[Path] = []
    for directory in (workspace_root, *workspace_root.parents):
        paths.extend(_config_files(directory / ".cargo"))
    cargo_home = environ.get("CARGO_HOME")
    if cargo_home:
        home = Path(cargo_home).expanduser()
        if not home.is_absolute():
            # Cargo runs with cwd=workspace_root, so a relative CARGO_HOME is
            # relative to the workspace, not to this process's directory.
            home = workspace_root / home
    else:
        home = Path.home() / ".cargo"
    paths.extend(_config_files(home))
    return tuple(dict.fromkeys(paths))


def _config_files(directory: Path) -> list[Path]:
    """Return the Cargo configuration file Cargo would read from ``directory``.

    When both spellings exist, Cargo prefers the extensionless ``config`` and
    ignores ``config.toml``. Returning both would let this preflight reject a
    setting, or a malformed file, that Cargo never reads.

    Returns
    -------
    list[Path]
        The configuration file Cargo would read, or an empty list when the
        directory has neither spelling.
    """
    for name in (_CONFIG_NAME, _CONFIG_TOML_NAME):
        candidate = directory / name
        if candidate.is_file():
            return [candidate]
    return []


def _effective_setting(
    config_path: Path,
    ancestors: frozenset[Path],
) -> _PathSetting | None:
    """Return the setting Cargo would apply for one configuration file."""
    canonical = config_path.resolve(strict=False)
    if canonical in ancestors:
        message = f"Cargo configuration include cycle at {config_path}"
        raise BumpPreflightError(message)
    document = _load(config_path)
    setting: _PathSetting | None = None
    for include_path, optional in _include_entries(document, config_path):
        if not include_path.is_file():
            if optional:
                continue
            # Reading a missing file raises the standard diagnostic.
            _load(include_path)
        nested = _effective_setting(include_path, ancestors | {canonical})
        if nested is not None:
            # Included files load left to right, so later values win.
            setting = nested
    own = _declared_path(document)
    if own is not None:
        # The file's own values are merged on top of everything it includes.
        setting = _PathSetting(value=own, source=config_path)
    return setting


def _include_entries(
    document: cabc.Mapping[str, typ.Any],
    config_path: Path,
) -> cabc.Iterator[tuple[Path, bool]]:
    """Yield each include target and whether Cargo treats it as optional."""
    value = document.get(_INCLUDE_KEY)
    if value is None:
        return
    if isinstance(value, str | bytes) or not isinstance(value, cabc.Sequence):
        message = f"{config_path}: include must be an array of paths or tables"
        raise BumpPreflightError(message)
    for entry in value:
        yield _include_entry(entry, config_path)


def _include_entry(entry: object, config_path: Path) -> tuple[Path, bool]:
    """Return one include entry's path and optional flag."""
    match entry:
        case str():
            path_text, optional = entry, False
        case cabc.Mapping():
            path_value = entry.get("path")
            if not isinstance(path_value, str):
                message = f"{config_path}: include table must name a string path"
                raise BumpPreflightError(message)
            optional_value = entry.get("optional", False)
            if not isinstance(optional_value, bool):
                message = f"{config_path}: include optional must be a boolean"
                raise BumpPreflightError(message)
            path_text, optional = path_value, optional_value
        case _:
            message = f"{config_path}: include entries must be paths or tables"
            raise BumpPreflightError(message)
    if not path_text.endswith(_TOML_SUFFIX):
        message = (
            f"{config_path}: included path must end with {_TOML_SUFFIX}: {path_text}"
        )
        raise BumpPreflightError(message)
    candidate = Path(path_text)
    if not candidate.is_absolute():
        # Cargo resolves an include path against the file that includes it.
        candidate = config_path.parent / candidate
    return candidate, optional


def _declared_path(document: cabc.Mapping[str, typ.Any]) -> str | None:
    """Return this file's top-level ``resolver.lockfile-path``, if declared."""
    resolver = document.get(_RESOLVER_TABLE)
    if not isinstance(resolver, cabc.Mapping):
        return None
    configured = resolver.get(_LOCKFILE_PATH_KEY)
    return configured if isinstance(configured, str) else None


def _load(config_path: Path) -> cabc.Mapping[str, typ.Any]:
    """Return the parsed configuration file, reporting read failures."""
    try:
        with config_path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        message = f"cannot read Cargo configuration {config_path}: {exc}"
        raise BumpPreflightError(message) from exc
