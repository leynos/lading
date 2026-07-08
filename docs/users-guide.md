# Lading user guide

`lading` is a command-line tool for managing release workflows in Rust
workspaces. It can:

- Bump versions across the workspace (`Cargo.toml` files) and keep internal
  dependency requirements in sync.
- Update version references inside TOML code fences in Markdown documentation.
- Plan and execute publication (`cargo package` + `cargo publish`) in dependency
  order, with pre-flight `cargo check`/`cargo test` validation.

> **Breaking change in 0.1.0 — `--live` interleaving**
>
> Prior to 0.1.0, `lading publish --live` ran a two-phase pipeline: all
> crates were packaged before any were published. From 0.1.0 onwards the
> live pipeline is interleaved — each crate is packaged and published in
> turn before the next crate is processed. Dry-run mode retains the
> original two-phase order. Workspaces that relied on the old sequencing
> must adopt the new per-crate ordering; no configuration knob restores
> the prior behaviour.

The 0.1.0 release also changes workspace README adoption:

> **Breaking change in 0.1.0 — workspace README adoption**
>
> Prior to 0.1.0, `lading publish` copied the workspace `README.md` into
> crates that set `readme.workspace = true` while preparing the staged
> workspace. From 0.1.0 onwards, `lading bump` performs that adoption and
> rewrites relative Markdown links before publishing. Commit the adopted
> crate README files produced by `lading bump` before running `lading publish`;
> publish staging no longer creates or repairs those files.

## Installation

### Install from a wheel (recommended for internal distribution)

Build a wheel from the repository, then install it:

```bash
make build-release
python -m pip install dist/*.whl
```

### Install for development (using uv)

Create a development environment and run `lading` via `uv`:

```bash
make build
uv run lading --help
```

## Tutorial

This tutorial assumes a Rust workspace with a root `Cargo.toml` and one or more
member crates.

### 1. Create `lading.toml`

Create a minimal configuration file at the workspace root:

```toml
[bump.documentation]
globs = ["README.md", "docs/**/*.md"]

[publish]
strip_patches = "per-crate"
```

`lading.toml` can be omitted entirely. When absent, `lading` uses the defaults
documented in the configuration reference below.

### 2. Bump versions

To update the workspace and member crate manifests to `1.2.3`:

```bash
lading bump 1.2.3
```

To preview changes without writing any files:

```bash
lading bump 1.2.3 --dry-run
```

After updating manifest versions, `lading bump` automatically refreshes any
git-tracked `Cargo.lock` files (excluding those under `target/`) that have an
adjacent `Cargo.toml`. The result header counts each changed category, e.g.
"N manifest(s)", "N documentation file(s)", "N readme file(s)", "N
lockfile(s)", joined with Oxford-comma grammar. In the per-file body list,
manifest paths carry no suffix; the other categories carry a parenthetical
suffix: `(documentation)` for Markdown docs whose TOML code fences were
updated, `(readme)` for crate READMEs adopted from the workspace README, and
`(lockfile)` for regenerated `Cargo.lock` files. In dry-run mode, every file
is listed, but none are modified.

Where a member crate sets `readme.workspace = true`, `lading bump` also adopts
the workspace `README.md` into that crate's directory and rewrites relative
Markdown links so they still resolve from the crate directory. Such adopted
READMEs appear in the output with the `(readme)` suffix described above.

If `bump.documentation.globs` is configured, `lading` also searches those
Markdown files for TOML code fences and updates version values that refer to
workspace crates.

### 3. Publish in dry-run mode

By default, `publish` runs `cargo publish --dry-run` so the full pipeline can
be validated without uploading crates.

```bash
lading publish
```

Before running `cargo check` and `cargo test`, `lading publish` validates that
all git-tracked `Cargo.lock` files are fresh under `--locked` mode. If any
lockfile is stale — for example after a `lading bump` that regenerated a nested
workspace lockfile — the command exits with code 1 and prints a repair command:

```text
Tracked Cargo.lock files are stale after manifest version changes.
Run the following to repair:
  cargo generate-lockfile --manifest-path <path>/Cargo.toml
```

Run the repair command, commit the updated lockfile, then re-run
`lading publish`.

To require a clean working tree before running the pre-flight checks, pass
`--forbid-dirty`:

```bash
lading publish --forbid-dirty
```

To perform a real publish (no `--dry-run`), pass `--live`:

```bash
lading publish --live
```

Dry-run publishing packages every publishable crate first, then runs
`cargo publish --dry-run` for every crate. Live publishing follows
`publish.order` crate by crate: `cargo package`, then `cargo publish`, then the
next crate. This lets a later crate depend on a newly published earlier crate
in the same `--live` run. Live publishing is not transactional; if a later
crate fails, crates already uploaded to crates.io are not rolled back. Reruns
skip versions that are already present on crates.io and continue with the
remaining crates.

`bump` adopts the workspace `README.md` for any member crate that sets
`readme.workspace = true`. The adopted README is written into the crate
directory and relative Markdown links are rewritten so they still resolve from
that directory. `publish` then stages the already-prepared workspace into a
temporary directory before packaging.

#### Dry-run limitations with unpublished workspace dependencies

`cargo package` validates dependency versions against the live crates.io index,
even in dry-run mode. When two or more workspace crates are released together
for the first time and one depends on another at a version that is not yet on
crates.io, `cargo package` will fail with an error similar to:

```text
error: failed to prepare local package for uploading

Caused by:
  failed to select a version for the requirement `inner_crate = "^0.8.0"`
  candidate versions found which didn't match: 0.7.0, 0.6.0, ...
  location searched: crates.io index
  required by package `outer_crate v0.8.0`
```

This affects dry-run release trains that introduce a new shared version across
multiple workspace crates. `lading publish --live` avoids the limitation by
publishing each crate immediately after it is packaged, so a later crate can
resolve a dependency that an earlier crate in `publish.order` just uploaded.
Plain dry-runs still use Cargo's live index. By default, `lading` downgrades
these index-lookup failures to warnings when the missing dependency is also in
the publish plan and appears earlier in publish order.

##### Manual staged publishing

When a release must be split manually, run `lading publish --live` for the
foundational crate first, then run `lading publish` (dry-run) or
`lading publish --live` for the remaining workspace once the new version is
indexed:

```bash

# 1. Publish the foundational crate live so crates.io has the new version.
lading publish --live --workspace-root path/to/workspace

# 2. Once the new version is indexed, publish (or dry-run) dependent crates.
lading publish --workspace-root path/to/workspace
```

`lading` skips crates whose versions are already on crates.io, so the second
invocation only acts on the remaining crates.

##### `--allow-unpublished-workspace-deps` (dry-run only)

For CI gating where a real publish is not desirable, dry-run mode defaults to
the same behaviour as passing `--allow-unpublished-workspace-deps`: it
downgrades the index-lookup failure to a warning when the missing dependency is
itself part of the planned publish set and appears earlier in publish order:

```bash
lading publish --allow-unpublished-workspace-deps
```

The override applies to both the `cargo package` step and the subsequent
`cargo publish --dry-run` step (which packages internally and hits the same
crates.io index lookup), so the dry run completes end-to-end.

Use `--no-allow-unpublished-workspace-deps` to opt out during dry-runs and keep
Cargo's index lookup strict:

```bash
lading publish --no-allow-unpublished-workspace-deps
```

`--allow-unpublished-workspace-deps` is rejected when combined with `--live`
because the failure cannot be bypassed during a real publish.
`--no-allow-unpublished-workspace-deps` remains valid with `--live`; it
preserves the strict behaviour that live publishes already use. When the
missing dependency is **not** in the publish plan, or when it appears **after**
the current crate in `publish.order`, the failure is still treated as an error.
Fix the explicit `publish.order` so foundational crates come before dependants,
or remove `publish.order` and rely on dependency-derived topological sorting.
Each such downgrade is counted and surfaced in the metrics summary emitted at
exit; see [Observability](#observability).

## Configuration reference (`lading.toml`)

`lading` looks for `lading.toml` in the workspace root. The file must be a TOML
table at the top level. Unknown keys are rejected with a configuration error.

All paths and globs are interpreted relative to the workspace root.

### Complete example

```toml
[bump]
exclude = ["some-private-crate"]
lockfile_manifests = ["crates/nested/Cargo.toml"]
rebuild_lockfiles = true

[bump.documentation]
globs = ["README.md", "docs/**/*.md"]

[publish]
exclude = ["some-internal-tooling-crate"]
order = ["core", "utils", "app"]
strip_patches = "per-crate" # "all" | "per-crate" | false

[preflight]
test_exclude = ["slow-integration-suite"]
unit_tests_only = false
aux_build = [["cargo", "+nightly", "test", "-p", "lint", "--no-run"]]
compiletest_extern = {
  ui_test_helpers = "target/debug/deps/libui_test_helpers.so"
}
env = { DYLINT_LOCALE = "en_GB" }
stderr_tail_lines = 40
```

### `[bump]`

- `exclude`: array of strings, default `[]`. Crate names to exclude from
  manifest updates.
- `lockfile_manifests`: array of strings, default `[]`. Additional
  `Cargo.toml` manifests whose adjacent `Cargo.lock` files should be
  regenerated after `lading bump`. The workspace root `Cargo.toml` is always
  included and should not be listed.
- `rebuild_lockfiles`: boolean, default `true`. Controls whether `lading bump`
  regenerates the workspace lockfile and configured nested lockfiles after
  manifest updates. Pass `--no-rebuild-lockfiles` to skip regeneration for a
  single run, or `--rebuild-lockfiles` to force regeneration when
  `rebuild_lockfiles` is configured as `false`.

Lockfile regeneration runs
`cargo update --workspace --manifest-path <manifest>` for the workspace root
and each configured nested manifest. This updates workspace package entries
while avoiding a full transitive dependency refresh.

Regeneration is not atomic, and `lading bump` attempts every configured
manifest rather than stopping at the first Cargo failure. Manifest versions are
written before any lockfile is refreshed, so a failure leaves the workspace
inconsistent: the manifests carry the new version but the affected lockfiles do
not. When several lockfiles are regenerated, `lading` raises one aggregated
error that lists every failed manifest with the exact repair command to run:

```text
Cargo lockfile regeneration failed for 2 manifest(s). Manifests already carry the new version, so the workspace is inconsistent until each lockfile below is repaired:
- Cargo lockfile regeneration failed for crates/a/Cargo.toml with exit code 101: <cargo error>
  cargo update --workspace --manifest-path crates/a/Cargo.toml
- Cargo lockfile regeneration failed for crates/b/Cargo.toml with exit code 101: <cargo error>
  cargo update --workspace --manifest-path crates/b/Cargo.toml
```

When only the workspace-root lockfile is regenerated, its lone failure surfaces
the plain Cargo error instead. To recover, fix the underlying Cargo error and
rerun `lading bump`, run the printed repair command for each listed manifest,
or use `--no-rebuild-lockfiles` and regenerate the lockfiles manually before
committing the bump.

### `[bump.documentation]`

- `globs`: array of strings, default `[]`. Glob patterns for Markdown files
  whose TOML code fences should be updated.

### `[publish]`

- `exclude`: array of strings, default `[]`. Crate names to exclude from
  publication.
- `order`: array of strings, default `[]`. Explicit publish order; overrides
  dependency-derived ordering when present.
- `strip_patches`: one of `"all"`, `"per-crate"`, or `false`; default
  `"per-crate"`. Controls how `[patch.crates-io]` is edited in the staged
  workspace before packaging.

### Observability

When `lading` runs, a structured JSON summary may appear in the log output at
`INFO` level just before the process exits. The flush is process-wide — any
command can emit it — and reports whichever metrics that run recorded. For
example, a `publish` run that downgraded an index-lookup failure emits:

```plaintext
lading metrics summary: [{"metric": "publish.index_lookup_downgrade", "labels": {"missing_crate": "...", "subcommand": "..."}, "value": 1}]
```

Each entry records a counter name, the label values that identify it, and the
accumulated count for the current invocation. The summary line is omitted
entirely when no metrics were recorded (quiet runs stay quiet).

#### `publish.index_lookup_downgrade`

Incremented on each downgrade event when a crates.io index-lookup failure for a
sibling workspace dependency is downgraded to a warning because
`allow_unpublished_workspace_deps` is enabled. Labels:

- `subcommand` — the Cargo subcommand that failed (`package` or `publish`).
- `missing_crate` — the name of the workspace dependency absent from the index.

### `[preflight]`

- `test_exclude`: array of strings, default `[]`. Crate names to exclude from
  `cargo test` by passing `--exclude`.
- `unit_tests_only`: boolean, default `false`. Append `--lib --bins` to the
  pre-flight `cargo test` invocation.
- `aux_build`: nested array of strings, default `[]`. Extra tokenized commands
  to run before cargo pre-flight checks.
- `compiletest_extern`: table of string keys and values, default `{}`. Extra
  `--extern` entries to append to `RUSTFLAGS` for compiletest-style suites.
- `env`: table of string keys and values, default `{}`. Environment overrides
  applied to git/cargo invocations run by `publish`.
- `stderr_tail_lines`: integer greater than or equal to zero, default `40`.
  Number of lines to tail from referenced `*.stderr` files when tests fail.

## Reference: CLI flags and environment variables

### `--workspace-root`

`--workspace-root` specifies the workspace root explicitly. The flag can appear
before or after the subcommand:

```bash
lading --workspace-root /path/to/workspace bump 1.2.3
lading bump 1.2.3 --workspace-root /path/to/workspace
```

When present, the resolved path is also exported as `LADING_WORKSPACE_ROOT` for
the duration of the command.

### `LADING_LOG_LEVEL`

Set `LADING_LOG_LEVEL` to control verbosity (`DEBUG`, `INFO`, `WARNING`,
`ERROR`, `CRITICAL`). The default is `INFO`.
