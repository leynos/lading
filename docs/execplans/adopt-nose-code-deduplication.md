# Adopt the episodic nose duplication gate

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & discoveries`, `Decision log`,
`Outcomes & retrospective`, `Conformance basis`, and `Verification plan` must
be kept up to date as work proceeds.

Status: COMPLETE — implemented and opened as draft PR #292.

## Purpose / big picture

`lading` has no automated defence against duplicated logic. Ruff, Interrogate,
Pylint, the df12 house rules and Skylos between them cover formatting, imports,
complexity, docstrings, logging and dead code, but no gate notices when the
same twenty-line routine is written twice in two modules. Reviewers catch it by
eye or not at all.

After this change, `make lint` fails when `nose` — a Rust-built multi-language
clone detector — reports a duplication family inside `lading/` that no reasoned
exception covers. A developer sees the offending spans, extracts the genuinely
shared logic, or records a reviewable exception with `make duplication-allow`.
Success is observable by planting a deliberate clone in a scratch copy of the
repository and watching the gate block it, then recording an exception and
watching the same run pass.

The gate is adopted from `leynos/episodic` PR #276 at immutable revision
`d9e5ac0d254f375e2986f52d91a3b88c117c833b`, which is the authority for the
implementation. Its opening description is stale; the merged code and ADR-021
take precedence. This plan records every deliberate deviation.

## Constraints

- Application Python floor (`requires-python = ">=3.13"`), interpreter matrix
  and runtime dependencies in `pyproject.toml` must not change. The gate is
  tooling: it runs on an explicitly selected CPython 3.14 and must not require
  importing `lading`, building native extensions, or starting services.
- `nose` stays pinned at `0.20.0`. No unpinned upstream main, no newer release,
  no PyChase, no pyscn. `cargo install`, floating installers and curl-to-shell
  bootstraps are prohibited; `cargo-binstall` must be run with
  `--disable-strategies compile,quick-install` so a missing trusted binary is a
  provisioning failure rather than a source build.
- No benchmark material may be copied from the reference: no
  `benchmarks/duplication/**`, no `benchmarks/score_support.py`, no
  `tests/test_duplication_benchmark*.py`, no detector competition, no
  benchmarking CI jobs. Do not claim episodic's precision, recall or timings.
- Do not import episodic's application fixes, exception entries, package names,
  database fixtures, PGlite/npm caching, Skylos or Pylint migrations, coverage
  changes, or unrelated dependencies. No shared multi-repository tooling
  framework.
- Do not add compatibility facades, shims, aliases or forwarding wrappers. Every
  caller is updated directly.
- Do not reduce the ranking budget or raise the size floor to obtain green CI.
- `surface = "all"` widens the reported surface; it does not make `top = 30`
  exhaustive. Only the top 30 ranked families are adjudicated.
- Comments and documentation use en-GB-oxendict spelling (`-ize`, not `-ise`).
  `make spelling` is a hard gate.
- No tracked file may exceed 400 lines.

## Tolerances (exception triggers)

- Scope: if the port needs more than the five gate modules plus their focused
  tests, the `Makefile`/`pyproject.toml`/CI wiring, the ADR, the developer
  guide section and the adjudication record, stop and escalate.
- Interface: if the application's public API, CLI surface or configuration
  schema must change, stop and escalate.
- Dependencies: if a new *runtime* dependency is required, stop and escalate.
  Tooling-only pins inside the gate's isolated environment are in scope.
- Behaviour: if adjudication of a blocking family would require changing
  observable behaviour, error messages, validation order or side-effect
  ordering, stop and escalate rather than refactor under pressure.
- Iterations: if the gate still fails after three adjudication passes, stop and
  escalate with the remaining families listed.

## Risks

- Risk: the gate's own modules become findings once `scripts/` is a scan root,
  creating a bootstrap loop where the first commit must except code it just
  wrote. Severity: medium Likelihood: medium Mitigation: measure the self-scan
  before committing to a roots list; if the gate's own modules form a family,
  scope roots to shipped production Python as the reference does and record the
  omission.
- Risk: `tests/conftest.py` registers ten plugin modules and two autouse
  fixtures, and `tests/` carries an `__init__.py`. The reference conftest
  relies on `tests/` being a PEP 420 namespace package, which does not hold
  here, so a mechanical copy could pull the whole BDD/e2e plugin stack into the
  helper tests. Severity: high Likelihood: high Mitigation: run the helper
  tests with `python -m pytest -c /dev/null --rootdir=. -p no:cacheprovider`
  from an isolated tooling environment, so no repository conftest is discovered
  at all.
- Risk: `make test` runs `pytest --doctest-modules` from the repository root
  with no `testpaths`, so new `scripts/**/*.py` docstrings with `>>>` examples
  are collected as doctests in the application venv (Python 3.13), where the
  gate's cyclopts-dependent modules are not importable. Severity: high
  Likelihood: medium Mitigation: keep `>>>` examples only in modules whose
  imports resolve in the application venv, and confirm with a collection run.
- Risk: the gate's modules land in `scripts/`, which `PY_SOURCES` typechecks on
  Python 3.13 while the modules are written for 3.14. Severity: medium
  Likelihood: high Mitigation: keep the modules to syntax that parses under 3.13
  (`type` alias statements are 3.12+, fine) and confirm `make typecheck`
  passes.

## Progress

- [x] (2026-09-26) Reconnaissance: reference modules, tests, ADR-021, episodic
      Makefile/CI/pyproject, nose 0.20.0 upstream docs.
- [x] (2026-09-26) Rename branch to `adopt-nose-code-deduplication`; push with
      upstream tracking.
- [x] (2026-09-26) Provision `nose 0.20.0` into `.tools/nose` with
      `cargo-binstall --disable-strategies compile,quick-install`.
- [x] (2026-09-26) Baseline scan: `--root lading` yields 10 families, `--root
      lading --root scripts` yields 11, `--root lading --root tests --root
      scripts` yields 190.
- [x] (2026-09-26) ExecPlan drafted and committed (`5319799`).
- [x] (2026-09-26) `.gitignore` entries; `[tool.nose]` and
      `[tool.duplication_gate]` configuration.
- [x] (2026-09-26) Five gate modules ported to `scripts/`.
- [x] (2026-09-26) Focused tests ported and green in isolation: 140 passed.
- [x] (2026-09-26) Makefile targets and `lint` wiring.
- [x] (2026-09-26) CI caching and gate steps; `NOSE_VERSION` pin agreement
      enforced by `tests/workflow_contracts/test_duplication_toolchain_contract.py`.
- [x] (2026-09-26) Adjudication of every blocking family; gate green with 10
      reasoned exceptions, 0 blocking, 0 stale.
- [x] (2026-09-26) Canary demonstration in a scratch workspace: clean case,
      planted clone blocking, narrow exception allowing, and an out-of-scope
      third copy blocking again. No planted clone or temporary exception was
      left behind.
- [x] (2026-09-26) Documentation: ADR-007, developer guide, AGENTS.md,
      `docs/contents.md`.
- [x] (2026-09-27) Full gate run green: all seven gates pass, including the
      Ambrleaks, Skylos and duplication stages reached for the first time.
- [x] (2026-09-27) Draft PR [#292](https://github.com/leynos/lading/pull/292)
      opened against `main`, carrying the whole branch.
- [x] (2026-09-26) `scripts/tests` scan-scope decision: excluded, with the
      glob-form hazard recorded and guarded (see Surprises).

## Surprises & discoveries

- Observation: `tests/` carries an `__init__.py`, so it is a regular package,
  not the PEP 420 namespace package the reference conftest assumes. Evidence:
  `tests/__init__.py` is 26 bytes; the reference comment explains the
  append-not-prepend ordering exists because `tests.conftest` would otherwise
  resolve to the gate's conftest. Impact: `scripts/tests/` cannot be a sibling
  namespace member; the helper suite must be isolated from repository conftest
  discovery explicitly.
- Observation: including `tests/` in the scan produces 190 families, dominated
  by test scaffolding, against 11 for the production and tooling roots.
  Evidence: `nose query --root lading --root tests --root scripts ... top=0`
  reports `{"families": 190}`. Impact: the scan must not include `tests/`; a
  30,000-line test tree with deliberately parallel scenario structure would
  make the gate meaningless.
- Observation: `lading/` already contains two independent atomic-write helpers,
  `write_atomic_text` in `lading/commands/bump_toml.py` and `write_atomically`
  in `lading/commands/publish_sccache_report.py`, and `nose` does not flag them
  as a family. Evidence: both appear in the baseline scan output only as
  unrelated families. Impact: the gate's own persistence helper is not a
  duplicate of either; the two application helpers differ in mode preservation
  and parent creation, and neither is importable from the gate's isolated
  interpreter.

- Observation: the `scripts` root initially reported 30 families against 11 for
  `lading` alone, because the ported helper tests sit under `scripts/tests/`
  and duplicate each other's fixtures. Evidence: the top family was
  `test_duplication_gate.py:151-253 ~ test_nose_detector.py:41-115`
  (copy-paste, 115.7). Impact: the same tests-are-not-production boundary that
  excludes `tests/` must apply to `scripts/tests/`.
- Observation: `--exclude` globs are anchored to each `--root`, **not** to the
  repository root. The repository-relative spelling `scripts/tests/**` matched
  nothing and excluded no files, leaving the gate reporting 30 families from a
  tree the configuration claimed to have skipped. Evidence:
  `--exclude scripts/tests/**` returned 30 families with 19 involving
  `scripts/tests`; `--exclude tests/**` returned 11 with 0, and `**/tests/**`
  returned 11 with
  0. Impact: the exclusion is written `**/tests/**`, and a contract test now
  rejects any exclusion glob that is not `**/`-prefixed or that names a
  directory no configured root contains. This is exactly the "empty or mistyped
  scope masquerading as a clean result" failure the task warns about, and it
  was silent: had the glob excluded *more* than intended, nothing would have
  reported it either.
- Observation: the `toml_coerce/_mappings.py ~ _sequences.py` family
  (copy-paste, 40.3) is shared *declaration* scaffolding, not shared logic.
  Evidence: a controlled experiment replacing every function body in both
  modules with a distinct trivial statement left a 230.1-token match (against
  284.7 with bodies intact), so the match survives the removal of all
  behaviour. Impact: adjudicated as an intentional parallel and recorded as an
  exception rather than extracted; extracting it would couple eight independent
  coercers to one docstring/signature generator.
- Observation: `top = 30` does not currently truncate this repository's
  surface. Evidence: an uncapped `top=0` scan at the configured settings
  returns exactly the same 10 families. Impact: the recorded exceptions cannot
  be hiding a lower-ranked family *today*, but that will change as the codebase
  grows, so stale-entry wording is kept qualified and `top=0` is documented as
  the confirmation scan.

## Decision log

- Decision: adopt the reference at `d9e5ac0d254f375e2986f52d91a3b88c117c833b`,
  not the PR's opening description. Rationale: the description describes the
  superseded ADR-020 approach; the merged code and ADR-021 are authoritative.
  Date/Author: 2026-09-26, implementing agent.
- Decision: keep the gate as isolated CPython 3.14 tooling rather than folding
  it into the application venv. Rationale: the reference wrapper declares
  `requires-python = ">=3.14"` and uses PEP 695 `type` alias statements;
  running it in the project venv would couple the lint gate to the
  application's dependency set and would let a change to `requires-python`
  break linting. The application floor stays `>=3.13` and is untouched.
  Date/Author: 2026-09-26, implementing agent.
- Decision: do not reuse an application atomic-write helper in the gate.
  Rationale: both existing helpers live under `lading.commands` and are
  unreachable from the gate's isolated environment, which installs only
  `cyclopts` and `tomlkit`. Consolidating them is a separate refactor with its
  own behavioural risk. The gate's helper is the tooling-side implementation
  the reference also ships in `scripts/`. Date/Author: 2026-09-26, implementing
  agent.
- Decision: extract `_raise_missing_dependency` in
  `lading/commands/publish_index_check.py` rather than record an exception for
  the 59.0 family. Rationale: the four dependency-index paths were identical
  logic -- build message, log warning, raise -- differing only in prose, which
  is unjustified repetition rather than intentional parallelism. The extraction
  leaves the wording at each call site, so all 31 message snapshots stay
  byte-identical; that is the evidence the change is behaviour-preserving.
  Date/Author: 2026-09-26, implementing agent.
- Decision: record the remaining ten families as reasoned exceptions rather
  than extract them. Rationale: nine are shared idioms (optional-input guards,
  DI fallbacks, type-checking import blocks, the absent-table-yields-defaults
  contract, two attribute-recording exception classes, and the Windows/POSIX
  bindings of one lock primitive) whose "shared" content is a single statement
  or a platform-independent obligation. Extracting them would mean generic
  helpers, injected key sets, or platform branches inside primitives -- exactly
  the manufactured abstractions the adoption criteria exclude. The tenth is the
  declaration-scaffolding family described above. Date/Author: 2026-09-26,
  implementing agent.
- Decision: revert `skylos-allow` to its original `$(value ...)` form instead
  of routing it through the new `cli_value` macro, even though the macro was
  already written. Rationale:
  `tests/workflow_contracts/test_skylos_whitelist_boundary.py` pins the
  existing behaviour by injecting `SYMBOL`/`REASON` through the environment, and
  `cli_value` requires a value to come from the `make` command line. The
  change broke that contract, and the Skylos target is outside this task's
  scope. `cli_value` now applies only to the duplication targets, which is what
  the task requires. Date/Author: 2026-09-26, implementing agent.
- Decision: make `duplication-test` run the whole `scripts/tests/` directory
  rather than an explicit file list. Rationale: the reference's explicit list
  already drifted -- it omitted `test_duplication_gate_e2e.py`, split out of
  the 596-line reference file to satisfy the 400-line limit in `AGENTS.md`.
  Directory collection cannot silently skip a newly added test file.
  Date/Author: 2026-09-26, implementing agent.
- Decision: key the named exceptions at file granularity where nose reports
  unnamed fragments. Rationale: a `::name` key cannot match a location that
  carries no unit name, and nose reports these regions as fragments, so file
  granularity is the tightest available key. Each affected entry records that
  its key covers the whole file, so a future family arising inside it is
  understood to be silenced and re-adjudicated. Date/Author: 2026-09-26,
  implementing agent.

## Outcomes & retrospective

Delivered on `adopt-nose-code-deduplication` and opened as draft PR
[#292](https://github.com/leynos/lading/pull/292). The branch changes 31 files
against `origin/main`, the overwhelming majority of them additions.

Main's [#291](https://github.com/leynos/lading/pull/291) landed while this
branch was in review and rewrote the Pylint tier, so the branch was rebased
onto it. The one conflict was in the developer guide's lint-stage list; main's
newer Pylint wording and this branch's account of the gate running last were
both kept, the stage count was corrected from six to seven, and ADR-003 — which
still called Skylos the final blocking check — gained a dated amendment. Main's
commit touches neither `lading/` nor `scripts/`, so the adjudication below is
unaffected.

### What shipped

- The gate itself: `scripts/nose_detector.py` (detector execution),
  `scripts/nose_schema.py` (report validation),
  `scripts/duplication_allowlist.py` (family matching, persistence),
  `scripts/duplication_gate.py` (CLI), plus `scripts/atomic_write.py` as the
  persistence primitive.
- Focused tests: 140 passing cases across nine modules under `scripts/tests/`,
  including end-to-end canary tests against the real pinned binary and property
  tests for whole-family matching.
- A toolchain contract at
  `tests/workflow_contracts/test_duplication_toolchain_contract.py`, which
  fails on version drift between `Makefile`, `.github/workflows/ci.yml` and
  `pyproject.toml`, on a re-enabled compile strategy, and on an inert exclusion
  glob.
- Configuration in `pyproject.toml`: `[tool.nose]` over the `lading` and
  `scripts` roots, and `[tool.duplication_gate]` with ten reasoned exceptions.
- Wiring: `make install-nose`, `make duplication`, `make duplication-test`,
  `make duplication-allow`, with the blocking check as the final stage of
  `make lint`.
- Documentation: ADR-006, a developers-guide section, an `AGENTS.md` bullet, and
  the documentation index.

### Adjudication outcome

The initial scan of this repository's own code produced the families recorded
in the decision log above. One was a genuine extraction, at the correct layer:
the five-argument fatal-index-miss helper in
`lading/commands/publish_index_check.py` collapsed to two parameters by
bundling four co-travelling strings into a frozen dataclass, removing repeated
call-site logic without inventing an abstraction. The remaining ten are
intentional parallel structure, each recorded with a reason naming the
independent contracts an extraction would wrongly couple. No exception is a
repository-wide wildcard, and none was mass-generated.

### Validation actually run

Run at `091b47a` on the rebased head, sequentially:

| Gate                    | Result                                                         |
| ----------------------- | -------------------------------------------------------------- |
| `make check-fmt`        | pass — 222 Python files formatted, 26 Markdown files unchanged |
| `make lint`             | pass — all seven stages, exit 0                                |
| `make typecheck`        | pass — `ty check --python-version 3.13`, "All checks passed!"  |
| `make test`             | pass — 1322 passed, 29 skipped, 80 snapshots passed            |
| `make spelling`         | pass                                                           |
| `make markdownlint`     | pass — 26 files, 0 errors                                      |
| `make duplication-test` | pass — 140 passed, 3 snapshots passed                          |
| `make nixie`            | pass — all diagrams validated                                  |

Within `make lint`, the previously-unexercised stages all executed and passed:
Ambrleaks (clean on `tests/`), Skylos (`dead_code` gate clean on `lading`), and
the duplication gate itself, reporting
`duplication gate passed; 10 allowed by reasoned exceptions`. Pylint scored
10.00/10 on both tiers.

### Lessons

- The gate's helper suite and the application suite are separate interpreters
  with separate dependency sets. Reaching for the application venv's `syrupy`
  from `scripts/tests/` looked free but was not: the tooling invocation is
  `--no-project`, so `syrupy` had to be added to the `duplication-test` recipe
  and to the conftest availability guard in the same change. The guard would
  otherwise have collected the suite and failed on import.
- `mdtablefix --ellipsis` rewrites ASCII `...` to a literal ellipsis, including
  inside inline code spans. A placeholder such as `REASON='...'` therefore
  cannot survive formatting; prose that points at the real command is both
  stable and clearer than a spawn of the command that drifts from it.
- The repository's three-tier lint is cumulative, and each tier is only reached
  once the previous one is clean. Fixing Ruff exposed seven Pylint findings;
  fixing those exposed twenty-seven df12 findings. Gate the whole chain, not
  the first stage, before believing a change is lint-clean.

## Context and orientation

`lading` is a Python 3.13+ CLI for Cargo workspace maintenance. Production code
lives in the `lading/` package (59 modules, ~12,700 lines) with entry point
`lading = "lading.cli:main"`. Repository tooling lives in `scripts/`
(`release_wheel_upload.py`, `upload_release_wheels.py`, and an extensionless
`publish-check/bin/cargo` shim). Tests live in `tests/` (142 files, ~30,400
lines) with `tests/workflow_contracts/` holding the repository's existing
convention for contract tests over the Makefile, CI workflows and
`pyproject.toml`.

`nose` (`corca-ai/nose`) is a Rust-built clone detector whose executable is
named `nose`. It reports *families*: sets of two or more source regions that
share structure. This is unrelated to the abandoned Python `nose` test
framework, whose only trace here is a `nosetests.xml` line in the standard
`.gitignore` template.

A "family" is a group of matched regions. A "witness" is nose's evidence kind
(`exact`, `copy-paste`, `similar`, and others). The "surface" is nose's
visibility classification (`default`, `hidden`, `shallow`, `divergence`, …).
`min-size` counts nose's intermediate-language tokens, not Python lines or AST
nodes. `top = 30` bounds the ranked view to the thirty highest-value families;
`surface = "all"` widens which families are eligible to be ranked but does not
make the bound exhaustive.

The gate separates into five modules:

- `scripts/nose_detector.py` — reads `[tool.nose]`, resolves and version-checks
  the pinned binary, builds the argument vector, runs one query.
- `scripts/nose_schema.py` — the error vocabulary, `Location`/`Finding`, and
  report validation.
- `scripts/duplication_allowlist.py` — reasoned exception entries, key matching,
  and locked atomic persistence.
- `scripts/atomic_write.py` — the tooling-side atomic replacement helper.
- `scripts/duplication_gate.py` — the cyclopts CLI: `check` and `allow`.

## Conformance basis

No Terms of Reference or technical-design document exists for this repository.
The governing artefacts are:

- `leynos/episodic` PR #276, merged revision
  `d9e5ac0d254f375e2986f52d91a3b88c117c833b`, specifically
  `docs/adr/adr-021-adopt-nose-duplication-gate.md`.
- `corca-ai/nose` at `v0.20.0`, the pinned detector.
- Repository governance: `AGENTS.md` (quality gates, refactoring heuristics,
  400-line limit, en-GB-oxendict spelling), `docs/documentation-style-guide.md`
  (ADR-007 shape), `docs/scripting-standards.md` (`scripts/tests/` layout).
- This repository's existing quality gates in `Makefile`: `check-fmt`, `lint`,
  `test`, `typecheck`, `spelling`.

Trace: reference ADR-021 -> `EP-M1` (provisioning) -> `make install-nose` prints
`nose 0.20.0`; -> `EP-M2` (gate core) -> `scripts/tests/*` green; -> `EP-M3`
(integration) -> `make lint` fails on a planted clone; -> `EP-M4`
(documentation) -> `docs/adr/007-*.md` indexed from `docs/contents.md`.

## Verification plan

Invariants the port must preserve, and how each is discharged:

- Obligation: `V1 whole-family matching` — an exception silences a family only
  when *every* reported location matches one of its keys. Method: property test
  plus named examples. Rationale: whole-family coverage is the entire safety
  property; a partial-cover regression would silently hide new clones. Domain:
  generated findings of two to five locations over four paths and three names;
  explicit cases for unnamed fragments and `::name` mismatches. Artefact:
  `scripts/tests/test_duplication_gate_properties.py`,
  `scripts/tests/test_duplication_gate.py`. Evidence: a property comparing
  `AllowEntry.matches` against an independently computed all/any coverage
  predicate; the seeded negative control is an entry covering only one of two
  locations, which must not match. Non-vacuity: generated keys include globs,
  exact paths, and `::name` suffixes; the negative control asserts `matches` is
  `False`.
- Obligation: `V2 added member blocks` — an entry listing the current members
  must still block when nose reports a further location. Method: named pytest
  example over a three-location family. Rationale: this is the failure mode the
  reference's key design exists to prevent. Domain: one entry over two of three
  locations. Artefact: `scripts/tests/test_duplication_gate.py`. Evidence:
  `partition_findings` places the family in `blocking`.
- Obligation: `V3 fail-closed execution` — a missing binary, version mismatch,
  timeout, non-zero exit, malformed JSON or invalid report raises rather than
  returning an empty finding list. Method: parameterized pytest with injected
  runners and real subprocess boundaries. Rationale: a gate that degrades to
  "clean" on error is worse than no gate. Domain: the enumerated failure modes
  above. Artefact: `scripts/tests/test_nose_detector.py`. Evidence: each case
  raises `GateConfigError`/`GateExecutionError` with an actionable message.
- Obligation: `V4 exit status contract` — 0 clean or fully allowed, 1 for
  unsuppressed families, 2 for configuration or execution errors. Method:
  end-to-end subprocess tests of the copied gate against a stub detector.
  Rationale: CI branches on the status. Domain: clean report, blocking report,
  malformed configuration. Artefact:
  `scripts/tests/test_duplication_gate_commands.py`.
- Obligation: `V5 atomic, idempotent persistence` — a repeated `allow` for the
  same key set updates the reason instead of appending; concurrent writers
  serialize on the sidecar lock and both entries survive; a failed replacement
  leaves the original file, its mode, and no temporary sibling. Method: named
  examples plus a real two-process contention test. Rationale: exceptions are
  edited by hand and by tooling, sometimes at once. Domain: reordered keys,
  unparsable input, injected replacement failure. Artefact:
  `scripts/tests/test_duplication_gate_persistence.py`.
- Obligation: `V6 pin agreement` — the version in `Makefile`, the CI workflow
  and `[tool.nose]` are identical. Method: contract test parsing all three
  sources. Rationale: a drifted pin reintroduces the version-skew failure the
  repository already documents for Ruff and ty. Domain: the three files.
  Artefact: `tests/workflow_contracts/test_duplication_toolchain_contract.py`.
- Obligation: `V7 provisioning discipline` — `make install-nose` is a no-op for
  a matching cached binary, and otherwise invokes cargo-binstall with the
  pinned version and compilation disabled from an approved source. Method:
  named pytest examples running the real Make target against stub binaries.
  Rationale: a source build or an unpinned download would be a supply-chain
  regression. Domain: missing, mismatched and matching cached detectors.
  Artefact: `scripts/tests/test_make_install_nose.py`.
- Obligation: `V8 end-to-end blocking` — a substantial planted clone makes the
  gate exit 1; an added unlisted member re-breaks it. Method: integration test
  in a scratch copy of the repository with the real pinned binary, plus a
  manual canary transcript. Rationale: proves the wiring, not just the units.
  Domain: a small unsaturated workspace so the ranking cap cannot hide the
  canary. Artefact: `scripts/tests/test_duplication_gate_commands.py` and the
  `Validation and acceptance` transcript. Evidence: exits 1, then 0 after
  `duplication-allow`, then 1 after the third member appears.

Axioms relied on (not verified here): nose 0.20.0's own report schema and
ranking behaviour are taken as given; the pinned binary's correctness and
provenance are established by checksum on the installer, not by this plan.
Residual gaps: the semantic channel is a witness-backed check, not a general
Type-4 guarantee, and no ranking budget makes lower-ranked duplication enforced.

## Plan of work

Stage A (understand and propose): reconnaissance of the reference and this
repository. Complete.

Stage B (configuration and provisioning): add `.tools/` and the sidecar lock to
`.gitignore`; add `[tool.nose]` and an empty `[tool.duplication_gate]` to
`pyproject.toml`; add `NOSE_VERSION`, `NOSE_TOOLS_DIR`, `NOSE_BIN`,
`CARGO_BINSTALL`, `NOSE_BINSTALL_VERSION`, `NOSE_BINSTALL_SHA256`, the
`cli_value` macro, `install-nose`, `duplication`, `duplication-test` and
`duplication-allow` to the `Makefile`, and wire `install-nose` plus the gate
into `lint`.

Stage C (port): write the five modules under `scripts/`, adapting imports, root
placeholders and the PEP 723 header to this repository. Validate each with a
direct run before writing tests.

Stage D (tests): port the focused tests into `scripts/tests/`, splitting the
two files that exceed the 400-line limit, replacing episode-specific fixtures,
and providing a conftest that does not inherit `tests/conftest.py`.

Stage E (adjudication): run the configured gate, inspect every blocking family,
extract genuinely shared logic where it exists, and record reasoned exceptions
where the parallel is intentional. Re-run until green.

Stage F (canary): in a scratch copy, plant a clone, watch the gate fail, record
an exception, watch it pass, add an out-of-scope member, watch it fail again.
Check the normalized output is byte-identical across repeated runs.

Stage G (documentation and delivery): ADR-007, a developer-guide section,
`AGENTS.md` gate list, `docs/contents.md` index entry, then the full gate run
and the draft PR.

## Milestones and plateaus

- `EP-M1` provisioning: `make install-nose` places a verified `nose 0.20.0` in
  `.tools/nose` and is a no-op when it is already there. Acceptance:
  `scripts/tests/test_make_install_nose.py` green; the target prints
  `already installed` on a second run. Recovery: delete `.tools/nose` and
  re-run. Compatibility decision: none; tooling only.
- `EP-M2` gate core: the five modules and their tests pass in isolation.
  Acceptance: `make duplication-test` green. Recovery: revert `scripts/` and
  `scripts/tests/`.
- `EP-M3` integration: `make lint` runs the gate and honours its exit status.
  Acceptance: a planted clone in a scratch workspace makes `make duplication`
  exit 1. Recovery: remove the planted clone.
- `EP-M4` documentation: ADR-007 accepted and indexed; developer guide documents
  roots, channels, budget, exception review and stale-entry limits. Acceptance:
  `make markdownlint`, `make nixie` and `make check-fmt` green.

## Concrete steps

All commands run from the worktree root
`/home/leynos/.lody/repos/github---leynos---lading/worktrees/<id>`.

```plaintext
make install-nose          # -> nose 0.20.0 in .tools/nose
make duplication-test      # -> focused helper suite green
make duplication           # -> duplication gate passed
make lint                  # -> full lint including the gate
```

## Validation and acceptance

- `make duplication-test` passes with the isolated interpreter.
- `make duplication` exits 0 on the adjudicated repository.
- In a scratch copy: planting a clone makes it exit 1 with the family listed;
  `make duplication-allow FIRST=... SECOND=... REASON=...` makes it exit 0; a
  third location added outside the exception makes it exit 1 again.
- `make check-fmt`, `make lint`, `make typecheck`, `make spelling` and
  `make markdownlint` pass.
- `make test` passes; the pre-existing suite is unchanged.

## Idempotence and recovery

`make install-nose` is idempotent by construction: it compares the installed
binary's reported version against the pin and only installs on a mismatch.
`make duplication-allow` is idempotent for a repeated key set: it updates the
reason in place rather than appending a second entry. Deleting `.tools/nose/`
and re-running the installer is always safe. No step mutates tracked files
except `duplication-allow`, which edits `pyproject.toml` under an advisory lock
and an atomic replacement.

## Artefacts and notes

The reference revision, the deliberate omissions, the scan scope and ranking
policy, the adjudication record and the exact validation outcomes are recorded
in the pull request description. Per-family adjudication reasons live beside
the exceptions in `[tool.duplication_gate]` and in `docs/adr/007-*.md`.

## Interfaces and dependencies

In `scripts/nose_schema.py`:

```python
class GateConfigError(ValueError): ...
class GateExecutionError(GateConfigError): ...

@dc.dataclass(frozen=True, slots=True)
class Location:
    file: str
    start: int
    end: int
    name: str | None

@dc.dataclass(frozen=True, slots=True)
class Finding:
    witness: str
    value: float
    locations: tuple[Location, ...]

def normalize_findings(report: object) -> list[Finding]: ...
```

In `scripts/duplication_allowlist.py`:

```python
@dc.dataclass(frozen=True, slots=True)
class AllowEntry:
    keys: tuple[str, ...]
    reason: str
    def matches(self, finding: Finding) -> bool: ...

def key_matches(key: str, location: Location) -> bool: ...
def validate_key(key: str, *, context: str) -> str: ...
def load_allowlist(pyproject_path: Path) -> tuple[AllowEntry, ...]: ...
def append_allow_entry(pyproject_path: Path, *, keys: Sequence[str], reason: str) -> None: ...
```

In `scripts/nose_detector.py`:

```python
@dc.dataclass(frozen=True, slots=True)
class NoseSettings:
    version: str
    roots: tuple[str, ...]
    mode: str
    min_size: int
    surface: str
    top: int | None
    exclude: tuple[str, ...]

type CommandRunner = cabc.Callable[[cabc.Sequence[str]], str]

def load_settings(pyproject_path: Path) -> NoseSettings: ...
def resolve_binary(settings: NoseSettings, *, runner: CommandRunner | None = None) -> str: ...
def build_command(binary: str, settings: NoseSettings) -> list[str]: ...
def run_detector(settings: NoseSettings, *, runner: CommandRunner | None = None) -> list[Finding]: ...
```

In `scripts/duplication_gate.py`:

```python
def partition_findings(
    findings: cabc.Sequence[Finding], allowlist: cabc.Sequence[AllowEntry]
) -> tuple[list[Finding], list[Finding], list[AllowEntry]]: ...
```

Dependencies of the gate environment, pinned in the PEP 723 header and in the
`duplication-test` target: `cyclopts==4.25.2`, `tomlkit==0.15.1`, plus
`pytest==9.0.2` and `hypothesis[asyncio]==6.165.6` for the tests. Nothing is
added to the application's runtime or dev dependency groups.

## Revision note

2026-09-26: initial draft, recording reconnaissance, the four open design
decisions (roots, isolation, atomic-write reuse, exceptions) and the full
verification plan before implementation began.
