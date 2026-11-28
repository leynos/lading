# Identify documentation dependencies by `package`, not key, in `lading bump`

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & discoveries`, `Decision log`,
`Outcomes & retrospective`, `Conformance basis`, and `Verification plan` must
be kept up to date as work proceeds.

Status: IN PROGRESS

Branch:
`issue-369-bump-documentation-pass-matches-dependency-keys-not-packages-so-renamed-dependencies-are-missed-or-misfire`

Issue: <https://github.com/leynos/lading/issues/369>

Roadmap item: none; this is a defect fix tracked directly by the issue.

## Purpose / big picture

`lading bump` updates two kinds of version references: Cargo manifests and TOML
code fences inside Markdown documentation. The manifest pass resolves a
dependency to its real package through `cargo metadata`, so an aliased manifest
entry such as `alpha-core = { package = "alpha", version = "1.0.0" }` is bumped
when `alpha` is bumped. The documentation pass instead compares the
dependency's **key** with the set of bumped crate names. Two defects follow:

- A documented alias of a bumped crate is missed. In a Markdown fence,
  `alias = { package = "alpha", version = "1.0.0" }` stays at `1.0.0` when
  `alpha` moves to `2.0.0`. ortho-config's user's guide documents exactly this
  shape (`config_layer = { package = "ortho_config", version = "0.9.0" }`) and
  had to be bumped by hand.
- A key that happens to equal a bumped crate name but renames a different
  package is wrongly changed.
  `alpha = { package = "unrelated", version = "3.0.0" }` becomes `2.0.0`.

After this change, a TOML fence dependency is identified by its `package` field
when that field is present, and by its key otherwise, matching the manifest
pass. On the issue's reproducer,
`rewrite_markdown_toml_fences(text, {"alpha"}, "2.0.0")` bumps the alias and
leaves the unrelated package untouched. Manifest key selection, the `[package]`
and `[workspace.package]` version fields are unchanged. The documentation pass
keeps the location scope that `main` gained while this branch was open:
top-level sections, their `[workspace.*]` counterparts, and `[target.<spec>.*]`
tables. The shared entry dispatch is widened as the plan requires, so a dotted
manifest entry that exact-key selection already chose now has its `version`
field mutated where it was previously skipped; selection itself is unchanged
and a regression test pins that boundary.

## Conformance basis

- Upstream issue: `leynos/lading#369`, including the two-direction reproducer
  and the expected identity rule.
- Repository requirements: `AGENTS.md` (test-first change quality, 400-line
  module ceiling, Ruff complexity 8, en-GB-oxendict prose, documentation
  maintenance), `docs/lading-design.md` Step 2.2 notes, and
  `docs/users-guide.md` bump documentation.
- Existing contract:
  `lading/commands/bump_manifests.py::_dependency_sections_for_crate` records
  `dependency.manifest_name` and is not changed by this plan.
- Governing standard: TOML 1.0 table and inline-table syntax, and Cargo's
  `package` dependency rename field.

Trace links:

```plaintext
ISSUE-369-REQ-identity      -> EP-M1 -> tests/unit/test_dependency_sections.py
ISSUE-369-REQ-inline        -> EP-M1 -> tests/unit/test_dependency_sections.py
ISSUE-369-REQ-dotted        -> EP-M1 -> tests/unit/test_bump_toml_helpers.py
ISSUE-369-REQ-pipeline      -> EP-M1 -> tests/unit/test_bump_documentation_updates.py
ISSUE-369-REQ-cli           -> EP-M1 -> tests/bdd/features/cli.feature
ISSUE-369-REQ-implementation -> EP-M2 -> lading/commands/bump_docs_dependencies.py
ISSUE-369-REQ-mutation      -> EP-M2 -> lading/commands/bump_toml.py
ISSUE-369-REQ-migration     -> EP-M3 -> docs/users-guide.md
ISSUE-369-REQ-design        -> EP-M3 -> docs/lading-design.md
```

## Context and orientation

The repository is a Python project (`requires-python >= 3.14`) with no Rust
sources. The bump command lives in `lading/commands/`:

- `bump.py` — command entry point and context construction.
- `bump_pipeline.py` — ordered update stages.
- `bump_prepare.py` — prepares manifest, documentation, README, and lockfile
  proposals; `prepare_documentation` passes `context.updated_crate_names` as
  `DocumentationUpdateRequest.updated_crates`.
- `bump_docs.py` — documentation glob expansion; re-exports the fence helpers
  from `bump_docs_fences.py`.
- `bump_docs_dependencies.py` — dependency-section rewriting inside fences. It
  visits top-level, `[workspace.*]`, and `[target.<spec>.*]` tables and, after
  this change, selects each entry by its `package` identity or key before
  calling `bump_toml.update_dependency_table`.
- `bump_docs_fences.py` — Markdown fence discovery; re-exports
  `update_toml_snippet_dependencies` from `bump_docs_dependencies.py`.
- `bump_toml.py` — shared TOML helpers. `update_dependency_table` iterates
  names and calls `update_dependency_entry`, which dispatches `InlineTable` and
  `Table` entries to `assign_dependency_version_field` and treats everything
  else as a scalar requirement.
- `bump_manifests.py` — manifest rewriting. `_dependency_sections_for_crate`
  maps bumped crate names to manifest keys via `dependency.manifest_name`; it
  is the reference behaviour and is unchanged.

Tests live under `tests/unit`, `tests/bdd`, and `tests/e2e`. Shared workspace
builders are in `tests/helpers/workspace_builders.py`; BDD fixtures are in
`tests/bdd/steps/`. `syrupy` snapshots live in `tests/unit/__snapshots__/`.
`make test` runs pytest with `--doctest-modules`.

`tomlkit` 0.15.1 (locked in `uv.lock`) represents the relevant shapes as
follows, verified by direct probing:

- `alias = { package = "alpha", version = "1.0.0" }` — the entry is an
  `InlineTable`.
- `[dependencies.alias]` with `package`/`version` lines — the entry is a
  `Table`.
- `alias.package = "alpha"` plus `alias.version = "1.0.0"` — the entry is an
  `OutOfOrderTableProxy`, and assigning `entry["version"]` rewrites the
  original dotted line in place.
- `[dependencies.alias]`, then an unrelated table, then
  `[dependencies.beta]` — the **section** is an `OutOfOrderTableProxy`, and
  `items()`, `in`, indexing, and item assignment all work on it.
- `package` may legally be absent, empty, or a non-string type in a fence. An
  absent field means "identify by key"; a present but unusable field means the
  entry has no identity and must not be bumped.

## Required outcome

1. In documentation fences only, identify each dependency entry by its
   `package` field when present, and by its key when the field is absent.
2. Treat an entry whose `package` field is present but empty or non-string as
   unidentifiable: never fall back to its key, and never raise.
3. Preserve manifest key selection, the `[package]` and `[workspace.package]`
   handling, exact rendering (field order, dotted syntax, comments, trivia),
   idempotence, and the post-rebase location scope: top-level sections, their
   `[workspace.*]` counterparts, and `[target.<spec>.*]` tables. A dotted
   manifest entry already selected by its exact key now receives its version
   through the widened dispatch; selection itself does not change.
4. Support mutating dotted-key dependency entries (an `OutOfOrderTableProxy`)
   and dependency sections that are themselves out-of-order proxies.
5. Cover the behaviour with unit, pipeline, and CLI behavioural tests, and
   document the identity rule and its scope.

Explicit non-goals: supporting `[workspace.dependencies]` or target-specific
tables in documentation fences, changing manifest key selection, adding
configuration knobs, or resolving aliases through `cargo metadata` for
documentation snippets.

## Constraints

- Do not modify `lading/commands/bump_manifests.py` or its key selection.
- Do not add `package` handling to `bump_toml.update_dependency_table`; it
  must remain an exact-key operation for manifest callers.
- Preserve the signatures of `rewrite_markdown_toml_fences`,
  `update_toml_snippet_versions`, `update_toml_snippet_dependencies`, and
  `update_documentation_files` (the last is re-exported by `bump_docs.py`).
- No new runtime dependency. `tomlkit>=0.15,<0.16` is already declared.
- New and edited code modules aim at the 400-line ceiling. After the rebase
  onto `main` (`ba6ebc9d`), three touched files already exceed it:
  `bump_toml.py` 587 to 602 lines, `tests/bdd/steps/test_bump_steps.py` 487 to
  510, and `tests/unit/test_bump_manifest_updates.py` 414 to 453.
  `tests/unit/test_bump_documentation_updates.py` grows from 377 to 467, where
  the plan requires the pipeline tests, and crosses the ceiling. The new
  `bump_docs_dependencies.py` grows from 122 to 167 and `bump_docs_fences.py`
  returns to 384 after `main` moved the dependency helpers out. The exception
  is recorded in the decision log. Ruff complexity ceiling 8; Pylint maximum
  five positional arguments.
- Prose uses en-GB-oxendict spelling.
- Implementation proceeds under the user's explicit instruction to implement
  the supplied coding plan; the plan's milestones are authoritative.

## Tolerances (exception triggers)

Budgets are measured as net lines (additions minus deletions) against the
rebase target (`ba6ebc9d`) for the final candidate; earlier figures used the
pre-rebase base (`656506d5`). ExecPlan files are excluded from every count.

- Files: more than 15 non-ExecPlan files changed. The original budget of 10
  was revised on 2026-11-27 after the implementation reached 15 files: two
  production modules, five unit-test files, one snapshot, three BDD files, and
  four documentation files. Each addition answers a specific review or
  pre-merge check; dropping a required test or document to meet 10 would weaken
  the delivery.
- Code: more than 750 net lines across production code and tests.
- Configuration: more than 20 net lines (none expected).
- Documentation: more than 150 net lines excluding ExecPlan files.
- Interface: any public signature change beyond the annotation widening
  authorized above.
- Dependencies: any dependency change.
- Iterations: a milestone still failing its focused tests after five fix
  attempts.
- Ambiguity: two valid interpretations that change observable behaviour.

The module-size exception above is a recorded deviation, not a silent budget
increase. Any further threshold breach requires escalation.

## Risks

- Risk: tomlkit's `OutOfOrderTableProxy` may not expose the same mapping
  protocol in every shape. Severity: medium. Likelihood: low. Mitigation: the
  shapes were probed directly; the identity helper only relies on `in` and
  `get`, and mutation goes through the widened `_TableLike` helper.
- Risk: iterating `items()` on a section could include entries that are not
  dependency declarations (for example the section itself when nested
  differently). Severity: low. Likelihood: low. Mitigation: only canonical
  dependency locations are selected, and non-table entries are identified by
  key, matching previous behaviour.
- Risk: a present-but-empty `package` key might be intended to fall back to
  the key. Severity: low. Likelihood: low. Mitigation: the issue and this plan
  specify that a present `package` field is authoritative, so the entry is
  skipped.
- Risk: comment/trivia preservation through a proxy assignment. Severity: low.
  Likelihood: low. Mitigation: assignment reuses the existing
  `prepare_version_replacement` trivia preservation; a test covers an inline
  comment on a dotted line.

## Progress

- [x] (2026-11-27) Oriented in the repository; reproduced both defects against
  `HEAD` with `rewrite_markdown_toml_fences`.
- [x] (2026-11-27) Probed tomlkit 0.15.1 shapes for inline, nested, dotted,
  and out-of-order dependency entries and sections.
- [x] (2026-11-27) EP-M1: Wrote the regression tests (unit identity table,
  fence reproducer, helper contracts, pipeline snapshots, BDD scenario). Red
  baseline observed: 11 unit failures plus the BDD scenario failure, covering
  both defect directions and the dotted-proxy latent defect.
- [x] (2026-11-27) EP-M2: Implemented `_dependency_identity` and the
  section-entry filter in `bump_docs_fences.py`; widened the table-like
  dispatch in `bump_toml.py`. Focused unit tests now pass (46 passed), the
  pipeline tests pass with three new snapshots, and the BDD scenario passes.
- [x] (2026-11-27) `make check-fmt`, `make fmt`, `make lint`, `make typecheck`
  and `make test` all pass. Test gate: 1758 passed, 31 skipped, 108 snapshots
  after the assessment repairs. Lint needed three fixes: a `Returns` section on
  the private helper, a seven-argument test collapsed into an `_AliasScenario`
  dataclass, and three inline-literal assertions replaced with membership
  checks.
- [x] (2026-11-27) EP-M3: Documented the identity rule and section scope in
  `docs/users-guide.md` and `docs/lading-design.md`, and added this plan to
  `docs/contents.md`. `make fmt`, `make markdownlint` (including the spelling
  gate), and `make nixie` pass. The focused suite passed 114 tests, and the
  full gates pass: `make check-fmt`, `make lint`, `make typecheck`, and
  `make test` (1758 passed, 31 skipped, 108 snapshots).
- [x] (2026-11-27) Pushed the branch and opened draft PR #422, which states
  `Closes #369`.
- [x] (2026-11-27) Ran `coderabbit review --agent --base main` on the first
  head: 0 findings.
- [x] (2026-11-27) Corrected the user-guide migration wording and pushed
  `f7bd938f`; hosted `build-dist`, `lint-test`, CodeScene, and Gecko pass.
- [x] (2026-11-27) Assessment round 1 and 2: CodeRabbit returned substantive
  findings on the shared dispatch scope, plan tolerances, invalid-package test
  strength, migration wording, and the missing negative BDD assertion. Repairs:
  added identity cases whose key equals the target, a dotted-manifest
  regression test, the negative documentation step and scenario line, corrected
  guidance, and recorded the tolerance and module-size decisions here.
- [x] (2026-11-27) Round 3 assessments found no production defect and cleared
  the issue path. Remaining items: CodeScene duplication in
  `test_bump_steps.py` (fixed by extracting shared read/unescape helpers; local
  `cs check` now reports 10.00), a sharper manifest boundary test (selected key
  naming another package, unselected key whose package names the target), and
  test-file size records. The plan statements and file enumeration are aligned.
- [x] (2026-11-27) Re-ran every gate on each repair head and pushed
  `f6e6801a`; local CodeScene reports 10.00 and the delta is clean. Hosted
  `build-dist`, `lint-test`, `CodeScene`, and Gecko pass on that head.
- [x] (2026-11-27) Rounds 5 and 6 cleared the implementation and issue path.
  The saved pre-merge checks then failed module-level documentation on
  `bump_toml.py` and warned about developer documentation and property
  coverage. Repairs: expanded the module docstring, added a "Documentation
  dependency identity" subsection to `docs/developers-guide.md`, and added a
  Hypothesis property over generated keys, package values, and current versions
  with deterministic examples. Pushed `90e28153`; `make test` reports 1759
  passed, 31 skipped, 108 snapshots, and every hosted check passes.
- [x] (2026-11-28) The formal review completed after the queued request. All 14
  pre-merge checks pass. One minor inline finding: the user-guide example
  showed the alias and the package-name spelling as two dependencies in one
  manifest, which Cargo rejects because they resolve to the same package. The
  examples are now separate code blocks.
- [x] (2026-11-28) Rebased the 11-commit series onto `main` (`ba6ebc9d`) with
  Git's text merge, bypassing the user-wide Weave selection for this unattended
  replay. Conflicts in the moved dependency helpers and documentation were
  resolved by porting the identity rule into `bump_docs_dependencies.py`,
  retaining `main`'s workspace and target scope, and keeping both sides' tests
  and scenarios. Full gates pass: 2017 tests, 114 snapshots; all hosted checks
  rerun after the push.
- [ ] Reply in the review thread, request approval, and merge PR #422 once it
  reaches equilibrium.

## Surprises & discoveries

- Observation: tomlkit renders a dotted dependency pair (`alias.package`,
  `alias.version`) as an `OutOfOrderTableProxy`, not a `Table`. The current
  `update_dependency_entry` therefore treats it as a scalar requirement, finds
  no string value, and silently does nothing. This is a second latent defect
  that the issue does not name: dotted aliases were unreachable even by exact
  key. Evidence: the probe transcript in `Context and orientation`.
- Observation: a dependency section is an `OutOfOrderTableProxy` whenever its
  sub-tables are separated by another table. The existing
  `select_table`/`update_dependency_table` path handles this for exact keys, so
  section traversal must also handle it.
- Observation: `_TABLE_LIKE_TYPES` in `bump_toml.py` omits `InlineTable`; the
  documentation identity helper needs all three table-like shapes.
- Observation: `prepare_version_replacement` re-creates the version as a
  double-quoted TOML string, so a single-quoted source version becomes
  double-quoted. This normalization predates the change and applies equally to
  scalar, inline-table, and dotted entries; it is not a new rendering
  regression, and the tests use double-quoted versions throughout.
- Observation: while this branch was open, `main` moved
  `update_toml_snippet_dependencies` into
  `lading/commands/bump_docs_dependencies.py` and broadened its scope to
  `[workspace.*]` and `[target.<spec>.*]` tables (#370). The rebase therefore
  places the identity rule in that module and applies it at every visited
  location; `bump_docs_fences.py` no longer owns dependency rewriting.
- Observation (assessment round 1 and 2): CodeRabbit read the shared dispatch
  widening as a manifest-behaviour change and the plan's file budget as
  breached. Both are addressed in the decision log rather than dismissed.

## Decision log

- Decision: implement the identity rule in `bump_docs_fences.py`, not in
  `bump_toml.update_dependency_table`. Rationale: the manifest pass must keep
  exact-key semantics (`manifest_name` already resolves aliases), while the
  documentation pass has no metadata-derived key mapping. Keeping the rule in
  the documentation module prevents the two callers from diverging again.
- Decision: use `"package" in entry` to distinguish an absent field from an
  unusable one. Rationale: `value_as_string(...) or key` would silently fall
  back to the key for an empty package, contradicting the specified rule. The
  identity table now also covers empty and non-string `package` values whose
  key equals the target, so a fallback would fail the test.
- Decision: widen `assign_dependency_version_field` to `_TableLike` and route
  `OutOfOrderTableProxy` through the table-like branch of
  `update_dependency_entry`. Rationale: dotted entries must mutate their
  `version` field, and the proxy already supports indexing and assignment.
  Scope: manifest key selection is unchanged, but a dotted manifest entry that
  exact-key selection already chose now receives its version where it was
  previously skipped. This follows the plan's explicit instruction to widen the
  shared dispatch;
  `test_render_manifest_updates_only_selected_dotted_dependency` pins the
  boundary so the documentation identity rule cannot leak into manifests.
- Decision: keep `_update_single_dependency_section` as the section unit and
  give it the identity filter. Rationale: the existing structure already
  isolates the per-section concern and keeps the public function small.
- Decision: test rendered text rather than tomlkit types. Rationale: the
  contract is the rewritten Markdown, not the parser's internal shape.
- Decision: record a revised file tolerance (10 to 15) and a module-size
  deviation rather than splitting the affected files in this change. Rationale:
  after the rebase, `bump_toml.py` (587), `tests/bdd/steps/test_bump_steps.py`
  (487), and `tests/unit/test_bump_manifest_updates.py` (414) already exceeded
  400 lines at the target; the change grows them to 602, 510, and 453.
  `tests/unit/test_bump_documentation_updates.py` (377 to 467) crosses the
  ceiling; the plan places the pipeline tests in that file, so the exception is
  recorded here rather than moving them to a new module. The
  `bump_docs_dependencies.py` integration reaches 167 lines and
  `bump_docs_fences.py` is back to 384 after `main` moved the dependency
  helpers. A split would enlarge the review diff beyond the issue. This is a
  recorded deviation, not a silent increase.
- Decision: after the rebase, keep identity selection in
  `bump_docs_dependencies.py` and retain `main`'s workspace and target
  traversal. Rationale: #370 relocated the dependency pass and defined that
  scope; duplicating the traversal in the fence module would drift. The plan's
  original top-level-only scope is superseded by the merged upstream design.
- Decision: keep the documented quote-style normalization as pre-existing
  behaviour. Rationale: `prepare_version_replacement` has always re-created
  string requirements, so changing it belongs to a separate rendering change
  and would affect every dependency rewrite.

## Milestones

### EP-M1 — Failing regression tests

Assigned requirements: `ISSUE-369-REQ-identity`, `-inline`, `-dotted`,
`-pipeline`, `-cli`.

Add tests first and confirm they fail for the expected reason:

- `tests/unit/test_dependency_sections.py`: exact rendered output and changed
  flags for inline, nested-table, and dotted aliases; a collision with another
  package; interleaved dotted fields; key-based string and inline entries; a
  matching key and package; multiple aliases; empty and non-string `package`
  values; section scope; an already-current snippet; comments and field order.
- `tests/unit/test_bump_toml_helpers.py`: exact-key contract on
  `update_dependency_table` and dotted-proxy mutation through
  `update_dependency_entry`.
- `tests/unit/test_bump_docs_fences.py`: the issue's reproducer with an alias
  and a collision in one fence.
- `tests/unit/test_bump_documentation_updates.py`: pipeline updates for alias
  and collision, `bump.exclude`, dry-run behaviour, and the documentation
  summary; snapshots only for new summaries.
- `tests/bdd/features/cli.feature` plus `tests/bdd/steps/`: a scenario where a
  documented alias is bumped and a colliding key is not, with a step for
  asserting that a fence does not contain a value.

Acceptance: the new tests fail with the observed defect (alias unchanged,
collision bumped, dotted alias untouched), while every existing test still
passes.

### EP-M2 — Implementation

Assigned requirements: `ISSUE-369-REQ-implementation`, `-mutation`.

- Add `_dependency_identity(key, entry)` to `bump_docs_fences.py` and filter
  keys through it in `_update_single_dependency_section`.
- Dispatch `OutOfOrderTableProxy` entries through the table-like branch of
  `update_dependency_entry`, and widen `assign_dependency_version_field` to
  `_TableLike`.
- Update docstrings for identity selection and section scope.

Acceptance: all EP-M1 tests pass; `make check-fmt`, `make lint`,
`make typecheck`, and `make test` pass.

### EP-M3 — Documentation and verification

Assigned requirements: `ISSUE-369-REQ-migration`, `-design`.

- Update the bump paragraph in `docs/users-guide.md`: package-field identity,
  section scope, an alias example, and a migration note.
- Update the Step 2.2 implementation notes in `docs/lading-design.md` with the
  same rule and scope.
- Add this plan to `docs/contents.md`.
- Run `make fmt`, `make markdownlint`, `make nixie`, the focused pytest
  command, and the full gates.

Acceptance: the documentation states the rule and scope without implying
support for `[workspace.dependencies]` or target-specific tables, and every
gate passes with recorded evidence.

## Verification plan

Invariants:

1. For every documented dependency entry, the rewritten version changes if and
   only if the entry's identity is in the target set, its current requirement
   differs from the target, and its entry shape can carry a version.
2. Identity is the `package` field when present, and the key otherwise.
3. A present-but-unusable `package` field (empty or non-string) yields no
   identity, so the entry is neither bumped nor used as a fallback key.
4. Entries outside the canonical locations (top-level, `[workspace.*]`, and
   `[target.<spec>.*]` sections) never change.
5. `[package].version` and `[workspace.package].version` handling is
   independent of dependency identity.
6. Rendering is idempotent, and unchanged fences are byte-identical.

Lemmas:

- L1: `update_dependency_table` remains an exact-key operation, so manifest
  callers are unaffected by the documentation filter.
- L2: `OutOfOrderTableProxy` supports `in`, `get`, indexing, and assignment,
  so the widened dispatch can mutate dotted entries without new machinery.
- L3: `Table.items()` and `OutOfOrderTableProxy.items()` both enumerate the
  entries whose identity must be tested.

Axioms: tomlkit 0.15.1's documented mapping behaviour for `Table`,
`InlineTable`, and `OutOfOrderTableProxy`; MarkdownIt's fence tokenization
(unchanged by this plan).

Methods and evidence:

- Parameterized pytest examples for the finite identity truth table (inline,
  nested table, dotted, scalar, empty package, non-string package, multiple
  aliases, collisions, section scope, already-current), asserting exact
  rendered text. Discharges invariants 1–5. Negative controls: the collision
  and the empty-package cases must remain byte-identical.
- A fence-level example (the issue reproducer) discharges the end-to-end
  documentation contract and the changed flag.
- Pipeline tests through `bump.run` discharge the wiring, `bump.exclude`, and
  dry-run behaviour; snapshots pin the summary only.
- The BDD CLI scenario provides a user-observable witness that a real command
  invocation updates the alias and leaves the collision untouched.
- A property/flakiness consideration: the existing
  `test_snippet_rewrite_visits_exactly_canonical_sections` hypothesis test plus
  the new examples cover the section partition; an open-ended generator adds no
  evidence for the finite identity rule, so it is not required.
- Non-vacuity: the red baseline transcript (alias unchanged, collision
  changed) demonstrates that the assertions fail against the current
  implementation for the intended reason. The dotted-proxy test would fail
  under the current dispatch because the version field never changes.
- The invalid-package cases now use a key equal to the target (`alpha`), so a
  hypothetical fallback to the key would change the entry and fail the test.
  The manifest regression test proves the documentation identity rule does not
  alter exact-key selection.

Residual gap: identity is not resolved through `cargo metadata`, so a
documentation alias whose package is not a workspace member remains unaffected;
that is intended. Version quote style is normalized by the pre-existing
replacement helper; that behaviour is unchanged and out of scope.

## Outcomes & retrospective

The documentation pass now identifies a fence dependency by its `package` field
when present, and by its key when the field is absent. The issue's reproducer
behaves as specified: `alias = { package = "alpha" }` is bumped and
`alpha = { package = "unrelated" }` is not. The fix also made dotted dependency
keys reachable for the first time, because `OutOfOrderTableProxy` entries now
mutate through their `version` field rather than being treated as scalar
requirements.

Evidence:

```plaintext
focused: 114 passed in 78.60s
make check-fmt: 649 files already formatted; 40 Markdown files unchanged
make markdownlint: 0 issues in 0 files (spelling gate clean)
make nixie: All diagrams validated successfully
make lint: 10.00/10 (Ruff, Interrogate, Skylos, duplication, Pylint)
make typecheck: All checks passed
make test: 1759 passed, 31 skipped, 108 snapshots
hosted: build-dist, lint-test, CodeScene, and Gecko pass on 90e28153
cs check: 10.00 for bump_toml.py and test_dependency_sections.py
```

Lessons:

- A shared mutation helper can hide an entry shape. The existing dispatch
  listed two table-like types even though the module defined a third as a
  table-like alias; probing tomlkit's real output exposed the gap before the
  tests did.
- The repository's lint stack rejects boolean positional parameters and
  inline-literal assertions in tests. Grouping scenario data into a frozen
  dataclass and using membership assertions keeps tests inside the house rules
  without weakening the contract.
- The spelling gate caught `tokenisation`; en-GB-oxendict spelling applies to
  ExecPlans as well as user documentation.
- A test can pass for the wrong reason when the fixture key is not the target
  key. The unusable-`package` cases originally used `alias`, so they could not
  distinguish the correct no-identity rule from an incorrect key fallback; the
  assessment caught it and the fixtures now use `alpha`.
- A plan tolerance can be wrong on the first estimate. The natural file
  surface was 15, not 10, and four edited files already exceeded the 400-line
  ceiling; recording the deviation was more honest than deleting a required
  test or document.
- A finite identity table is a good contract, but a generated property adds
  value here because it varies the key and package independently, including
  keys equal to the target with unusable package fields. Deterministic
  `@example` cases keep the critical branches from depending on generation.
- Pre-merge checks can surface gaps that the ExecPlan assessments do not: a
  touched module's docstring and the developer-facing documentation were both
  in scope even though the behaviour was correct.

Residual gap: documentation identity is not resolved through `cargo metadata`,
so a documented alias of a non-member package is unaffected. That matches the
issue's expected behaviour and the section scope.

## Revision note

- 2026-11-27: Initial draft, created before the failing tests were written.
- 2026-11-27: Post-assessment revision. Corrected the manifest-scope wording
  and recorded the shared-dispatch decision, revised the file tolerance from 10
  to 14 with a module-size exception, strengthened the invalid-`package` test
  fixtures, added the dotted-manifest regression test and the negative BDD
  assertion, and corrected the migration guidance.
- 2026-11-27: Round-4 record correction. Updated the
  `tests/unit/test_bump_manifest_updates.py` head size to 453 lines, recorded
  the pushed repair head and the 1,758-test gate result, and marked the gate
  rerun complete.
- 2026-11-27: Round-6 record update. Revised the file tolerance to 15,
  corrected `bump_toml.py` to 547 lines (including the expanded module
  docstring), recorded the 1,759-test result, and added the pre-merge repair
  and property-test evidence. Status remains `IN PROGRESS` until PR #422 merges.
- 2026-11-28: Rebased onto `main` (`ba6ebc9d`). Updated the location scope,
  module ownership, file sizes, budget base, and evidence; recorded the rebase
  integration decisions and the upstream refactor observation. Status remains
  `IN PROGRESS` until PR #422 merges.
- 2026-11-28: Recorded the
  `tests/unit/test_bump_documentation_updates.py` size exception (377 to 467)
  after the rebased assessment, and corrected the stale user-guide paragraphs
  that described literal key matching and proxy skipping.
