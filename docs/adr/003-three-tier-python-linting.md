# ADR-003: Use three-tier Python linting

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
as a development dependency.

## Decision

`make lint` is the canonical Python lint gate and runs three tiers in order:

1. Ruff checks formatting-adjacent style and broad correctness rules.
2. Interrogate runs with `--fail-under 100` against `lading` and requires 100%
   docstring coverage.
3. Pylint runs through the pinned `pylint-pypy-shim` command and applies the
   selected complementary checks.

The Makefile keeps lint tooling wired as prerequisites as well as recipe
commands. `lint` depends on `build` before checking `interrogate`, so
`uv sync --group dev` installs the development dependency before Make verifies
the virtual-environment tool.

## Consequences

New package modules, helper functions, and refactors must include docstrings at
the time they are introduced. Missing documentation fails `make lint` before
the Pylint tier runs.

Contributors can still use Ruff and targeted tests during inner-loop work, but
changes are not ready until the full `make lint` target succeeds.
