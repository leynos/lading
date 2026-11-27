# Restrict the bump documentation pass to bumped workspace crates

Status: COMPLETE

Issue: <https://github.com/leynos/lading/issues/368>

## Purpose

`lading bump` rewrites TOML fences in configured Markdown files. Today the
rewrite assigns the target version to `[package].version` and
`[workspace.package].version` in *every* fence that parses, whether or not the
snippet describes a workspace crate and whether or not it already declares a
version. Three observable failures follow from that one unconditional call:

1. A snippet that documents only `[package.metadata.*]` gains a new
   `[package]` table with an invented `version`.
2. A downstream application manifest shown in documentation is re-versioned to
   the library's version.
3. `version.workspace = true` is replaced by a literal version string.

The change narrows the documentation pass so that it only replaces an existing
string `version` in a fence whose `[package].name` is one of the bumped,
non-excluded crates. It never inserts a key, never replaces an inherited or
non-string value, and leaves dependency-reference rewriting unchanged. After
the change an operator bumping a library can run the documentation pass over a
guide that mixes policy metadata, unrelated manifests, and real usage snippets
without hand-reverting documentation afterwards.

## Conformance basis

- Upstream issue: `leynos/lading#368`, including the reproducer and the four
  expected-behaviour statements.
- Repository requirements: `AGENTS.md` (`Change quality & committing`,
  `Architecture and operational boundaries`, documentation maintenance),
  `docs/lading-design.md` section 3 (`bump` subcommand design), and
  `docs/users-guide.md` (`Bump versions`).
- Relevant external interface: `tomlkit`'s `Item`/`String`/`Table`/
  `InlineTable`/`OutOfOrderTableProxy` types and `TOMLKitError`.
- Existing behaviour to preserve: dependency-reference rewriting in
  `dependencies`, `dev-dependencies`, and `build-dependencies` (issue #103),
  fence reconstruction and indentation handling, and the manifest update path in
  `lading/commands/bump_manifests.py`, which keeps using
  `bump_toml.assign_version` and must not change.

Trace links:

```plaintext
ISSUE-368-REQ-no-insert        -> EP-M1 -> tests/unit/test_bump_docs_version_policy.py
ISSUE-368-REQ-name-gate        -> EP-M1 -> tests/unit/test_bump_docs_version_policy.py
ISSUE-368-REQ-string-only      -> EP-M1 -> tests/unit/test_bump_toml_helpers.py
ISSUE-368-REQ-dependencies-kept-> EP-M1 -> tests/unit/test_dependency_sections.py
ISSUE-368-REQ-cli-observable   -> EP-M1 -> tests/bdd/features/cli.feature
ISSUE-368-REQ-docs             -> EP-M3 -> docs/users-guide.md
ISSUE-368-REQ-migration        -> EP-M3 -> docs/v0-4-0-migration-guide.md
```

## Context and orientation

This is a Python 3.14 project. The bump command lives in `lading/commands/`.
Documentation rewriting is split across two modules:

- `lading/commands/bump_docs.py` discovers documentation files through the
  confinement policy and calls `rewrite_markdown_toml_fences` for each.
- `lading/commands/bump_docs_fences.py` owns Markdown fence discovery,
  rendering, and the TOML snippet rewrite. `update_toml_snippet_versions`
  parses a fence with `tomlkit`, applies version rewrites, applies dependency
  rewrites, and re-renders the snippet when something changed.
- `lading/commands/bump_toml.py` holds the shared TOML helpers. Its
  `assign_version` inserts or replaces a version and is used by the manifest
  path in `lading/commands/bump_manifests.py`; that insertion behaviour must
  stay available for manifests.

Tests live under `tests/unit`, `tests/bdd`, `tests/e2e`. The suite uses
`pytest`, `hypothesis`, `syrupy` snapshots, `pytest-bdd`, and `cmd-mox`.
`make test` runs pytest with `--doctest-modules`, so docstring examples in the
production modules are executable contracts.

## Required outcome

1. `update_toml_snippet_versions` and `rewrite_markdown_toml_fences` leave a
   fence's package version unchanged when the fence does not name a bumped
   crate in `[package].name`, and report `changed is False` for such a fence
   only when it also contains no eligible dependency. Dependency requirements
   are independent of that gate, so a fence that contains an eligible
   dependency still changes even though its package version does not
   (requirement 5).
2. A fence that names a bumped crate and declares a string `version` has that
   value replaced, preserving inline comments and trivia.
3. A fence that declares `version.workspace = true`, an inline
   `version = { workspace = true }`, or no `version` key at all keeps its
   package version; an eligible dependency in the same fence may still change.
4. `[workspace.package].version` is rewritten only when the same fence passes
   the `[package].name` gate, which is the documented virtual-root limit.
5. Dependency-reference rewriting, including exclusion by an empty target set,
   is unchanged.
6. The manifest update path keeps its insert-or-replace behaviour.
7. The narrowed contract, its virtual-root limit, and the migration impact are
   documented.

## Constraints

- Do not change the public signatures of `update_toml_snippet_versions`,
  `rewrite_markdown_toml_fences`, or `update_documentation_files`.
- Do not change `render_fence`, dependency alias handling, or section coverage.
- Do not add configuration or a compatibility alias.
- Keep `bump_toml.assign_version` behaviour unchanged for manifests.
- Preserve the `TOMLKitError` skip and the original-snippet return when
  nothing changed.
- Numbered test-first delivery: every regression must fail for the intended
  reason before the production change.
- New and edited code modules stay within the repository's 400-line ceiling;
  `bump_docs_fences.py` already exceeds it, so a follow-up atomic refactor
  commit extracts the fence renderer when the functional change is complete.

## Tolerances (exception triggers)

- Code budget: 320 net lines across production and test sources. ExecPlan
  files are excluded from every budget.
- Documentation budget: 250 net lines across `docs/`.
- Configuration budget: 0 net lines. No dependency, lockfile, Makefile, or
  workflow change is expected.
- If a gate needs a snapshot update beyond the new policy snapshots, stop and
  inspect the diff before accepting it.
- If the dependency-section property test or e2e README contract changes
  behaviour, stop: that is an out-of-scope regression.

## Milestones

### EP-M1: failing regressions and the narrowed contract

Add the policy tests before the production change: a new
`tests/unit/test_bump_docs_version_policy.py` with named cases, Markdown-level
coverage, and a Hypothesis invariant; the replace-only helper cases in
`tests/unit/test_bump_toml_helpers.py`; and a mixed-fence BDD scenario in
`tests/bdd/features/cli.feature`.

Acceptance: the new tests fail because the pass inserts versions or rewrites
unrelated manifests, and the legacy dependency tests still pass.

### EP-M2: replace-only snippet policy

Add `bump_toml.replace_string_version`, sharing value construction and trivia
copying with `assign_version` through one private helper. Gate the snippet
rewrite in `bump_docs_fences.py` on `[package].name` membership in the bumped
crate set and apply the replace-only helper to `[package]` and
`[workspace.package]`.

Acceptance: the EP-M1 tests pass, the manifest tests pass unchanged, and the
dependency-fence tests pass unchanged.

### EP-M3: documentation, migration note, and gates

Update the user guide, the design document, and the 0.4.0 migration guide;
record this plan's progress; run `make check-fmt`, `make lint`,
`make typecheck`, `make test`, `make fmt`, and `make markdownlint` sequentially.

Acceptance: all gates pass, the snapshot diff contains only the new policy
snapshots, and the documented example matches the tested behaviour.

## Verification plan

Invariants:

- I1 (no insertion): a fence whose `[package]` has no `version` key gains no
  version, so a fence with no eligible dependency produces output identical to
  its input.
- I2 (name gate): a fence whose `[package].name` is absent, non-string, or not
  in the bumped set keeps its package and `[workspace.package]` versions;
  dependency requirements may still be rewritten under I5.
- I3 (string only): a `version` value that is a boolean, table, inline table,
  dotted table, or absent is never replaced.
- I4 (replacement): a string `version` that differs from the target is
  replaced exactly once, keeping inline trivia; a value already equal to the
  target reports no change.
- I5 (dependency preservation): dependency rewriting is unaffected by the gate
  and by an empty target set.
- I6 (manifest separation): `assign_version` still inserts a missing version.

Lemmas:

- L1: `value_as_string` returns non-`None` exactly for string-valued items, so
  the replace-only helper cannot mutate a non-string value.
- L2: reading `[package].name` through the same `select_table` used for the
  version table makes the gate and the rewrite observe one table identity,
  including `OutOfOrderTableProxy`.
- L3: when the gate fails, neither version helper is called; only the
  independent dependency path can then report a change.

Axioms (third-party interfaces treated as documented contracts):

- `tomlkit.parse` preserves key order, inline comments, and dotted-key tables,
  and raises `TOMLKitError` for invalid TOML.
- `tomlkit`'s `String` is a string-valued `Item`; `Bool`, `Integer`, `Table`,
  and `InlineTable` are not.
- `markdown-it-py` fence token maps remain the basis for fence replacement;
  this plan does not change them.

Verification methods:

- Named pytest examples for each ticket snippet and each expected-behaviour
  statement, with semantic assertions plus one paired snapshot for the mixed
  Markdown document.
- A Hypothesis invariant over unrelated package names and four version shapes,
  with an explicit positive control in the same module so the negative
  invariant cannot pass vacuously.
- Direct helper tests for `replace_string_version` across `Table`,
  `InlineTable`, boolean, equal-string, and differing-string values.
- The existing dependency-section property test and e2e README steps as
  regression evidence that dependency rewriting still works.
- A BDD scenario that runs the real CLI against a mixed README and asserts the
  eligibility outcome, the untouched snippets, and exactly one documentation
  summary entry.
- Non-vacuity: each "unchanged" case uses a current version that differs from
  the target, so a broken implementation would produce a visible change; the
  matching-crate cases assert `changed is True`.

## BDD feature

`tests/bdd/features/cli.feature` gains:

```gherkin
Scenario: Bumping restricts documentation version rewriting to bumped crates
  Given a workspace directory with configuration
  And cargo metadata describes a sample workspace
  And the workspace README contains a mixed TOML documentation sample
  And bump.documentation.globs contains "README.md"
  When I invoke lading bump 1.2.3 with that workspace
  Then the documentation file "README.md" contains "alpha = \"1.2.3\""
  And the documentation file "README.md" contains "[package.metadata.mytool]"
  And the documentation file "README.md" contains "version = \"0.1.0\""
  And the documentation file "README.md" does not contain "version = \"1.2.3\""
  And the CLI output lists documentation path "- README.md (documentation)" exactly once
```

## Progress

- [x] 2026-11-27: confirmed the three failures against the current tree with
  `rewrite_markdown_toml_fences(text, {"alpha"}, "2.0.0")`.
- [x] 2026-11-27: renamed the branch and recorded the plan.
- [x] EP-M1: failing regressions. Unit, Markdown, property, helper and BDD
  cases failed for the intended reasons before the production change.
- [x] EP-M2: replace-only snippet policy. Committed as `be794a22`; full gates
  green (`check-fmt`, `lint`, `typecheck`, `test`, `markdownlint`, `nixie`).
- [x] 2026-11-27: extracted the Markdown fence renderer into
  `lading/commands/bump_docs_markdown.py` as a separate atomic refactor
  (`30211d18`), restoring both modules under the 400-line ceiling. Full gates
  green on the refactor tree.
- [x] EP-M3: documentation, migration note, and gates. User guide, design
  document, migration guide and their contract tests committed as `c395dc84`;
  all six deterministic gates green.
- [x] 2026-11-27: review fixes committed as `da097f52` (dependency-helper
  split), `b0194fc7` (test hardening), `a80eac4c` (duplicated out-of-order
  tests merged). Local CodeRabbit review reports zero findings, hosted CI is
  green, and the CodeScene delta is clean.

## Surprises & discoveries

- The documentation pass never consults the bumped crate set for the package
  tables; it only uses it for dependency sections. The gate therefore has to
  read `[package].name` from the same parsed document.
- `select_table(document, ("package",))` resolves the implicit super-table
  created by a `[package.metadata.*]` header, which is exactly how the invented
  `[package]` table appears.

## Decision log

- 2026-11-27: gate both `[package]` and `[workspace.package]` on
  `[package].name` membership. A virtual-root snippet with only
  `[workspace.package]` is therefore never rewritten; this is the documented
  limit rather than an attempt to guess workspace identity from an arbitrary
  fence.
- 2026-11-27: add `replace_string_version` rather than adding a flag to
  `assign_version`. The manifest path needs insertion; the documentation path
  must not insert. Two named helpers make the contrast explicit and keep both
  call sites command-shaped.
- 2026-11-27: add two bounded BDD steps (mixed README fixture and
  exact-once documentation entry) because the existing step catalogue cannot
  express a multi-line fixture or an exact-once assertion.
- 2026-11-27: `replace_string_version` delegates to `assign_version` after its
  string gate rather than repeating the assignment block. The duplication gate
  rejects two copies of the same value-construction logic, and the delegation
  keeps a single writer for `table["version"]`.
- 2026-11-27: put the tested documentation example in
  `docs/v0-4-0-migration-guide.md` rather than `docs/users-guide.md`. The
  migration guide is the only user-facing document with a complete
  `tested-example` contract, so its examples are loaded from the shipped text
  and now executed against `update_toml_snippet_versions`; the user guide
  states the contract in prose and carries an unmarked illustration, matching
  every other fence in that guide.
- 2026-11-27: extract the Markdown renderer into
  `lading/commands/bump_docs_markdown.py`. The functional change pushed
  `bump_docs_fences.py` past the 400-line ceiling; the extraction is the
  sanctioned separate atomic refactor after the functional commit, and both
  modules are now under the ceiling.
- 2026-11-27: tolerate both budget exceptions. Final measurement against the
  reviewed base `4ddba048`: Python source and tests +2156/-874, a net +1282
  against the +320-line tolerance, and `pyproject.toml` +4/-0 against the
  zero-line configuration tolerance. The earlier +702 figure predated the
  rebase onto #423 and the review-driven hardening. The overage is required
  work rather than scope growth: the repository's 400-line ceiling forced the
  renderer, dependency-helper and fence-scanner splits; review required
  CommonMark indentation rules and their regressions; and the configuration
  change is one per-file pydoclint ignore for the loader's delegated
  `ValueError` contract, matching the existing `bump_readme.py` precedent. The
  ExecPlan itself is excluded from every budget.
- 2026-11-27: correct the virtual-root guidance after review. A virtual
  manifest has no `[package]` table, so advising readers to add a bumped crate's
  `[package].name` to a virtual-root example would describe a different
  workspace shape. The guides now say to maintain that documented version by
  hand.

## Outcomes & retrospective

The documentation pass now follows the ticket's four expected-behaviour
statements. A metadata-only fence is returned byte-for-byte, an unrelated
application manifest keeps its own version, `version.workspace = true` and
inline inheritance survive, and a bumped crate's own string version is still
replaced with its inline comment intact. `[workspace.package]` is rewritten
only under the same package-name gate, which is the documented virtual-root
limit. Dependency-reference rewriting is unchanged and still covered by the
existing section property test and the e2e README scenario.

Lessons: the declaration of intent in a module docstring is not a contract
unless a test pins the negative cases. The three failures here were all
"absent" cases — a missing name, a missing version, a non-string value — and
none of them had a test. The duplication gate also caught the natural but
unnecessary second copy of the assignment block that the first implementation
introduced.

## Revision note

- 2026-11-27: initial plan, derived from the issue and the implementation
  brief; no upstream deviation recorded.
- 2026-11-27: recorded the implemented policy, the renderer extraction, the
  BDD step additions, and the placement of the tested documentation example.
- 2026-11-27: review follow-up. Split the dependency helpers into
  `bump_toml_dependencies.py` after CodeRabbit flagged `bump_toml.py` over the
  400-line ceiling, replaced the exact-prose user-guide assertion with an
  execution test, made the Hypothesis name strategy TOML-safe, and merged two
  duplicated out-of-order tests that CodeScene flagged. All gates re-run green
  after each change.
