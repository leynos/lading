# Confine every `lading bump` write target and validate the complete plan before mutation

Status: IN PROGRESS

Issue: <https://github.com/leynos/lading/issues/409>

Downstream consumer: <https://github.com/leynos/ortho-config/issues/438>

## Purpose

`lading bump` must never rewrite a file outside the workspace it was asked to
operate on. Today the bump pipeline discovers member manifests, documentation
files, README destinations, and lockfile destinations in several independent
places, and some validation happens only after earlier stages have already
written files. A symlinked crate directory, a symlinked `Cargo.toml`, or a
documentation glob such as `../outside/README.md` therefore lets a bump modify
an external sentinel, and a malformed lockfile-manifest path can fail after
manifests have already been rewritten.

This plan makes the bump command build and validate one complete write plan
before it mutates anything. The write plan covers the root manifest, every
member manifest, configured documentation, README-adoption destinations, and
lockfile-related manifests and destinations. Validation proves two things for
every target: the target is lexically inside the canonical workspace root, and
every filesystem component between the root and the target is a real directory
or a regular file rather than a symlink or special file. The write phase
consumes only the validated plan.

After this change an operator can run `lading bump` on a workspace containing a
hostile or accidental symlink and receive a nonzero exit status with a
diagnostic naming the rejected input, while every intended target and every
external sentinel remains byte-for-byte unchanged.

## Conformance basis

- Upstream issue: `leynos/lading#409`, including its required outcomes and
  acceptance tests.
- Downstream requirement: `leynos/ortho-config#438`, which selects Lading as
  the owner of the confinement behaviour rather than duplicating a
  repository-specific bump helper.
- Repository requirements: `AGENTS.md` (quality gates, documentation
  maintenance, architecture boundaries), `docs/lading-design.md` section 3
  (`bump` subcommand design), and `docs/developers-guide.md`.
- The existing lockfile containment checks in
  `lading/commands/bump_lockfile_paths.py` are retained, not replaced.
- `#353` / PR `leynos/lading#353` (configured non-member manifests) is open,
  not merged. This plan documents the extension hook instead of routing
  `[[bump.manifest_rewrites]]` through it.

Trace links:

```plaintext
ISSUE-409-REQ-confinement -> EP-M1 -> tests/unit/test_confinement.py
ISSUE-409-REQ-discovery   -> EP-M2 -> tests/unit/test_confined_discovery.py
ISSUE-409-REQ-plan        -> EP-M3 -> tests/unit/test_bump_plan.py
ISSUE-409-REQ-evidence    -> EP-M4 -> tests/unit/test_bump_preflight_cli.py
ISSUE-409-REQ-migration   -> EP-M5 -> docs/users-guide.md
```

## Context and orientation

The repository is a Python project. The bump command lives in
`lading/commands/`. `bump.run()` normalizes the workspace root, loads the
workspace graph, and hands a `_BumpContext` to `bump_pipeline._run_pipeline()`.
That pipeline currently processes the workspace manifest, then member
manifests, then documentation, then README adoption, then lockfiles; each stage
both computes and writes its own changes.

Relevant modules:

- `lading/commands/bump.py` — command entry point and context construction.
- `lading/commands/bump_pipeline.py` — ordered update stages.
- `lading/commands/bump_manifests.py` — manifest parsing and rewriting.
- `lading/commands/bump_docs.py` — documentation glob expansion and rewriting.
- `lading/commands/bump_readme.py` — workspace README adoption.
- `lading/commands/bump_lockfiles.py` and its helper modules — lockfile
  projection and regeneration.
- `lading/workspace/graph_build.py` — `cargo metadata` to workspace graph.
- `lading/workspace/metadata.py` — the `cargo metadata` invocation.
- `lading/cli.py` — the composition root that selects the command runner and
  loads the workspace graph before dispatch.
- `lading/utils/path.py` — the existing root normalizer.

Tests are organized under `tests/unit`, `tests/integration`, `tests/bdd`, and
`tests/e2e`. Shared helpers live in `tests/helpers`. The suite uses `syrupy`
snapshots, `pytest-bdd`, `cmd-mox` for command stubs, and real temporary
directories. `make test` runs pytest with `--doctest-modules`.

The repository enforces a 400-line ceiling for new and edited code modules, a
maximum of four positional arguments (Ruff) or five (Pylint), Ruff complexity
8, and en-GB-oxendict spelling in comments and documentation.

## Required outcome

1. Establish one canonical workspace root for the whole run. Validate the
   original `[workspace].members` and `[workspace].exclude` patterns before
   expansion or Cargo discovery. Reject empty, absolute, rooted,
   drive-qualified, UNC, and parent-traversing patterns under both POSIX and
   Windows spellings.
2. Inspect lexical components below the canonical root with `lstat` before any
   resolution. Reject symlinked ancestors, final symlinks, dangling links,
   in-workspace symlink aliases, and non-regular existing targets. Prove
   canonical containment as a second, independent check. A root reached through
   a symlink in its own ancestry remains valid.
3. Build and validate the complete write plan before the first mutation:
   root and member manifests, configured documentation, README destinations,
   and lockfile manifests and destinations.
4. Validate parent directories and containment for intended new files (README
   adoption). Distinguish an absent optional target from an unreadable or
   unsafe one. Never open a FIFO or other special file to discover its type.
5. Write only from the validated plan. Do not rediscover or broaden the target
   set after preflight.
6. Fail with a nonzero status and an actionable diagnostic naming the rejected
   input. On any preflight failure, leave every intended target and external
   sentinel unchanged.
7. Specify and test the read-only discovery and dry-run contract. Keep runtime
   lockfile regeneration after a successful preflight. Distinguish later
   execution failures from validation failures.
8. Document the missing-member, unmatched-glob, and disabled-rebuild policies
   and their migration implications.

Explicit non-goals: full rollback after a write or subprocess failure, and
protection against a concurrent adversary that changes paths between validation
and writing. Per-file atomic replacement remains the only write-atomicity
guarantee.

## Constraints

- No new runtime dependency is introduced. `tomlkit` was already declared at
  `tomlkit>=0.13,<0.16`, and this change raises its floor to `tomlkit>=0.15`
  because Cargo accepts TOML 1.1 syntax that `tomllib` rejects; the decision
  log records the version change. The rest of the policy uses `os.lstat`,
  `os.scandir`, `fnmatch`, `pathlib`, and `stat` from the standard library.
- Publish discovery and publish behaviour must not change. Only the bump path
  gains the confined loader and `--no-deps` metadata call.
- Existing public APIs and behaviour that tests pin (output format, change
  ordering, lockfile aggregate errors) must be preserved unless this plan says
  otherwise.
- New and edited production modules stay at or below 400 lines.
- All code, documentation, and commit text uses en-GB-oxendict spelling.
- Every `assert` in tests carries a diagnostic message that states the failed
  expectation.
- No `open()` call is made on a target merely to classify it.

## Tolerances (exception triggers)

- Stop and escalate if a required behaviour cannot be implemented without
  changing the publish command's discovery path.
- Stop and escalate if real Cargo mutates workspace state under
  `cargo metadata --no-deps` and no documented read-only alternative exists.
- Stop and escalate if a new module would exceed 400 lines after a reasonable
  split, because that indicates the decomposition is wrong.
- Stop and escalate if preserving an existing pinned output requires
  reintroducing an unconfined discovery path.
- Stop and escalate before changing any file outside `lading/`, `tests/`, and
  `docs/` other than `pyproject.toml` for the release bump.

## Milestones

### EP-M1: confinement policy and preflight diagnostics

Create `lading/utils/confinement.py` with:

- `BumpPreflightError`, `UnsafeBumpTargetError`, `BumpTargetExaminationError`,
  and `MissingBumpTargetError`. Each message names the rejected configuration
  key or path and the reason.
- `ConfinedPath`, a frozen value carrying the canonical root, the lexical
  components, and the absolute path.
- `establish_workspace_root()`, which expands `~`, resolves strictly, requires
  a directory, permits symlinks in the root's own ancestry, and reports a
  missing root as a preflight error.
- `validate_relative_pattern()`, which rejects empty, absolute, rooted,
  drive-qualified, UNC, and `..` patterns under POSIX and Windows spellings.
- `confine_relative_path()`, which walks components with `os.lstat`, rejects
  symlinks and junctions, requires real directories for parents and a regular
  file for an existing final component, allows an absent final component only
  when `may_create` is set, and proves canonical containment.
- `expand_confined_glob()`, which expands patterns component by component with
  `os.scandir`, `fnmatch.fnmatchcase`, and a non-following recursive `**`
  descent; rejects symlink matches and special files; ignores directory matches
  for file patterns; surfaces scan errors; and returns sorted root-relative
  paths.

Map `BumpPreflightError` in `lading/cli.py` to a dedicated `Preflight error:`
stderr prefix and exit status 1.

Acceptance: `tests/unit/test_confinement.py` covers lexical rejection, symlink
and dangling-link rejection, in-root aliases, FIFOs (with a capability skip),
injected junction predicates, exact-call permission failures, absence versus
examination errors, symlinked root ancestry, pattern expansion, and a `open()`
sentinel proving no special file is opened.

### EP-M2: confined, read-only bump discovery

Create `lading/workspace/confined_discovery.py` with
`load_bump_workspace(root, *, runner=None)`. The loader validates and parses
the root `Cargo.toml`, coerces `[workspace].members` and `[workspace].exclude`
with `validate_string_sequence`, validates and expands member patterns, applies
exclusions, and rejects missing literals, unmatched globs, and matched
directories without a regular `Cargo.toml`. It supports virtual and
root-package workspaces.

Add keyword-only `no_deps: bool = False` to
`lading/workspace/metadata.py:load_cargo_metadata()` and call it with
`no_deps=True` from the bump loader. Require Cargo's reported workspace root to
match the canonical root and suggest `--workspace-root` on mismatch.

Validate every reported member manifest path (including implicit members and
the root package) before `build_workspace_graph()` reads any manifest. Require
lexical containment, a `Cargo.toml` basename, and a confined existing regular
file. Preserve `WorkspaceModelError` for missing member identifiers.
Cross-check explicit members against Cargo's result and allow validated
implicit extras.

In `lading/commands/bump.py`, establish the canonical root first, use the
loader when no graph is injected, and validate injected graph roots and crate
manifest paths.

Add a loader callable to `lading/cli.py:_run_with_context()` with
`load_workspace` as its default; pass `load_bump_workspace` only for the CLI
bump command. Update the CLI dispatch and the entrypoint test seams.

Acceptance: `tests/unit/test_confined_discovery.py` and updated CLI tests pass.
Publish tests remain green and publish discovery is unchanged.

### EP-M3: one immutable plan, prepared then committed

Create `lading/commands/bump_plan.py` with frozen `PlannedWrite` and `BumpPlan`
models, and a category enum covering manifests, documentation, README
destinations, and lockfiles. Proposals are keyed by confined absolute path, and
later stages compose from earlier proposed content. A path present in both
documentation and README categories keeps both categories.

Rewrite `bump_pipeline` into `_prepare_plan(context)`, `_commit_plan(plan)`,
and gated regeneration. Preparation covers:

- Pure manifest preparation with root and member selectors applied to one
  document when the root is also a member, preserving exclusion behaviour.
- Documentation preparation with confined glob expansion. Unmatched globs are
  no-ops; read and decode failures for selected files are preflight errors.
- Lazy README adoption when members opt in, using proposed source contents and
  validating destinations as may-create. `ReadmeTranspositionError` remains the
  error for a missing required source or an external crate root.
- Lockfile planning. Extend `LockfileRepository` with `plan_lockfiles()` and
  `regenerate_planned()` while retaining the existing methods and façade
  exports. Discover Git manifests once, merge and validate all manifests
  including the root, query Cargo with `--no-deps --manifest-path` for each
  effective workspace root, and validate and deduplicate the resulting
  `Cargo.lock` destinations. Reject an inherited `CARGO_RESOLVER_LOCKFILE_PATH`.

Commit writes in deterministic root-manifest, member-manifest, documentation,
README order, then regenerates lockfiles when the rebuild-and-changed-manifest
gate is satisfied. Dry runs report the prepared plan without writing or running
regeneration.

Acceptance: `tests/unit/test_bump_plan.py` plus updated bump tests pass.
Dry-run and live runs report the same change set, and no write occurs before
the last preflight check.

### EP-M4: command-level and real-Cargo evidence

Add `tests/helpers/fs_snapshot.py` (non-following tree snapshots with
`os.scandir`, `lstat`, regular-file bytes, and `readlink` targets) and a
recording `CommandRunner` helper. Add `tests/unit/test_bump_preflight_cli.py`
with a valid member ordered before each invalid input. Cover unsafe member
patterns, documentation, README and lockfile targets, special files, and
examination failures. Assert named diagnostics, exit status 1, unchanged
workspace and sentinel snapshots, no temporary files or created lockfiles, no
Cargo updates, and no metadata call for pre-Cargo rejections.

Add live and dry-run success tests, a root-member single-write test,
overlapping documentation and README targets, equal summaries, and exact
`--no-deps` metadata arguments. Add a BDD scenario for lockfile rejection and
update repository doubles.

Add a real-Cargo integration lane with availability skip, a 60-second timeout,
an isolated `CARGO_HOME`, offline mode, and path dependencies. Cover missing
and stale lockfiles, unchanged dry-run snapshots, metadata field parity,
member-pattern outcomes, and root, member, and nested-workspace lockfile
destinations.

Acceptance: the new tests fail before the implementation and pass after it;
`make test` passes.

### EP-M5: documentation, migration, and release hand-off

Create this ExecPlan (done), add an ADR for confinement, `--no-deps` discovery,
lockfile destinations, and prepare-then-commit, and index both in
`docs/contents.md`. Update `docs/users-guide.md` with the member-pattern and
exclusion policy, symlink and non-regular rejection, documentation-read
failures, effective workspace lockfile destinations, environment rejection, the
read-only discovery and dry-run contract, and the validation, rollback,
concurrency, and Cargo-config redirect limits. Add migration notes for each
behaviour change and correct the rerun advice where a same-version rerun skips
regeneration. Update the design and developer guides and the roadmap. Document
the non-member manifest extension hook.

Run `make check-fmt`, `make lint`, `make typecheck`, `make test`,
`make markdownlint`, and `make nixie` before review. Bump `pyproject.toml`,
follow the documented release flow, push, and open a draft pull request. Record
the first fixed version and immutable commit on the issue.

Acceptance: every gate passes; the PR links #409, this plan, and the downstream
issue; the release version and commit are recorded.

## Missing-member and unmatched-glob policy

- A literal member pattern that does not exist is an error naming the pattern.
- A glob member pattern that matches nothing is an error naming the pattern.
- A member pattern match that is a regular file is ignored, as Cargo does; a
  matched special file is an error.
- An exclusion is a literal path prefix and is not expanded as a glob; a glob
  member match beneath an excluded directory is excluded, while a wildcard in
  `[workspace].exclude` names a literal directory. An explicitly listed member
  keeps Cargo's precedence.
- A member directory without a regular `Cargo.toml` is an error naming the
  directory.
- A documentation glob that matches nothing is a no-op.
- A documentation glob matching a directory ignores that directory.
- `[workspace].exclude` entries are validated lexically and treated as
  literal path prefixes; an exclusion that names no directory is not an error.
- Configured lockfile manifests are validated whenever lockfile regeneration is
  enabled for the run. A disabled rebuild ignores them, as before.

## Migration implications

- A workspace whose members were reachable only through a symlinked ancestor
  below the root now fails instead of being bumped. Move or copy the member
  into the workspace, or make the path real.
- Documentation globs that escape the workspace now fail instead of silently
  updating an external file. Narrow the glob to the workspace.
- A selected documentation file that cannot be read or decoded now fails the
  run instead of being skipped with a warning.
- Configured lockfile manifests are resolved to the effective workspace root
  Cargo reports, so a nested manifest that belongs to the outer workspace
  reports the outer `Cargo.lock` instead of an adjacent lockfile.
- A same-version rerun still skips manifest writes and therefore skips lockfile
  regeneration; repair a stale lockfile explicitly with
  `cargo update --workspace --manifest-path <manifest>` rather than assuming a
  rerun will fix it. A single regeneration failure re-raises the original Cargo
  error; only an aggregated multi-manifest failure prints one repair command
  per failed manifest.
- `CARGO_RESOLVER_LOCKFILE_PATH` must be unset for bump runs.

## Verification plan

Invariants:

- I1 (lexical): every accepted pattern is relative, non-traversing, and
  non-empty under both POSIX and Windows spellings.
- I2 (physical): every accepted existing target has no symlink or non-directory
  ancestor below the canonical root, and its final component is the expected
  kind (regular file, or absent for may-create).
- I3 (containment): the canonical form of every accepted target is inside the
  canonical workspace root.
- I4 (plan completeness): the write phase never receives a path that was not
  validated during preparation.
- I5 (no mutation on rejection): a preflight failure leaves workspace and
  sentinel snapshots unchanged.
- I6 (read-only discovery): bump discovery with `--no-deps` does not create or
  modify files for missing or stale lockfiles.

Lemmas:

- L1: a component walk that rejects symlinks and non-directories at every
  ancestor makes the final canonical path equal the lexical path.
- L2: a `**` descent that examines entries with `scandir` and never follows
  symlinks cannot produce a match outside the root.
- L3: composing README content from the proposed root README keeps the plan
  consistent when the root README is also a documentation target.

Axioms (external interfaces treated as documented contracts):

- `os.lstat` reports symlinks without following them.
- `os.scandir` with `entry.is_symlink()` and `entry.stat(follow_symlinks=False)`
  does not follow links.
- `cargo metadata --no-deps` in a workspace does not resolve dependencies and
  therefore does not need to write `Cargo.lock`; this is exercised by the
  real-Cargo lane rather than assumed.
- `Path.replace()` replaces the directory entry rather than following a final
  symlink; the symlink rejection is what prevents the preceding resolution from
  turning a final symlink into an external target.

Verification methods:

- Parameterized pytest tables for lexical partitions and pattern shapes.
- Exact-call monkeypatched `lstat`/`scandir` failures for examination errors.
- `os.mkfifo` with a capability skip for special-file rejection, plus an
  `open()` sentinel.
- Snapshot-based command tests with external sentinels for I5.
- A recording runner to prove no Cargo update or metadata call happens on
  pre-Cargo rejection and that discovery uses exact `--no-deps` arguments.
- Real-Cargo integration tests for I6 and lockfile destination resolution.
- Non-vacuity: each rejection test is exercised with a valid member placed
  before the invalid input, and each test asserts the external sentinel is
  unchanged, so a passing run cannot come from rejecting everything.

## BDD feature

The existing `tests/bdd/features/cli.feature` gains:

```gherkin
Scenario: Bumping rejects an escaping configured lockfile manifest
  Given a workspace directory with configuration
  And cargo metadata describes a sample workspace
  And a lockfile manifest configured at "../outside/Cargo.toml"
  When I invoke lading bump 1.2.3 with that workspace
  Then the CLI exits with code 1
  And the stderr contains "Preflight error"
  And the stderr contains "../outside/Cargo.toml"
  And the workspace manifest version is "0.1.0"
```

## Progress

- [x] 2026-11-22: Oriented in the repository; baseline `make build` and
  `pytest` (1412 passed, 30 skipped) recorded.
- [x] 2026-11-22: ExecPlan drafted.
- [x] 2026-11-22: EP-M1 landed. `lading/utils/confinement/` package (errors,
  patterns, walk, glob), `Preflight error:` CLI mapping, and 43 unit tests.
  `make check-fmt`, `make lint`, `make typecheck`, and `make test` (1455
  passed, 30 skipped) all green.
- [ ] EP-M1 follow-up: none outstanding.
- [x] 2026-11-23: EP-M2 landed. `lading.workspace.confined_discovery` loads
  the bump workspace through `cargo metadata --no-deps`, validates declared
  patterns before Cargo and every reported manifest after it, and rejects
  mismatched roots. The CLI selects it only for bump. `make lint`,
  `make typecheck`, and `make test` (1469 passed, 31 skipped) green.
- [x] 2026-11-23: EP-M3 landed. `bump_plan` owns the frozen plan and commit
  order, `bump_prepare` prepares manifests, documentation, and READMEs, and
  `bump_lockfile_planning` resolves every lockfile destination through Cargo
  once. The pipeline now prepares, commits, then regenerates; the legacy
  `_update_manifest`, `resolve_documentation_targets`, and
  `update_documentation_files` helpers were removed. `make check-fmt`,
  `make lint`, `make typecheck`, and `make test` (1487 passed, 31 skipped)
  green.
- [x] 2026-11-23: EP-M4 landed. Added the non-following `fs_snapshot` helper,
  a recording command runner, a workspace/metadata fixture builder, 18
  command-level preflight tests, the BDD lockfile-rejection scenario, and a
  12-test real-Cargo lane with an isolated `CARGO_HOME` and offline mode. Real
  Cargo confirmed that `cargo metadata --no-deps` leaves missing and stale
  lockfiles untouched. `make check-fmt`, `make lint`, `make typecheck`, and
  `make test` (1518 passed, 31 skipped) green.
- [x] 2026-11-23: EP-M5 documentation landed: ADR-012, the ExecPlan and ADR
  are indexed, and the users, design, developer, roadmap, and 0.4.0 migration
  guides describe the contract, migration, limits, and non-member manifest hook.
  `pyproject.toml` is bumped to `0.4.0b1`.
- [x] 2026-11-23: CodeRabbit review cleared. All 15 findings were verified
  against the code and fixed: the migration impact table and prerelease install
  commands, the preflight diagnostic scope (`ReadmeTranspositionError` is now a
  preflight error), native-identity proposal keys, native path components on
  Windows, the Cargo-root suggestion, snapshot permission and junction
  handling, native open sentinels, the plan-before-write ordering assertion,
  TOML-safe member serialization, scoped scan failures, the 400-line split of
  the confinement tests, the `MatchExpectation` docstring, and the
  root-manifest Cargo query in lockfile planning.
- [x] 2026-11-23: Second CodeRabbit review cleared. Added Cargo
  configuration-file rejection for `resolver.lockfile-path` (with a real-Cargo
  test), deferred the unmatched-glob decision until expansion finishes, applied
  Cargo's descendant exclusion semantics, removed the duplicate README
  log-and-re-raise, propagated snapshot examination errors, added
  tested-example markers and a loader contract for the migration guide,
  corrected the member-glob policy and single-manifest repair guidance, split
  the preflight test module, and strengthened the exclusion, ordering, and
  recursive-glob tests.
- [x] 2026-11-23: Third CodeRabbit review cleared. Rejected hard-linked write
  targets, made canonical containment strict, split the root helpers out of the
  walk module, fixed the Cargo-home configuration lookup, deferred the
  unmatched-glob decision to the whole expansion, rejected special intermediate
  glob components, treated trailing separators as directory-only, dropped
  proposals that restore the original file, typed the README reader with
  `ConfinedPath`, and marked and executed every migration example in an
  isolated command boundary.
- [x] 2026-11-23: Third-review remediation committed. Added hard-link
  rejection, strict canonical resolution, a split root/support module,
  CARGO_HOME-root configuration probing, whole-glob unmatched semantics,
  special intermediate-component rejection, directory-only trailing separators,
  restore-original proposal removal, typed README callbacks, strict
  tested-example loading, executed migration examples in an isolated boundary,
  and stronger regeneration observation in the resolution tests.
- [x] 2026-11-23: Fourth-review remediation committed. Accepted an explicit
  `"."` root member, matched documentation names with `fnmatch.fnmatch`,
  applied the unmatched policy to directory-only selectors, resolved a relative
  `CARGO_HOME` against the workspace root, required existing parents in
  creatable containment, copied recorded environments, and extended the example
  loader to tilde and length-matched fences. The migration guide is retitled
  with both endpoints and no longer ties its historical pins to the current
  project version.
- [x] 2026-11-23: Fifth CodeRabbit review cleared and coverage remediated. The
  effective Cargo configuration file is now the extensionless `config` when
  both spellings exist; planning queries Cargo from the workspace root so both
  phases layer the same configuration; proposal identity uses the file's device
  and inode for existing targets; snapshots record the raw link text; the
  migration-guide release check matches complete version components; the fence
  fixture contains a standalone shorter fence; the users guide documents
  configuration-file rejection; and this plan states the explicit
  single-manifest repair command. Targeted tests cover the remaining rejection
  branches, and typing-only import blocks are excluded from coverage the same
  way as existing modules.
- [x] 2026-11-23: Sixth CodeRabbit review cleared. Glob expansion now rejects
  hard-linked regular-file matches with the walk's policy, so two names for one
  inode cannot be planned as one write. The lockfile-path preflight follows
  Cargo configuration `include` entries recursively with Cargo's merge order.
  The migration guide documents the diagnostic-stream row, the Windows virtual
  environment command, and hard-linked targets; the user guide and ADR record
  the same changes. The example loader rejects fenced blocks nested in block
  quotes, and a working-directory test uses `chdir_for_test`.
- [x] 2026-11-23: Seventh CodeRabbit review cleared. `CARGO_HOME` is no longer
  tilde-expanded, matching Cargo; the lockfile repository validates the
  inherited process environment its runner also receives; `relative_to_root`
  preserves parent components for the lexical validator; snapshots classify the
  root itself; fence detection recognizes list items; the metadata rejection
  cases are parametrized; the migration guide's example test validates recorded
  argv; and the impact table classifies every rejected preflight input.
- [x] 2026-11-23: Eighth CodeRabbit review cleared. Discovery validates
  implicit member manifests, reached through path dependencies, before Cargo
  can read them; the configured lockfile repository is selected only when it is
  `None`; a trailing backslash is a directory selector only on Windows; fence
  bodies lose the opening indentation and a closing fence may be indented at
  most three spaces; the expansion-failure test no longer depends on the host
  account; and the user and migration guides qualify lockfile validation and
  classify the implicit-member rejection.
- [x] 2026-11-23: Ninth CodeRabbit review cleared and the CodeScene delta
  reduced. The effective root manifest is confined before planning; a dangling
  final symlink reaches the strict containment proof; example fences are read
  with their container depth so block quotes and list items work; the
  dependency path is serialized as a TOML string; the implicit-member fixture
  declares a real path dependency. The glob policy was split into `glob_types`,
  `glob_expansion` and `glob`, complexity was extracted from the config,
  planning, discovery, root and example helpers, duplicated test families were
  parametrized, and one import-preamble duplication exception was recorded.
- [x] 2026-11-23: Tenth CodeRabbit review cleared. Exclusions follow Cargo's
  literal-prefix rule, legacy `dev_dependencies`/`build_dependencies` tables
  are followed, workspace dependency paths are followed only when a member
  inherits them, the Cargo-normalized dependency path is checked, the metadata
  payload validation moved to `metadata_validation.py`, an injected graph may
  spell its root through a symlink alias, example markers work inside list
  items, and the rejection harness tolerates an existing lockfile. The review's
  `uv.lock` finding was already satisfied: the lock records `lading 0.4.0b1`.
- [x] 2026-11-23: Twelfth CodeRabbit review cleared. Cargo configuration is
  read with `tomlkit`, so TOML 1.1 multiline inline tables do not abort the
  preflight; include paths are lexically normalized before probing. Lockfile
  plans record their validated environment and regeneration refuses a changed
  one. Excluded glob matches still have an existing manifest checked. Implicit
  dependencies are resolved by their absolute lexical destination, workspace
  dependencies are re-resolved to closure, and preparation rebases
  alias-spelled manifests like validation. Glob traversal deduplicates state
  pairs, and example fences use the list container's indentation. The fence
  processor moved to `bump_docs_fences`, discovery test setup is shared, and
  the port and migration docs state the required members and repair-command
  working directory.
- [x] 2026-11-23: Thirteenth CodeRabbit review cleared. Cargo include paths
  are lexically normalized before probing; a real-Cargo experiment showed an
  optional `missing/../shared.toml` still loads when `missing` is absent. The
  fence renderer preserves unchanged fences, list markers and closer lines, and
  splits source lines only on CR, LF and CRLF. Injected crates must have a root
  containing their manifest; `package.workspace` pointers are checked before
  Cargo; `graph_relative_manifest` rejects `..`. The stale-lockfile message now
  names the repair command instead of an ineffective rerun, and the migration
  guide records the required port members and the qualified override rule.
- [x] 2026-11-23: Fifteenth CodeRabbit review cleared. Member components that
  combine a leading dot with a wildcard are rejected before Cargo, because
  Cargo's glob crate can turn them into `..`. Workspace pointers are validated
  in every parsed manifest, including the finishing pass and whitespace-only
  pointers, and the dependency traversal moved to `implicit_paths`. Example
  fences handle quotes before and after list markers, respect the established
  list indentation, and reject unmarked fences inside quoted lists. Repair
  commands quote manifest paths.
- [x] 2026-11-26: Sixteenth CodeRabbit review cleared. Example fences restore
  the enclosing list's indentation for later siblings, reject an opener
  indented beyond its container, and convert nested list markers inside a
  re-rendered body to continuation spaces while preserving block quotes. A
  Cargo configuration include must be a regular file before it is opened, and
  the workspace root lookup maps a vanished working directory to an examination
  error. Windows glob components may not carry their own drive or root, and the
  hidden-wildcard rejection is limited to a literal `.` or `..` prefix. A glob
  match whose platform reports no link count is re-examined with `lstat`, the
  symlink rejection no longer probes existence, and the lockfile port documents
  its required members and failure points. Command tests now record each atomic
  write, so the exactly-once assertions observe the write itself.
- [x] 2026-11-27: Seventeenth review round cleared from the ExecPlan and
  original-issue assessments. Lockfile planning now runs whenever regeneration
  is enabled, so a same-version run still validates configured destinations
  before rewriting an outdated documentation file; regeneration stays gated on
  a manifest change and `--no-rebuild-lockfiles` keeps its exception. README
  adoption rebases an alias-spelled crate root through
  `graph_relative_manifest`, and command-level rejection cases now cover a
  final-manifest symlink, a dangling member link, an in-root alias, and an
  unmatched member glob. Member-pattern declaration moved to
  `member_declarations.py`, bringing `confined_discovery.py` within the plan's
  400-line limit.
- [x] 2026-11-27: Second assessment round repairs. The same-version lockfile
  test documents an older version, and a control test proves the valid plan
  would rewrite that file, so the rejection is shown to withhold a pending
  write. The command-line composition root was split into `cli_app`,
  `cli_helpers`, and `cli_entrypoint`, bringing `lading/cli.py` from 734 to 359
  lines; the dispatch and app test seams moved to the modules that own them, and
  `python -m lading.cli` registers commands once.
- [x] 2026-11-27: Third assessment round repairs. The rejection fixture now
  starts at `0.0.9` as well, the final-manifest symlink case keeps a valid
  `alpha` member ordered before the symlinked `beta` member, and the plan
  records `tomlkit` as a floor raise on an existing dependency rather than a
  new one. The rejection test fails against the previous planning gate, so the
  same-version evidence is non-vacuous.
- [ ] EP-M5 release: tag and release `v0.4.0b1` after review convergence, then
  record the tag commit on issue #409.

## Surprises & discoveries

- The session worktree was attached to `leynos/cuprum`, not `leynos/lading`.
  The task is unambiguously about `lading`, so a lading worktree was created on
  the requested branch and the cuprum worktree was left untouched.
- PR #353 (`[[bump.manifest_rewrites]]`) is open, not merged, so the non-member
  manifest capability is documented as an extension hook.
- `lading/cli.py` currently loads the workspace graph for every command before
  dispatch, including bump, so the new confined loader must be selected at that
  seam rather than only inside `bump.run()`.
- A 400-line limit applies to every new module, so the confinement policy is a
  package (`errors`, `patterns`, `walk`, `glob`) rather than one module.
- `cmd-mox` keeps one double per program, so a fixture cannot register both
  `cargo metadata` spellings. The shared stub helper serves both from one
  handler, and the publish pre-flight dispatcher falls back to a pre-existing
  handler stub for invocations its prefix table does not cover.
- Public confinement helpers take an options dataclass (`ConfineOptions`,
  `GlobOptions`) because the project's four-argument lint ceiling makes long
  keyword-only signatures impractical.
- A fixture whose `cargo metadata` claims the outer workspace root for a
  nested manifest now plans the *outer* lockfile, because that is the file
  Cargo would actually rewrite. The BDD and E2E nested-lockfile fixtures were
  reshaped into genuinely standalone nested workspaces so they keep testing a
  distinct destination.
- Cargo expands `[workspace].members` globs to directories and ignores
  non-directory matches. Rejecting a stray file under `crates/` would refuse a
  workspace Cargo already accepts, so member expansion selects directories and
  ignores regular-file matches while still rejecting special files.

## Decision log

- The ExecPlan lives at `docs/execplans/bump-confinement-preflight.md` because
  the issue's delivery plan names that path explicitly. The repository
  convention of one plan per branch is otherwise unchanged.
- Confinement errors are one hierarchy defined in
  `lading/utils/confinement.py`, rather than a generic hierarchy plus a bump
  translation layer. The module documents the bump scope and the intended
  re-use rule for future commands.
- Preflight validation reads environment variables at the adapter/composition
  boundary (`CargoLockfileRepository` and the CLI), matching the existing
  `CommandRunner` injection style.
- Lockfile destinations are resolved through Cargo's reported workspace root
  rather than assumed adjacent to each manifest, because a nested manifest can
  belong to the outer workspace.
- One reasoned duplication exception was recorded for the import headers shared
  by `bump_docs`, `bump_pipeline`, and `publish`. The overlap is an import
  block, not shared logic; extracting it would add an artificial module with no
  behaviour of its own.
- The write phase keeps atomic per-file replacement and adds no rollback. The
  guarantee is validation before mutation.
- The release is `0.4.0b1`, not final `0.4.0`, because the documented release
  procedure forbids a final 0.x release until cuprum 0.2.0 final exists and
  PyPI still lists only cuprum `0.1.0`, `0.2.0b1`, and `0.2.0b2`. A pre-release
  tag is permitted and gives ortho-config an immutable commit to pin.
- The Cargo configuration preflight inspects only the file Cargo would read in
  each directory: the extensionless `config` when present, otherwise
  `config.toml`. Rejecting a setting from a file Cargo ignores would refuse a
  valid workspace.
- Lockfile planning runs `cargo metadata` from the workspace root, matching
  regeneration, so both phases layer the same Cargo configuration. Cargo still
  selects the manifest's workspace through `--manifest-path`.
- Proposal identity is the target's device and inode when it exists, falling
  back to the normalized native path for an absent may-create target. This
  composes aliases on case-insensitive filesystems while keeping distinct files
  distinct on case-sensitive volumes. Because glob matches can therefore share
  an identity, glob expansion rejects any regular-file match with more than one
  hard link.
- The lockfile-path preflight follows Cargo configuration `include` entries
  recursively, resolving paths against the including file and applying Cargo's
  left-to-right, own-value-wins merge order. Reading only top-level values
  would let a committed include redirect the lockfile after validation.
- A relative `CARGO_HOME` is joined literally, without tilde expansion, and the
  lockfile repository checks the inherited process environment rather than a
  validation-only mapping. Both choices keep the preflight looking at the same
  configuration and environment Cargo uses.
- `relative_to_root` preserves `..` components instead of collapsing them
  through `os.path.relpath`. The lexical validator then rejects the path rather
  than permitting a validation/read disagreement across a symlinked component.
- Automatic detection of a lockfile left stale by a failed regeneration stays
  out of scope. The failure already reports a per-manifest repair command, and
  a same-version rerun is documented as no repair; issue #84 owns recovery.
- Declared members alone cannot bound what Cargo reads: an in-workspace path
  dependency is an implicit member. Discovery therefore parses member
  dependency tables, resolves path entries with `lstat`, rejects symlinked
  components, and confines each implicit manifest before Cargo runs. Dependency
  paths outside the workspace are not write targets and are left to Cargo.
- A configured lockfile repository is selected by an `is not None` check, not
  by truthiness, so an injected false-valued adapter is never replaced by the
  Cargo adapter.
- The glob policy is three modules: `glob_types` for the options, `glob` for
  validation, unmatched policy and containment, and `glob_expansion` for the
  scan engine. Splitting keeps each module under the CodeScene complexity
  threshold without widening any public import.
- `relative_to_root` keeps `..` components, so `prove_containment` also tests
  presence with `lstat`: a dangling final link must reach strict resolution
  rather than look absent.
- Cargo treats `[workspace].exclude` entries as literal path prefixes and does
  not expand wildcards there. The preflight now follows Cargo, so a wildcard
  exclude does not skip a real directory's manifest check.
- Cargo reads legacy `dev_dependencies` and `build_dependencies` spellings from
  pre-2024 editions, so the implicit-member walk follows the first declared
  spelling of each table.
- A `[workspace.dependencies]` path is followed only when a member inherits the
  dependency with `workspace = true`; an unused entry never makes Cargo open
  the target manifest.
- Cargo removes redundant steps before a path lookup, so the preflight checks
  both the declared spelling and the normalized path.
- The metadata payload schema checks live in `metadata_validation.py`; the
  discovery module keeps the TOML and glob policy.
- Cargo configuration is parsed with `tomlkit` rather than `tomllib`: Cargo
  accepts TOML 1.1 syntax such as multiline inline tables, which Python 3.14's
  reader rejects. A configuration Cargo can read must not abort the preflight.
- Include paths are lexically normalized before probing, matching Cargo's
  `..` removal; symlinks are still not resolved.
- `LockfilePlan` records the Cargo-relevant environment it was validated
  against, and regeneration refuses to run when `CARGO_HOME` or the override
  variable changed.
- An excluded glob match's manifest is confined when it already exists, but a
  missing manifest is not an error: Cargo may parse an existing one when an
  explicit member covers the directory.
- Example fences track the list container's indentation separately from the
  opener's own indentation, so only a container-relative closing fence ends an
  example. A marker on a list continuation line inherits the enclosing list's
  content indentation, and block quotes before or after a list marker are
  preserved when the body is re-rendered.
- A member component that begins with `.` and contains a wildcard is rejected.
  Cargo's glob crate can turn such a component into `..`, and Cargo would then
  open a manifest outside the workspace before the reported-member check.
- Every parsed manifest's `package.workspace` pointer is checked, including
  whitespace-only pointers and manifests reached in the finishing pass.
- Cargo include paths are lexically normalized before probing. An experiment
  with the installed Cargo and an isolated `CARGO_HOME` showed that an optional
  `missing/../shared.toml` still loads when `missing` is absent, because Cargo
  removes the redundant step before the existence check.

- An example fence's continuation indentation is the enclosing list container's
  indentation, so a nested list marker inside a re-rendered body becomes spaces
  while a block quote marker is preserved. An opener indented beyond its
  container is rejected rather than accepted as unmarked content.
- A Cargo configuration include that exists but is not a regular file is
  rejected before it is opened, matching the manifest rule: reading a FIFO
  would block the preflight.
- Workspace-root expansion maps a vanished or unreadable working directory to a
  preflight examination error instead of escaping as an `OSError`.
- A Windows pattern component that carries its own drive or root is rejected
  even when the whole pattern looks relative, because `Path` would otherwise
  discard the validated prefix.
- The hidden-wildcard rejection applies only when the literal prefix is `.` or
  `..`; a component such as `.github/*` cannot become a parent traversal and is
  accepted.
- The exactly-once command tests record each atomic write through a spy that
  calls the real writer, so the assertion observes the write rather than
  inferring it from file content.
- Lockfile planning is read-only, so it runs whenever regeneration is enabled,
  not only when a manifest changes. Validation-before-mutation then holds for a
  same-version run that rewrites an outdated documentation file; regeneration
  remains gated, and `--no-rebuild-lockfiles` keeps its documented exception.
- README preparation derives the crate directory through
  `graph_relative_manifest`, as manifest preparation does, so an injected graph
  spelled through a root alias is confined beneath the canonical root.
- The command-line composition root exceeded the plan's 400-line limit once
  bump discovery selection was added to it. Rather than leave the limit unmet,
  the shared app, pure helpers, and process entry point moved into `cli_app.py`,
  `cli_helpers.py`, and `cli_entrypoint.py`; the command adapters and the
  patched runner and workspace-loader seams remain in `cli.py`.
- `tomlkit` was already a declared runtime dependency at `>=0.13,<0.16`; the
  Cargo-configuration parser raises its floor to `>=0.15,<0.16` because Cargo
  accepts TOML 1.1 syntax such as multiline inline tables, which `tomllib`
  rejects. The `pyproject.toml` requirement change and its `uv.lock` recording
  are the only files outside `lading/`, `tests/`, and `docs/` that this work
  touches, besides the release version bump already authorized by the plan.

## Outcomes & retrospective

Implementation and documentation are complete. The first fixed version is
`0.4.0b1`: a final 0.4.0 release is deferred by the repository's documented
release procedure until cuprum 0.2.0 final is available, and PyPI still lists
only `0.1.0`, `0.2.0b1`, and `0.2.0b2`. The immutable commit will be recorded
here and on issue #409 once the release tag is pushed.

## Revision note

- 2026-11-22: Initial draft, created before implementation.
- 2026-11-23: Recorded the delivered milestones, the ADR, the migration
  guidance, and the `0.4.0b1` release decision.
- 2026-11-23: Recorded the fifth-review decisions on configuration precedence,
  planning working directory, and filesystem-aware proposal identity.
- 2026-11-23: Recorded the sixth-review decisions on hard-linked glob matches
  and Cargo configuration includes.
- 2026-11-23: Recorded the seventh-review decisions on literal `CARGO_HOME`,
  environment binding, parent-component preservation, and deferred
  stale-lockfile detection.
- 2026-11-26: Recorded the sixteenth-review decisions on fence containers,
  non-regular includes, root-lookup errors, Windows components, hidden
  wildcards, and observing the write itself.
- 2026-11-27: Recorded the assessment decisions on lockfile validation without
  a manifest change, README adoption through an injected alias, and the
  `tomlkit` requirement-floor change. Recorded the same-version control
  fixture, the `cli.py` split that follows the 400-line constraint, and the
  final correction to the dependency record.
