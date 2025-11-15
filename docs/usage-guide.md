# Lading Usage Guide

The `lading` command-line tool orchestrates versioning and publication tasks
for Rust workspaces. This guide documents the CLI scaffolding introduced in
roadmap Step 1.1 and the manifest version propagation delivered in Step 2.1.

## Installation and invocation

The CLI ships with the repository and can be executed via the `lading` console
script or directly with Python:

```bash
uv run lading --help
```

The console script resolves to :func:`lading.cli.main`. Invoking the
implementation module remains supported for development workflows:

```bash
uv run python -m lading.cli --help
```

## Global options

### `--workspace-root <path>`

Specify the root of the Rust workspace that `lading` should operate on. The
flag can appear before or after the subcommand:

```bash
python -m lading.cli --workspace-root /path/to/workspace bump
python -m lading.cli bump --workspace-root /path/to/workspace
```

If the flag is omitted, the CLI defaults to the current working directory. The
resolved path is also exported as the `LADING_WORKSPACE_ROOT` environment
variable so that downstream helpers and configuration loading can share the
location without re-parsing CLI arguments.

## Logging

`lading` emits operational logs for each CLI command so release engineers can
audit what the tool is doing, including the external processes (for example,
`cargo` invocations) it spawns. The log stream defaults to level `INFO` and is
emitted on standard error. Set the `LADING_LOG_LEVEL` environment variable to
one of the standard Python levels (`DEBUG`, `INFO`, `WARNING`, `ERROR`, or
`CRITICAL`) to adjust verbosity when troubleshooting:

```bash
LADING_LOG_LEVEL=DEBUG uv run lading publish
```

When the variable is unset, the CLI retains the default `INFO` level.

## Configuration file: `lading.toml`

`lading` looks for a `lading.toml` file at the workspace root. When the file is
present the CLI resolves the workspace directory, uses `cyclopts`' TOML loader
to read the file, and exposes the resulting configuration to the active
command. If the file is absent the loader now returns an empty document, so the
commands run with the default configuration. The configuration is validated
with a dataclass-backed model to ensure that string lists and boolean flags
conform to the schema described in the design document.

An example minimal configuration looks like:

```toml
[bump]

[bump.documentation]
globs = ["README.md", "docs/**/*.md"]

[publish]
strip_patches = "all"
```

If the file contains invalid values the CLI prints a descriptive error and
exits with a non-zero status. Commands invoked programmatically via
`python -m lading.cli` load the configuration on demand, so helper scripts and
tests can rely on the default behaviour when `lading.toml` is not supplied.

## Subcommands

### `bump`

`bump` synchronises manifest versions across the workspace. The command
requires the target version as a positional argument and rejects inputs that do
not match the `<major>.<minor>.<patch>` semantic version pattern, while
allowing optional pre-release and build metadata. All validation happens before
the command loads workspace metadata, so mistakes fail fast.

When the version string passes validation, `bump` updates the workspace
`Cargo.toml` and each member crate's manifest, unless the crate name appears in
`bump.exclude` within `lading.toml`.

```bash
python -m lading.cli --workspace-root /workspace/path bump 1.2.3
```

Running the command updates:

- `workspace.package.version` and any root `[package]` entry inside the main
  `Cargo.toml`.
- `package.version` for each workspace crate not listed in `bump.exclude`.
- Dependency requirements in `[dependencies]`, `[dev-dependencies]`, and
  `[build-dependencies]` sections when they point to workspace members whose
  versions were bumped. Existing requirement operators such as `^` or `~` are
  preserved, and other inline options (for example `path = "../crate"`) remain
  untouched.
- Markdown files matching any glob configured under `bump.documentation.globs`.
  Each TOML fence in those files is parsed with `tomlkit` so that `[package]`,
  `[workspace.package]`, and dependency entries that name workspace crates
  inherit the new version while preserving indentation and fence metadata.

`lading` prints a short summary that lists every file it touched. For example:

```text
Updated version to 1.2.3 in 3 manifest(s):
- Cargo.toml
- crates/alpha/Cargo.toml
- crates/beta/Cargo.toml
```

All paths are relative to the workspace root. Documentation files appear in the
same list with a `(documentation)` suffix, and the summary prefix reports both
manifest and documentation counts. When every manifest already records the
requested version, the CLI reports:
`No manifest changes required; all versions already 1.2.3.`

Pass `--dry-run` to preview the same summary without writing to disk. Example:

```text
Dry run; would update version to 1.2.3 in 3 manifest(s):
- Cargo.toml
- crates/alpha/Cargo.toml
- crates/beta/Cargo.toml
```

### `publish`

`publish` now produces a publication plan for the workspace. The command reads
`publish.exclude` from `lading.toml`, honours any crate manifests that declare
`publish = false`, and prints a structured summary listing the crates that will
be published. Additional sections document crates skipped by manifest flags or
configuration, along with any exclusion entries that do not match a workspace
crate. After building the plan, `publish` validates the workspace by running
`cargo check --workspace --all-targets` followed by
`cargo test --workspace --all-targets` directly inside the workspace root. Each
command reuses a temporary target directory so that build artefacts are
isolated and discarded once the checks finish. The commands execute after a
`git status --porcelain` cleanliness check so that the pre-flight run sees the
same files that would be published. Any non-zero exit aborts the command with a
descriptive error message.

Workspaces can opt out of expensive integration test suites by configuring a
`[preflight]` section. Listing crate names under `preflight.test_exclude`
instructs the CLI to pass `--exclude <crate>` for each entry when it executes
`cargo test`. The check still runs for all other workspace members so a single
misbehaving crate does not block the release pipeline. Example configuration:

```toml
[preflight]
test_exclude = ["cucumber", "beta-cli"]
```

Setting `preflight.unit_tests_only = true` limits the pre-flight invocation to
library and binary targets by appending `--lib --bins`. This helps release
pipelines focus on fast unit suites while leaving integration, doc, and example
tests for dedicated CI jobs:

```toml
[preflight]
unit_tests_only = true
```

Auxiliary builders and compiletest helpers are configured in the same table:

- `aux_build` – each entry is an array of command tokens that should run before
  Cargo. For example,
  `aux_build = [["cargo", "+nightly", "test", "-p", "lint", "--no-run"]]`
  precompiles a UI harness so that later compiletest invocations can reuse the
  artifacts.
- `compiletest_extern` – map crate names to artifact paths. Lading resolves the
  paths relative to the workspace root and appends `--extern crate=path` to the
  `RUSTFLAGS` passed to `cargo test`, ensuring proc-macro helpers are available
  to compiletest.
- `env` – a table of environment overrides that should be present whenever
  Lading runs `git status`, `cargo check`, `cargo test`, or auxiliary builders.
  This is ideal for localisation variables such as `DYLINT_LOCALE`.
- `stderr_tail_lines` – the number of lines to tail from any compiletest
  `*.stderr` files referenced in the test output when `cargo test` fails. The
  default of `40` prints context along with the file paths so UI drift is
  easier to debug.

If the working tree contains uncommitted changes the run only halts when you
explicitly pass `--forbid-dirty`. Skipping the flag leaves the git status guard
disabled so you can iterate on fixes while still exercising the pre-flight
commands. Add `--forbid-dirty` when you need to guarantee the publish plan was
built from a clean tree.

```bash
python -m lading.cli --workspace-root /workspace/path publish
```

Example output:

```text
Publish plan for /workspace/path
Strip patch strategy: all
Crates to publish (1):
- alpha @ 0.1.0

Staged workspace at: /tmp/lading-publish-abc123/workspace
Copied workspace README to:
- crates/alpha/README.md
```

The publish plan sorts crates so that internal dependencies appear before the
crates that rely on them. The deterministic order is calculated with a
topological sort over the workspace graph. If the workspace defines
`publish.order` in `lading.toml`, that explicit list takes precedence once it
is validated for missing, duplicate, or unknown crate names. Any dependency
cycles are reported as errors so that release engineers can fix their manifests
before continuing.

When the configuration excludes additional crates, or a manifest sets the
`publish = false` flag, the plan prints dedicated sections. These make the
reasons for skipping crates visible to the operator. The current release stops
after producing the plan and running the pre-flight checks; cargo packaging and
publication will arrive in a later milestone.

After staging the workspace, `publish` also normalises the root
`Cargo.toml` according to the `publish.strip_patches` strategy:

- `"all"` removes the entire `[patch.crates-io]` section so every crate will
  resolve dependencies from crates.io.
- `"per-crate"` removes only the entries that match the crates scheduled for
  publication, leaving third-party or out-of-scope patches intact.
- `false` preserves the patch table, which is useful when the release process
  relies on local registries or custom overrides.

The staged manifest is edited in place within the temporary clone, so the
original workspace configuration remains untouched.

The preparation phase now clones the entire workspace into a temporary build
directory before any packaging steps run. The CLI prints the location of this
staging area so operators can inspect generated artifacts. Crates that declare
`readme.workspace = true` receive a copy of the workspace `README.md` within
the staged workspace. The summary lists each propagated README to confirm the
files are ready for `cargo package`. The staging copy preserves symbolic links
by default so workspaces that link to external assets avoid recursively copying
those directories. Programmatic callers can override this behaviour by passing
``PublishOptions(preserve_symlinks=False)`` when invoking
``lading.commands.publish.prepare_workspace``. When callers no longer need the
staging tree they can opt into ``PublishOptions(cleanup=True)`` to remove the
temporary directory automatically at process exit.

## Testing hooks

Behavioural tests invoke the CLI as an external process and spy on the `python`
executable with [`cmd-mox`](./cmd-mox-usage-guide.md). Setting
`LADING_USE_CMD_MOX_STUB` to a truthy value such as `1` or `true` forces
publish pre-flight checks to proxy through the cmd-mox IPC server so that the
suite can assert on `cargo::<subcommand>` invocations without launching real
tools. This pattern keeps the tests faithful to real user interactions while
still providing strict control over command invocations. Use the same approach
when adding new end-to-end scenarios.

## Workspace discovery helpers

Roadmap Step 1.2 introduces a thin wrapper around `cargo metadata` to expose
workspace information to both commands and library consumers. Import
`lading.workspace.load_cargo_metadata` to execute the command with the current
or explicitly provided workspace root:

```python
from pathlib import Path

from lading.workspace import load_cargo_metadata

metadata = load_cargo_metadata(Path("/path/to/workspace"))
print(metadata["workspace_root"])
```

The helper normalises the workspace path, invokes
`cargo metadata --format-version 1` using `plumbum`, and returns the parsed
JSON mapping. Any execution errors or invalid output raise `CargoMetadataError`
with a descriptive message so callers can present actionable feedback to users.

### Workspace graph model

`load_workspace` converts the raw metadata into a strongly typed
`WorkspaceGraph` model backed by `msgspec.Struct` definitions. The graph lists
each crate, its manifest path, publication status, and any dependencies on
other workspace members. The message returned by the CLI mirrors this
information so that users can confirm discovery succeeded before later roadmap
features begin mutating manifests.

```python
from pathlib import Path

from lading.workspace import load_workspace

workspace = load_workspace(Path("/path/to/workspace"))
print([crate.name for crate in workspace.crates])
```

The builder reads each crate manifest with `tomlkit` to detect
`readme.workspace = true` directives while preserving document structure for
future round-tripping.
