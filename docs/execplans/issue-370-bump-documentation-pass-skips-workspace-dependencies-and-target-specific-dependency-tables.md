# Cover workspace and target-specific dependency tables in the bump documentation pass

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & discoveries`, `Decision log`,
`Outcomes & retrospective`, `Conformance basis`, and `Verification plan` must
be kept up to date as work proceeds.

Status: COMPLETE

Branch:
`issue-370-bump-documentation-pass-skips-workspace-dependencies-and-target-specific-dependency-tables`

Pull request: <https://github.com/leynos/lading/pull/423>

Issue: <https://github.com/leynos/lading/issues/370>

Roadmap item: none.

## Purpose / big picture

`lading bump` rewrites crate versions in a Cargo workspace, then rewrites the
TOML code fences it finds in configured Markdown files so that the versions a
reader copies out of the documentation match the workspace. The manifest pass
covers four places a dependency can be declared: the top-level `[dependencies]`,
`[dev-dependencies]`, and `[build-dependencies]` tables, plus their
`[workspace.*]` counterparts. The documentation pass covers only the first
three.

Two reader-visible locations are therefore skipped. A fence that shows

```toml
[workspace.dependencies]
alpha = "1.0.0"
```

keeps the stale version, and so does a fence that shows a target-specific table:

```toml
[target.'cfg(unix)'.dependencies]
alpha = "1.0.0"
```

Reproduced on `main` at commit `ec5597e` with

```python
rewrite_markdown_toml_fences(text, {"alpha"}, "2.0.0")
```

both snippets report `changed=False` and keep `1.0.0`.

After this change `update_toml_snippet_dependencies` visits the same sections
as the manifest pass, `[workspace.*]` included, plus every immediate
`[target.<spec>.<section>]` dependency table. The same two snippets report a
change and render `alpha = "2.0.0"`. Manifest rewriting is untouched: target
tables inside a `Cargo.toml` are deliberately out of scope, because Cargo
applies them only to the matching target and the bump already updates the
unconditional sections that carry the same dependency.

## Conformance basis

- Upstream issue: `leynos/lading#370`, including its reproducer and expected
  behaviour.
- Repository requirements: `AGENTS.md` (failing-first bug fixes, 400-line file
  limit, quality gates, documentation maintenance), the en-GB-oxendict spelling
  rule, and `docs/developers-guide.md` section "Compatibility shims" (no
  compatibility aliases for private symbols; public names stay importable).
- Design references: `docs/lading-design.md` (bump pipeline and the
  documentation stage) and `docs/users-guide.md` ("Bump" section,
  `[bump.documentation]`).
- No ADR is required: this restores an existing documented behaviour contract
  rather than changing an architectural decision.

Trace links:

```plaintext
ISSUE-370-REQ-workspace -> EP-M1 -> tests/unit/test_bump_documentation_updates.py::test_documentation_pass_rewrites_workspace_and_target_tables
ISSUE-370-REQ-target    -> EP-M1 -> tests/unit/test_bump_documentation_updates.py::test_documentation_pass_rewrites_target_selector_variants
ISSUE-370-REQ-parity    -> EP-M2 -> tests/unit/test_bump_manifest_writing.py::test_manifest_pass_leaves_target_tables_unchanged
ISSUE-370-REQ-property  -> EP-M3 -> tests/unit/test_dependency_sections.py
ISSUE-370-REQ-behaviour -> EP-M3 -> tests/bdd/features/cli.feature
ISSUE-370-REQ-e2e       -> EP-M3 -> tests/e2e/steps/test_e2e_steps.py
ISSUE-370-REQ-docs      -> EP-M4 -> docs/users-guide.md, docs/lading-design.md
```

## Constraints

- Keep manifest behaviour, public names, and signatures stable.
  `bump_toml.update_dependency_sections` and `bump_toml.update_section` change
  only in their parameter annotations (`TOMLDocument | _TableLike`), never in
  logic, defaults, or manifest callers.
- `update_toml_snippet_dependencies` keeps the signature
  `(document, dependency_targets, target_version) -> bool` and keeps returning
  `False` immediately when `dependency_targets` is empty.
- `update_toml_snippet_dependencies` must stay importable from
  `lading.commands.bump_docs` (it is listed in that module's `__all__`) and from
  `lading.commands.bump_docs_fences` (see `Decision log`).
- No new external dependency. `tomlkit` is already a runtime dependency.
- No single code file may exceed 400 lines. `bump_toml.py` (532 lines) is
  already over the limit and must not grow; `bump_docs_fences.py` (411 lines)
  is over the limit today and must come back under it.
- Package-version handling, fence reconstruction (`render_fence`,
  `replace_markdown_fences`), and `update_documentation_files` are unchanged.
- Comments and documentation use en-GB-oxendict ("-ize"/"-yse"/"-our")
  spelling.
- No compatibility aliases for private, underscore-prefixed symbols. Public
  names are re-exported rather than renamed.
- Target traversal is confined to immediate `target` selector children.
  `[workspace.target.*]` and deeper dotted selectors are out of scope.

## Tolerances (exception triggers)

- Scope: if the implementation needs to touch more than 6 non-ExecPlan
  production or test files beyond those named in this plan, stop and escalate.
- Interface: if `update_dependency_sections` must change behaviour, or any
  public signature must break, stop and escalate.
- Behaviour: if a tomlkit 0.15.1 shape makes `[target.'cfg(unix)'.dependencies]`
  unreachable through `select_table` in a way this plan does not describe, stop
  and escalate with the probe transcript before choosing a workaround.
- Iterations: if the focused suite still fails after 3 fix attempts on any
  milestone, stop and escalate.
- Size: if `bump_toml.py` grows by more than 4 lines, or any code file must
  exceed 400 lines after this change, stop and escalate. Discharged during the
  review round: `tests/bdd/steps/test_bump_steps.py` (424 lines at base) and
  `tests/e2e/steps/test_e2e_steps.py` (528 at base) were already over the limit
  and both grew in the first pass. Rather than escalate an avoidable growth,
  the new BDD steps moved to their own module and the end-to-end assertion
  became a builder-backed comparison, so neither file is longer than at base.
- Ambiguity: if a required behaviour conflicts with the users' guide promise
  for fence rewriting, stop and escalate rather than choosing unilaterally.

Budgets, with the ExecPlan file itself excluded from every count: about 120 net
lines of code (one 90-line module, 3 annotation lines, test additions), 0 lines
of configuration, and about 80 net lines of documentation. No comparable
historical budget exists for this repository area; the estimates come from
surveying the two production modules and the four test surfaces named in the
plan of work.

## Risks

- Risk: `tomlkit` may return an `InlineTable` or a scalar for `target`, making
  traversal silently skip a shape a reader expects to be updated. Severity:
  low. Likelihood: certain for inline tables. Mitigation: the documented limits
  are asserted by unit tests and stated in the users' guide.
- Risk: a dependency entry that tomlkit parses as an `OutOfOrderTableProxy` is
  skipped by the shared updater because it is neither `Table` nor
  `InlineTable`. Severity: low. Likelihood: medium. Mitigation: this is
  pre-existing manifest behaviour, not introduced here; a test names it as a
  documented limitation.
- Risk: triplicating the section loop over target selectors could double-report
  `changed`. Severity: low. Likelihood: low. Mitigation: call the shared
  updater once per selector and combine with `changed |= ...`, never
  short-circuit `or`.
- Risk: the extended Hypothesis strategy could reject too many generated
  documents and stop exercising the interesting cases. Severity: medium.
  Likelihood: low. Mitigation: the strategy composes prefixes from a small
  finite alphabet rather than filtering, so every generated document is valid.

## Progress

- [x] (2026-11-27) Reproduced the defect against `main` in the locked
  environment (`tomlkit` 0.15.1): both ticket snippets report `changed=False`.
  Probed the `target` shapes: quoted and double-quoted `cfg(...)` selectors and
  bare triples resolve to `Table`; an out-of-order `[target]` parent resolves to
  `OutOfOrderTableProxy`; `target = { ... }` resolves to `InlineTable`, and
  `target = "..."` to a scalar, both of which `select_table` rejects.
- [x] EP-M1 (2026-11-27) Red observed: the thirteen new cases in
  `tests/unit/test_bump_documentation_updates.py` all failed with
  `AssertionError: ... assert False is True`, i.e. `changed` was `False` for
  every workspace and target location.
- [x] EP-M2 (2026-11-27) Green: `bump_docs_dependencies.py` added,
  `update_toml_snippet_dependencies` moved into it, `bump_toml` annotations
  widened, both re-exports rewired. The thirteen cases and the existing
  `test_dependency_sections.py` and `test_bump_manifest_writing.py` suites pass
  (29 tests, 6 snapshots unchanged); `make lint` reports 10.00/10 and
  `make typecheck` is clean.
- [x] EP-M3 (2026-11-27) Coverage landed: the Hypothesis property now
  generates root, `workspace`, and three target prefixes (single-quoted
  `cfg(unix)`, double-quoted `cfg(windows)`, bare triple) crossed with the
  three canonical sections, plus four decoy locations; the snapshot was
  regenerated after review. Unsupported shapes, idempotence, multi-selector
  traversal, and the `OutOfOrderTableProxy` limitation have example tests. The
  manifest guard, the `bump.run` case with its own snapshot, the BDD live and
  dry-run scenarios, and the end-to-end fixture extension all pass.
- [x] EP-M4 (2026-11-27) Documentation updated in `docs/users-guide.md`,
  `docs/lading-design.md`, and `docs/developers-guide.md`. The full gate
  sequence passed in order: `make fmt`, `make check-fmt`, `make lint`
  (10.00/10), `make typecheck`, `make test` (1755 passed, 31 skipped, 106
  snapshots), `make markdownlint`, `make nixie`.
- [x] (2026-11-27) First `coderabbit review --agent` pass returned two minor
  findings, both against `docs/users-guide.md` and both verified in the code: a
  fence with an unreachable location still rewrites a supported sibling
  location, and the out-of-order example needed a third header to reproduce the
  skip. The guide was corrected, a test now pins the sibling rewrite, and the
  gate sequence was re-run (1756 tests, 106 snapshots, lint 10.00/10).
- [x] (2026-11-27) The ExecPlan assessment (`EP-370-A1`) returned not clear with
  three findings, all verified: the plan overclaimed alias parity, the guide
  assumed an unconditional dependency always exists alongside a target entry,
  and two already-oversized test files grew. All three are addressed. New BDD
  steps moved to `tests/bdd/steps/test_documentation_steps.py`, so
  `test_bump_steps.py` is back to its pre-change 424 lines and
  `config_fixtures.py` fell from 400 to 369; the end-to-end builder now exposes
  `workspace_readme(...)`, so the assertion is a complete-text comparison and
  `test_e2e_steps.py` fell from 528 to 527. The dry-run scenario asserts
  byte-for-byte equality with the fixture, and the proxy test makes one call
  covering both the skipped entry and its rewritten sibling. The alias claim
  was corrected in the plan and the docs, and pinned by parametrized tests.
- [x] (2026-11-27) The original-issue assessment (`ISSUE-370-A1`) returned not
  clear with five findings. Four were new: an inline dependency *section*
  (`dependencies = { ... }`) is skipped and was undocumented; two overclaims
  remained in prose (the module docstring promising the fence and manifest
  "cannot drift apart", and "other crates … left untouched" in the guide); no
  test proved an out-of-order `target` parent is still traversed; and the same
  size finding. All are addressed: the inline-section shape is documented,
  distinguished from a supported inline entry, and pinned in the
  unsupported-shapes parametrization; both prose claims are corrected; and
  `test_documentation_pass_rewrites_under_out_of_order_target_parent` proves
  the positive case. The assessment also retracted its earlier alias-parity
  demand, confirming the key-matching contract stands.
- [x] (2026-11-27) The ExecPlan follow-up confirmed findings 1 and 2 resolved
      and
  raised three more, all addressed. `tests/e2e/steps/test_e2e_steps.py` was
  still over 400 lines even though it no longer grew, so it was split: the
  publish steps moved to `tests/e2e/steps/test_e2e_publish_steps.py` and the
  shared CLI runner became `e2e_steps_helpers.run_lading_in_e2e_workspace`,
  leaving 371 and 163 lines. The dry-run step now compares `read_bytes()`,
  because `read_text` normalizes line endings. The module docstring now
  separates what the two passes share from what is documentation-only.

## Surprises & discoveries

- `bump_docs_fences.py` is 411 lines on `main`, eleven lines over the 400-line
  limit, and `bump_toml.py` is 532. Moving `update_toml_snippet_dependencies`
  out of the former repairs it as a side effect; the latter must not grow
  beyond annotation changes.
- The plan supplied with the issue describes the top-level-only loop as living
  in `bump_docs.py`. It actually lives in `bump_docs_fences.py`, which
  `bump_docs.py` re-exports. Moving the function satisfies the instruction
  without touching fence reconstruction.
- `tomlkit` 0.15.1 creates the same `Table` for `[target.'cfg(unix)'.x]` and
  `[target."cfg(windows)".x]`; the quoting style is not observable after
  parsing, so one code path covers both. The locked version is 0.15.1, while
  the ticket's probes used 0.15.2; the shapes above were re-confirmed under
  0.15.1.
- `[target.'cfg(unix)'.dependencies]` placed before a bare `[target]` header
  produces an `OutOfOrderTableProxy` for `target`, which `select_table`
  accepts. Key iteration and item assignment both work on the proxy.
- An `OutOfOrderTableProxy` *section* still updates its plain scalar entries,
  so the limitation is narrower than first assumed: only a dependency **entry**
  that resolves to a proxy is skipped. That needs a third header reopening the
  entry, for example `[target.'cfg(unix)'.dependencies.alpha]`, then
  `[target.'cfg(unix)'.dependencies]`, then
  `[target.'cfg(unix)'.dependencies.alpha.package]`. Without the third header
  `tomlkit` yields a plain `Table` and the entry is rewritten normally. The
  first probe of this shape only covered the workspace case and misreported
  which entry was skipped; the target case was measured directly before the
  test was written.
- The first draft of the users' guide said a skipped shape left the whole fence
  unchanged. It does not: the root and workspace passes run before the target
  traversal, so a fence holding both an unreachable `target` value and a
  supported `[dependencies]` table still changes. The guide now scopes the
  promise to the location, and a test pins the behaviour.
- The regenerated snapshot caught a defect in the new property fixture: the
  decoy section names were appended to `_DECOY_LOCATIONS` as bare strings, so
  `"".join` split them into characters and rendered
  `[d.e.c.o.y.-.d.e.p.e.n.d.e.n.c.i.e.s]`. The property still passed, because
  the corrupted table was correctly left alone. Reviewing the snapshot diff,
  not the green run, is what surfaced it.

## Decision log

- Decision: put the traversal in a new module
  `lading/commands/bump_docs_dependencies.py` and move
  `update_toml_snippet_dependencies` into it, re-exporting the function from
  `bump_docs.py` and `bump_docs_fences.py`. Rationale: `bump_docs_fences.py` is
  already over the 400-line limit, and the new logic is documentation-scoped
  table location, which is a different responsibility from fence
  reconstruction. Both re-exports are public names already listed in `__all__`,
  so they are re-exports rather than compatibility shims for private symbols.
  Date/Author: 2026-11-27, implementation session.
- Decision: reuse the unchanged `update_dependency_sections` rather than
  writing a documentation-specific section updater. Rationale: the manifest
  pass and the documentation pass must agree on section vocabulary and
  requirement-operator composition; sharing the function makes disagreement
  structurally impossible. The only widening is the annotation that lets a
  table be passed where a document is passed today. Date/Author: 2026-11-27,
  implementation session. Corrected 2026-11-27 after review: the shared updater
  matches dependency **keys** literally in both passes, but only the manifest
  pass maps a bumped crate name to its manifest alias (via
  `dependency.manifest_name`, derived from Cargo metadata). The documentation
  pass is handed crate names and has no metadata, so alias resolution is
  manifest-only. That asymmetry predates this change; the review comment
  claiming alias parity was about this plan's wording, not about the new
  traversal, and both behaviours are now pinned by tests and stated as limits
  in the users' guide.
- Decision: do not add alias resolution to the documentation pass. Rationale:
  the issue asks for the same *sections* as the manifest pass, not for
  crate-to- key mapping, and resolving aliases would require threading the
  workspace graph into the documentation stage — a public-surface change the
  plan's constraints forbid. The current behaviour is pinned by
  `test_documentation_pass_matches_fence_keys_literally` and documented.
  Date/Author: 2026-11-27, review-response session.
- Decision: traverse `target` one level only. Rationale: Cargo's own grammar
  puts a target spec directly under `target`, and a deeper traversal would
  rewrite tables whose meaning this tool cannot verify. Skipping is silent, in
  keeping with the manifest pass, which likewise ignores tables it does not
  model. Date/Author: 2026-11-27, implementation session.
- Decision: widen `bump_toml._update_named_sections` as well as the two public
  functions named in the plan. Rationale: `update_dependency_sections` forwards
  its `document` argument to that helper, so widening only the public signature
  would leave a type error at the forward. The change is still annotation-only
  and adds no executable line. Date/Author: 2026-11-27, implementation session.
- Decision: keep `update_toml_snippet_dependencies` importable from
  `bump_docs_fences` rather than dropping it from that module's `__all__`.
  Rationale: it is a public name on both modules today and the repository's
  compatibility policy forbids removing public re-exports without a migration;
  the module-level import costs one line and preserves the documented surface.
  Date/Author: 2026-11-27, implementation session.
- Decision: leave manifest target tables alone and guard that with a test.
  Rationale: the ticket is about documentation; extending manifest rewriting to
  target tables would change `lading bump` output for existing users and is not
  requested. Date/Author: 2026-11-27, implementation session.

## Verification plan

Obligations and their discharge:

1. Invariant: after the pass, every `alpha` entry in a canonical location is
   rewritten to the target version, and every non-target key is untouched.
   Method: the extended Hypothesis property in
   `tests/unit/test_dependency_sections.py`, which composes root, `workspace`,
   and up to two target selector prefixes, plus decoy sections. Non-vacuity:
   the strategy includes `st.just(())` for the empty prefix set and at least
   one nested location, so both "nothing canonical present" and "only nested
   location present" are reachable; existing decoy names stay in the alphabet.
2. Invariant: `changed` is `True` exactly when at least one canonical location
   is present. Method: the same property asserts `changed is bool(canonical)`.
   Non-vacuity: the strategy can generate documents with only decoy sections,
   which must report `False`.
3. Invariant: manifest rewriting does not gain target-table support as a side
   effect. Method:
   `tests/unit/test_bump_manifest_writing.py::test_manifest_pass_leaves_target_tables_unchanged`.
   Non-vacuity: the same fixture asserts that the unconditional
   `[dependencies]` entry in the same document *is* rewritten, so a function
   that rewrote nothing would fail.
4. Invariant: unsupported shapes are skipped without error and without
   changing text. Method: parametrized examples for scalar and inline `target`
   values, inline selector values, a deeper dotted selector, and malformed
   TOML; each asserts `changed is False` and identical text. The
   `OutOfOrderTableProxy` limitation gets its own test, which asserts the proxy
   entry is skipped and a plain sibling entry in the same section is rewritten,
   so a function that reached nothing would fail. Non-vacuity: the sibling
   assertion pins the positive direction, and the shared helper is exercised by
   fifteen supported locations elsewhere in the module.
5. Lemma: repeated application is idempotent. Method: an example test applying
   `rewrite_markdown_toml_fences` twice and asserting the second call reports
   `changed is False` with identical text.

Axioms relied on: `tomlkit` 0.15.1 parses `[target.'<spec>'.<section>]` into a
`Table` whose child key is the unquoted spec text; `tomlkit` raises
`TOMLKitError` for malformed TOML, which `update_toml_snippet_versions` already
turns into an unchanged snippet. Both are exercised by the probes recorded in
`Progress` and by the malformed-fence example test.

## Context and orientation

lading is a Python 3.14, `uv`-managed command-line tool. The relevant modules
are:

- `lading/commands/bump_toml.py` owns the shared TOML primitives. Its
  `DEPENDENCY_SECTIONS` constant is the canonical vocabulary (`dependencies`,
  `dev-dependencies`, `build-dependencies`).
  `update_dependency_sections(document, mapping, target_version, *,
  include_workspace_sections=False)`
  applies a section-to-names mapping to the document, optionally repeating it
  under `workspace`. `select_table(document, keys)` resolves a key path to a
  `Table` or `OutOfOrderTableProxy`, returning `None` for anything else.
- `lading/commands/bump_docs_fences.py` owns Markdown fence discovery and
  rendering, and holds `update_toml_snippet_dependencies`,
  `update_toml_snippet_versions`, and `rewrite_markdown_toml_fences`.
- `lading/commands/bump_docs.py` owns filesystem discovery
  (`prepare_documentation_updates`, `DocumentationUpdateRequest`) and
  re-exports the fence helpers.
- `lading/commands/bump_manifests.py` is the manifest pass; it calls
  `update_dependency_sections` for package sections and
  `update_workspace_dependency_sections` for workspace sections.
- Tests: `tests/unit/test_bump_documentation_updates.py` drives `bump.run` and
  documentation updates; `tests/unit/test_dependency_sections.py` holds the
  section-vocabulary property test and snapshot;
  `tests/unit/test_bump_manifest_writing.py` holds manifest render tests.
  Behavioural tests are `pytest-bdd` scenarios in
  `tests/bdd/features/cli.feature` with steps in `tests/bdd/steps/`. End-to-end
  tests build a real workspace in `tests/e2e/helpers/workspace_builder.py` and
  assert in `tests/e2e/steps/test_e2e_steps.py`.

Quality gates are Makefile targets run sequentially: `make fmt`,
`make check-fmt`, `make lint`, `make typecheck`, `make test`,
`make markdownlint`, `make nixie`.

## Plan of work

EP-M1 (red). Add reproducer tests to
`tests/unit/test_bump_documentation_updates.py` at the
`rewrite_markdown_toml_fences` boundary. Call it with each ticket snippet, the
target set `{"alpha"}`, and `"2.0.0"`; assert `changed is True` and that the
returned text contains `alpha = "2.0.0"`. Parametrize the three workspace
sections, quoted `cfg(...)` selectors including
`[target.'cfg(unix)'.dependencies]`, a double-quoted selector such as
`"cfg(windows)"`, a bare triple such as `x86_64-unknown-linux-gnu`, and all
three target section variants. Run the file and record the failure.

EP-M2 (green). Create `lading/commands/bump_docs_dependencies.py`, move
`update_toml_snippet_dependencies` and its private helper into it, and add the
target traversal. Widen the two annotations in `bump_toml.py`. Update
`bump_docs_fences.py` and `bump_docs.py` imports. Rerun the EP-M1 tests.

EP-M3 (coverage). Extend the property strategy and snapshot; add the
supported/unsupported shape examples, the idempotence test, the entry-proxy
limitation test, and the manifest guard. Add the `bump.run` case, the BDD live
and dry-run scenarios, and the end-to-end fixture extension.

EP-M4 (documentation and gates). Update `docs/users-guide.md`,
`docs/lading-design.md`, and `docs/developers-guide.md`; run the full gate
sequence in order.

## Validation and acceptance

Observable acceptance: with `bump.documentation.globs` configured and a README
containing a `[workspace.dependencies]` fence and a
`[target.'cfg(unix)'.dependencies]` fence, running `lading bump 1.2.3` rewrites
both fences to `1.2.3` and lists the README with the `(documentation)` suffix.
In dry-run the README is unchanged but still listed.

Commands, run in this order:

```bash
make fmt
make check-fmt
make lint
make typecheck
make test
make markdownlint
make nixie
```

Focused runs:

```bash
uv run pytest -v tests/unit/test_dependency_sections.py \
  tests/unit/test_bump_documentation_updates.py \
  tests/bdd/steps/test_common_steps.py tests/e2e/test_e2e.py
```

## Artefacts and notes

Probe transcript on the locked environment (`tomlkit` 0.15.1), before any code
change:

```plaintext
question: what does rewrite_markdown_toml_fences return for the two ticket snippets?
answer:   changed=False for both, versions stay "1.0.0"

question: what type is document["target"] for each selector spelling?
answer:   [target.'cfg(unix)'.dependencies]          -> Table
          [target."cfg(windows)".dev-dependencies]   -> Table
          [target.x86_64-unknown-linux-gnu...]       -> Table
          sub-tables before a bare [target] header   -> OutOfOrderTableProxy
          target = { ... }                           -> InlineTable (rejected)
          target = "nonsense"                        -> String (rejected)
          [target] with inline selector value        -> InlineTable child (rejected)
```

## Outcomes & retrospective

Delivered as planned. `update_toml_snippet_dependencies` now visits the
top-level dependency sections, their `[workspace.*]` counterparts, and every
immediate `[target.<spec>.<section>]` table. The two snippets from the issue
report a change and render `alpha = "2.0.0"`. `bump_toml` gained only
annotation widening across three signatures, so manifest behaviour, public
names, and defaults are unchanged; a new guard test pins that a target-specific
table inside a `Cargo.toml` keeps its version. `bump_docs_fences.py` fell from
411 to 372 lines, under the 400-line limit, and the new module is 112 lines.

The failing-first requirement was met before any production change: thirteen
parametrized cases at the `rewrite_markdown_toml_fences` boundary failed with
`changed is False` and were transcribed into `Progress`.

Verification evidence: the property test exercises fifteen canonical locations
and four decoys across forty generated documents per run; the snapshot pins the
full rendered rewrite; the two BDD scenarios cover live and dry-run behaviour
with mocked Cargo and git; and the end-to-end suite rewrites a real README
through the real CLI. `make test` ran 1755 tests with 106 snapshots.

Lessons: (1) a snapshot regenerated for a new fixture must be read, not just
accepted — the character-split decoy header passed every assertion; (2) probing
one nesting level (workspace) does not predict another (target) when `tomlkit`
proxy behaviour is involved, so each documented limitation needs its own
measurement; (3) the df12 pylint tier rejects four or more substring assertions
in one test and requires a reason on every type-check suppression, which pushes
test assertions toward structure rather than text.

## Revision note

Initial draft, 2026-11-27.

Revision 2, 2026-11-27: marked `COMPLETE` once implementation, coverage, and
documentation were delivered and the full gate sequence passed, and recorded
the pull request. No scope change.
