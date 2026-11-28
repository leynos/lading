"""Shared entry-point helpers for process-level CLI tests.

The CLI is reachable through ``python -m lading.cli`` and, when installed,
through the ``lading`` console script. Tests that pin process behaviour should
exercise both so a packaging or entry-point change cannot pass one route only.
"""

import importlib.metadata
import sys
import sysconfig
from pathlib import Path

import pytest

ENTRY_POINT_KINDS = ("module", "console-script")


def console_script() -> Path | None:
    """Return the installed ``lading`` script, or :data:`None` if absent."""
    entry_points = importlib.metadata.entry_points(
        group="console_scripts", name="lading"
    )
    if not entry_points:
        return None
    script = Path(sysconfig.get_path("scripts")) / "lading"
    return script if script.is_file() else None


def entry_point_argv(kind: str) -> list[str]:
    """Return the argv prefix that starts one supported entry point.

    Parameters
    ----------
    kind
        ``"module"`` for ``python -m lading.cli`` or ``"console-script"`` for
        the installed script.

    Returns
    -------
    list[str]
        The argv prefix to place before the CLI arguments.
    """
    if kind == "module":
        return [sys.executable, "-m", "lading.cli"]
    script = console_script()
    if script is None:
        pytest.skip(
            "the lading console script is not installed in this environment, "
            "so only the module entry point is exercised"
        )
    return [str(script)]
