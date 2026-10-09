"""Shared helper for removing run-dependent paths from snapshot values.

Values that carry an absolute path would otherwise embed either a
``tmp_path`` (which changes every run) or a host-specific tool location in a
syrupy snapshot. Normalizing the path to a stable placeholder keeps the
snapshot deterministic while still pinning the rest of the contract: the
argv, ordering, and relative shape that the test cares about.
"""

import collections.abc as cabc
import os
import shlex
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path


#: Snapshot spelling for the temporary directory a run created.
TMP_PLACEHOLDER = "<tmp>"


def normalized(
    text: str, path: Path | str, *, placeholder: str = TMP_PLACEHOLDER
) -> str:
    """Replace each occurrence of ``path`` in ``text`` with ``placeholder``.

    Parameters
    ----------
    text : str
        The value about to be snapshotted.
    path : Path | str
        Path whose textual form should be normalized, or the literal text to
        replace. Accepting ``str`` as well as ``Path`` matters for one case:
        Markdown link prefixes such as ``"../../"`` are platform-independent
        text, not filesystem paths, and ``str(Path("../../"))`` renders with
        backslashes on Windows. A caller holding such a prefix must be able to
        pass the text it means rather than a coerced path. The replacement is
        a literal string substitution, so a path that is a prefix of another
        path in ``text`` normalizes both occurrences.
    placeholder : str, optional
        Replacement token, by default ``TMP_PLACEHOLDER``.

    Returns
    -------
    str
        ``text`` with occurrences of the path rendered as ``placeholder``.
    """
    return text.replace(str(path), placeholder)


def normalized_argv(argv: cabc.Iterable[str], path: Path | str) -> list[str]:
    """Return ``argv`` with native path text replaced in every element.

    A command line captured as a ``list`` or ``tuple`` was being normalized by
    calling ``repr`` on it and substituting inside the rendering, which leaves
    ``PosixPath`` or ``WindowsPath`` behind in the result: ``repr`` of a
    ``Path`` names the concrete class, so a snapshot recorded on one platform
    rejects correct calls on the other. Normalizing element by element keeps
    the structure and the quoting that an argv list carries, and drops the
    class name from the contract entirely.

    Parameters
    ----------
    argv : Iterable[str]
        Command words, in order.
    path : Path | str
        Path or literal text to replace, as for ``normalized``.

    Returns
    -------
    list[str]
        The command words, normalized.
    """
    return [normalized(word, path) for word in argv]


def normalized_captured(
    fields: cabc.Iterable[tuple[str, object]], path: Path | str
) -> dict[str, str]:
    """Return captured collaborator arguments as portable text.

    A test double records what it was called with, and the recording often
    holds ``Path`` objects beside plain strings and scalars. Snapshotting that
    mapping directly puts ``PosixPath`` or ``WindowsPath`` in the contract, so
    a snapshot recorded on one platform rejects correct calls on the other;
    rendering each value to text keeps the arguments that matter and drops the
    class name.

    Path values are rendered with ``os.fspath`` rather than ``repr``, because
    ``repr`` of a ``Path`` names the concrete class and would leave the
    platform in the contract -- the defect this helper exists to prevent.
    ``repr`` is used for values that are neither text nor path-like, so a
    string stays distinguishable from the tuple or integer beside it.

    Parameters
    ----------
    fields : Iterable[tuple[str, object]]
        Name and value pairs to render.
    path : Path | str
        Path or literal text to replace, as for ``normalized``.

    Returns
    -------
    dict[str, str]
        One normalized rendering per field, in the order given.
    """
    return {name: normalized(_as_portable_text(value), path) for name, value in fields}


def _as_portable_text(value: object) -> str:
    """Render one captured value without naming a concrete ``Path`` class."""
    match value:
        case str():
            return value
        case os.PathLike():
            return os.fspath(value)
        case _:
            return repr(value)


def normalized_invocations(
    invocations: cabc.Iterable[typ.Any], path: Path | str
) -> list[str]:
    """Return recorded invocations as portable ``command`` / ``cwd`` strings.

    A test double records what it was asked to run, often as a small dataclass
    holding a ``tuple[str, ...]`` and a ``Path | None``. Snapshotting that
    structure directly embeds both the temporary directory and the concrete
    ``Path`` class name. Rendering each invocation as a single deterministic
    line keeps the observable contract -- the argv, and where it ran -- while
    the placeholder removes everything run-dependent.

    Parameters
    ----------
    invocations : Iterable[Any]
        Objects exposing ``command`` and ``cwd`` attributes.
    path : Path | str
        Path or literal text to replace, as for ``normalized``.

    Returns
    -------
    list[str]
        One normalized ``<command> (cwd=<dir>)`` entry per invocation, in
        order. ``cwd`` renders as ``None`` when the invocation carried none.
    """
    return [
        normalized(
            f"{shlex.join(list(invocation.command))} (cwd={invocation.cwd})", path
        )
        for invocation in invocations
    ]


__all__ = [
    "TMP_PLACEHOLDER",
    "normalized",
    "normalized_argv",
    "normalized_captured",
    "normalized_invocations",
]
