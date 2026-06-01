"""Ports for runtime dependencies used by command workflows."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from pathlib import Path


class CommandRunner(typ.Protocol):
    """Protocol describing a callable used to execute shell commands."""

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Execute ``command`` through ``CommandRunner.__call__``.

        Parameters
        ----------
        command:
            Command and arguments to run.
        cwd:
            Working directory for the invocation, or :data:`None` to use the
            current process working directory.
        env:
            Environment overrides for the invocation, or :data:`None` to inherit
            the current process environment.

        Returns
        -------
        tuple[int, str, str]
            Exit code, decoded stdout, and decoded stderr.

        Raises
        ------
        Exception
            Implementations may raise runner-specific exceptions when a command
            cannot be prepared, spawned, or routed.
        """
