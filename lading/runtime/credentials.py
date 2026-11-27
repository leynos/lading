r"""Read registry token values from Cargo's credentials file.

``cargo login`` stores tokens in ``$CARGO_HOME/credentials.toml`` (or the legacy
extensionless ``credentials``) under ``registry.token`` for crates.io and
``registries.<name>.token`` for others. Cargo sends them without lading
seeing them, but a build script or a failing command can still print one, so
the entry point adds them to the values it hides from subprocess output.

A file that cannot be read or parsed contributes nothing and produces a
warning that names the file and the kind of failure but never repeats its
contents. Credentials a Cargo credential provider fetches on demand never pass
through lading at all, so they cannot be found here or hidden.

Examples
--------
>>> import tempfile
>>> from pathlib import Path
>>> with tempfile.TemporaryDirectory() as directory:
...     home = Path(directory)
...     _ = (home / "credentials.toml").write_text(
...         '[registry]\ntoken = "abc"\n[registries.x]\ntoken = "def"\n',
...         encoding="utf-8",
...     )
...     credential_file_tokens(home)
('abc', 'def')
"""

import collections.abc as cabc
import logging
import tomllib
from pathlib import Path

from lading.toml_coerce import optional_string_field

LOGGER = logging.getLogger(__name__)
_CREDENTIAL_FILES = ("credentials", "credentials.toml")


def _token_of(table: object) -> str:
    """Return ``table``'s ``token`` string, or ``""`` when it has none."""
    return optional_string_field(table, "token") or ""


def _tokens_in(document: cabc.Mapping[str, object]) -> list[str]:
    """Return every ``registry.token`` and ``registries.<name>.token`` value."""
    tables: list[object] = [document.get("registry")]
    registries = document.get("registries")
    if isinstance(registries, cabc.Mapping):
        tables.extend(registries.values())
    return [token for table in tables if (token := _token_of(table))]


def credential_file_tokens(cargo_home: Path | None) -> tuple[str, ...]:
    """Return the token values stored in Cargo home's credentials files.

    Parameters
    ----------
    cargo_home : Path | None
        Cargo's home directory, or ``None`` when there is none.

    Returns
    -------
    tuple[str, ...]
        The distinct token values, longest first. An unreadable or malformed
        file is skipped with a warning that never includes its contents.
    """
    paths = (
        () if cargo_home is None else (cargo_home / name for name in _CREDENTIAL_FILES)
    )
    values: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            # The exception's own text can quote the file, so only its type is
            # reported.
            LOGGER.warning(
                "Ignoring unreadable Cargo credentials file %s (%s)",
                path,
                type(exc).__name__,
            )
            continue
        values.update(_tokens_in(document))
    return tuple(sorted(values, key=lambda value: (-len(value), value)))
