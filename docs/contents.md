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

## Decision records

- [ADR-003: Use three-tier Python linting][adr-003] - accepted linting policy
  for Ruff, Interrogate, and Pylint.

## Reference documents

- [Documentation style guide](documentation-style-guide.md) - writing,
  formatting, and naming conventions for project documentation.
- [Scripting standards](scripting-standards.md) - conventions for robust helper
  scripts, secure command execution, and command mocking.
- [cmd-mox usage guide](cmd-mox-usage-guide.md) - testing guidance for command
  spies, fixtures, and process-boundary assertions.

[adr-003]: adr/003-three-tier-python-linting.md
