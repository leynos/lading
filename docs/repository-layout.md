# Repository layout

This document explains where important project material lives and what each
area owns. It is the canonical guide for repository structure; keep it updated
when directories are added, renamed, removed, or given new responsibilities.

## Tree overview

The tree below is a compact orientation sketch, not a complete file listing.

```plaintext
.
├── .github/
│   └── workflows/
├── .rules/
├── docs/
├── lading/
│   ├── commands/
│   ├── testing/
│   ├── utils/
│   └── workspace/
├── scripts/
│   └── publish-check/
├── tests/
│   ├── bdd/
│   ├── e2e/
│   ├── helpers/
│   ├── integration/
│   └── unit/
├── AGENTS.md
├── Cargo.toml
├── Makefile
├── pyproject.toml
└── uv.lock
```

## Top-level files and directories

| Path                 | Responsibility                                                                                                                    |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `AGENTS.md`          | Local contributor and agent operating instructions. These instructions supersede older repository guidance.                       |
| `Makefile`           | Canonical entry point for build, lint, format, test, typecheck, Markdown lint, and Mermaid validation gates.                      |
| `pyproject.toml`     | Python package metadata, dependency declarations, console-script entry point, and tool configuration.                             |
| `uv.lock`            | Locked Python dependency graph for reproducible development and test environments.                                                |
| `Cargo.toml`         | Workspace manifest used by `lading` examples and validation scenarios that model Rust workspace releases.                         |
| `.github/workflows/` | Continuous Integration (CI), wheel build, release, and CodeScene workflow definitions.                                            |
| `.rules/`            | Python coding standards referenced by `AGENTS.md`; update these when language-level conventions change.                           |
| `docs/`              | Long-lived project documentation. Start with `docs/contents.md` and keep docs synchronized with code and decisions.               |
| `lading/`            | Source package for the `lading` command-line application and reusable implementation modules.                                     |
| `scripts/`           | Helper scripts and test shims that support development workflows. Follow `docs/scripting-standards.md` before changing this tree. |
| `tests/`             | Unit, integration, behavioural, end-to-end, and shared test support code.                                                         |

_Table 1: Responsibilities of the top-level repository paths._

## Source package

The `lading/` package is grouped by feature and operational boundary:

- `lading/cli.py` owns command-line application wiring and the public console
  entry point.
- `lading/commands/` contains command implementations and command-specific
  helpers for version bumping and publishing.
- `lading/config.py` owns configuration loading and validation.
- `lading/testing/` contains reusable test-facing helpers that are part of the
  project support surface.
- `lading/utils/` contains shared infrastructure helpers for command
  execution, paths, and process handling.
- `lading/workspace/` owns Cargo workspace metadata loading and typed workspace
  graph models.

## Tests

The `tests/` tree separates coverage by behaviour and blast radius:

- `tests/unit/` covers individual functions, models, and command internals.
- `tests/integration/` covers interactions between project modules and
  command shims.
- `tests/e2e/` exercises externally observable workflows through process
  boundaries.
- `tests/bdd/` contains behavioural test support for user-oriented scenarios.
- `tests/helpers/` contains shared builders, fixtures, and workspace metadata
  helpers used across test layers.

Prefer adding coverage at the narrowest level that proves the behaviour, then
add broader tests when command-line workflows, process boundaries, filesystem
effects, or release sequencing are involved.

## Documentation

Documentation belongs in `docs/` unless it is a short entry point such as
`README.md` or an executable policy file such as `AGENTS.md`.

- Use `docs/contents.md` as the documentation index.
- Use `docs/users-guide.md` for user-facing workflows and reference material.
- Use `docs/developers-guide.md` for maintainer workflows and internal
  implementation practices.
- Use `docs/lading-design.md` for architecture, constraints, and design
  rationale.
- Use `docs/roadmap.md` for delivery planning and task tracking.
- Use `docs/documentation-style-guide.md` as the formatting and document-type
  authority.

Do not embed repository-layout guidance in the developer guide. Link back to
this document instead.
