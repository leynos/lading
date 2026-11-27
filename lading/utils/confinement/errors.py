"""Error vocabulary for the bump confinement preflight.

The confinement policy is currently bump-scoped, so its errors derive from the
package-level :class:`lading.exceptions.LadingError` and share one base class
that the CLI maps to a dedicated diagnostic and exit status. A future command
that needs the same guarantees can reuse the checking helpers with its own
error base, which is why the vocabulary is separate from the walking code.
"""

from lading.exceptions import LadingError

__all__ = [
    "BumpPreflightError",
    "BumpTargetExaminationError",
    "MissingBumpTargetError",
    "UnsafeBumpTargetError",
]


class BumpPreflightError(LadingError):
    """Base class for errors raised while validating a bump write plan."""


class UnsafeBumpTargetError(BumpPreflightError):
    """Report a target that is absolute, escaping, symlinked, or special."""


class BumpTargetExaminationError(BumpPreflightError):
    """Report an I/O failure while examining a bump target."""


class MissingBumpTargetError(BumpPreflightError):
    """Report a required bump target that does not exist."""
