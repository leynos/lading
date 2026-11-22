"""Filesystem confinement policy for ``lading bump`` write targets.

Every bump write target — the workspace manifest, member manifests, configured
documentation, adopted READMEs, and lockfile destinations — passes through these
helpers before any file is written. The policy has three layers:

1. Lexical validation (:func:`validate_relative_pattern`) rejects empty,
   absolute, rooted, drive-qualified, UNC, and parent-traversing patterns under
   both POSIX and Windows spellings, so a configuration mistake cannot name a
   path outside the workspace even before the filesystem is consulted.
2. A component walk (:func:`confine_relative_path`) inspects each path component
   below the canonical root with :func:`os.lstat`. Symlinks, junctions,
   non-directory ancestors, and non-regular final components are rejected. The
   final component is classified from its ``lstat`` result, never by opening it,
   so a FIFO or device cannot block the command.
3. Canonical containment is proved with :func:`os.path.realpath` as an
   independent check, which also demonstrates that a root reached through a
   symlink in its own ancestry remains usable.

Glob expansion (:func:`expand_confined_glob`) builds on the same primitives and
never follows a symlink.

The error vocabulary is bump-scoped because :class:`BumpPreflightError` is what
the CLI maps to a dedicated diagnostic and exit status. A future command that
needs the same guarantees can reuse the checking helpers with its own error
base; the helpers take no command-specific state.

Examples
--------
>>> import tempfile
>>> from pathlib import Path
>>> with tempfile.TemporaryDirectory() as directory:
...     root = establish_workspace_root(directory)
...     _ = (root / "Cargo.toml").write_text("", encoding="utf-8")
...     confine_relative_path(root, "Cargo.toml", key="manifest").path.name
'Cargo.toml'
>>> validate_relative_pattern("crates/alpha", key="members")
('crates', 'alpha')
"""

from lading.utils.confinement.errors import (
    BumpPreflightError,
    BumpTargetExaminationError,
    MissingBumpTargetError,
    UnsafeBumpTargetError,
)
from lading.utils.confinement.glob import (
    GlobOptions,
    MatchExpectation,
    UnmatchedPolicy,
    expand_confined_glob,
)
from lading.utils.confinement.patterns import validate_relative_pattern
from lading.utils.confinement.walk import (
    ConfinedPath,
    ConfineOptions,
    LinkPredicate,
    TargetKind,
    confine_relative_path,
    establish_workspace_root,
    is_link_like,
    prove_containment,
    relative_to_root,
)

__all__ = [
    "BumpPreflightError",
    "BumpTargetExaminationError",
    "ConfineOptions",
    "ConfinedPath",
    "GlobOptions",
    "LinkPredicate",
    "MatchExpectation",
    "MissingBumpTargetError",
    "TargetKind",
    "UnmatchedPolicy",
    "UnsafeBumpTargetError",
    "confine_relative_path",
    "establish_workspace_root",
    "expand_confined_glob",
    "is_link_like",
    "prove_containment",
    "relative_to_root",
    "validate_relative_pattern",
]
