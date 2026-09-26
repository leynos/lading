"""Cuprum catalogue for lading command execution.

This module defines the shared programme catalogue that registers all
external executables permitted within the lading package. Using a
centralized catalogue ensures consistent allowlist enforcement across
the codebase.

Migration context: This is Step 5.1 of the Cuprum migration. Subsequent
steps will migrate existing plumbum and subprocess code to use this
catalogue via ``scoped(catalogue=LADING_CATALOGUE)``.

The catalogue is staged but intentionally not yet wired into the execution
path: every production invocation still goes through
``lading.runtime.subprocess_runner``, which spawns processes directly. The
wiring lands with the Phase 5.2 production migration in ``docs/roadmap.md``,
which rewires the spawning backend behind the ``CommandRunner`` protocol onto
``scoped(catalogue=LADING_CATALOGUE)``; do not mistake this module for live
enforcement in the meantime. Task 5.1.4 selected the cuprum 0.2.0 beta, so the
keyword form above is the one that holds; the interface reference is §7 of
``docs/lading-design.md``.
"""

from __future__ import annotations

from cuprum import Program, ProgramCatalogue, ProjectSettings

# Programme objects for allowed executables
CARGO = Program("cargo")
GIT = Program("git")
SCCACHE = Program("sccache")

# Project settings for the lading package
_LADING_PROJECT = ProjectSettings(
    name="lading",
    programs=(CARGO, GIT, SCCACHE),
    documentation_locations=("docs/lading-design.md#command-execution-migration",),
    noise_rules=(),
)

# Shared catalogue for all lading modules. External executables must be
# registered here before they can be invoked via cuprum's ``sh.make()``.
#
# - cargo: Required for workspace discovery (``cargo metadata``) and
#   publish operations (``cargo check``, ``cargo test``, ``cargo package``,
#   ``cargo publish``).
# - git: Required for pre-flight dirty-tree checks (``git status``) and
#   end-to-end test infrastructure.
# - sccache: Queried for compiler-cache statistics by
#   ``lading publish --sccache-stats`` (issue #252); the binary invoked is the
#   one ``RUSTC_WRAPPER`` names.
LADING_CATALOGUE = ProgramCatalogue(projects=(_LADING_PROJECT,))

__all__ = ["CARGO", "GIT", "LADING_CATALOGUE", "SCCACHE"]
