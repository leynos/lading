"""Cuprum catalogue for lading command execution.

This module defines the shared programme catalogue that registers all
external executables permitted within the lading package. Using a
centralised catalogue ensures consistent allowlist enforcement across
the codebase.

Migration context: This is Step 5.1 of the Cuprum migration. Subsequent
steps will migrate existing plumbum and subprocess code to use this
catalogue via ``scoped(allowlist=LADING_CATALOGUE.allowlist)``.
"""

from __future__ import annotations

from cuprum import Program, ProgramCatalogue, ProjectSettings

# Programme objects for allowed executables
CARGO = Program("cargo")
GIT = Program("git")

# Project settings for the lading package
_LADING_PROJECT = ProjectSettings(
    name="lading",
    programs=(CARGO, GIT),
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
LADING_CATALOGUE = ProgramCatalogue(projects=(_LADING_PROJECT,))

__all__ = ["CARGO", "GIT", "LADING_CATALOGUE"]
