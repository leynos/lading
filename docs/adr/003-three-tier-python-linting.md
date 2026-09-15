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

## Decision

`make lint` is the canonical Python lint gate and runs five stages in order:

1. Ruff checks formatting-adjacent style and broad correctness rules.
2. Interrogate runs with `--fail-under 100` against `lading` and requires 100%
   docstring coverage.
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

New package modules, helper functions, and refactors must include docstrings at
the time they are introduced. Missing documentation fails `make lint` before
the Pylint tier runs.

The separate CPython stage lets the df12 plug-in analyse current syntax without
changing the PyPy compatibility boundary of the existing Pylint pass. The
package pin in `pyproject.toml` and the `DF12_PYTHON_LINTS_REF` tool pin must
be updated together.

Contributors can still use Ruff and targeted tests during inner-loop work, but
changes are not ready until the full `make lint` target succeeds.

## Addendum: docstring coverage for tests and scripts (2026-09-07)

Adopted 2026-09-07. This addendum extends the Interrogate stage of the decision
above; the accepted body of this ADR is unchanged.

`make lint` now runs a second Interrogate invocation alongside the existing
production-package pass: `interrogate --fail-under 100 tests scripts`. The
shape-based `--ignore-nested-functions` and `--ignore-nested-classes` flags
apply only to that second invocation, passed on its command line in the
Makefile; they are never project-wide settings. Nested test closures and
test-local stub classes are exempt by structure, and every remaining definition
under `tests` and `scripts` must still carry a docstring.

The production `lading` pass remains unexempted: it keeps enforcing docstrings
on every definition it measures, including nested ones.

`tests/workflow_contracts/test_lint_target.py` enforces this contract at the
Makefile boundary, pinning both absolute 100% passes and confining the
nested-definition exemptions to the `tests` and `scripts` invocation.
