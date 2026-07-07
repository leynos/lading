# Regenerate discovered nested lockfiles during `lading bump`

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`,
and `Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: IN PROGRESS

## Purpose / big picture

`lading bump` rewrites crate versions across a Cargo workspace and then
regenerates lockfiles. Today it only regenerates the workspace root
`Cargo.lock` plus lockfiles whose manifests are explicitly listed in the
`bump.lockfile_manifests` configuration array. `lading publish`, however,
validates *every* git-tracked `Cargo.lock` in the repository (excluding files
under `target/` directories) and aborts when any of them is stale. The result
is a frustrating failure mode observed in real use: a user runs `lading bump`,
then `lading publish` fails with:

```plaintext
Tracked Cargo.lock files are stale after manifest version changes.
This commonly happens after running `lading bump`; repair each stale lockfile
directly:
- <repo>/crates/cargo-bdd/tests/fixtures/minimal/Cargo.lock
  cargo generate-lockfile --manifest-path <repo>/crates/cargo-bdd/tests/fixtures/minimal/Cargo.toml
- <repo>/crates/rstest-bdd/tests/ui_lints/Cargo.lock
  cargo generate-lockfile --manifest-path <repo>/crates/rstest-bdd/tests/ui_lints/Cargo.toml
```

The user is left wondering why bump could not do this itself. Worse, the users'
guide (`docs/users-guide.md`, "After updating manifest versions" in the bump
section) already claims that bump "automatically refreshes any git-tracked
`Cargo.lock` files (excluding those under `target/`) that have an adjacent
`Cargo.toml`" — a promise the code does not keep.

After this change, `lading bump` discovers every git-tracked `Cargo.lock` using
the same discovery helper the publish pre-flight uses
(`lading.commands.lockfile.discover_tracked_lockfiles`), merges the discovered
manifests with the configured `bump.lockfile_manifests` list, and regenerates
all of them. A subsequent `lading publish` in the same repository then passes
its lockfile freshness check without manual repair. Success is observable by
running `lading bump <version>` in a workspace containing a tracked nested
fixture lockfile and seeing that lockfile listed in the bump output with a
`(lockfile)` suffix and refreshed on disk.

## Constraints

- Do not change the `CommandRunner` protocol
  (`lading/runtime.py`) or the signatures of
  `lading.commands.lockfile.discover_tracked_lockfiles` and
  `lading.commands.lockfile.validate_lockfile_freshness`.
- The existing `bump.lockfile_manifests` and `bump.rebuild_lockfiles`
  configuration keys must keep working with their current meanings.
  `--no-rebuild-lockfiles` must continue to skip all regeneration.
- Regeneration order must keep the workspace root `Cargo.lock` first so
  existing output snapshots and user expectations hold.
- Workspaces that are not git repositories must keep working: discovery
  degrades to a warning plus the configured list, exactly as the publish
  pre-flight degrades today.
- All code comments and documentation use en-GB-oxendict spelling.
- No new external dependencies.
- No single code file may exceed 400 lines (repository rule); adding to
  `lading/commands/bump_lockfiles.py` (155 lines) and `lading/commands/bump.py`
  must respect this.

## Tolerances (exception triggers)

- Scope: if the implementation (excluding tests, snapshots, and docs) requires
  changes to more than 5 files or more than 250 net lines, stop and escalate.
- Interface: if `regenerate_lockfiles` or `resolve_lockfile_paths` must change
  their public signatures in a way that breaks existing callers, stop and
  escalate. (Adding a new public function to `bump_lockfiles` is within
  tolerance.)
- Behaviour: if the Stage A prototype shows that
  `cargo update --workspace --manifest-path <nested>` does *not* freshen a
  nested fixture lockfile that `cargo metadata --locked` reports as stale, stop
  and escalate with the evidence before choosing a different cargo command
  (candidate fallback: `cargo generate-lockfile`, which is what the publish
  pre-flight already recommends to users).
- Iterations: if the focused test suite still fails after 3 fix attempts on
  any milestone, stop and escalate.
- Ambiguity: if any repository under test relies on tracked lockfiles that
  must deliberately stay stale (none are known; the publish pre-flight already
  hard-fails on them), stop and escalate rather than adding an exclusion
  mechanism unilaterally.

## Risks

- Risk: `cargo update --workspace` on a nested manifest may not refresh the
  recorded version of a *path* dependency on a bumped workspace crate, because
  `--workspace` restricts updates to packages defined in that (nested)
  workspace. Severity: high. Likelihood: low. Mitigation: Stage A is a
  throwaway prototype that reproduces the reported layout (root workspace crate
  - nested fixture package with a path dependency on it) and proves which cargo
  command restores freshness under `cargo metadata --locked`. The command
  choice is decided by evidence before any production code changes.
- Risk: newly discovered nested manifests may fail under cargo (for example,
  deliberately broken lint fixtures), making bump fail where it previously
  succeeded. Severity: medium. Likelihood: low. Mitigation: any tracked
  lockfile that cargo cannot read already fails the publish pre-flight today,
  so such repositories are already broken for lading's workflow. The failure
  message from `LockfileRegenerationError` names the manifest and exit code;
  `--no-rebuild-lockfiles` remains as an escape hatch. Documented in the users'
  guide update.
- Risk: dry-run currently makes no subprocess calls in `_process_lockfiles`;
  after this change it runs `git ls-files` (read-only) to list what would be
  regenerated. Severity: low. Likelihood: certain. Mitigation: `git ls-files`
  mutates nothing. Tests pass a stub runner, so no real subprocess runs in the
  unit suite. The behaviour is documented.
- Risk: bump output snapshots (`syrupy` `.ambr` files) change because more
  lockfiles are listed. Severity: low. Likelihood: high. Mitigation: regenerate
  snapshots deliberately with `--snapshot-update` only after eyeballing the new
  output, and commit them with the code change.

## Progress

- [x] (2026-07-07 00:00Z) Investigated current behaviour: bump regenerates
  root + configured manifests only; publish pre-flight discovers all tracked
  lockfiles; users' guide already promises discovery-based refresh.
- [x] (2026-07-07 12:20Z) Stage A: prototype proved
  `cargo update --workspace --manifest-path <nested>` restores freshness for a
  nested fixture package with a path dependency on a bumped workspace crate. See
  `Artifacts and notes`. No fallback command needed.
- [x] (2026-07-07 13:10Z) Stage B: red tests landed and observed failing for
  the expected reasons — three new unit tests plus the two extended wiring
  tests failed with `AttributeError: ... has no attribute
  'merge_discovered_manifests'`, and the new BDD scenario failed with
  `'- fixtures/minimal/Cargo.lock (lockfile)'` absent from the CLI output.
- [x] (2026-07-07 13:40Z) Stage C: implemented `merge_discovered_manifests`
  in `lading/commands/bump_lockfiles.py`, wired it into
  `bump._process_lockfiles` for both dry-run and live paths, extended the
  autouse `stub_lockfile_regeneration` fixture, and updated the
  `lockfile.py` call-graph docstring. Full suite green: 683 passed, 62
  snapshots passed (no snapshot content changed).
- [x] (2026-07-07 13:50Z) Side quest: restored the `make typecheck` gate —
  ty 0.0.8 (unpinned, installed by CI and locally via `uv tool install ty`)
  flagged six pre-existing diagnostics on the clean tree. Committed
  separately as "Restore a passing typecheck gate under ty 0.0.8".
- [ ] Stage D: documentation, pre-flight message wording, snapshot updates,
  full quality gates, CodeRabbit review.

## Surprises & discoveries

- Observation: `docs/users-guide.md` already documents the desired behaviour
  ("automatically refreshes any git-tracked `Cargo.lock` files") even though
  the code only regenerates configured manifests. Evidence:
  `docs/users-guide.md` bump section versus
  `lading/commands/bump.py::_process_lockfiles`, which reads only
  `context.configuration.bump.lockfile_manifests`. Impact: this change closes a
  documented behaviour gap rather than adding new surface; the
  `lockfile_manifests` doc text must be re-scoped to "manifests discovery
  cannot see" (for example, lockfiles not tracked by git).

- Observation: the BDD infrastructure already anticipated discovery in bump —
  `_mock_cargo_metadata` in `tests/bdd/steps/metadata_fixtures.py` and the
  "workspace has tracked Cargo.lock files" given step both stub
  `git ls-files "**/Cargo.lock" "Cargo.lock"` even though nothing in bump
  invoked it. Evidence: the stubs existed before this change and cmd-mox
  stubs are non-strict, so they sat unused. Impact: the existing lockfile
  scenarios worked unchanged once discovery was wired in; only the new
  nested-lockfile scenario needed a new given step.
- Observation: cmd-mox registers one `CommandDouble` per command name and
  dispatches stubs by name alone (`controller._make_response`), so a second
  `stub("cargo::update")` re-configures the first rather than adding an
  argument-matched alternative. Evidence: `cmd_mox/controller.py::_get_double`
  returns the existing double. Impact: the nested-lockfile given step
  registers `cargo::update` without `with_args` so one response serves both
  the root and nested manifest invocations.
- Observation: `uv tool install ty` now resolves to ty 0.0.8, which fails
  `make typecheck` on the clean tree with six diagnostics in
  `lading/commands/bump_toml.py` and `lading/commands/publish_index_check.py`.
  Evidence: `git stash && make typecheck` reproduced all six without this
  branch's changes; a scratch probe confirmed ty 0.0.8 does not narrow
  bindings after calls to `NoReturn` helpers but does honour `NoReturn` for
  reachability. Impact: fixed ahead of the feature commit (if/elif/else
  restructure plus one `type: ignore[index]` mirroring an existing comment)
  so the gate is green before CodeRabbit review.

## Decision log

- Decision: make discovery always-on for bump rather than adding a
  `bump.discover_lockfiles` toggle. Rationale: the publish pre-flight already
  treats every tracked lockfile as required-fresh, so there is no coherent use
  case for bumping versions while leaving a tracked lockfile stale.
  `rebuild_lockfiles = false` and `--no-rebuild-lockfiles` already exist as
  global escape hatches. Fewer configuration keys, and bump/publish stay
  consistent. Date/Author: 2026-07-07, planning session.
- Decision: keep `bump.lockfile_manifests` rather than deprecating it.
  Rationale: it remains the only way to regenerate a lockfile that git does not
  track (for example, generated fixtures ignored by `.gitignore`), and removing
  a configuration key is a breaking change out of scope here. Date/Author:
  2026-07-07, planning session.
- Decision: perform the union of configured and discovered manifests inside
  `lading/commands/bump_lockfiles.py` via a new public helper, and keep
  `bump._process_lockfiles` a thin caller. Rationale: `bump_lockfiles` already
  owns manifest validation and de-duplication (`_resolve_manifest_paths`);
  colocating discovery keeps the feature testable without driving the whole
  bump command. Date/Author: 2026-07-07, planning session.

- Decision: land the Stage B red tests without interim strict-xfail markers,
  committing them together with the Stage C implementation.
  Rationale: the red failures were observed and transcribed directly (the
  plan's validation evidence), and the combined commit keeps the suite green
  at every commit boundary as required by the repository's gating rules; a
  strict-xfail round-trip would have added churn without extra proof.
  Date/Author: 2026-07-07, implementation session.
- Decision: fix the pre-existing ty 0.0.8 typecheck failures in a separate
  commit on this branch rather than ignoring them or pinning ty.
  Rationale: CI installs ty unpinned (`uv tool install ty` in
  `.github/workflows/ci.yml`), so main's next CI run would fail regardless;
  the gates must pass before CodeRabbit review; and the fixes are small,
  behaviour-preserving restructures. Pinning would hide the drift rather
  than resolve it.
  Date/Author: 2026-07-07, implementation session.

## Outcomes & retrospective

To be completed as milestones land.

## Context and orientation

lading is a Python (3.13, `uv`-managed) command-line tool that automates
version bumps and crates.io publication for Rust Cargo workspaces. The relevant
pieces:

- `lading/commands/bump.py` orchestrates `lading bump`. After rewriting
  manifest versions it calls `_process_lockfiles(context, changed_manifests)`,
  which returns the tuple of lockfile paths that were (or, in dry-run, would
  be) regenerated. It currently passes only
  `context.configuration.bump.lockfile_manifests` (a tuple of workspace-relative
  `Cargo.toml` path strings) to the helpers below.
  `context.base_options.command_runner` is an optional `CommandRunner` — a
  callable taking a command sequence and returning
  `(exit_code, stdout, stderr)` — used for dependency injection in tests;
  `None` means "use the real subprocess runner".
- `lading/commands/bump_lockfiles.py` owns regeneration.
  `resolve_lockfile_paths(workspace_root, lockfile_manifests)` maps configured
  manifests to lockfile paths for dry-run reporting.
  `regenerate_lockfiles(workspace_root, lockfile_manifests, *, runner=None)`
  validates each manifest path (must stay inside the workspace and be named
  `Cargo.toml`), always prepends the workspace root manifest, de-duplicates,
  and runs `cargo update --workspace --manifest-path <manifest>` per entry.
- `lading/commands/lockfile.py` owns discovery and freshness validation,
  currently used only by the publish pre-flight.
  `discover_tracked_lockfiles(workspace_root, runner, *, manifest_exists=...)`
  runs `git ls-files "**/Cargo.lock" "Cargo.lock"`, filters out paths with a
  `target` component, keeps only lockfiles with an adjacent `Cargo.toml`, and
  returns absolute lockfile paths. In a non-git directory it logs a warning and
  returns an empty tuple.
- `lading/commands/publish_preflight.py` contains
  `_validate_lockfile_freshness`, which discovers tracked lockfiles, probes
  each with `cargo metadata --locked`, and raises `PublishPreflightError` with
  the "Tracked Cargo.lock files are stale after manifest version changes"
  message quoted above.
- `lading/config.py` defines `BumpConfig` with `exclude`,
  `lockfile_manifests`, `rebuild_lockfiles`, and `documentation` fields. No
  configuration change is needed for this plan.
- Tests live under `tests/unit/` (pytest, with `syrupy` snapshots under
  `tests/unit/__snapshots__/`) and `tests/bdd/` (pytest-bdd; feature files in
  `tests/bdd/features/`, notably `cli.feature` scenarios "Bump rebuilds tracked
  lockfiles" and "Publish pre-flight aborts when lockfile is stale").
  `tests/helpers/workspace_builders.py` provides `_make_workspace` and
  `_make_config` used by `tests/unit/test_bump_lockfile_rebuild.py`.
- Quality gates are Makefile targets, run sequentially (never in parallel):
  `make test`, `make lint`, `make check-fmt` (`make fmt` to fix),
  `make typecheck`, and for Markdown `make markdownlint` and `make nixie`.

"Stale" throughout means: `cargo metadata --locked` fails for the adjacent
manifest with cargo's "needs to be updated" / "cannot update the lock file"
wording, i.e. the lockfile no longer matches the manifests it locks.

## Plan of work

Stage A (prototyping, no production code): prove the cargo command. In the
scratchpad directory, build a miniature repository imitating the failure
report: a git-initialized Cargo workspace with one member crate `alpha`
(version 0.1.0), plus a nested, tracked, non-member package at
`fixtures/minimal/` whose `Cargo.toml` declares
`alpha = { path = "../..", version = "0.1.0" }` (adjust the path to point at the
`alpha` crate) and which has a committed `Cargo.lock`. Then simulate a bump by
editing versions to 0.2.0 (both `alpha`'s version and the fixture's version
requirement), confirm
`cargo metadata --locked --manifest-path fixtures/minimal/Cargo.toml` fails as
stale, run
`cargo update --workspace --manifest-path fixtures/minimal/Cargo.toml`, and
confirm the freshness probe now passes. If it does not, try
`cargo generate-lockfile` and record the outcome in `Decision Log` before
proceeding (see Tolerances). The prototype is throwaway; its findings are
recorded here, not committed.

Stage B (red): specify the behaviour with failing tests.

1. In `tests/unit/test_bump_lockfiles.py`, add tests for a new function
   `lading.commands.bump_lockfiles.merge_discovered_manifests` (final name at
   implementer's discretion, recorded in `Decision Log` if it differs): given a
   workspace root, a configured manifest tuple, and a stub runner whose
   `git ls-files` output lists nested lockfiles, it returns the configured
   entries followed by workspace-relative POSIX manifest strings for each
   discovered lockfile, without duplicates (a manifest both configured and
   discovered appears once, in its configured position). A second test drives
   the non-git fallback: the stub runner returns exit code 128 with "not a git
   repository" on stderr and the function returns just the configured tuple.
   Mark these
   `@pytest.mark.xfail(strict=True, reason="discovery merge not implemented")`
   until red is observed, then remove the marker during Stage C.
2. In `tests/unit/test_bump_lockfile_rebuild.py`, extend the existing
   monkeypatch-style tests: patch discovery (not the real git) so that
   `bump.run` passes the union of configured and discovered manifests to
   `regenerate_lockfiles`, and assert the dry-run path lists discovered
   lockfile paths in its output. Existing assertions such as
   `"lockfile_manifests": ()` will need the new expected union values.
3. In `tests/bdd/features/cli.feature`, extend the "Bump rebuilds tracked
   lockfiles" scenario (or add a sibling scenario "Bump refreshes discovered
   nested lockfiles") so the workspace contains a tracked nested lockfile that
   is *not* configured in `lading.toml`, and assert the CLI output lists it
   with the `(lockfile)` suffix and that it was refreshed. Reuse the existing
   step vocabulary in `tests/bdd/steps/` where possible; add a Given step for
   the nested fixture only if none fits.

Run the focused tests and confirm each fails for the expected reason before
Stage C.

Stage C (green): implement the minimal change.

1. In `lading/commands/bump_lockfiles.py`, add the discovery merge function.
   It imports `discover_tracked_lockfiles` from `lading.commands.lockfile`,
   defaults its runner to `lading.runtime.subprocess_runner` when `None`
   (matching `regenerate_lockfiles`), converts each discovered lockfile path to
   `(lockfile.parent / "Cargo.toml").relative_to(workspace_root)` rendered as a
   POSIX string, sorts the discovered entries for determinism, and returns
   configured entries first followed by unseen discovered entries. The existing
   `_resolve_manifest_paths` continues to prepend the root manifest and
   de-duplicate resolved paths, so the root lockfile stays first regardless of
   what discovery returns.
2. In `lading/commands/bump.py::_process_lockfiles`, call the merge function
   once (passing `context.base_options.command_runner`), and feed its result to
   both the dry-run `resolve_lockfile_paths` branch and the live
   `regenerate_lockfiles` branch, so dry-run and live runs report the same set.
3. Update module docstrings that describe the old contract: the header of
   `bump_lockfiles.py` ("any configured nested lockfiles") and the call-graph
   paragraph in `lockfile.py` (which currently says discovery is publish-only).

Stage D (refactor, documentation, cleanup):

1. Regenerate affected `syrupy` snapshots deliberately and review the diffs.
2. `docs/users-guide.md`: the bump section's discovery claim becomes true —
   tighten it to mention that configured `lockfile_manifests` are additionally
   regenerated; re-scope the `lockfile_manifests` key description to "manifests
   whose lockfiles git does not track (discovery finds tracked ones
   automatically)"; extend the "Lockfile regeneration runs…" paragraph to say
   the command runs for the workspace root, each configured manifest, and each
   discovered tracked lockfile's manifest.
3. `lading/commands/publish_preflight.py::_build_stale_lockfile_message`:
   soften "This commonly happens after running `lading bump`" to reflect that
   bump now repairs tracked lockfiles itself — for example, "This can happen
   after manifest edits outside `lading bump`, or after running bump with
   `--no-rebuild-lockfiles`; repair each stale lockfile directly:". Update the
   snapshot
   `tests/unit/publish/__snapshots__/test_preflight_lockfile_validation.ambr`,
   the assertion in `tests/bdd/features/cli.feature` ("Publish pre-flight
   aborts when lockfile is stale"), and the quoted message in
   `docs/users-guide.md` to match.
4. Update `docs/developers-guide.md` if it documents the bump lockfile flow
   (search for "lockfile" there before editing).
5. Run the full gate suite sequentially and commit.

Commit after each stage that leaves the tree green (AGENTS.md requires small,
gated commits): Stage B red tests are committed together with Stage C so the
suite never lands red; Stage D lands as one or two focused commits (behaviour
message + docs may be separate).

## Concrete steps

All commands run from the repository root
(`/data/leynos/Projects/lading.worktrees/regenerate-lockfiles`), except the
Stage A prototype, which runs in the session scratchpad directory.

Stage A prototype sketch (scratchpad):

```bash
mkdir proto && cd proto && git init -q
cargo new --lib alpha
# Write a workspace Cargo.toml with members = ["alpha"], version 0.1.0 in alpha
mkdir -p fixtures/minimal
# Write fixtures/minimal/Cargo.toml: [package] name = "minimal" ...
#   [dependencies] alpha = { path = "../../alpha", version = "0.1.0" }
cargo generate-lockfile --manifest-path fixtures/minimal/Cargo.toml
git add -A && git commit -qm seed
# Simulate the bump:
sed -i 's/0\.1\.0/0.2.0/' alpha/Cargo.toml fixtures/minimal/Cargo.toml
cargo metadata --locked --manifest-path fixtures/minimal/Cargo.toml \
  --format-version=1 >/dev/null; echo "stale probe exit: $?"   # expect non-zero
cargo update --workspace --manifest-path fixtures/minimal/Cargo.toml
cargo metadata --locked --manifest-path fixtures/minimal/Cargo.toml \
  --format-version=1 >/dev/null; echo "fresh probe exit: $?"   # expect 0
```

Focused test runs during Stages B and C (adjust node IDs to the tests actually
written):

```bash
uv run pytest tests/unit/test_bump_lockfiles.py -q
uv run pytest tests/unit/test_bump_lockfile_rebuild.py -q
uv run pytest tests/bdd -q -k lockfile
```

Expected red transcript shape for Stage B (strict xfail proves the intended
failure): the new unit tests report `XFAIL`, and the extended existing tests
fail on the changed expectations (for example, an assertion diff showing
`lockfile_manifests` missing the discovered entry).

Snapshot regeneration when output changes (Stage D, after reviewing why):

```bash
uv run pytest tests/unit -q --snapshot-update
git diff -- '*.ambr'   # review before staging
```

Full gates before each commit, sequentially:

```bash
make test
make lint
make check-fmt
make typecheck
make markdownlint
make nixie
```

## Validation and acceptance

Acceptance behaviour: in a git-tracked Cargo workspace containing a nested,
tracked, non-member fixture package with its own `Cargo.lock` and *no*
`bump.lockfile_manifests` configuration, running `lading bump 0.2.0` lists the
nested lockfile in the output with a `(lockfile)` suffix and leaves
`cargo metadata --locked` passing for the nested manifest;
`lading bump 0.2.0 --dry-run` lists the same lockfile without modifying it; and
`lading publish` (in that repository, after a real bump) no longer fails its
lockfile freshness pre-flight for lockfiles that bump could regenerate.

Red-Green-Refactor evidence to record here as work proceeds:

- Red: the Stage B commands above fail — new unit tests as strict `xfail`,
  extended tests with assertion diffs naming the missing discovered manifest.
- Green: after Stage C, the same commands pass with the xfail markers
  removed; `uv run pytest tests/unit tests/bdd -q` reports no failures.
- Refactor: after Stage D docstring/doc/message changes, the full gate suite
  passes: `make test`, `make lint`, `make check-fmt`, `make typecheck`,
  `make markdownlint`, `make nixie` all exit 0.

Quality criteria: all six gates pass; the BDD scenario for discovered nested
lockfiles passes; no snapshot changes land unreviewed; users' guide text
matches actual CLI messages verbatim where it quotes them (there is a
`tests/unit/test_users_guide.py` that may enforce parts of this — keep it
green).

## Idempotence and recovery

Every step is re-runnable. Discovery (`git ls-files`) and the freshness probe
(`cargo metadata --locked`) are read-only. `cargo update --workspace` is
idempotent: rerunning it on a fresh lockfile is a no-op. Regeneration is not
atomic across manifests (documented in `regenerate_lockfiles`): if cargo fails
partway, earlier lockfiles stay updated; the recovery path is to fix the cargo
error and rerun `lading bump`, which converges. The Stage A prototype lives
entirely in the scratchpad and is deleted afterwards. If a milestone must be
abandoned mid-way, `git status` plus `git checkout -- <path>` restores the
tree; committed milestones are individually revertable.

## Artifacts and notes

Stage A prototype transcript (2026-07-07, scratchpad `proto/`): a
git-initialized workspace with member crate `alpha` 0.1.0 and a standalone
fixture package `fixtures/minimal` (empty `[workspace]` table, path dependency
`alpha = { path = "../../alpha", version = "0.1.0" }`, committed `Cargo.lock`).
After editing both versions to 0.2.0:

```plaintext
$ cargo metadata --locked --manifest-path fixtures/minimal/Cargo.toml ...
stale probe exit: 101
error: cannot update the lock file .../fixtures/minimal/Cargo.lock because
--locked was passed to prevent this
$ cargo update --workspace --manifest-path fixtures/minimal/Cargo.toml
    Updating alpha v0.1.0 (...) -> v0.2.0
    Updating minimal v0.1.0 (...) -> v0.2.0
$ cargo metadata --locked --manifest-path fixtures/minimal/Cargo.toml ...
fresh probe exit: 0
```

Conclusion: `--workspace` does refresh path-dependency entries whose manifest
versions changed (the update is "necessary to satisfy other dependency
requirements"), so bump can reuse its existing cargo invocation unchanged for
discovered manifests. One incidental finding: a standalone nested package needs
an empty `[workspace]` table to avoid being claimed by the outer workspace —
real fixture packages with their own lockfiles already have this.

## Interfaces and dependencies

No new dependencies. At the end of Stage C the following must exist:

In `lading/commands/bump_lockfiles.py`:

```python
def merge_discovered_manifests(
    workspace_root: Path,
    lockfile_manifests: cabc.Sequence[str],
    *,
    runner: CommandRunner | None = None,
) -> tuple[str, ...]:
    """Return configured manifests plus discovered tracked-lockfile manifests.

    Configured entries keep their order and come first; manifests implied by
    git-tracked ``Cargo.lock`` files (via
    :func:`lading.commands.lockfile.discover_tracked_lockfiles`) follow in
    sorted order, skipping any already configured. In a non-git workspace the
    configured tuple is returned unchanged.
    """
```

`lading/commands/bump.py::_process_lockfiles` calls it once and passes the
result to the existing `resolve_lockfile_paths` (dry-run) and
`regenerate_lockfiles` (live) helpers, whose signatures do not change.
