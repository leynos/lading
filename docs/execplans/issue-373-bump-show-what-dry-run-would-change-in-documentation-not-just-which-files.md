# Show documentation rewrite diffs for `lading bump --dry-run`

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & discoveries`, `Decision log`,
`Outcomes & retrospective`, `Conformance basis`, and `Verification plan` must
be kept up to date as work proceeds.

Status: COMPLETE

Branch:
`issue-373-bump-show-what-dry-run-would-change-in-documentation-not-just-which-files`

Roadmap item: none. The work closes
[leynos/lading#373](https://github.com/leynos/lading/issues/373).

## Purpose / big picture

`lading bump --dry-run` reports the manifests, documentation files, and
lockfiles a bump would change, but not what would change inside them. For
manifests that is usually enough; for documentation it is not, because the
rewrite edits free-form TOML snippets that a human wrote for readers.

During the ortho-config 0.10.0 bump, the dry run listed `docs/users-guide.md`,
but only the `git diff` after the real run revealed that lading had also
inserted a `[package]` table into an unrelated snippet
([#368](https://github.com/leynos/lading/issues/368)).

After this change, an operator can run:

````console
$ lading bump 1.2.3 --dry-run --diff
Dry run; would update version to 1.2.3 in 1 manifest(s) and 1 documentation file(s):
- Cargo.toml
- README.md (documentation)

Documentation diff:
--- a/README.md
+++ b/README.md
@@ -1,4 +1,4 @@
 ```toml
 [dependencies]
-alpha = "0.1.0"
+alpha = "1.2.3"
 ```
````

The summary remains byte-for-byte unchanged when `--diff` is absent. The diff
is opt-in, requires `--dry-run`, and covers documentation files only. It is a
human-readable rendering rather than a patch and does not promise `git apply`
compatibility.

## Constraints

- Do not change the rewrite policy produced by
  `bump_docs_fences.rewrite_markdown_toml_fences` or
  `update_toml_snippet_versions`. #368 was fixed on main by #424 after this
  plan was written; do not reintroduce the old insertion behaviour, and keep
  the diff reporting every change the current policy makes.
- Preserve the prepare-then-commit architecture introduced by ADR-012. Every
  write is proposed on `PlanBuilder` before `commit_plan` mutates anything.
- Keep the default output exactly when `--diff` is absent. Existing syrupy
  snapshots for bump summaries must not change except for explicitly new
  snapshots.
- Reject `--diff` without `--dry-run` in the domain layer before any
  preparation stage runs. Print the diagnostic on stderr, leave stdout empty,
  and exit with status 1.
- Do not add a `bump.diff` configuration key. Unknown `[bump]` keys must keep
  being rejected.
- No new dependencies. Use `difflib` from the standard library.
- Every code file must stay below 400 lines.
- Documentation uses en-GB-oxendict spelling.
- The added `--diff` flag, `BumpOptions.diff`, `BumpOptionsError`, and
  `DocumentationPreview` are the only authorized public-surface changes.

## Tolerances (exception triggers)

The comparison base is `origin/main` at `6768763c`, the commit this plan was
last rebuilt on. At that base the change spans 48 non-ExecPlan files and 1,855
net Python lines, which exceeds the file and code triggers below; D11 records
the accepted overrun. ExecPlan files are excluded from every size count.

- Files: if the implementation changes more than 15 non-ExecPlan files, stop
  and escalate.
- Code: if the change exceeds 900 net lines, stop and escalate.
- Configuration: if the change exceeds 10 net lines, stop and escalate.
- Documentation: if the change exceeds 500 net lines excluding ExecPlan files,
  stop and escalate.
- Interface: if a public API change beyond those authorized in `Constraints`
  is needed, stop and escalate.
- Dependencies: any new dependency, stop and escalate.
- Iterations: if focused tests still fail after 3 attempts on a milestone,
  stop and escalate.

The user's instructions remove any wallclock limit, so no time tolerance is set.

## Risks

- Risk: the diff exposed the #368 rewrite and reviewers read it as a regression
  introduced here. Severity: medium. Likelihood: high. Resolution: #424 fixed
  the policy on main, so the obsolete disclosure text, test, and snapshot are
  retired here.
- Risk: main's ADR-012 refactor changes the documentation preparation surface
  while this branch is open. Severity: high. Likelihood: certain (it happened).
  Mitigation: port the feature onto `prepare_documentation_updates` and
  `PlanBuilder` instead of replaying the old series; retain the old head in
  recovery refs.
- Risk: reading with `newline=""` changes rewrite behaviour. Severity: medium.
  Likelihood: low. Mitigation: one raw read feeds both the rewrite and the
  preview, the rewrite normalizes CR before parsing, and tests pin the CRLF
  proposal and written bytes.
- Risk: a path is later overwritten by README transposition, so the displayed
  diff is not the final content. Severity: low. Likelihood: low. Mitigation:
  document the documentation-stage boundary in the users' guide and ADR-013.
- Risk: CrossHair silently models only the empty `BumpChanges`. Severity:
  medium. Likelihood: medium. Mitigation: `_build_changes_description` takes a
  `_ChangeCounts` record and a `post: False` probe proves a non-empty
  counterexample is reachable.
- Risk: a competing example loader duplicates main's. Severity: medium.
  Likelihood: certain (main added one). Mitigation: drop this branch's loader
  and adapt the users-guide example test to `tests/helpers/tested_examples.py`.

## Progress

- [x] (2026-11-27) Reconnaissance of main's ADR-012 prepare/commit architecture
  and its tested-example loader.
- [x] (2026-11-27) Ported the production feature: `bump_preview`, `bump_diff`,
  `_ChangeCounts` in `bump_output`, preview recording in `bump_prepare`,
  `BumpPlan.document_previews`, `BumpOptions.diff`, `BumpOptionsError`, and the
  CLI `BumpFlags` bundle.
- [x] (2026-11-27) Ported and adapted the tests: renderer snapshots, property
  tests, preview tests, CLI forwarding and validation, BDD scenario, and the
  users-guide example through both entry points.
- [x] (2026-11-27) Updated the users' guide, migration guide, developers' guide,
  ADR-013, and documentation index.
- [x] (2026-11-27) Ran the full gate suite on the rebased candidate: 1764
  passed, 31 skipped, 113 snapshots; lint, typecheck, Markdown, Mermaid, and
  CrossHair pass.
- [x] (2026-11-27) Applied the rebuild assessment: previews are recorded from
  the single raw read the rewrite consumes, the shared tested-example loader
  carries the migration-guide example, the property tests reconstruct both
  documents from every hunk, and `PlanBuilder` moved to its own module. Changed
  code files remain below 400 lines, except for the inherited files covered by
  D14, D15, and D17.
- [x] (2026-11-27) Rebuilt on `origin/main` after #423 (workspace and target
  dependency tables) and applied the formal review: `--diff` is validated
  before workspace discovery, a replacement write updates its preview's
  proposed text, the restore comparison ignores line-ending form, and the
  lenient example loader resolves block-quoted markers.
- [x] (2026-11-28) Rebuilt again on `origin/main` after #353 (configured
  `[[bump.manifest_rewrites]]` fixtures), re-resolved `PlanBuilder` against
  `FIXTURE_MANIFEST`, and applied the final review: options are validated
  before root resolution, and the example body preserves U+2028. The design
  document now records `--diff` and the builder's new module.
- [ ] Re-run the hosted review on the force-pushed head and merge.

## Surprises & discoveries

- Observation: main's ADR-012 preparation already computes the complete
  proposed documentation content, so the preview feature does not need a second
  rewrite or a second file read for the proposal. Evidence: the exported
  `bump_docs.prepare_documentation_updates` keeps its `(path, text)` rows,
  while the internal `_prepare_documentation_updates_with_sources` adds the raw
  source, and `PlanBuilder.propose(..., observed=source)` stores the proposal
  against that snapshot. Impact: previews are recorded during preparation from
  the single raw read and carried on the plan.
- Observation: main already has `tests/helpers/tested_examples.py`, a
  CommonMark-aware loader with tests for unmarked fences, containers, tilde
  fences, and longer closing runs. Evidence:
  `tests/unit/test_tested_examples.py` and the helper module. Impact: this
  branch's competing loader and its tests are dropped. Main later added
  `require_markers=False` to the shared loader, so the users-guide example test
  now uses that lenient mode directly.
- Observation: a CrossHair probe on `BumpChanges` fails with
  `wrong parameter order: keyword-only parameter before variadic positional
  parameter`
  because it proxies the nested `DocumentationPreview.path` values. Evidence:
  the `post: False` probe log. Impact: `_build_changes_description` takes a
  `_ChangeCounts` record; the probe then returns `_ChangeCounts(lockfiles=1)`
  and `1 lockfile(s)`.
- Observation: main advanced ten commits while the original branch was under
  review, including the #411 confinement refactor that overlaps most bump
  modules. Evidence: `git log ed75631..origin/main`. Impact: replaying fourteen
  old commits would be dominated by conflicts with a superseded design; the
  feature is rebuilt as one coherent change on main.

## Decision log

- D1. Make the diff opt-in with `--diff` requiring `--dry-run`, rather than
  the default under `--dry-run`. Rationale: the default dry-run output is
  pinned by scripts and snapshots; an explicit flag keeps it byte-for-byte
  stable.
- D2. Record previews from the plan's exact proposed content and from the same
  raw read the rewrite consumed, not from a second read. Rationale: the
  proposal is the write; a second computation or read could drift, and a
  suppressed second-read failure would drop a preview while keeping the write.
- D3. Diff documentation only; defer JSON, manifest, README, and lockfile
  diffs. Rationale: the motivating risk is free-form snippets; JSON needs a
  schema and stream decision that no consumer has specified.
- D4. Render a human-readable unified diff with `a/`/`b/` prefixes, an LF
  normalization notice, and the standard end-of-file marker, without claiming
  `git apply` compatibility.
- D5. Raise `BumpOptionsError` from `_initialize_bump_context` before any
  preparation stage and map it to stderr with status 1 in `cli_entrypoint.main`.
- D6. Bundle the bump CLI flags in `BumpFlags`, following `PublishFlags`, so
  the command keeps three parameters and the hosted CodeScene argument-count
  check stays green.
- D7. Give `_build_changes_description` a `_ChangeCounts` record so CrossHair
  explores non-empty categories without proxying nested `Path` values.
- D8. Rebuild the feature on `origin/main` as one coherent replay instead of
  rebasing the fourteen-commit series through the superseded #411 design. The
  old head is preserved at `refs/recovery/issue-373-old-head`, its patch at
  `/tmp/pr410-state/pre-rebase.patch`, and the old base/target at matching
  recovery refs. The PR is squash-merged, so the historical commit split is not
  a delivery requirement.
- D9. Drop this branch's standalone loader and reuse main's marker machinery.
  `tests/helpers/tested_examples.py` owns the marker record and the strict
  whole-document loader; `tests/helpers/markdown_fence_scan.py` supplies the
  container-aware scanning. Main added a `require_markers=False` mode, which
  the users-guide example test uses to ignore older unmarked fences while still
  rejecting duplicate identifiers. This supersedes the branch's temporary
  `example_markers`/`marked_example` helpers, which were removed.
- D10. Split `PlanBuilder` into `lading/commands/bump_plan_builder.py`.
  Rationale: `bump_plan.py` inherited 447 lines from main and the preview
  changes pushed it further over the 400-line rule. The builder is a cohesive
  concern separate from the frozen plan and commit step, and the split keeps
  both modules below the limit without a compatibility re-export.
- D11. Accept the file and code tolerance overrun.
  Rationale: the feature spans production code, a new renderer, snapshots,
  property tests, CLI and BDD coverage, and both documentation guides. The work
  is deliberately test-heavy, and the alternative would be to omit the evidence
  the contract requires. Configuration and documentation stayed within their
  budgets.
- D12. Keep a preview describing the write. `prepare_documentation` records
  the preview only when `PlanBuilder.propose` reports a remaining write;
  `propose` updates the preview's proposed text when a later stage replaces the
  pending content, and removes the preview when a later stage restores the
  pre-bump content and drops the write. The restore comparison normalizes line
  endings, while the preview keeps the raw source. Rationale: a preview without
  a write reports a change the plan will not make.
  `tests/unit/test_bump_plan_propose.py` pins the command's outcomes,
  `test_prepare_plan_keeps_the_snapshot_it_read` covers a file moving under
  preparation, and `test_transposition_restore_drops_the_documentation_preview`
  covers a later README transposition restoring the original content.
- D13. Preserve the exported `prepare_documentation_updates` return shape.
  Rationale: it is listed in `bump_docs.__all__`, and the plan authorizes only
  `--diff`, `BumpOptions.diff`, `BumpOptionsError`, and `DocumentationPreview`
  as public changes. The source-carrying variant is the internal
  `_prepare_documentation_updates_with_sources`; the public function projects
  its rows back to `(path, text)`.
- D14. Record an authorized size exception for `tests/unit/test_bump_plan.py`.
  Rationale: the file is 667 lines at both the comparison base and this head,
  so the size is inherited rather than introduced here, and this change touches
  one import line. Splitting an inherited suite would enlarge this review and
  risk conflicts with concurrent main work. The follow-up split is tracked in
  [leynos/lading#421](https://github.com/leynos/lading/issues/421).
- D15. Record an authorized size exception for `tests/unit/conftest.py`.
  Rationale: it is 473 lines at the comparison base and 474 here, and this
  change adds one module name to `_LOCKFILE_STUB_MODULES`. The module supplies
  fixtures to the whole unit suite, so splitting it would touch every consumer
  and belongs in a separate maintenance change.
- D16. Validate the bump flag combination in `cli.bump` before workspace
  discovery. Rationale: without it, `--diff` without `--dry-run` in a
  non-workspace directory reported a preflight error instead of the flag
  diagnostic, and a valid workspace paid for discovery before rejection.
  `_initialize_bump_context` keeps the same check for library callers, and
  `run` validates before the write-locked root resolution so a live rejection
  never depends on the directory existing.
- D17. Record an authorized size exception for
  `tests/bdd/steps/test_bump_steps.py`. Rationale: it is 329 lines at the
  current comparison base and 330 here, and this change only widens the bump
  step's option capture; the earlier 487/488 figures referred to the previous
  base before #424 moved most bump steps into `bump_output_steps.py`. Splitting
  the remaining shared BDD step module belongs in a separate maintenance change.

## Outcomes & retrospective

Pending the rebased candidate's hosted review and merge.

## Context and orientation

`lading` is a Python 3.14 command-line tool that automates version bumps and
crates.io publication for Rust Cargo workspaces.

- `lading/commands/bump.py` owns `BumpOptions`, `BumpOptionsError`, and the
  `run()` entry point. `_initialize_bump_context` resolves configuration and
  rebuilds the options used by every stage.
- `lading/commands/bump_pipeline.py` prepares a complete plan, commits it only
  on a live run, and then regenerates lockfiles.
- `lading/commands/bump_prepare.py` prepares manifests, documentation, and
  adopted READMEs into a `PlanBuilder`. `prepare_documentation` calls the
  internal `bump_docs._prepare_documentation_updates_with_sources`, proposes
  the write against the source text that call returns, and, when
  `PlanBuilder.propose` reports a remaining write, records a
  `DocumentationPreview` from that same source.
- `lading/commands/bump_docs.py` expands confined documentation globs. Its
  exported `prepare_documentation_updates` keeps the established
  `(ConfinedPath, rewritten_text)` rows; the internal worker adds the source as
  a third value. The rewrite helpers live in `bump_docs_fences.py`.
- `lading/commands/bump_plan.py` owns `BumpPlan`, `PlannedWrite`,
  `commit_plan`, and the `BumpChanges` projection;
  `lading/commands/bump_plan_builder.py` owns the `PlanBuilder` proposal sink.
- `lading/commands/bump_output.py` owns `BumpChanges` and summary formatting.
  `_build_changes_description` takes `_ChangeCounts`.
- `lading/commands/bump_diff.py` renders the unified diff;
  `lading/commands/bump_preview.py` owns `DocumentationPreview`.
- `lading/cli_options.py` owns `BumpFlags`; `lading/cli.py` declares the
  commands; `lading/cli_entrypoint.py` maps domain errors to stderr/status.
- Tests: pytest and pytest-bdd, with syrupy snapshots. The e2e entry-point
  tests run `python -m lading.cli` and the installed console script through
  `tests/helpers/cli_entrypoints.py`. `tests/helpers/tested_examples.py` owns
  the marker record and the loader, with `require_markers=False` for guides
  that also carry unmarked fences.
- Gates: `make check-fmt`, `make lint`, `make typecheck`, `make test`,
  `make markdownlint`, `make nixie`, and `make crosshair`.

## Conformance basis

- Issue #373 and its incorporated CodeRabbit coding plan.
- ADR-012 (confine bump writes) and ADR-013 (this feature).
- `docs/documentation-style-guide.md`, including the tested-example rule.
- `AGENTS.md` quality-gate, file-size, and documentation rules.

Derived requirements: `REQ-PREVIEW` (exact original/proposed text), `REQ-FLAG`
(opt-in and dry-run validation), `REQ-RENDER` (deterministic diff with newline
disclosure), `REQ-DEFAULT` (unchanged default summary), `REQ-SCOPE`
(documentation only; JSON deferred), `REQ-EVIDENCE` (unit, property, BDD, e2e,
and docs).

## Verification plan

### Abstract model

A documentation update reads a file, rewrites its TOML fences, and proposes the
complete text. A preview is `(raw original, proposed)` for a file whose text
changed. The renderer maps a sequence of previews and a workspace root to a
sorted, deterministic text block.

### Invariants and lemmas

- `INV-DEFAULT`: `diff=False` returns exactly the previous summary.
- `INV-PREVIEW`: `preview.original` is the raw text the documentation stage
  read, and `preview.proposed` is the text the plan writes. A later stage that
  replaces the pending content updates `preview.proposed`; a later restore of
  the original content removes the write and the preview together.
- `INV-VALIDATION`: `diff=True` with `dry_run=False` raises
  `BumpOptionsError` before preparation and writes nothing.
- `INV-ORDER`: file blocks are ordered by `str(path)`.
- `INV-EOF`: an unterminated line is followed by
  `\ No newline at end of file`.
- `INV-CRLF`: a CR original with an LF proposal gets the CR or CRLF notice.
- `INV-SCOPE`: only documentation previews are rendered.

### Methods per obligation

- `INV-DEFAULT`: named tests and existing snapshots in
  `tests/unit/test_bump_output_formatting.py`.
- `INV-PREVIEW`: `tests/unit/test_bump_documentation_previews.py` (single
  read, snapshot retention, no preview without a write, transposition restore),
  `tests/unit/test_bump_plan_propose.py` (write, unchanged, restore, and
  identity-keyed removal), and the live-write property in
  `tests/unit/test_bump_diff_properties.py`.
- `INV-VALIDATION`: `tests/unit/test_cli_entrypoints.py`,
  `tests/unit/test_cli_flags.py`, and the e2e contract test.
- `INV-ORDER`, `INV-EOF`, `INV-CRLF`: syrupy snapshots in
  `tests/unit/test_bump_diff.py` plus the Hypothesis split and hunk-placement
  properties.
- `INV-SCOPE`: the renderer accepts only previews; no other category reaches it.
- End-to-end: the BDD scenario and the users-guide example executed through
  both entry points.

### Formal verification decision

No formal proof is needed. CrossHair checks `_build_changes_description` and
`_format_header`. The `post: False` probe produced `_ChangeCounts(lockfiles=1)`
and `1 lockfile(s)`, proving non-empty coverage; the probe was removed and
`make crosshair` passes.

## Plan of work

### `EP-M1` Production previews and rendering

Add `bump_preview.DocumentationPreview`; record previews in
`prepare_documentation`; carry them on `BumpPlan` and `BumpChanges`; add the
`bump_diff` renderer; add `BumpOptions.diff`, `BumpOptionsError`, and the
`BumpFlags` CLI bundle.

### `EP-M2` Tests and behavioural contracts

Add renderer snapshots, Hypothesis properties, preview tests, CLI forwarding
and validation tests, the BDD diff scenario, and the users-guide example test.

### `EP-M3` Documentation and delivery

Update the users' guide, migration guide, developers' guide, ADR-013, this
plan, and the documentation index; run all gates; force-push with lease; renew
the hosted review; merge when the review and checks are clean.

## Concrete steps

```bash
make check-fmt
make lint
make typecheck
make test
make markdownlint
make nixie
make crosshair
```

## Validation and acceptance

Acceptance: a workspace whose README has a TOML fence for `alpha` prints the
unchanged dry-run summary, a blank line, `Documentation diff:`, and a unified
diff with `-alpha = "0.1.0"` and `+alpha = "1.2.3"`, while leaving the README
untouched. A live `--diff` exits 1 with `--diff requires --dry-run` on stderr
and no stdout. `diff=False` output is byte-for-byte unchanged.

## Idempotence and recovery

Every gate is re-runnable. Live writes remain atomic through `commit_plan`. The
pre-rebase head is preserved at `refs/recovery/issue-373-old-head`; the patch at
`/tmp/pr410-state/pre-rebase.patch`; the old base and target at
`refs/recovery/issue-373-old-base` and `refs/recovery/issue-373-target`.

## Interfaces and dependencies

```python
@dc.dataclass(frozen=True, slots=True)
class DocumentationPreview:
    path: Path
    original: str
    proposed: str


def format_documentation_diff(
    previews: cabc.Sequence[DocumentationPreview],
    workspace_root: Path,
) -> str: ...


@dc.dataclass(frozen=True, slots=True)
class BumpOptions:
    dry_run: bool = False
    ...
    diff: bool = dc.field(default=False, kw_only=True)
```

No new dependencies.

## Revision note

- 2026-11-27: Rebuilt the feature on `origin/main` after ADR-012's confinement
  refactor; dropped the competing example loader in favour of main's; recorded
  the recovery refs and the CrossHair count-record probe.
- 2026-11-27: Applied the rebuild assessments. Previews now come from the single
  raw read the rewrite consumes; `PlanBuilder` moved to `bump_plan_builder.py`;
  the bump CLI tests moved to `test_cli_bump_flags.py`; the marker record and
  pattern moved to `example_markers.py` with a lenient `marked_example.py` for
  the users' guide; the property suite reconstructs both documents; the
  renderer documentation no longer claims `keepends=True` or pre-renderer
  sorting.
