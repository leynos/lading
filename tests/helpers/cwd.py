"""Shared helper for tests that change the working directory.

Several unit tests call ``monkeypatch.chdir(tmp_path)`` to exercise
cwd-relative path resolution (workspace-root defaulting, relative build
directories, and so on). ``tmp_path`` is normally an empty directory, which
is fine for plain ``pytest`` runs.

It is not fine under mutmut's baseline run. mutmut instruments every mutated
call with a trampoline that resolves ``[tool.mutmut] source_paths``
(``"lading/"`` in this project's ``pyproject.toml``) against the *current*
working directory on every hit, with ``strict=True``
(``mutmut/__main__.py::record_trampoline_hit``). Chdir'ing into a bare
``tmp_path`` starves that lookup of a "lading" directory and crashes the
baseline with ``FileNotFoundError`` (issue #196), aborting the whole
mutation-testing job before any mutants are generated.
"""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def chdir_for_test(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Change the working directory to ``path`` for the rest of the test.

    Pre-creates a "lading" placeholder directory under ``path`` so mutmut's
    trampoline can resolve its configured ``source_paths`` regardless of the
    harness the test runs under. The placeholder is inert under plain pytest,
    where no such instrumentation exists.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        The active monkeypatch fixture, used to restore the original working
        directory when the test ends.
    path : Path
        The directory to change into.

    Examples
    --------
    >>> chdir_for_test(monkeypatch, tmp_path)  # doctest: +SKIP
    """
    (path / "lading").mkdir(exist_ok=True)
    monkeypatch.chdir(path)
