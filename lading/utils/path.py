"""Filesystem helpers used across :mod:`lading`."""

from __future__ import annotations

from pathlib import Path


def normalise_workspace_root(value: Path | str | None) -> Path:
    """Return an absolute workspace path with ``~`` expanded.

    Parameters
    ----------
    value : Path | str | None
        Candidate workspace root. ``None`` selects the current working
        directory.

    Returns
    -------
    Path
        Absolute path with the user directory expanded and redundant
        segments resolved. Missing paths are permitted.

    Examples
    --------
    >>> normalise_workspace_root("~/workspace").is_absolute()
    True
    >>> normalise_workspace_root(None) == Path.cwd().resolve()
    True
    """
    if value is None:
        return Path.cwd().resolve()
    return Path(value).expanduser().resolve(strict=False)
