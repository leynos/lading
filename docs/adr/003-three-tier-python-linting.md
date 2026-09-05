# ADR-003: Use multi-stage Python linting

## Status

Accepted.

## Context

The Python lint workflow uses Ruff for broad style and correctness checks,
Interrogate for docstring coverage, and Pylint through `pylint-pypy-shim` for
focused rule families that complement Ruff. The shared df12 house rules add
project-specific structural, assertion, suppression, snapshot, alias, and
annotation checks that the existing stages do not provide. Syrupy snapshots
also need an explicit redaction scan.

That documentation requirement needs to be part of the normal lint gate rather
than an optional local check. It also needs to run after the virtual
environment has been created and synchronized, because Interrogate is installed
as a development dependency.

Docstring coverage initially gated only the `lading` production package while
`tests` and `scripts` stayed measured but unenforced. Triaging the gap showed
that most missing documentation in the ungated scopes sat in nested helper
closures and test-local stub classes whose docstrings would only restate the
enclosing test's name; the definitions that carry real contracts — module-level
helpers, recording doubles, and protocol or case classes — are much fewer.
Excluding those nested definitions by shape, rather than by a hand-maintained
ignore list, keeps the absolute 100% threshold enforceable across the whole
Python estate.

## Decision

`make lint` is the canonical Python lint gate and runs five stages in order:

1. Ruff checks formatting-adjacent style and broad correctness rules.
2. Interrogate runs with `--fail-under 100` and requires 100% docstring
   coverage, once against `lading` and once against `tests` and `scripts`. The
   second pass relies on the shape-based `--ignore-nested-functions` and
   `--ignore-nested-classes` options configured in `pyproject.toml`; test
   closures and test-local stub classes are exempt by structure, and every
   remaining definition must carry a docstring.
3. Pylint runs through the pinned `pylint-pypy-shim` command and applies the
   selected complementary checks.
4. Pylint loads `df12-python-lints` v0.1.0 under CPython 3.14 and enables all
   diagnostics shipped by that release. Version-gated diagnostics use the
   project's Python 3.13 baseline.
5. `ambrleaks`, from the same pinned release and running under CPython 3.14,
   scans Syrupy snapshots under `tests`.

The Makefile keeps lint tooling wired as prerequisites as well as recipe
commands. `lint` depends on `build` before checking `interrogate`, so
`uv sync --group dev` installs the development dependency before Make verifies
the virtual-environment tool.

## Consequences

New package modules, helper functions, tests, and scripts must include
docstrings at the time they are introduced. Missing documentation fails
`make lint` before the Pylint tier runs.

Nested definitions inside tests and scripts remain exempt from the gate, so a
closure or fixture double added without documentation will not fail the build.
That exemption is deliberate: such definitions document themselves through
their enclosing test or helper, and forcing a restating docstring onto each one
would dilute the signal that meaningful documentation provides. Module-level
definitions and class bodies at any level stay fully gated.

The separate CPython stage lets the df12 plug-in analyse current syntax without
changing the PyPy compatibility boundary of the existing Pylint pass. The
package pin in `pyproject.toml` and the `DF12_PYTHON_LINTS_REF` tool pin must be
updated together.

Contributors can still use Ruff and targeted tests during inner-loop work, but
changes are not ready until the full `make lint` target succeeds.
