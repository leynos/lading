"""Runtime ports and adapters for external process execution.

Use this package at command boundaries that need to run external programs while
remaining testable. :class:`CommandRunner` is the port that command workflows
depend on. :class:`SubprocessContext` carries the concrete subprocess settings
needed by the default adapter, including working directory, environment, and
optional stdin. :func:`subprocess_runner` is the production entry point for
running commands, mirroring output to the active streams, and returning captured
stdout and stderr.

Tests can swap in another :class:`CommandRunner` without changing production
modules. For example:

.. code-block:: python

    from lading.runtime import CommandSpawnError, subprocess_runner

    try:
        exit_code, stdout, stderr = subprocess_runner(["cargo", "metadata"])
    except CommandSpawnError as exc:
        raise RuntimeError("cargo could not be started") from exc
"""

from lading.runtime.runner import CommandRunner, coerce_text
from lading.runtime.subprocess_runner import (
    CommandSpawnError,
    LadingError,
    SubprocessContext,
    subprocess_runner,
)

__all__ = [
    "CommandRunner",
    "CommandSpawnError",
    "LadingError",
    "SubprocessContext",
    "coerce_text",
    "subprocess_runner",
]
