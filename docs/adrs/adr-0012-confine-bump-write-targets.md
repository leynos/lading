# ADR-012: Confine every bump write target and validate the complete plan before mutation

## Status

Accepted.

## Date

2026-11-23

## Context and problem statement

`lading bump` rewrites the workspace manifest, member manifests, configured
documentation, adopted READMEs, and lockfiles. Before this decision each stage
discovered and wrote its own targets in sequence, and some validation ran only
after earlier stages had already changed files.

Two classes of failure followed from that arrangement.

- **Escaping writes.** A member directory that was a symlink to a sibling
  outside the workspace, a member `Cargo.toml` that was a final symlink, or a
  documentation glob such as `../outside/README.md` was accepted. The pipeline
  resolved the path, read or wrote it, and modified an external file.
- **Late failure.** A malformed configured lockfile-manifest path was validated
  only when lockfile regeneration began, after every manifest and every
  documentation file had been rewritten. The command then failed with a
  workspace that was already partly bumped.

The downstream consumer is [ortho-config#438][ortho-config-438], which
currently keeps an ad-hoc bump helper because it cannot rely on Lading to
confine write targets or to validate the whole plan before mutating the
workspace. The maintainer selected Lading as the owner of this behaviour, so
the fix belongs here rather than in a second repository-specific implementation.

The issue also records what the guarantee is *not*: full rollback after a write
or subprocess failure and protection against a concurrent process changing
paths between validation and writing are separate problems.

## Decision Drivers

- A bump must never modify a file outside the workspace root it was given.
- Validation must complete before the first workspace mutation.
- A rejected plan must name the offending input and exit nonzero.
- Discovery must not change workspace state, including lockfiles.
- The behaviour must hold for future target kinds, including configured
  non-member manifests.
- Publish discovery and behaviour must not change.

## Decision

### One canonical root and a lexical pattern policy

The workspace root is expanded, strictly resolved, and required to be a
directory. Symlinks in the root's own ancestry are permitted: a workspace
reached through an ancestor symlink is still a real workspace.

`[workspace].members` and `[workspace].exclude` patterns, plus configured
documentation and lockfile patterns, are validated lexically before the
filesystem is consulted. Empty, absolute, rooted, drive-qualified, UNC, and
parent-traversing patterns are rejected under both POSIX and Windows spellings.
The `..` test splits on both separators, so `..\outside` is rejected even on a
POSIX host where a backslash is otherwise a literal filename character.

### A component walk before any resolution

Each component below the canonical root is inspected with `os.lstat` before
anything is resolved. The walk rejects symlinks, Windows junctions and reparse
points, non-directory ancestors, non-regular final components, and existing
regular file targets with a link count above one. The hard-link check matters
because Cargo opens an existing `Cargo.lock` and writes through its inode: a
lockfile that is a hard link to an external file would otherwise be rewritten
outside the workspace even though its path is confined. An absent final
component is accepted only for an intended new file, and only when every parent
is a real directory. The final component is classified from its `lstat` result
and never opened, so a FIFO or device cannot block the command.

Canonical containment is then proved with `os.path.realpath` as an independent
second check. A symlinked component would already have been rejected; the
containment proof catches a mistake in that reasoning rather than replacing it.

### A shared confinement package

The policy lives in `lading/utils/confinement/`, split into `errors`,
`patterns`, `walk`, and `glob` by responsibility. Its scope is every bump write
target. A future command that needs the same guarantees may reuse the checking
helpers with its own error base; the helpers hold no command-specific state.
Errors derive from `BumpPreflightError`, which the CLI maps to a dedicated
`Preflight error:` diagnostic and exit status 1.

### Bump-only confined discovery

`lading/workspace/confined_discovery.py` loads the bump workspace. It validates
and parses the root manifest, validates and expands the declared member
patterns, applies exclusions, and only then invokes `cargo metadata --no-deps`.
Every reported member manifest is required to be lexically contained, named
`Cargo.toml`, and a confined existing regular file before the graph builder
reads it. Cargo's reported workspace root must equal the canonical root; a
mismatch suggests `--workspace-root`.

`--no-deps` is the discovery contract, not an optimization. Bump needs the
member set, and dependency resolution is what makes Cargo want to write
`Cargo.lock`. The real-Cargo integration lane proves that a missing lockfile
stays absent and a stale lockfile stays byte-for-byte unchanged. Publish keeps
its existing full-metadata discovery path.

### An immutable plan, prepared then committed

`lading/commands/bump_plan.py` defines a frozen `BumpPlan` of `PlannedWrite`
values keyed by confined path. `lading/commands/bump_prepare.py` prepares every
manifest, documentation file, and adopted README, composing later stages from
earlier proposed content so a README adopted from a documentation-rewritten
workspace README carries the rewritten text. Categories accumulate, so a path
targeted by both the documentation and README stages appears in both report
lists but is written once.

Only after preparation succeeds does `commit_plan()` write, in deterministic
root-manifest, member-manifest, documentation, README order. A dry run reports
the prepared plan and writes nothing.

### Effective lockfile destinations

`lading/commands/bump_lockfile_planning.py` validates every configured and
git-discovered manifest, asks Cargo with `--no-deps --manifest-path` which
workspace each manifest belongs to, and deduplicates the resulting `Cargo.lock`
destinations. A member manifest therefore contributes the outer workspace
lockfile; a nested standalone workspace contributes its own adjacent lockfile.
`regenerate_planned()` consumes the validated plan without rediscovery.

`CARGO_RESOLVER_LOCKFILE_PATH` is rejected when non-empty, because Cargo would
otherwise write a lockfile the validated plan does not name. Other Cargo
configuration that redirects target or cache directories is not part of this
decision; the guarantee covers the lockfiles the plan names.

### Limits

The guarantee is **validation before mutation**. Per-file replacement is
atomic, and a rejected plan leaves every target unchanged, but a failure during
an actual write or subprocess call is not rolled back. A concurrent process
that changes a path after validation is likewise out of scope. These limits are
stated in the user guide rather than implied away.

## Consequences

- A hostile or accidental symlink in a workspace produces exit status 1 with a
  diagnostic naming the rejected input, before anything is written.
- An invalid configured lockfile manifest no longer leaves a half-bumped
  workspace.
- Discovery is read-only and cheap; `cargo metadata --no-deps` does not resolve
  or write lockfiles.
- A workspace whose members were reachable only through a symlinked ancestor
  below the root now fails. The user guide records the migration.
- Configured lockfile manifests now report the lockfile of the workspace Cargo
  reports, which can be the outer workspace rather than an adjacent file.
- The confinement package is available to future commands, but nothing else
  consumes it yet.

## Amendments

### 2026-11-23: Cargo configuration lockfile overrides

The Decision above rejects `CARGO_RESOLVER_LOCKFILE_PATH` when non-empty. Cargo
accepts the same setting as `resolver.lockfile-path` in a configuration file it
layers, so planning also rejects a non-empty setting found in the configuration
file Cargo would read for the workspace root, each ancestor, and `$CARGO_HOME`.
When a directory contains both `.cargo/config` and `.cargo/config.toml`, only
the extensionless `config` is inspected, because Cargo ignores the other file.
The check follows each file's `include` entries recursively, resolving relative
paths against the including file and applying Cargo's left-to-right, own-value
wins merge order, so a value supplied by an included file is rejected too.
Planning runs its metadata query from the workspace root so it layers the same
configuration as regeneration. A relative `CARGO_HOME` is joined to that root
literally: Cargo does not expand a leading `~`, so neither does the preflight.
The repository adapter checks the inherited process environment, which is also
the environment its Cargo invocations receive, rather than accepting a
validation-only override.

### 2026-11-23: Hard-linked glob matches

The component walk rejects an existing regular-file target with more than one
hard link. Glob expansion now applies the same policy to every selected
regular-file match. Without it, two names for one inode reached the plan under
one identity, so only the first was written and the others silently kept their
old content.

### 2026-11-23: Implicit member manifests

Cargo treats a path dependency inside the workspace directory as an implicit
workspace member and reads its manifest while discovering the member set, so
the declared-member walk alone cannot keep a special file away from Cargo.
Discovery parses each declared member's dependency tables, including target
tables and `[workspace.dependencies]`, resolves path entries component by
component with `lstat`, rejects symlinked components, and confines every
in-workspace dependency's `Cargo.toml` as a regular file before invoking Cargo.
Path dependencies outside the workspace are not members and remain Cargo's
responsibility. A missing dependency manifest fails the preflight, which Cargo
would also reject.

### 2026-11-23: Injected crate roots and workspace pointers

An injected crate's `root_path` must be the directory containing its validated
manifest, because README adoption uses that path as the destination. A
`package.workspace` pointer in the requested root manifest is checked before
Cargo runs: a pointer that names a different workspace is rejected with the
`--workspace-root` suggestion, so Cargo never opens an unvalidated manifest
through it. Cargo include paths are lexically normalized before probing,
matching Cargo's own optional-include handling.

### 2026-11-23: Configuration grammar, environment binding and fence containers

Cargo configuration is parsed with `tomlkit`, because Cargo accepts TOML 1.1
syntax such as multiline inline tables and Python 3.14's `tomllib` rejects it.
Include paths are lexically normalized before probing, matching Cargo's removal
of redundant `..` steps. A `LockfilePlan` records the Cargo-relevant
environment it was validated against, and regeneration refuses to run when
`CARGO_HOME` or `CARGO_RESOLVER_LOCKFILE_PATH` changed. An excluded glob match
has its manifest confined when one already exists, but a missing manifest is
not an error. Example fences track the list container's indentation separately
from the opener's own indentation.

### 2026-11-23: Literal exclusions and implicit dependency shapes

Cargo treats `[workspace].exclude` entries as literal path prefixes; a wildcard
character names a directory with that character rather than matching a family.
Discovery now follows that rule, so an exclusion no longer skips the manifest
check for a directory Cargo keeps as a member. The implicit-member walk also
follows the legacy `dev_dependencies` and `build_dependencies` spellings,
follows `[workspace.dependencies]` paths only when a member inherits them, and
checks the normalized path Cargo actually opens as well as the declared
spelling. The metadata payload checks moved to
`lading/workspace/metadata_validation.py` to keep the TOML and glob policy in
one module and the metadata schema in another.

### 2026-11-23: Effective workspace-root manifests and dangling links

Lockfile planning now confines the `Cargo.toml` of each effective workspace
root before it enters the plan, not only the lockfile destination. A reported
root whose manifest is a symlink is therefore rejected rather than passed to
`cargo update`. `prove_containment` tests presence with `os.path.lexists`, so a
dangling final symlink reaches the strict resolution and is reported as an
examination failure instead of looking absent.

### 2026-11-23: Parent components reach the lexical validator

`relative_to_root` uses `Path.relative_to` rather than `os.path.relpath`.
`relpath` collapses `link/..` lexically, but the operating system resolves it
through `link`, so a metadata path could be validated against a different file
from the one the graph builder reads. Preserving `..` lets the lexical
validator reject the path.

## Alternatives considered

- **Resolve once and check containment.** `Path.resolve()` follows the links it
  is meant to reject, so the final component or an ancestor can already point
  outside the workspace before the containment test runs. Rejected.
- **Validate each target at write time.** That preserves per-file atomicity but
  not the whole-operation guarantee: a later invalid target would still fail
  after earlier writes. Rejected.
- **Duplicate the check in ortho-config.** The maintainer selected Lading as
  the owner. A second implementation would drift, and the downstream issue
  specifically asks for a reusable fix here. Rejected.
- **Add `--locked` or `--frozen` to discovery.** Both fail on a missing or
  stale lockfile, which is exactly the state a bump is meant to repair.
  Rejected in favour of `--no-deps` plus the read-only integration lane.

## References

- [Issue #409: confine all bump write targets][issue-409]
- [ortho-config#438: downstream requirement][ortho-config-438]
- [Issue #84: report and recover when lockfile refresh fails after
  rewrites][issue-84]
- [ADR-010: Adopt the nose duplication gate](adr-0010-adopt-nose-duplication-gate.md)

[issue-409]: https://github.com/leynos/lading/issues/409
[ortho-config-438]: https://github.com/leynos/ortho-config/issues/438
[issue-84]: https://github.com/leynos/lading/issues/84
