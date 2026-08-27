# ADR-003: Use layered Python linting

## Status

Accepted.

## Context

The Python lint workflow already used Ruff for broad style and correctness
checks, then Pylint through `pylint-pypy-shim` for focused rule families that
complement Ruff. The project now also requires complete docstring coverage for
package code so internal APIs stay discoverable as modules are refactored.

That documentation requirement needs to be part of the normal lint gate rather
than an optional local check. It also needs to run after the virtual
environment has been created and synchronized, because Interrogate is installed
as a development dependency. Cross-module dead-code detection also needs a
blocking, deterministic production scan, without treating test-only references
as application liveness.

## Decision

`make lint` is the canonical Python lint gate and runs four tiers in order:

1. Ruff checks formatting-adjacent style and broad correctness rules.
2. Interrogate runs with `--fail-under 100` against `lading` and requires 100%
   docstring coverage.
3. Pylint runs through the pinned `pylint-pypy-shim` command and applies the
   selected complementary checks.
4. Skylos runs separately through a pinned `uv tool run` environment against
   `lading`, with dead-code analysis only, no uploads or provenance collection,
   and no repository-wide grep verification.

The Makefile keeps lint tooling wired as prerequisites as well as recipe
commands. `lint` depends on `build` before checking `interrogate`, so
`uv sync --group dev` installs the development dependency before Make verifies
the virtual-environment tool.

## Consequences

New package modules, helper functions, and refactors must include docstrings at
the time they are introduced. Missing documentation fails `make lint` before
the Pylint tier runs. Genuine dead code must be removed. A verified static
analysis false positive requires a precise, reasoned Skylos entry point or
named allow-list exception in `pyproject.toml`.

Contributors can still use Ruff and targeted tests during inner-loop work, but
changes are not ready until the full `make lint` target succeeds.

## Addendum — 2026-08-27

The lint architecture now treats Skylos as the fourth Python lint tier and the
final blocking check. It scans production modules only, excludes tests, and
runs in strict gate mode. Skylos is invoked through a command-only macro with
Python 3.14 and a pinned release because it parses source using its own runtime
AST; fixing the interpreter version prevents phantom findings for syntax added
by newer Python releases.

Every finding remains subject to caller verification. Genuine dead code is
removed. For a verified false positive, contributors must first use a typed
entry-point rule. A named whitelist exception is appropriate only when that
rule cannot model the runtime boundary, and it must include a caller-specific
reason through `make skylos-allow SYMBOL=... REASON=...`.
