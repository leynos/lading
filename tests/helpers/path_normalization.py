"""Shared helper for removing run-dependent paths from snapshot values.

Values that carry an absolute path would otherwise embed either a
``tmp_path`` (which changes every run) or a host-specific tool location in a
syrupy snapshot. Normalizing the path to a stable placeholder keeps the
snapshot deterministic while still pinning the rest of the contract: the
argv, ordering, and relative shape that the test cares about.
"""

import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path


def normalized(text: str, path: Path, *, placeholder: str = "<tmp>") -> str:
    """Replace each occurrence of ``path`` in ``text`` with ``placeholder``.

    Parameters
    ----------
    text : str
        The value about to be snapshotted.
    path : Path
        Path whose textual form should be normalized. The replacement is a
        literal string substitution, so a path that is a prefix of another
        path in ``text`` normalizes both occurrences.
    placeholder : str, optional
        Replacement token, by default ``"<tmp>"``.

    Returns
    -------
    str
        ``text`` with occurrences of ``str(path)`` replaced.
    """
    return text.replace(str(path), placeholder)


__all__ = ["normalized"]
