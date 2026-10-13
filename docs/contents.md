# Documentation contents

This index lists the long-lived documentation for `lading`. Start here when
looking for project guidance, then follow the link that matches the task.

## Index

- [Documentation contents](contents.md) - this index of the repository's
  documentation set.
- [Agent instructions](../AGENTS.md) - contributor operating rules, quality
  gates, and repository-specific development guidance.
- [User guide](users-guide.md) - command-line usage, `lading.toml`
  configuration, and user-facing workflows.
- [Developer guide](developers-guide.md) - maintainer workflows, internal APIs,
  testing patterns, and implementation notes.
- [Repository layout](repository-layout.md) - responsibilities and conventions
  for the main directories and files in the repository.
- [Lading design](lading-design.md) - architecture, goals, constraints, and
  design rationale for the crate management tool.
- [Roadmap](roadmap.md) - phased delivery plan and tracked implementation
  tasks.
- [Cuprum beta assessment](cuprum-v0-2-0-beta1-adoption-assessment.md) -
  phase 5 compatibility, migration workarounds, and remaining upstream features.

## Migration guides

- [Migrate to lading 0.3.0](v0-3-0-migration-guide.md) -
  explains lockfile discovery, configuration fallback, and regeneration before
  publishing.

## Execution plans

Execution plans are living delivery documents under `execplans/`, one per
branch, recording the plan, progress, decisions, and retrospective for a change.

- [Regenerate discovered nested lockfiles][execplan-regenerate-lockfiles] -
  completed plan for discovery-driven lockfile regeneration in the bump command.

## Decision records

- [ADR-003: Use multi-stage Python linting][adr-003] - accepted linting policy
  for Ruff, Interrogate, Pylint, df12 house rules, snapshot redaction, and
  Skylos dead-code detection.
- [ADR-004: In-process metrics accumulator flushed at exit][adr-004] - accepted
  design for the `lading.utils.metrics` backend and metric contracts.
- [ADR-005: Draft-then-publish releases with a standalone wheel
  uploader][adr-005] - original GitHub-only publication design, with a dated
  addendum linking to the current process in ADR-009.
- [ADR-006: Align cuprum selection across dependency paths][adr-006] - accepted
  policy for pinning, locking, and bumping cuprum on both the repository and
  the standalone script path.
- [ADR-007: Emit bounded per-event relay observability logs][adr-007] - accepted
  contract for privacy-safe subprocess relay decision events.
- [ADR-008: Adopt the shared CV-005 contract library][adr-008] - accepted
  decision to run the pinned shared checker instead of local copies.
- [ADR-009: Publish Python distributions to PyPI first][adr-009] - accepted
  decision for PyPI Trusted Publishing, idempotent filename checks, and the
  GitHub Release as a second target.

## Reference documents

- [Documentation style guide](documentation-style-guide.md) - writing,
  formatting, and naming conventions for project documentation.
- [Scripting standards](scripting-standards.md) - conventions for robust helper
  scripts, secure command execution, and command mocking.
- [cmd-mox usage guide](cmd-mox-usage-guide.md) - testing guidance for command
  spies, fixtures, and process-boundary assertions.
- [cuprum users' guide](cuprum-users-guide.md) - imported `cuprum`
  0.2.0-beta1 guide to catalogues, program allowlists, execution scopes, output
  options, and hooks.
- [cuprum 0.2.0 migration guide](cuprum-v0-2-0-migration-guide.md) - imported
  `cuprum` 0.2.0-beta1 guide to the API changes from 0.1.x, including
  `ScopeConfig` and `RunOutputOptions`.
- [Lading 0.4.0 migration guide](v0-4-0-migration-guide.md) - the interpreter
  floor moving to CPython 3.14, PyPI publication, and how to upgrade or defer.
- [Complexity antipatterns and refactoring
  strategies](complexity-antipatterns-and-refactoring-strategies.md) -
  reference on cyclomatic and cognitive complexity metrics, the bumpy road
  antipattern, and refactoring techniques that reduce cognitive load.

[adr-003]: adr/003-three-tier-python-linting.md
[adr-004]: adr/004-in-process-metrics-backend.md
[adr-005]: adr/005-release-wheel-publication.md
[adr-006]: adr/006-align-cuprum-selection-across-dependency-paths.md
[adr-007]: adr/007-relay-observability-events.md
[adr-008]: adr/008-adopt-the-shared-cv005-contract-library.md
[adr-009]: adr/009-pypi-first-distribution-publication.md
[execplan-regenerate-lockfiles]: execplans/regenerate-lockfiles.md
