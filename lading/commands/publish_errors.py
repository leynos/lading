"""Exception types shared by publish command workflows.

``publish_errors`` defines the public error boundary for publish orchestration.
Pre-flight components raise :class:`PublishPreflightError` when local checks
cannot prove the workspace is ready to publish. The live and dry-run publish
pipelines raise :class:`PublishError` when cargo publication work fails after
those checks have completed.

Both classes inherit from :class:`lading.exceptions.LadingError`, carry their
message through the standard exception ``args`` tuple, and avoid additional
mutable state. Callers of ``lading.commands.publish.run`` can catch
:class:`PublishPreflightError` to handle both validation and publish failures
through one path, or catch :class:`PublishError` first when publish-phase
failures need distinct handling.

Examples
--------
>>> from lading.commands.publish_errors import PublishError
>>> try:
...     raise PublishError("cargo publish failed")
... except PublishError as exc:
...     str(exc)
'cargo publish failed'
"""

from __future__ import annotations

from lading.exceptions import LadingError


class PublishPreflightError(LadingError):
    """Raise when required pre-publication checks fail.

    Parameters
    ----------
    *args
        Positional arguments passed to :class:`LadingError`; the first
        argument is conventionally the human-readable failure message.

    Attributes
    ----------
    args
        The message arguments stored by :class:`LadingError`.

    Notes
    -----
    Pre-flight helpers raise this exception before publication begins, for
    example when the working tree is dirty, an auxiliary build command fails,
    or a cargo check/test pre-flight command exits unsuccessfully.
    """


class PublishError(PublishPreflightError):
    """Raise when crate publishing fails after pre-flight checks.

    Parameters
    ----------
    *args
        Positional arguments passed to :class:`PublishPreflightError`; the first
        argument is conventionally the human-readable cargo failure message.

    Attributes
    ----------
    args
        The message arguments stored by :class:`PublishPreflightError`.

    Notes
    -----
    ``PublishError`` subclasses :class:`PublishPreflightError` so callers can
    handle all publish command failures through the broader pre-flight error
    boundary while still distinguishing failures from the cargo publish phase
    when needed.
    """


__all__ = [
    "PublishError",
    "PublishPreflightError",
]
