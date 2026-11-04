"""Cargo crates.io index-miss transcripts shared by publish tests.

Cargo reports a dependency that exists at a non-matching version differently
from one that is absent from the index entirely. Both diagnostics carry a
non-zero exit code and the ``location searched: crates.io index`` marker, so
tests parameterize over both shapes to keep their coverage aligned
(issue #367).
"""

INDEX_MISSING_STDERR_BETA = (
    "error: failed to prepare local package for uploading\n"
    "\n"
    "Caused by:\n"
    '  failed to select a version for the requirement `alpha = "^0.1.0"`\n'
    "  candidate versions found which didn't match: 0.0.1\n"
    "  location searched: crates.io index\n"
    "  required by package `beta v0.1.0`\n"
)

# Cargo's diagnostic when the dependency is absent from the index entirely,
# rather than present at a non-matching version. This is the first-publication
# case for a newly extracted crate (issue #367).
INDEX_MISSING_PACKAGE_STDERR_BETA = (
    "error: failed to prepare local package for uploading\n"
    "\n"
    "Caused by:\n"
    "  no matching package named `alpha` found\n"
    "  location searched: crates.io index\n"
    "  required by package `beta v0.1.0`\n"
)

INDEX_MISSING_STDERR_UNPARSEABLE = (
    "error: failed to prepare local package for uploading\n"
    "\n"
    "Caused by:\n"
    "  failed to select a version for the requirement without a quoted name\n"
    "  location searched: crates.io index\n"
)

INDEX_MISSING_STDERR_EXTERNAL = (
    "error: failed to prepare local package for uploading\n"
    "Caused by:\n"
    '  failed to select a version for the requirement `external_crate = "^1"`\n'
    "  location searched: crates.io index\n"
)

INDEX_MISSING_PACKAGE_STDERR_EXTERNAL = (
    "error: failed to prepare local package for uploading\n"
    "Caused by:\n"
    "  no matching package named `external_crate` found\n"
    "  location searched: crates.io index\n"
)

__all__ = [
    "INDEX_MISSING_PACKAGE_STDERR_BETA",
    "INDEX_MISSING_PACKAGE_STDERR_EXTERNAL",
    "INDEX_MISSING_STDERR_BETA",
    "INDEX_MISSING_STDERR_EXTERNAL",
    "INDEX_MISSING_STDERR_UNPARSEABLE",
]
