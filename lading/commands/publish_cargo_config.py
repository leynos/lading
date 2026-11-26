r"""Find the registry Cargo will publish to by default.

``lading publish --expect-digests`` can only compare against crates.io, so it
must refuse a run whose effective default registry is anything else. Cargo
decides that default from its environment and configuration files; this module
follows the documented order so the refusal comes before anything is staged,
rather than leaving ``--registry crates-io`` to override the configuration
silently:

1. the ``CARGO_REGISTRY_DEFAULT`` environment variable;
2. ``.cargo/config.toml`` (or the legacy ``.cargo/config``) in the workspace
   root, then in each ancestor directory, nearest first;
3. ``config.toml`` (or ``config``) in the Cargo home directory.

The environment value and the Cargo home are passed in, resolved once by the
command-line entry point, so nothing here reads ambient state.

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
import tomllib
from pathlib import Path

from lading.commands.publish_errors import ExpectedDigestsError

_CONFIG_NAMES = ("config.toml", "config")


def _candidate_files(workspace_root: Path, cargo_home: Path | None) -> list[Path]:
    """Return Cargo's config files for ``workspace_root``, highest precedence first."""
    directories = [workspace_root, *workspace_root.parents]
    locations = [directory / ".cargo" for directory in directories]
    if cargo_home is not None:
        locations.append(cargo_home)
    return [location / name for location in locations for name in _CONFIG_NAMES]


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
    workspace_root: Path, *, cargo_home: Path | None, env_default: str | None
) -> str | None:
    """Return the default registry Cargo would publish to, or ``None``.

    Parameters
    ----------
    workspace_root : Path
        The workspace root; it and its ancestors are searched for
        ``.cargo/config.toml``.
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
    for config_file in _candidate_files(workspace_root, cargo_home):
        if config_file.is_file() and (default := _default_from(config_file)):
            return default
    return None
