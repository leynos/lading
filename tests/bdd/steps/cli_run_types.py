"""Shared typing contract for captured CLI run results.

This module deliberately imports no sibling step module. ``test_common_steps``
imports the bump and publish step modules at module scope so their step
definitions register with pytest-bdd, so a contract defined there would force
every consumer into a ``TYPE_CHECKING``-guarded import to avoid a cycle. A
dependency-free module can be imported normally from any step module.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path


class CliRunResult(typ.TypedDict):
    """Captured result of a ``lading`` CLI subprocess invocation."""

    returncode: int
    stdout: str
    stderr: str
    workspace: Path
