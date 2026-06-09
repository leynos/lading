"""Package-level base exception for the lading toolkit.

`LadingError` is the common root for expected domain failures raised by
`lading` itself. Configuration, workspace metadata, lockfile, and publish
modules define their local root exceptions by inheriting from this class, so
callers can catch a single package-level type without also catching unrelated
Python runtime failures.

Examples include `ConfigurationError`, `PublishPreflightError`,
`CargoMetadataError`, `WorkspaceModelError`, `LockfileDiscoveryError`, and
`LockfileRegenerationError`. Feature-specific subclasses should continue to inherit
from their local root exception, preserving precise handling within each
component while keeping a consistent package-wide exception hierarchy.
"""

from __future__ import annotations


class LadingError(Exception):
    """Base class for all lading exceptions."""
