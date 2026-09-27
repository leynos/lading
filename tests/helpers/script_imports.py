"""Import the modules beside ``scripts/upload_release_wheels.py``.

The uploader is a PEP 723 script rather than an installed package, so the
script's own directory becomes a module root at run time: the workflow invokes
``uv run --script scripts/upload_release_wheels.py``, which puts ``scripts``
first on ``sys.path``, and the script imports its siblings by bare name. Tests
reproduce that with this helper rather than reaching for the package layout the
scripts do not have.
"""

from __future__ import annotations

import importlib
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    import types

    import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIRECTORY = REPOSITORY_ROOT / "scripts"


def import_script_module(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> types.ModuleType:
    """Import ``name`` from the scripts directory, as the script would.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        The active monkeypatch fixture, used to prepend the scripts directory
        and restore ``sys.path`` when the test ends.
    name : str
        The sibling module's name, without the ``.py`` suffix.

    Returns
    -------
    types.ModuleType
        The imported module.

    Examples
    --------
    >>> import_script_module(monkeypatch, "release_gh")  # doctest: +SKIP
    """
    monkeypatch.syspath_prepend(str(SCRIPT_DIRECTORY))
    return importlib.import_module(name)
