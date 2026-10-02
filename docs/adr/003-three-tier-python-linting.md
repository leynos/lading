# ADR-003: Use multi-stage Python linting

## Status

Accepted.

## Context

The Python lint workflow uses Ruff for broad style and correctness checks,
Interrogate for docstring coverage, and Pylint under PyPy for focused rule
families that complement Ruff. The shared df12 house rules add project-specific
structural, assertion, suppression, snapshot, alias, and annotation checks that
the existing stages do not provide. Syrupy snapshots also need an explicit
redaction scan. Cross-module dead-code detection also needs a blocking,
deterministic production scan, without treating test-only references as
application liveness.

That documentation requirement needs to be part of the normal lint gate rather
than an optional local check. It also needs to run after the virtual
environment has been created and synchronized, because Interrogate is installed
as a development dependency. Cross-module dead-code detection also needs a
blocking, deterministic production scan, without treating test-only references
as application liveness.

## Decision

`make lint` is the canonical Python lint gate and runs six stages in order:

1. Ruff checks formatting-adjacent style and broad correctness rules.
2. Interrogate runs with `--fail-under 100` against `lading` and requires 100%
   docstring coverage.
3. Pylint runs under the pinned `pypy@3.12` managed interpreter through
   `uv tool run` and applies the selected complementary checks. See the
   [2026-09-25 amendment](#amendment-2026-09-25-plain-pylint-on-pypy-312) for
   the runner that replaced the original shim-based invocation.
4. Pylint loads `df12-python-lints` v0.1.0 under CPython 3.14 and enables all
   diagnostics shipped by that release. Version-gated diagnostics use the
   project's Python 3.13 baseline.
5. `ambrleaks`, from the same pinned release and running under CPython 3.14,
   scans Syrupy snapshots under `tests`.
6. Skylos runs separately through a pinned `uv tool run` environment against
   `lading`, with dead-code analysis only, no uploads or provenance collection,
   and no repository-wide grep verification. It is the final, blocking check.

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

## Addendum: Skylos dead-code detection (2026-08-27)

Adopted 2026-08-27. This addendum extends the decision above with a sixth
stage; the accepted body of this ADR is otherwise unchanged.

Skylos is the final, blocking stage of `make lint`. It scans production modules
only, excludes `tests`, and runs in strict gate mode. The scan is dead-code
analysis only: no uploads, no provenance collection, and no repository-wide
grep verification, so the gate stays deterministic and test-only references
cannot be mistaken for application liveness. Skylos never modifies source files.

Skylos is invoked through a command-only macro, `$(SKYLOS_CLI)`, that pins both
the interpreter and the release:
`uv tool run --python 3.14 --from 'skylos==$(SKYLOS_VERSION)' skylos`. Skylos
parses source through its own runtime AST, so fixing the interpreter version
prevents phantom findings for syntax added by newer Python releases, and the
release pin keeps local and Continuous Integration (CI) runs on the same rule
set. Scan options are held separately in `$(SKYLOS)`, so the command macro
stays reusable by the whitelist target. That tool environment is deliberately
independent of the project lock file, so the interpreter and the release are
both pinned on the command line rather than resolved from `uv.lock`.

Every finding remains subject to caller verification. Genuine dead code is
removed. For a verified false positive, contributors must first use a typed
entry-point rule. The `type` selector may be `function`, `method`, or
`parameter`; the last form covers protocol-mandated parameters such as a signal
handler's `frame`. A named whitelist exception is appropriate only when that
rule cannot model the runtime boundary, and it must include a caller-specific
reason through `make skylos-allow SYMBOL=... REASON=...`.

## Amendment (2026-09-25): plain Pylint on PyPy 3.12

Adopted 2026-09-25. This amendment replaces the third-tier Pylint runner
described in the decision above; the rest of this ADR is unchanged.

The third stage no longer runs Pylint through `pylint-pypy-shim`. PyPy 8
implements Python 3.12, and `uv` 0.12.19 (2026-09-25) ships it as a managed
interpreter, so Pylint runs on it directly without the shim's object-build
patch. `make lint` now invokes
`uv tool run --managed-python --python $(PYLINT_PYTHON)` with
`--from 'pylint==$(PYLINT_VERSION)' pylint`, with `PYLINT_PYTHON` defaulting to
`pypy@3.12` and `PYLINT_VERSION` defaulting to `4.0.9`. The interpreter is
pinned to the `3.12` release line, not bare `pypy`, so a new PyPy release
cannot change the parsed grammar without a commit.

`pyproject.toml` no longer disables the `syntax-error` message. While it was
disabled, any module the PyPy runtime could not parse produced no messages at
all, so the lint passed without linting it. Nine modules in this repository
were skipped that way under PyPy 3.11. PyPy 3.12 parses all of them, and they
lint clean. A parse failure now fails the lint.

## Amendment (2026-10-02): one Python 3.14 baseline, four gated trees

Adopted 2026-10-02. This amendment supersedes the interpreter sentences in the
decision above and the PyPy 3.12 amendment of 2026-09-25. The rest of this ADR
is unchanged.

### A single baseline

Python 3.14 is now the project's baseline: the version `requires-python`
declares, the version Continuous Integration (CI) installs, and the version
every gateway parses with. `PYTHON_BASELINE` in the Makefile is the one place
that version is written; Ruff's `target-version`, Pylint's `py-version` and
`--py-version`, the interpreters behind `uv tool run`, and
`ty --python-version` all read from it. Bumping the baseline is one edit in the
Makefile plus the two `pyproject.toml` mirrors, and
`tests/workflow_contracts/test_python_baseline_contract.py` derives its
expectations from the baseline itself, so those assertions do not need
rewriting.

The baseline moved together with the `leynos/episodic` exemplar, whose lint
gateways this repository now follows.

### The gate reaches every Python tree

The third stage no longer runs on PyPy. It runs on the managed CPython at the
baseline, through `uv tool run --managed-python --python $(PYLINT_PYTHON)`, with
`PYLINT_PYTHON` defaulting to `$(PYTHON_BASELINE)`. The source and the df12
rules are both written to that baseline, so the interpreter Pylint parses with
is the same one the package declares support for, and no grammar boundary
remains between the stages.

The set of linted and typechecked files is now discovered rather than
enumerated. `PYTHON_SOURCE_ROOTS` names `lading`, `.github`, `tests`, `scripts`,
`benches`, and `benchmarks`; the roots that exist are walked, and dependency
caches and build dropouts are pruned. `lading` is a root in its own right, not
something reached through another tree: omitting it left the production package
entirely ungated while the suite still passed.

The walk has two predicates, not one. A `*.py` suffix test alone leaves a real
hole: `scripts/publish-check/bin/cargo` is a Python program with no extension,
because `cargo` is the name it must carry to shadow the real binary on `PATH`.
A suffix-only sweep cannot see it, and because `PYTHON_SOURCES` is a plain
variable the omission is silent -- the gate reports green over a file it never
read. The second predicate matches the executable's `uv run python` shebang,
which is what makes the file Python in the first place and survives edits to
its body. `tests/workflow_contracts/test_lint_environment.py` holds the rule:
one test asserts the shebang predicate exists, another expands `make -n lint`
and `make -n typecheck` and asserts the extensionless file is on both command
lines, so a file that is discovered but never passed to a tool also fails.

The predicate is spelled with `awk` rather than `grep`. Make opens a comment at
an unescaped `#` even inside quotes, and the brace expression a shebang match
wants collides with `find`'s own `\( \)` grouping; the `awk` form carries no
`#`, no backslash and no bracket expression across the Make-to-shell boundary,
so no quoting layer can silently eat part of the pattern.

`PYLINT_TARGETS` is that file list, not a directory. Pylint treats a directory
containing an `__init__.py` as a package and does not recurse into it, so
directory targeting under-reports: `tests/` stops at `tests/` whenever
`tests/__init__.py` exists. The file list also makes the gate reach every
module, including the ones this module's own contract tests live in.

One find caveat is load-bearing. The `find` that builds `PYTHON_SOURCES` emits
its own diagnostics on standard error, and it runs in a recipe whose output is
already consumed; the roots are therefore filtered to those that currently
exist, so a missing optional root cannot turn into a build error or, worse, a
silently truncated list.

### The df12 stages

`df12-python-lints` is pinned to commit
`4cf41736cce2f7ba2778882a5c629c044568a0e5` in both the Makefile
(`DF12_PYTHON_LINTS_REF`) and `pyproject.toml`, so a moved tag cannot change
what the gate runs. The plugin is provisioned by `uv run --isolated`, which
keeps its environment independent of `uv.lock`: a lint result must not depend
on the state of the project lock file.

A single df12 invocation enables the structural, assertion, suppression, and
annotation families -- `R9101`, `C9102`, `R9103`, `R9104`, `C9105`, `C9106`,
`C9107`, `R9108`, `R9109`, `R9110`, `R9111`, and `C9112`. `C9102`
(`assert-missing-message`) fires on an `assert` with no failure message; it was
registered by v0.3.0 of the plugin and is reached now because the targets are a
file list.

Suppressions are the last resort. Every finding so far has been fixed by
changing the code: a `TYPE_CHECKING`-only import that an annotation genuinely
needs at runtime is imported at runtime, an alias that the plugin rejects as a
re-export-by-assignment is spelled as a `from ... import ... as ...`, and the
alias name itself is lower-cased so Ruff's `N812` does not fire. No repository-
wide wildcard or mass-generated allowance exists.

### `C9112` and the withdrawn pytest-bdd exemption

At the 3.14 baseline, annotations are evaluated lazily by default (PEP 649 and
PEP 749), so `from __future__ import annotations` no longer changes how this
repository's code runs. It is removed from every module.

For a while the rule ran on its own recipe line, carrying an `--ignore-paths`
exemption for the pytest-bdd step modules. The hazard it guarded against is
real: pytest-bdd calls `inspect.signature` while collecting, and PEP 649/749
resolves a step's annotations through the *defining module's* globals rather
than the module doing the resolving, so a step annotation naming a
`TYPE_CHECKING`-only import raises during collection -- removing the future
import from `tests/bdd/steps/config_fixtures.py` alone failed the suite with
`NameError: name 'Path' is not defined`.

The exemption was still wrong, because the step modules do not keep the future
import; they import the names they annotate with at runtime. Running the rule
over the whole tree with no exemption reports nothing, which is the direct
evidence: the exemption had no subject, and the second pass over the same file
list bought nothing. The split is therefore gone and `C9112` sits in the shared
enable-list with the rest of the family.

The reason to keep the rule armed rather than drop it is that it is the only
thing that would notice a typing-only import reappearing on a step annotation.
`pyproject.toml` lists `runtime-evaluated-decorators` for the same hazard, so
in practice Ruff's `TC004` reports it first; `C9112` is the backstop. A bare
`python` shebang is not the only thing the file-list change had to account for,
and this is the other half of the same lesson: a gate is only as good as its
ability to see the file.

### Consequences of the widened gateway

Adding a Python tree to the repository is no longer enough to gate it, but
adding it to `PYTHON_SOURCE_ROOTS` is. A module under any listed root is
linted, typechecked, and counted for docstrings the moment it lands.

`tests/test_pylint_tier_contract.py` pins the interpreter and the release;
`tests/workflow_contracts/test_lint_environment.py` pins the df12 enable-list
and the commit pin, and holds the discovery rule -- that the shebang predicate
exists, and that the extensionless source it exists for reaches both `lint` and
`typecheck`; `tests/workflow_contracts/test_python_baseline_contract.py` pins
the baseline and its mirrors. Between them, a baseline bump, a narrowed
predicate, a re-widened exemption, or a floating plugin revision fails a test
rather than passing quietly.
