# Design Document: Lading Crate Management Tool

Version: 1.0\
Status: Proposed\
Date: 05 October 2025

## 1. Introduction

### 1.1. Purpose

This document specifies the design for a generalized, configuration-driven
Python utility named `lading`. This tool will streamline versioning and
publication workflows for arbitrary Rust workspaces. It is intended to
supersede and replace the existing, repository-specific `bump_version.py` and
`run_publish_check.py` scripts, abstracting their core logic into a reusable
and robust command-line application.

### 1.2. Scope

The project encompasses the following key deliverables:

1. A unified command-line interface (CLI) tool, `lading`, built with
   `cyclopts`, providing `bump` and `publish` subcommands.
2. A single, unified TOML configuration file, `lading.toml`, to define
   workspace-specific behaviours, minimizing the need for command-line
   arguments.
3. A workspace discovery mechanism that infers the dependency graph, crate
   locations, and publication order by parsing the output of `cargo metadata`.
4. A `bump` command to propagate version changes across the workspace,
   including `Cargo.toml` files for both the workspace and individual crates,
   and to synchronize documentation files.
5. A `publish` command to execute pre-publish checks and publish crates to a
   registry in the correct topological order, with support for both dry-run and
   live modes.
6. Support for the `readme.workspace = true` manifest key, ensuring the
   workspace's `README.md` is adopted into member crates during version bumps
   before packaging.

### 1.3. Goals

- **Decoupling:** Eliminate hardcoded paths, crate names, and
  repository-specific assumptions from the tooling.
- **Generalization:** Create a tool that can be applied to any Rust workspace
  with minimal configuration.
- **Automation:** Reduce manual effort and the risk of human error in release
  processes.
- **Configuration over Code:** Favour declarative configuration in
  `lading.toml` over imperative logic within the tool itself.
- **Clarity and Maintainability:** Establish a clean, well-tested Python
  codebase that is easy to understand and extend.

### 1.4. Current coupling

#### `run_publish_check.py`

- The workflow is driven by the statically defined
  `PUBLISHABLE_CRATES` tuple imported from `publish_workspace_members`, which
  hard-codes the crate list and release ordering for this repository.
- Crate directories are resolved under `<workspace>/crates/<name>`, which only
  matches the current workspace layout and fails for workspaces that colocate
  crates elsewhere.
- Live publish commands and the locked publish variant are keyed off concrete
  crate names, making the publish pipeline unusable when the workspace contains
  a different set of packages.
- The dry-run mode packages one crate and checks others based on crate names,
  which will not hold in a generic workspace.

#### `bump_version.py`

- Member version updates assume that `ortho_config` should bump
  `ortho_config_macros` together, coupling the script to rstest-bdd-specific
  crates.
- Documentation updates only rewrite TOML fences that reference the
  `ortho_config` dependency and only touch `README.md` and
  `docs/users-guide.md`, missing other files in a different workspace layout.
- The script derives the workspace root as two directories above the script,
  preventing reuse when the tools are vendored into another project or invoked
  against a different repository.

## 2. Core Components

### 2.1. The `lading` Command-Line Interface

The primary user interaction point will be the `lading` CLI. It will be
implemented using the `cyclopts` library to provide a modern, type-annotated,
and environment-aware interface.

The structure will be as follows:

```shell
lading [--workspace-root <path>] <subcommand> [options]
```

- `--workspace-root`: An optional global flag to specify the path to the Rust
  workspace root. If omitted, it defaults to the current working directory.
- **Subcommands:**

- `bump`: Manages version bumping.
- `publish`: Manages the publication process.

#### Implementation notes (Step 1.1)

- The initial CLI scaffolding lives in `lading/cli.py` and exposes a
  `main()` entry point alongside the `cyclopts.App` instance. Packaging wires
  this entry point through a `lading` console script for ergonomic execution.
- `--workspace-root` is implemented as a global flag that can be positioned
  before or after the subcommand. The bootstrapper removes the flag from the
  argument list, normalizes it via the shared
  `lading.utils.normalize_workspace_root` helper (implemented with
  `pathlib.Path` alone), and stores the resolved path in the
  `LADING_WORKSPACE_ROOT` environment variable so that Cyclopts can hydrate
  per-command options without bespoke parsing hooks.
- Behavioural coverage uses `pytest-bdd` with `cmd-mox` spies to exercise the
  CLI through an actual `python -m lading.cli` invocation. This ensures the
  scaffolding works end-to-end, not just through direct function calls.
- Configuration loading is centralized in `lading/config.py`. The module builds
  a `cyclopts.config.Toml` loader anchored at the workspace root, validates the
  resulting data with frozen dataclasses, and exposes a context manager so that
  downstream code can access the active configuration without passing it
  through every call. The CLI sets up this context before dispatching a
  subcommand and falls back to on-demand loading when commands are invoked
  programmatically.
- The `cyclopts.App` is constructed with `result_action="return_value"`, so a
  command's return value reaches `_dispatch_and_print` unchanged. That
  function, and `main()` around it, own printing and exit codes: a string
  result is printed once to stdout, an integer becomes the exit status, and a
  `SystemExit` carrying a message is reported on stderr with status 1.
  Cyclopts's own diagnostics go to stderr and help goes to stdout. The
  contract, its exceptions, and the dependency floor are recorded in
  [ADR-011](adrs/adr-0011-cyclopts-4-cli-result-and-diagnostic-boundary.md).

### 2.2. Configuration: `lading.toml`

A `lading.toml` file located at the workspace root defines the tool's
behaviour. When the file is absent the CLI treats it as an empty document and
runs with the default configuration, so workspaces can opt in to overrides
incrementally. The design prioritizes inference to keep this file as minimal as
possible.

**Schema Definition:**

```toml
# lading.toml

# `bump` table: Configuration for the 'bump' command.
[bump]
# A list of crate names to exclude from the version bump process.
# The tool will infer all publishable crates by default and apply
# version bump to these.
# exclude = []

# `publish` table: Configuration for the 'publish' command.
[publish]
# A list of crate names to exclude from the publishing process.
# Useful for examples, internal tools, or private crates within the workspace.
# The tool will infer all publishable crates by default.
# exclude = []

# Optional explicit ordering for publication. If not specified, the tool
# will determine the order topologically from the dependency graph.
# This should only be used to resolve ambiguity or enforce a specific sequence.
# Crate names listed here must be valid members of the workspace.
# order = ["crate-a", "crate-b"]

# Strategy for stripping [patch.crates-io] directives from Cargo.toml during
# publication. This is often necessary to ensure the registry uses published
# versions of workspace dependencies instead of local path overrides.
#
# Possible values:
# - "all": The entire [patch.crates-io] section is removed from the temporary
#   workspace manifest before any checks are run.
# - "per-crate": Before publishing each crate, its specific entry is removed
#   from the [patch.crates-io] section. This allows subsequent crates in the
#   publish order to still resolve local paths.
# - false: No patches are stripped.
#
# If unset, the tool defaults to "all" for dry runs and "per-crate" for live
# runs.
strip_patches = "per-crate"
```

Implementation detail: the publish command rewrites the staged workspace
`Cargo.toml` immediately after cloning the workspace tree. The helper loads the
manifest with `tomlkit` so formatting and comments survive the transformation
and either removes the entire `[patch.crates-io]` table when the strategy is
`"all"` or deletes only the entries whose crate names appear in the publish
plan when using `"per-crate"`. Setting the value to `false` leaves the patch
table untouched so bespoke overrides (for example, third-party forks) remain
available during packaging. Any parse errors or missing manifests surface as
`PublishPreparationError` to keep the staging workflow predictable.

#### Publish data flow

The publish data flow shows how the publish command coordinates crate planning,
workspace staging, manifest preparation, and command execution. The coordinator
delegates live and dry-run sequencing to `publish_pipeline`, which uses the
`publish_execution` command adapter through an injected runner.

```mermaid
graph TD
    A["CLI: lading publish"] --> B["Module: lading.commands.publish"]
    B --> C["Module: lading.commands.publish_plan (publish_plan.py)"]
    C --> C1["Build PublishPlan with publishable_names"]
    B --> S["Module: lading.commands.publish_staging (publish_staging.py)"]
    S --> S1["Copy workspace and resolve staged crate paths"]
    B --> D["Module: lading.commands.publish_manifest (publish_manifest.py)"]
    D --> D1["_apply_strip_patch_strategy(staging_root, plan, strategy)"]
    D1 --> D2{"strip_patches configuration"}
    D2 -->|"False"| E["Skip manifest modification"]
    D2 -->|"all"| F["Remove all patch.crates-io entries"]
    D2 -->|"per-crate"| G["Remove crates-io entries for plan.publishable_names"]
    F --> H["Cleanup empty patch tables and write Cargo.toml"]
    G --> H
    H --> I["Updated staged manifest used for publish"]

    B --> P["Module: lading.commands.publish_pipeline (publish_pipeline.py)"]
    P --> P1["Dispatch live or dry-run package/publish pipeline"]
    P --> J["Module: lading.commands.publish_execution (publish_execution.py)"]
    J --> K["_invoke: subprocess execution with error adaptation"]
    J --> K2["_run_timed_cargo: per-crate timing and duration metric"]
    P --> L["Module: lading.commands.publish_sccache (publish_sccache.py)"]
    L --> L1["SccacheSession: query lifecycle and side effects"]
    L --> R["Module: lading.commands.publish_sccache_report (publish_sccache_report.py)"]
    R --> R1["SccacheLedger, formatting, atomic report writing"]
    L --> M["Module: lading.commands.publish_sccache_stats"]
    M --> M1["detect_wrapper, query_snapshot, parse_counters"]
    B --> N["Module: lading.commands.publish_preflight (publish_preflight.py)"]
    N --> O["Run pre-flight checks before publication dispatch"]
```

_Figure 1: Publish data flow from planning and staging through Cargo execution._

### 2.3. Workspace Discovery and Model

The tool's internal representation of the workspace is critical for its
operation. This model will be constructed at runtime by executing
`cargo metadata --format-version 1` and parsing its JSON output. This approach
is superior to manual TOML parsing as it correctly handles path dependencies,
build scripts, and complex workspace configurations.

#### Implementation notes (Step 1.2)

- Workspace discovery is anchored in `lading.workspace.metadata`. The module
  invokes `cargo metadata --format-version 1` through the active
  `CommandRunner`, normalizing the workspace root via
  `lading.utils.normalize_workspace_root` before invoking the command.
- Failures to locate the `cargo` executable raise
  `CargoExecutableNotFoundError`; non-zero exit codes raise
  `CargoMetadataInvocationError`; malformed JSON payloads raise
  `CargoMetadataParseError`. Each derives from `CargoMetadataError`, giving
  higher layers a single umbrella type while preserving detail for logging.
- The wrapper returns the parsed JSON mapping directly. Later roadmap steps
  will build dedicated models on top of this structure without re-running the
  command or reparsing JSON.
- `lading.workspace.models` defines the `WorkspaceGraph`, `WorkspaceCrate`, and
  `WorkspaceDependency` types as `msgspec.Struct` instances so that workspace
  data is immutable and efficiently serialized.
- `build_workspace_graph` reads each crate manifest with `tomlkit` to detect
  `readme.workspace = true` entries while preserving formatting for later
  round-tripping.
- `load_workspace` constructs the graph once per CLI invocation and passes it
  to command handlers, allowing them to share discovery results without
  re-running `cargo metadata`.

The discovery process will populate an internal data structure representing the
workspace graph, containing:

- **Workspace Root:** The absolute path to the workspace directory.
- **Crate List:** A collection of all crates, each containing:

- `name`: The crate name (e.g., `my-crate`).
- `version`: The current version string.
- `path`: The absolute path to the crate's root directory.
- `manifest_path`: The absolute path to the crate's `Cargo.toml`.
- `publish`: A boolean indicating if the crate is intended for publication
  (derived from `package.publish` in `Cargo.toml`).
- `dependencies`: A list of its dependencies within the workspace. Each entry
  retains both the canonical crate name and the manifest key, so renamed
  dependencies (e.g., `alpha-core = { package = "alpha" }`) can be matched back
  to the correct manifest entry when updating requirements.
- `readme_is_workspace`: A boolean flag derived from checking if
  `package.readme.workspace` is `true`.

This internal graph enables reliable dependency resolution and topological
sorting for the `publish` command.

## 3. `bump` Subcommand Design

The `bump` command will synchronize versions across the workspace.

**Command Signature:**

```shell
lading bump <new_version> [--dry-run]
```

- `<new_version>`: The new semantic version string (e.g., `1.2.3`). This is a
  required argument.
- `--dry-run`: If present, the command will report all changes it would make
  without writing to any files.

**Execution Flow:**

1. **Discover Workspace:** Build the internal workspace model.
2. **Update Workspace **`Cargo.toml`**:** Set `workspace.package.version` to
   `<new_version>`.
3. **Update Member Crates:** For each crate in the workspace (not just
   publishable ones):

    - Update `package.version` in its `Cargo.toml`.
    - Iterate through its `[dependencies]`, `[dev-dependencies]`, and
      `[build-dependencies]`. If a dependency is a workspace member, update its
      version string to match `<new_version>`, preserving any existing version
      operators (e.g., `^`, `~`). This prevents version drift between internal
      crates.

4. **Handle Workspace READMEs:** For each crate where `readme_is_workspace` is
   `true`:

    - Copy the `README.md` file from the workspace root to the crate's
      directory, rewriting relative Markdown links for the crate directory and
      overwriting any existing file when content changes. This action is part
      of the versioning workflow, so the result can be reviewed and committed
      before publishing.

5. **Update Documentation Files:** _(deferred to Step 2.2)_

    - Introduce configuration-driven glob patterns for documentation files.
    - For each matching file, scan for TOML fenced code blocks (three backticks
      with the language tag "toml").
    - Within each fence, parse the content and update the version of any
      dependency that is also a workspace member to `<new_version>`. This
      replaces the previous hardcoded logic.

6. **Report Changes:** Output a summary of all files that were (or would be)
   modified.

### Implementation notes (Step 2.1)

- Manifest rewrites use `tomlkit` so comments and formatting remain intact. The
  implementation updates both `[package]` and `[workspace.package]` sections in
  the workspace manifest when present.
- `bump.exclude` is respected when iterating workspace crates. Any crate name
  listed in the configuration keeps its existing `package.version` value during
  the update pass.
- Dependency requirements referencing workspace members are rewritten using the
  workspace graph. For each crate we map dependencies to their `[dependencies]`,
  `[dev-dependencies]`, or `[build-dependencies]` sections and update the
  requirement string only when the target crate's version changes. Leading
  operators such as `^` and `~` are preserved so caret and tilde semantics
  continue to apply.
- Crates excluded via `bump.exclude` still have their dependency requirements
  refreshed when they point at bumped members. This keeps the workspace graph
  consistent without forcing the excluded crate to change its own version.
- The command reports a concise summary that enumerates every manifest path on
  its own line. The live mode prefix is `Updated version to <version> in …`,
  while dry runs use `Dry run; would update version to <version> in …`. When no
  manifest requires changes the CLI reports:
  `No manifest changes required; all versions already <version>.`
- A `--dry-run` flag bypasses file writes entirely while still computing the
  manifest diff. This allows automation to preview the impact of a bump without
  touching the workspace.
- Version arguments are validated at the CLI layer before the workspace model
  loads. Invalid formats raise a user-facing error without touching the
  filesystem, while values may include optional pre-release and build metadata.
- The legacy `bump.doc_files` configuration knob has been retired until
  documentation rewriting arrives in Step 2.2 to avoid implying unsupported
  behaviour.

### Implementation notes (Step 2.2)

- `bump.documentation.globs` accepts glob patterns relative to the workspace
  root. Each pattern expands to a list of Markdown files that should have their
  TOML fences rewritten during a bump.
- Markdown fences are parsed with `markdown-it-py` so indentation and
  language info strings are preserved. The fence bodies are parsed with
  `tomlkit`, updating `[package]`, `[workspace.package]`, and dependency
  entries that reference workspace crates. Existing requirement operators and
  inline trivia remain intact.
- The publish workflow stages a clean workspace copy in a temporary directory
  before packaging. The staging directory must live outside the source tree to
  avoid recursive copies. Crates that opt into `readme.workspace = true` must
  already have adopted READMEs from the preceding `bump` workflow, so operators
  can review the packaged assets before publishing. Symbolic links remain links
  by default to avoid cloning large external trees; callers can opt into
  dereferencing by disabling `preserve_symlinks` via `PublishOptions`. Likewise,
  `PublishOptions(cleanup=True)` registers an `atexit` hook that removes the
  temporary build directory after the process exits.
- Documentation rewrites honour `--dry-run`; the command reports the files but
  skips writing to disk. The CLI summary now reports both manifest and
  documentation counts, and documentation entries are suffixed with
  `(documentation)` for clarity.

### Lockfile repository port (bump side)

The bump domain reaches Cargo lockfile projection and regeneration through a
`LockfileRepository` port defined in `lading.commands.bump_lockfiles`.
`CargoLockfileRepository` is the cargo- and git-backed adapter; it is
constructed with an optional `CommandRunner`, merges configured manifests with
manifests discovered from tracked lockfiles, and delegates projection and
regeneration through the `lading.commands.bump_lockfiles` compatibility façade.
The façade keeps the port, adapter, and established public imports in one
boundary while cohesive helper modules hold the implementation:
`bump_lockfile_manifests` merges configured and discovered manifests,
`bump_lockfile_paths` validates manifests and projects lockfile paths, and
`bump_lockfile_regeneration` invokes Cargo and aggregates failures.
`BumpOptions.lockfile_repository` is the injection point; when `None`, bump
substitutes `CargoLockfileRepository` bound to the default subprocess runner,
and the CLI binds the adapter at the composition root. This keeps the bump
domain free of a raw `CommandRunner` (issue #82).

## 4. `publish` Subcommand Design

The `publish` command orchestrates the publication of crates to the designated
registry.

**Command Signature:**

```shell
lading publish [--live] [--forbid-dirty] [--keep-staging] [--sccache-stats]
               [--sccache-stats-json PATH]
```

- `--live`: By default, the command simulates the entire process, including
  `cargo package` and `cargo publish --dry-run`, without uploading to the
  registry. Specifying `--live` takes the process to completion by packaging
  and publishing each crate before moving to the next crate in the publish
  order.
- `--forbid-dirty`: Require a clean working tree before running the pre-flight
  checks. When omitted the git status guard is skipped so that developers can
  iterate on pending changes.
- `--keep-staging`: Retain the staged workspace copy after publication and log
  where it is, instead of removing it. The copy is the whole workspace plus its
  verify build, so retaining it is the exception and the flag names the
  exception; `LADING_KEEP_STAGING` supplies its default. Removing the copy is
  otherwise unconditional, covering success, failure, interruption and
  `SIGTERM` (issue #269).
- `--skip-preflight` / `--no-skip-preflight`: Skip, or reinstate, the
  compilation-heavy part of the pre-flight for callers that have already
  verified the workspace. The flag overrides `[preflight] skip` in both
  directions and takes its default from `LADING_SKIP_PREFLIGHT`, so a CI
  workflow can suppress the repeat without editing the command line. The
  lockfile guard and the opt-in working-tree guard are unaffected.
- `--sccache-stats` / `--sccache-stats-json PATH`: Opt-in compiler-cache
  instrumentation (issue #252). Lading queries the sccache binary named by
  `RUSTC_WRAPPER` for a baseline after pre-flight and again after every
  per-crate cargo invocation, logs one bounded line per crate with the counters
  attributable to it, and optionally writes a JSON report. Snapshots are
  differenced rather than zeroed so the caller's own job-wide report keeps its
  meaning, and every failure is a WARNING: measurement never fails a release
  rehearsal. The environment variables `LADING_SCCACHE_STATS` and
  `LADING_SCCACHE_STATS_JSON` supply defaults so CI can enable it without
  editing a Makefile.

**Execution Flow:**

1. **Execute pre-flight checks:** Before publishing, run a series of checks in
   the workspace itself to ensure integrity:

    - Run `cargo check --all-targets` for the entire workspace.
    - Run `cargo test --all-targets` for the entire workspace.

    Implementation detail: the command executes both cargo subcommands via
    `plumbum` directly in the workspace root so that any relative path
    dependencies remain accessible. Build artefacts are isolated by exporting a
    per-run target directory created with `tempfile.TemporaryDirectory`; the
    directory (and any compiled artefacts) is discarded automatically once the
    checks complete. A preceding `git status --porcelain` verifies that the
    working tree is clean only when operators pass `--forbid-dirty`. Skipping
    the flag leaves the guard disabled so local experiments can reuse the same
    entrypoint. For testing and controlled environments the helper honours the
    `LADING_USE_CMD_MOX_STUB` environment variable: when set to a truthy value
    (`1`, `true`, `yes`, or `on`) the pre-flight invocations contact the
    cmd-mox IPC server instead of spawning real processes. Each subcommand is
    encoded as `cargo::<name>` so that behavioural tests can record
    expectations without interfering with the `cargo metadata` stub used
    elsewhere.

    The configuration layer exposes a `[preflight]` table so workspaces can
    selectively skip crates during the `cargo test` pre-flight run. Entries in
    `preflight.test_exclude` translate directly into `--exclude <crate>` flags
    passed to the cargo invocation, letting release pipelines avoid expensive
    cucumber suites or other integration-heavy members while still validating
    the remaining crates. Setting `preflight.unit_tests_only = true` narrows
    the command to library and binary targets by appending `--lib --bins`,
    providing a lightweight alternative when integration and example tests are
    not required for release validation. Additional hooks support compiletest
    workflows:

    - `preflight.aux_build` – a list of auxiliary commands that run before the
      cargo invocations so rule crates can precompile UI helpers.
    - `preflight.compiletest_extern` – a mapping of crate names to artefact
      paths that Lading injects into `RUSTFLAGS` as `--extern` arguments for
      the cargo test invocation.
    - `preflight.env` – a table of environment overrides applied to every
      pre-flight command so localization knobs such as `DYLINT_LOCALE` stay in
      sync with the surrounding harness.
    - `preflight.stderr_tail_lines` – the number of lines tailed from
      compiletest `*.stderr` files when cargo test fails, exposing the debug
      diff directly in the CLI output.
    - `preflight.skip` – suppress the auxiliary builds and the cargo
      check/test pair outright. A caller that has already built and tested the
      same commit otherwise pays for the whole suite twice; measured on a warm
      cache, the repeat was 883 of the 936 seconds a Linux publish step took.
      The lockfile guard still runs, and so does the working-tree guard when
      `--forbid-dirty` opts into it, because neither repeats the caller's work
      and both still decide whether the publication would be correct.
      `lading publish` logs the setting, flag, or environment variable that
      requested the skip, and records the `publish.preflight` counter and
      `publish.preflight.duration` observation for it.

    Any `PublishPreflightError` aborts execution before `plan_publication`,
    `publish_staging.staged_workspace`, or
    `publish_pipeline._dispatch_publication` run.

2. **Discover Workspace:** Build the internal workspace model.
3. **Determine Publishable Crates:**

    - Filter the crate list to include only those where `publish` is not
      `false`.
    - Remove any crates listed in `publish.exclude` from `lading.toml`.
    - Record crates skipped by manifest settings and configuration so the CLI
      can explain how the plan was derived. Report any configuration exclusions
      that do not match workspace crates to help users prune stale entries.

4. **Determine Publish Order:**

    - If `publish.order` is defined in `lading.toml`, validate that it contains
      all publishable crates and use this order.
    - If `publish.order` is not defined, perform a topological sort on the
      dependency graph of publishable crates to generate the correct
      publication sequence. If the graph contains cycles, the command will
      abort with an error.

Implementation note: the planner now performs a deterministic topological sort
using Kahn's algorithm with a lexicographically ordered queue so that parallel
branches remain stable across runs. The resulting `PublishPlan` raises a
`PublishPlanError` when a cycle prevents ordering, surfacing the crates
involved to the operator. When `publish.order` is configured the planner
validates that every publishable crate appears exactly once and that no unknown
names are listed before returning the user-specified order.

<!-- markdownlint-disable-next-line MD029 -->
1. **Stage the workspace and prepare its manifest:**
   `publish_staging.staged_workspace` creates an isolated workspace copy that
   is removed when publication ends. Within that staged workspace, determine
   the patch stripping strategy based on the `publish.strip_patches`
   configuration value and the execution mode (`--live` versus the default
   dry-run mode).

    - If strip_patches is "all" (or is unset and this is the default dry-run
      mode), remove the entire [patch.crates-io] section from the Cargo.toml.

### Lockfile inspection repository port (publish side)

The publish pre-flight domain reaches tracked-lockfile discovery and freshness
validation through a `LockfileInspectionRepository` port defined in
`lading.commands.lockfile_repository`, alongside its git- and cargo-backed
adapter `CargoLockfileInspectionRepository`; it binds a `CommandRunner` and the
optional pre-flight base environment, applying that environment to invocations
that do not supply their own. `publish_preflight._run_preflight_checks` is the
composition root: it constructs the adapter and passes it to the domain helpers
`_collect_stale_lockfiles` and `_validate_lockfile_freshness` in
`publish_lockfile_preflight.py`, which depend only on the port. Together with
the bump-side `LockfileRepository`, the two ports keep VCS, filesystem, and
cargo execution concerns out of the lockfile domain logic (issue #82).

`_collect_stale_lockfiles` deliberately classifies every tracked `Cargo.lock`
rather than short-circuiting on the first stale one (issue #83). When stale
lockfiles are found, `_build_stale_lockfile_message` composes a diagnostic
message that lists each offending lockfile alongside its own
`cargo generate-lockfile --manifest-path ...` repair command;
`_validate_lockfile_freshness` then raises it as a `PublishPreflightError`, so
the operator can remediate the whole workspace in a single pass rather than
re-running the pre-flight once per lockfile. Only unexpected failures from
`cargo metadata --locked` — those not attributable to a stale lockfile — raise
immediately on first occurrence, leaving the aggregation path unaffected.

### Publish Preflight Sequence

The preflight sequence diagram illustrates the pre-flight checks that run
before crate publication. Auxiliary build commands (if configured) execute
first, followed by cargo check and cargo test with environment overrides
applied. Preflight failures abort the publish workflow; success advances to
crate-by-crate publishing.

```mermaid
sequenceDiagram
    participant Caller
    participant publish.py
    participant publish_preflight
    participant publish_plan
    participant publish_staging
    participant publish_pipeline
    participant publish_execution

    Caller->>publish.py: publish(…, forbid_dirty=…)
    publish.py->>publish_preflight: _run_preflight_checks(...)
    publish_preflight->>publish_execution: _invoke(cargo check/test, ...)
    publish_execution-->>publish_preflight: (rc, stdout, stderr)
    alt rc != 0
        publish_preflight-->>publish.py: PublishPreflightError
        publish.py-->>Caller: PublishPreflightError
    else rc == 0
        publish.py->>publish_plan: plan_publication(workspace, configuration, workspace_root)
        publish_plan-->>publish.py: PublishPlan
        publish.py->>publish_staging: staged_workspace(plan)
        publish_staging-->>publish.py: PublishPreparation
        publish.py->>publish_pipeline: _dispatch_publication(plan, preparation)
        publish_pipeline->>publish_execution: _invoke(cargo package/publish, ...)
        publish_execution-->>publish_pipeline: (rc, stdout, stderr)
        publish_pipeline-->>publish.py: Success
        publish.py-->>Caller: Success
    end
```

_Figure 2: Publish pre-flight sequence from checks through crate publication._

### Publishing iteration

1. **Prepare staged workspace:** Apply patch stripping in the temporary
   workspace before invoking Cargo for individual crates.

    - **Patch Handling (per-crate)**: If strip_patches is "per-crate" (or is
      unset and this is a live run), remove the specific patch entry for the
      current crate from the Cargo.toml within the working tree snapshot being
      prepared for packaging.
    - **README Handling:** If the crate has `readme.workspace = true`, rely on
      the README adopted during `lading bump`; publish does not copy README
      files during staging.

2. **Dispatch Cargo operations:** The publishing mode determines how Cargo is
   invoked for the planned crate order.

    - **Dry-run:** Run `cargo package` for every publishable crate first, then
      run `cargo publish --dry-run` for every crate. This validates the full
      batch without uploading anything.
    - **Live:** For each crate, run `cargo package` and then `cargo publish`
      before moving to the next crate. This makes a newly uploaded earlier
      crate visible to later crates that depend on it in the same release
      train.

    Every per-crate cargo invocation streams its output as it runs and is
    timed; the start and success log lines carry the crate's `n/total`
    position, the success line reports the elapsed seconds, and the duration
    is recorded under the `publish.cargo.duration` metric with `subcommand`
    and `crate` labels, so a slow packaged build is attributable to one crate
    (issue #251). Commands whose stdout is a document rather than progress,
    such as the `cargo metadata --locked` freshness probe, are captured
    without being mirrored to the console: a multi-megabyte single line
    breaks CI log capture and hides everything that follows it.

    When the registry reports that a crate version already exists, Lading logs
    a warning and continues to the next crate instead of aborting the
    publication loop. Live publishing is not transactional: if a later crate
    fails, any earlier successful uploads remain on crates.io and are skipped
    on a subsequent run.

## 4a. `clean` Subcommand Design

The `clean` command removes staging copies that earlier releases left behind.
Publishes no longer leak them (issue #269), but a long-lived host already holds
one per past run.

**Command Signature:**

```shell
lading clean [--location PATH] [--remove]
```

- `--location PATH`: Directory to search. Defaults to the system temporary
  directory, which is where a publish stages unless `TMPDIR` says otherwise.
- `--remove`: Delete what is found. Reporting is the default, and the flag
  names the exception, the same shape as `--keep-staging` on `publish`.

This is the only command that deletes directories the user did not name, so its
scope is the design. It considers the immediate children of one directory and
does not recurse; it matches names by `publish_staging.STAGING_PREFIX`,
imported rather than repeated so the two cannot drift; and it refuses symbolic
links rather than following them, so nothing outside the searched directory can
be reached. A removal that fails is logged and the sweep continues, so one busy
tree does not abandon the rest.

The report names the bytes each tree holds and their total, because reclaiming
space is the reason to run it.

### Cross-process staging claim

A staged tree left behind by a still-running publish is indistinguishable, by
name and shape, from one abandoned by a release that never cleaned up: both
carry `STAGING_PREFIX` and both are ordinary directories. The publish that owns
a tree runs in a different process from `clean`, so the in-memory
`_ACTIVE_STAGING_ROOTS` set that guards termination cleanup within one process
cannot answer for it. Crossing that process boundary is what the claim exists
to do.

Every automatically created staging tree therefore carries a
`.lading-staging-lock` file, which its publisher opens and holds under an
exclusive, non-blocking lock for the tree's whole life: `fcntl.flock` on POSIX,
`msvcrt.locking` on Windows, both behind the single `staging_lock` module so
callers do not choose a platform.

The claim is a kernel-held lock rather than a marker file recording a process
identifier, because a marker fails in both directions that matter here. A
reused process identifier can make a dead owner look alive, and a publish
killed outright leaves its marker behind, making the tree permanently
unremovable — the exact leftover `clean` exists to sweep. A kernel lock avoids
both: it is dropped when its holder dies, however it dies, so there is no stale
state to reconcile and nothing to time out.

The design has to close the gap between asking and deleting, so `clean` does
not check the claim and then remove the tree: `hold_for_removal` holds the
claim across the `shutil.rmtree` call itself. A publish could claim the tree in
the interval between a check and a delete, so the claim must span the whole
removal, not merely precede it.

Windows cannot honour that shape directly: an open handle inside a directory
stops that directory being deleted there, so the claim cannot be held while the
tree is removed on that platform. The same rule, though, is what keeps a live
tree safe: the publisher's own open handle makes the removal itself fail rather
than succeed, so an in-use tree is never deleted there. The claim turns that
failure into an orderly skip. There is deliberately no separate "is this tree
in use" query: probing a kernel lock means taking it, so such a query would
mutate while presenting as a read, and its answer would be stale before the
caller could act on it. `hold_for_removal` is the one way to ask, and it
returns the answer together with the claim that keeps it true.

The lock is advisory, a courtesy rather than a guarantee: `claim` returns
whether it succeeded, and a filesystem that refuses to lock does not stop a
publish, only leaves it to log a warning that a concurrent
`lading clean --remove` could take its tree. A publish that retains its staged
tree still releases the claim once its block ends, since no publish is left
reading it. A tree with no lock file predates the mechanism and stays
removable, and deleting the lock file by hand defeats the claim entirely.

One window remains open by construction: `_normalize_build_directory` creates
the tree with `mkdtemp` and only calls `claim` afterwards, because a directory
cannot be created already locked. Closing that gap would require the lock to
live outside the tree it protects, which would orphan lock files on removal and
change what an absent lock file means.

## 5. Refactoring and Project Structure

The legacy repository-specific scripts have been consolidated into the `lading`
package structure.

**Proposed Directory Structure:**

```plaintext
lading/
  ├── __init__.py
  ├── cli.py  # Cyclopts app definition and command wiring
  ├── commands/
  │   ├── __init__.py
  │   ├── bump.py  # Logic for the `bump` subcommand
  │   └── publish.py  # Logic for the `publish` subcommand
  ├── config.py  # Frozen dataclasses for `lading.toml`
  ├── utils/
  │   ├── __init__.py
  │   └── path.py  # Filesystem helpers such as `normalize_workspace_root`
  └── workspace/
      ├── __init__.py
      ├── metadata.py  # `cargo metadata` invocation and parsing
      └── models.py  # Workspace graph and manifest helpers

tests/
  ├── conftest.py
  ├── fixtures/
  │   └── simple_workspace/
  │       ├── Cargo.toml
  │       └── lading.toml
  └── test_*.py
```

This structure separates concerns, improves testability, and establishes a
clear architecture for future development.

## 6. Testing Strategy

A robust testing strategy is essential for a tool that modifies source files
and performs releases.

- **Unit Tests:** Each module (`config.py`, `workspace/metadata.py`,
  `workspace/models.py`, command helpers) will have comprehensive unit tests.
  Logic within the `bump` and `publish` commands will be unit-tested with
  mocked filesystem and subprocess calls. Where behaviour is identical across
  inputs (for example, ensuring metadata decoding handles both text and byte
  streams) the suite will rely on parametrized tests to avoid duplication while
  exercising each variant.
- **Integration Tests:** The CLI itself (`cli.py`) will be tested using
  `cyclopts.testing.invoke`. These tests will run against mock workspaces
  defined in `tests/fixtures/` to verify command-line parsing, configuration
  loading, and the orchestration of different modules.
- **End-to-End Tests:** A small suite of end-to-end tests will operate on
  temporary Git repositories. These tests will initialize a Rust workspace,
  commit a `lading.toml`, and then execute `lading bump` and
  `lading publish --dry-run`, asserting that the files are correctly modified
  and the `cargo` commands are executed in the right sequence.

This multi-layered approach will ensure correctness from the lowest-level
utilities to the highest-level user-facing commands.

### Phase 4 testing updates

- Introduced `pytest-cov` as a development dependency, so coverage can be
  reported via `uv run pytest --cov` without additional tooling. Phase 4 sets a
  floor of >90% line coverage for new modules; focused unit tests now exercise
  configuration validation edges, publish manifest handling, cmd-mox IPC
  fallback mechanisms, and workspace model error paths to keep defensive code
  paths observable.
- End-to-end behavioural coverage now lives under `tests/e2e/` and executes
  the CLI in a temporary Git repository. The suite uses real git operations
  (`git init`, `git commit`, `git status`) while stubbing `cargo` interactions
  via cmd-mox (`cargo metadata`, `cargo::check`, `cargo::test`,
  `cargo::package`, and `cargo::publish`) so that workflows can be validated
  without a Rust toolchain.
- cmd-mox remains the default mechanism for mocking external commands. Tests
  covering publish pre-flight and `cargo metadata` IPC use stubbed cmd-mox
  modules rather than spawning real processes, keeping suites deterministic
  while still traversing streaming/IPC code paths.

### Phase 4 documentation updates

- Added `docs/users-guide.md` as the primary end-user guide. It contains an
  installation guide, a tutorial, and a complete `lading.toml` reference.
- Added tests that assert the user guide documents every supported `lading.toml`
  option, so configuration schema changes cannot silently drift out of sync
  with the documentation.
- Standardized release builds via `make build-release`, which uses the
  repository-managed virtual environment (`uv run python -m build`) to produce
  `sdist` and `wheel` artefacts.

## 7. Command Execution Migration (Phase 5)

### 7.1. Rationale for Cuprum Adoption

Production command execution is standard-library `subprocess`, centralized in
one runner module: `lading/runtime/subprocess_runner.py` holds the sole
`subprocess.Popen` call, inside `_spawn_process`, and exposes
`subprocess_runner` as its public entry point, which delegates to
`invoke_via_subprocess`. Plumbum survives only in two end-to-end test helpers,
`tests/e2e/helpers/git_helpers.py` and
`tests/e2e/helpers/e2e_steps_helpers.py`; no module under `lading/` imports it.
`lading/utils/path.py` is already pure pathlib. Lading's logging of external
commands is established rather than ad hoc: `log_command_invocation` in
`lading/utils/process.py` sources exactly one INFO record per external command
invocation, plus a DEBUG record with the redacted environment diff.
[Cuprum](https://github.com/leynos/cuprum/) offers the following advantages as
additions to, or replacements for, that arrangement:

- **Security-first design**: Allowlist-based program registration adds
  enforcement to the staged catalogue; unknown executables raise
  `UnknownProgramError` rather than silently executing arbitrary commands.
- **Unified API**: Both synchronous and asynchronous execution through
  `run_sync()` and `run()`, replacing the split between plumbum in the
  end-to-end helpers and `subprocess` in production.
- **Built-in observability**: Hooks and structured events integrate naturally
  with logging without custom wrappers, complementing the existing
  single-invocation log rather than replacing it.
- **Pipeline composition**: Native support for command pipelines via the `|`
  operator, matching plumbum's ergonomics while adding safety guarantees.
- **Typed command building**: `sh.make()` constructs `SafeCmd` instances from
  curated programs, with keyword arguments transformed into `--flag=value`
  format automatically.

The rationale is bounded by §1 of the
[beta adoption assessment](cuprum-v0-2-0-beta1-adoption-assessment.md#1-recommendation-and-scope),
which records that only the catalogue, the pathlib conversion, and the
source-level assessment were complete when it was written. Its §1.1 now carries
a follow-up recording that the published beta has since been selected and
validated on both dependency paths by step 5.1.4, which also migrated the
release uploader. The bulk of the migration -- the production backend and every
other caller -- remains unimplemented and belongs to 5.2.

### 7.2. Migration Scope

The migration covers six areas:

1. **`lading/runtime/subprocess_runner.py`**: Replace the spawning backend
   behind the existing `lading/runtime/runner.py` `CommandRunner` protocol. The
   module's public `subprocess_runner` entry point and the `subprocess.Popen`
   call inside `_spawn_process` form the only production process-spawning
   boundary, so replacing that backend is the primary migration point.
   Preserving real-time stream relay through the same boundary is what makes it
   the most complex one.
2. **Production callers**: Route every remaining caller through the selected
   adapter. `lading/workspace/metadata.py` already builds a plain argument
   vector and delegates to an injected `CommandRunner`, so it is a caller to
   migrate rather than a spawner. `lading/commands/publish_execution.py` also
   delegates to the production runner and owns error translation and per-crate
   duration timing. The `pathlib` conversion is already the finished part of
   the phase: `lading/utils/path.py` no longer depends on plumbum and carries
   no execution work.
3. **Real cmd-mox passthrough**: Route `lading/testing/cmd_mox_runner.py`
   through the same adapter while preserving IPC routing, cargo subcommand
   namespacing, real-command overrides, PATH filtering, and PWD handling.
4. **End-to-end test helpers**: Migrate both plumbum helpers,
   `tests/e2e/helpers/git_helpers.py` and
   `tests/e2e/helpers/e2e_steps_helpers.py`, preserving `GitCommandError`.
5. **Direct-subprocess test invocations**: Migrate the six test files that
   import `subprocess`: `tests/bdd/steps/test_common_steps.py`,
   `tests/e2e/test_upload_release_wheels_cli.py`,
   `tests/e2e/test_staging_cleanup_on_termination.py`,
   `tests/integration/test_cargo_shim_cli.py`,
   `tests/integration/test_lockfile_discovery.py`, and
   `tests/workflow_contracts/test_lint_target.py`.
6. **Release scripts**: The uploader's cuprum boundary now lives in
   `scripts/release_gh.py`, which holds the release catalogue, the `Program` for
   `gh`, and `run_gh`, the script's only process edge. It uses the beta forms
   -- `scoped(catalogue=RELEASE_CATALOGUE)` and
   `run_sync(output=RunOutputOptions(capture=True))` -- and was migrated in
   5.1.4 alongside the pin. `scripts/release_wheel_upload.py` keeps the upload
   logic and takes the runner as an injected `UploadRunner`, so the logic is
   testable without a process. Dependency-internal subprocess use and the cargo
   shim's `os.execvp` process replacement stay outside this phase.

The sequencing, dependencies, and per-task success criteria for this scope are
recorded in the roadmap's command execution modernization section, and the
streaming, catalogue, and compatibility details follow in §7.3, §7.4, and §7.5.

### 7.3. Catalogue Definition

A project catalogue registers allowed executables. The catalogue is defined in
`lading/utils/commands.py` using Cuprum's `ProgramCatalogue.from_programs`
factory:

```python
from cuprum import Program, ProgramCatalogue

# Program objects for allowed executables
CARGO = Program("cargo")
GIT = Program("git")
# Queried for compiler-cache statistics by `lading publish --sccache-stats`;
# the binary invoked is the one RUSTC_WRAPPER names.
SCCACHE = Program("sccache")

# Shared one-project catalogue for all lading modules
LADING_CATALOGUE = ProgramCatalogue.from_programs(
    CARGO,
    GIT,
    SCCACHE,
    name="lading",
    documentation_locations=("docs/lading-design.md#command-execution-migration",),
)
```

Command construction uses cuprum's scoped context manager, naming the catalogue
directly, and passes the same catalogue to `sh.make()`:

```python
from cuprum import RunOutputOptions, scoped, sh
from lading.utils.commands import CARGO, LADING_CATALOGUE

with scoped(catalogue=LADING_CATALOGUE):
    cargo_builder = sh.make(CARGO, catalogue=LADING_CATALOGUE)
    metadata = cargo_builder("metadata", "--format-version", "1")
    result = metadata.run_sync(output=RunOutputOptions(capture=True))
```

The catalogue is what the scope needs, because every `Program` it registers is
admissible. `ScopeConfig` remains available for the cases a catalogue cannot
express: a narrower allowlist than the catalogue carries, scope-level hooks, a
scope timeout, or an environment overlay.

Two call forms were removed before 0.2.0 and raise `TypeError`: the flat
`scoped(allowlist=...)` and `run_sync(capture=...)` keywords. Capture settings
now travel in a `RunOutputOptions` instance passed as `output=`.

#### Implementation Notes (Step 5.1)

- The catalogue is defined in `lading/utils/commands.py` and exported via
  `lading.utils.LADING_CATALOGUE` for convenient access across the codebase.
- `CARGO` and `GIT` program constants are also exported, enabling type-safe
  command construction without string literals.
- `from_programs` stores the project name and the reference to this design
  document section in the single `ProjectSettings` object it creates, so the
  metadata stays discoverable when debugging catalogue-related issues.
- Unit tests verify catalogue construction, program registration, and
  `UnknownProgramError` handling for unregistered programs.
- Behaviour-driven development (BDD) scenarios document the expected behaviour
  for downstream consumers, including command construction within scoped
  contexts.

#### Implementation notes (Step 5.1.4)

Step 5.1.4 selects the published `cuprum` beta on both dependency paths and
migrates the call sites that the beta broke. The pin has since moved to
`cuprum==0.2.0b2`, which adds the environment and broken-pipe policies §7.5
relies on, without further call-site changes. The selection policy -- an exact
pin on each path, a lock per path, cross-path alignment enforced by a contract
test, and manual bumps -- is
[ADR-006](adrs/adr-0006-align-cuprum-selection-across-dependency-paths.md).

- The repository path is `pyproject.toml` plus `uv.lock`. The standalone paths
  are the PEP 723 metadata blocks in `scripts/upload_release_wheels.py` and
  `scripts/format_markdown.py`, each with its own adjacent lock. Six places
  must name the same version;
  `tests/workflow_contracts/test_cuprum_selection.py` reads the expected
  version from `pyproject.toml` and fails if any site disagrees, so a bump
  moves them together or fails.
- The same test checks that each lock is fresh, reading the Git index rather
  than the working tree: `make build` and the standalone BDD scenario both
  re-lock silently before the suite runs, so a tree-reading check cannot fail.
  Staging the lockfiles is therefore enough to verify them; a commit is not
  required first.
- All call sites moved to the beta forms in the same commit as the pin. No
  compatibility shim accepts both call forms, and no re-export alias remains.
- Above the catalogue, the beta's API is otherwise unchanged: `Program`,
  `ProjectSettings`, `ProgramCatalogue(projects=...)`, `sh.make()`,
  `SafeCmd.argv`, and `UnknownProgramError` all behave as before.
- No GitHub release is published by validation. Every test that runs the
  uploader in a child process uses the stub helper in
  `tests/helpers/gh_stub.py`, which puts the stub first on `PATH`, removes
  `GH_TOKEN` and `GITHUB_TOKEN`, points `GH_CONFIG_DIR` at an empty directory,
  sets `GH_HOST=stub.invalid` and `GH_PROMPT_DISABLED=1`, and uses the tag
  `v0.0.0-stub`.
- The migration is scoped to the pin and the released API. Rewiring the
  production subprocess backend behind `CommandRunner` onto the catalogue stays
  with 5.2, and the routes that still spawn directly are listed in §7.2.

### 7.4. Compatibility with cmd-mox

The existing cmd-mox integration for test isolation must be preserved. The
migration will maintain the `LADING_USE_CMD_MOX_STUB` environment variable
pattern, routing invocations through inter-process communication (IPC) when
enabled. Real passthrough lives in `lading/testing/cmd_mox_runner.py`:
`cmd_mox_runner` is the `CommandRunner`-conforming adapter entry point,
`_handle_cmd_mox_passthrough` performs local real execution through
`invoke_via_subprocess`, and `normalize_cmd_mox_command` namespaces cargo
subcommands for cmd-mox. `_build_cmd_mox_passthrough_env` and
`_merge_cmd_mox_path_entries` build the filtered `PATH`, and the working
directory is carried as `PWD` for the passthrough execution. Behavioural tests
therefore continue to function without a Rust toolchain.

The integration pattern separates catalogue-based command construction from the
actual execution backend. Selection happens in `lading/cli.py`, where
`_select_runner()` returns `cmd_mox_runner` when `LADING_USE_CMD_MOX_STUB` is
set and `subprocess_runner` otherwise, so the execution layer routes commands
through cmd-mox IPC rather than spawning real processes:

```python
from cuprum import ExecutionContext, Program, ProgramCatalogue, sh, scoped

def _invoke(
    program: Program,
    args: tuple[str, ...],
    *,
    catalogue: ProgramCatalogue,
    cwd: Path | None = None,
) -> CommandResult:
    """Execute command, routing through cmd-mox when stub mode is enabled."""
    if _should_use_cmd_mox_stub():
        # Route to cmd-mox IPC server for test isolation
        return _invoke_via_cmd_mox(program, args, cwd)

    # Production path. `catalogue` is the per-invocation catalogue from
    # §7.6.3: LADING_CATALOGUE's programmes plus any validated exact path.
    # Activating it and building from it must name the same object, or a
    # registered path would be denied by the enclosing allowlist.
    # `cwd` travels in the execution context. It is a reserved builder
    # keyword: passing it to the builder raises TypeError, because older
    # cuprum serialized it into a `--cwd=` flag instead.
    with scoped(catalogue=catalogue):
        cmd_builder = sh.make(program, catalogue=catalogue)
        cmd = cmd_builder(*args)
        return cmd.run_sync(context=ExecutionContext(cwd=cwd))
```

This block is a sketch of the intended 5.2 end state rather than today's code.
It takes a `catalogue` argument rather than reaching for `LADING_CATALOGUE`,
because a registered exact path is not in the shared catalogue and nesting
cannot add it (§7.6.3). `lading/cli.py` does define `_select_runner()`, but it
returns a `CommandRunner` and knows nothing about cuprum; the `_invoke` in
`lading/commands/publish_execution.py:47` takes an argument vector and
delegates to the subprocess runner. The released boundary is
`lading/runtime/subprocess_runner.py`, as §7.2 and §7.5 record, and only the
release uploader runs through cuprum today.

Test authors configure expectations via the cmd-mox fixture, and the
`LADING_USE_CMD_MOX_STUB` environment variable controls which execution path is
taken at runtime.

### 7.5. Streaming Output Migration

The `lading/runtime/subprocess_runner.py` module owns the subprocess boundary:
it holds the only `subprocess.Popen` call, inside `_spawn_process`, which is
reached through `subprocess_runner` and `invoke_via_subprocess`. Threaded
stream relay in that module provides real-time output during long-running cargo
operations. `lading/commands/publish_execution.py` only delegates to that
runner and owns error translation and per-crate duration timing. The migration
will retain this behaviour through a cuprum adapter, as follows:

For screen readers: The following sequence shows a Lading caller passing its
working directory, standard input, environment, and relay policy to the Cuprum
adapter. The adapter uses Cuprum to validate and spawn the child process.
Cuprum relays incremental output to the sink while retaining capture if the
sink fails, then returns the child's exit status and captured streams through
the adapter to the caller.

```mermaid
sequenceDiagram
    participant Caller as Lading caller
    participant Adapter as Cuprum adapter
    participant Cuprum as Cuprum
    participant Child as Child process
    participant Sink as Relay sink

    Caller->>Adapter: run command with cwd, stdin, env, and relay policy
    Adapter->>Cuprum: make and run_sync or run
    Cuprum->>Child: spawn validated executable
    Child-->>Cuprum: stdout and stderr incrementally
    Cuprum->>Sink: echo captured output
    Sink-->>Cuprum: continue capture after relay failure
    Child-->>Cuprum: exit status
    Cuprum-->>Adapter: CommandResult
    Adapter-->>Caller: exit_code, stdout, stderr
```

_Figure 3: Cuprum adapter relays incremental output while preserving capture
and returns the command result to the Lading caller._

1. The streaming requirement is already met by cuprum's live stdout/stderr
   relay, so no line-iteration API is required for lading's publish streaming
   requirement.
2. Two differences recorded against 0.2.0-beta1 now have native answers, so
   neither needs a shim. `ExecutionContext(env_mode=EnvMode.REPLACE)` starts
   the child from an empty environment and applies only the supplied mapping,
   which reproduces the runner's existing `Popen(env=...)` contract; the
   default remains an overlay.
   `RunOutputOptions(broken_pipe_policy= BrokenPipePolicy.BEST_EFFORT)` stops
   echoing a stream whose reader has gone away while capture and child reaping
   continue, which reproduces lading's relay policy. The default stays strict.
3. The adapter therefore passes `c_locale_env(normalize_environment(...))` as
   a `REPLACE` environment and bootstraps no `os.execve` child. Echo still goes
   through a text facade over `write_to_relay_sink`, as §7.6.4 requires: the
   facade is what preserves the sticky binary fallback and emits the ADR-007
   events, which `BEST_EFFORT` does not replace. `BEST_EFFORT` is selected as a
   secondary guard that should stay unfired while the facade absorbs the error.
   The facade must avoid exposing a raw `.buffer`, because cuprum writes raw
   bytes to that attribute instead of taking the text path.
   `max_echo_line_bytes=None` is selected for current unbounded relay parity.
   The two opt-ins are pinned by `tests/unit/test_cuprum_policies.py`
   (`EnvMode`) and `tests/unit/test_cuprum_echo_policies.py`
   (`BrokenPipePolicy`).
4. Cargo output continues to be relayed as it is produced, not buffered until
   completion, and recorded output remains complete.

The goal is to eliminate the current split between plumbum and subprocess while
preserving the user experience of seeing cargo output as it happens rather than
buffered at completion. Sections 1, 3, 4, and 7 of the
[beta adoption assessment](cuprum-v0-2-0-beta1-adoption-assessment.md) assess
these outcomes.

### 7.6. Adapter Compatibility Policy (Step 5.1.5)

This section is the policy a maintainer implements in 5.2. It is not the
adapter: `lading/runtime/subprocess_runner.py` still owns the only production
spawn call, and every statement below is about the boundary that replaces it.
The substantive decision, its alternatives, and the rejected options are
[ADR-012](adrs/adr-0012-record-command-runner-adapter-compatibility-policy.md).

Five requirements bound the adapter. R1 is the result and failure boundary, R2
the child environment, R3 executable authorization and resolution, R4 the
relay, and R5 the caller and cmd-mox boundary. Each states behaviour that the
beta2 API can reproduce exactly; where it cannot, the residual gap is named
rather than smoothed over. The API facts here were measured against the pinned
`cuprum==0.2.0b2` on CPython 3.14/Linux while preparing this policy.

#### 7.6.1. Result and failure boundary (R1)

`CommandRunner(command, *, cwd=None, env=None, echo_stdout=True)` keeps its
signature and returns `tuple[int, str, str]`. The adapter always requests
capture, so both streams are decoded text; a non-zero exit is data and never an
exception. `SafeCmd.run_sync()` returns a `CommandResult` whose `stdout` and
`stderr` are `None` only when capture is off, which the adapter never selects.
Callers never see a cuprum type.

Every failure is classified before it reaches a caller. The mapping is fixed:

| Situation                                            | Cuprum behaviour                             | Adapter result                               |
| ---------------------------------------------------- | -------------------------------------------- | -------------------------------------------- |
| Empty argument vector                                | No call is made                              | `ValueError`, as today                       |
| Program absent from the catalogue given to `sh.make` | `UnknownProgramError` (`LookupError`)        | `CommandPreparationError`, chained           |
| Program denied by the active scope allowlist         | `ForbiddenProgramError` (`PermissionError`)  | `CommandPreparationError`, chained           |
| Executable-binding resolver failed                   | `ExecutableResolutionError` (`RuntimeError`) | `CommandPreparationError`, chained           |
| Launch failed: absent, non-executable, or bad `cwd`  | `FileNotFoundError` or `PermissionError`     | `CommandSpawnError(program, cause)`, chained |
| Child exited non-zero                                | A result with `exit_code != 0`               | `(exit_code, stdout, stderr)`, data          |

_Table 1: Failure classification for the cuprum adapter._

Two properties of that table are load-bearing.

`ForbiddenProgramError` subclasses `PermissionError`, so it is an `OSError`. An
adapter that wraps the call in `except OSError` and raises `CommandSpawnError`
would report a policy rejection as a missing binary. The adapter must therefore
match the cuprum preparation errors explicitly, and only then the launch
`OSError`. `ExecutableResolutionError` subclasses `RuntimeError` instead, so
the same blanket handler would miss it entirely. Both are the reason the
mapping is written out rather than inferred from the exception hierarchy.

`CommandPreparationError` does not exist yet. It is the one exception type this
policy adds, in `lading/runtime/` beside the adapter, and 5.2.1 introduces it
with the adapter itself. It exists because the alternative -- reusing
`CommandSpawnError` for policy rejection -- would make
`lading/workspace/metadata.py` translate a catalogue or scope denial into
`CargoExecutableNotFoundError`, the misleading missing-Cargo diagnostic this
policy forbids. It subclasses `LadingError`, carries the requested program name
and the cuprum cause, and is caught nowhere except where a caller already
handles a lading failure. No domain caller imports a cuprum exception.

A launch `OSError` is not narrowed by inspecting the message or the errno. A bad
`cwd` raises `FileNotFoundError` exactly as a missing executable does, and a
directory or non-executable file raises `PermissionError`, so the adapter
reports all of them as `CommandSpawnError` and leaves the distinction to a
caller that can check `cwd` before launching. Existing translations are
unchanged: `metadata` still maps a `CommandSpawnError` naming `cargo` to
`CargoExecutableNotFoundError`, and publish still maps a spawn failure to
`PublishPreflightError`. Those translations now run only for their existing
spawn case, because preparation failures no longer reach them.

One caller classifies by cause and must be extended in the same commit as 5.2.1.
`lading/commands/bump_lockfile_regeneration.py` maps the cause of a
`LockfileRegenerationError` to the bounded metric label `command_spawn`,
`runner_value`, or `cargo_exit`. It catches only `CommandSpawnError` and
`ValueError` today, so a new preparation failure would be recorded as
`cargo_exit` -- a cargo exit that never happened. 5.2.1 adds the prepared error
to that handler and to the label set, and the metric contract in the
developers' guide gains the label in the same change.

#### 7.6.2. Child environment (R2)

`env=None` inherits the parent environment with the existing C-locale
normalization. A supplied mapping, including `{}`, replaces inheritance. Both
compose as they do today: values are stringified, then
`lading.utils.process.c_locale_env` pins `LC_ALL=C`, `LANG=C` and `LANGUAGE=''`.

The adapter expresses replacement with
`ExecutionContext(env=<composed>, env_mode=EnvMode.REPLACE)`, importing
`EnvMode` from `cuprum.context`. Three measured properties make that a faithful
reproduction of `Popen(env=...)`:

- A per-call `REPLACE` starts from an empty environment and applies only the
  supplied mapping. A parent sentinel and an ambient `env(...)` overlay set by
  an enclosing scope are both absent in the child, while `os.environ` in the
  parent is unmodified.
- `env={}` and `env=None` under `REPLACE` both produce the empty environment,
  so the `None`-means-inherit distinction is made by the adapter, not by
  cuprum. Forwarding `None` unchanged would give the child an empty
  environment, which is why the adapter composes the parent environment into an
  explicit mapping before the call. `None` genuinely inherits only under the
  default `OVERLAY`.
- With the C-locale pins present, the child's key set is exactly the supplied
  keys plus those three. CPython adds `LC_CTYPE` only when the environment
  carries no locale variable at all (PEP 538), so it does not appear here.

Ambient scope overlays are discarded by a per-call `REPLACE`, which is why the
adapter does not need to compose against, or clear, anything scope-related.
Nothing in this path mutates `os.environ`; the prohibition is absolute, because
a mutation would race every other command in the process.

`lading/testing/cmd_mox_runner.py` keeps its distinct composition step. It
builds an inherited environment from `os.environ`, merges the caller's mapping,
adds `PWD`, and filters `PATH`; that final mapping is then handed to real
execution as an exact replacement, not an overlay. Composition before the
boundary and replacement at it are separate decisions, and only the second is
this policy's subject.

An exec shim is not selected. beta2's documented replacement mode closes the
gap that motivated the beta1 proposal, and adding a bootstrap would introduce a
second error boundary for no contract gain. If a future beta regresses
replacement, the deviation is a design change, not an implementation choice: a
shim would need an isolated interpreter bootstrap, a target resolved against
the intended `PATH` and `cwd`, values carried in the environment rather than in
secret-bearing argv, inherited stdin or an explicit payload, signal and exit
parity, the target's identity in logs, and a spawn-error channel that can never
be confused with a target exit code. The bootstrap inherits the parent
environment and is not an isolation boundary.

#### 7.6.3. Executable authorization and resolution (R3)

Authorization has three separate surfaces, and the adapter keeps them separate.

**Catalogue membership** is checked once, when `sh.make()` builds the command.
The adapter passes the intended catalogue explicitly and activates the same one
with `scoped(catalogue=...)`, so the builder resolves it without relying on the
active scope.

**Execution scope** is checked when the command runs, against the innermost
allowlist. A catalogue scope narrows the allowlist to exactly that catalogue's
programs, and nested catalogue scopes intersect: a program in an inner
catalogue but not in the enclosing one is still denied. Measured, with an outer
catalogue holding both programmes and an inner one holding only the first: the
first ran, the second raised `ForbiddenProgramError`, and reversing the nesting
denied the second again. A narrower `scoped(ScopeConfig(allowlist=...))` is
likewise preserved. The adapter therefore uses an appropriately narrow scope
and never assumes that naming a catalogue grants permission.

**Executable identity** is what actually runs. The adapter registers the
validated executable as an exact-path `Program`, and that registration must be
in the _same_ catalogue it activates -- not in a separate local one. Two
catalogues cannot be combined by nesting, because their allowlists intersect
rather than union: an absolute-path `Program` registered only in an inner
catalogue is denied while an outer catalogue that lacks it is active. So the
adapter builds one per-invocation catalogue carrying the intended programmes
plus each validated exact path, activates it, and builds from it.

A matching basename is not enough. Catalogue lookup is keyed by the whole
`Program` string, so registering one path authorizes that path and not another
executable of the same name. The per-invocation catalogue is derived from
`LADING_CATALOGUE` rather than replacing it, is scoped to one invocation, and
the adapter never permits all programs.

A configured `RUSTC_WRAPPER` is **not** always absolute, so it cannot be
registered as it stands. `detect_wrapper` accepts any value whose basename
starts with `sccache`, and its tests pin the bare-name case:
`RUSTC_WRAPPER=sccache` returns `Path("sccache")`.

`detect_wrapper` returns a `Path` and `_query` passes `str(wrapper)` to the
runner, so the exact string the operator wrote does not survive: `Path`
normalizes, and the runner sees the normalized form. Which resolution applies
therefore depends on what that form looks like, and two cases behave
differently. Both were measured with positive and negative controls:

- **No separator -- a bare name.** `RUSTC_WRAPPER=sccache` reaches the runner as
  `sccache` and resolves on `PATH`. `./sccache` normalizes to the same string,
  so it takes the same route and also resolves on `PATH`, not against `cwd`.
- **A separator -- a relative path.** `RUSTC_WRAPPER=bin/sccache` keeps its
  slash, and the platform resolves it against the working directory, not
  `PATH`. Confirmed in both directions: a file under `PATH` alone did not
  satisfy it, and a file under `cwd` did. A bare name showed the mirror image,
  resolving from `PATH` and failing when only `cwd` held the file.

The policy preserves both routes rather than flattening them, so an operator
who names a relative path keeps the `cwd`-relative meaning they already have.
The adapter resolves a non-absolute wrapper accordingly -- bare names against
the effective child `PATH`, values containing a separator against the requested
`cwd` -- and uses an absolute value as written. It does not "correct" a
relative path to a `PATH` lookup, which would change current behaviour.

Resolution yields an absolute, existing, executable path, and _that_ is what
gets registered. Registration always happens after resolution, never on the
operator's raw string.

A wrapper that does not resolve, or that resolves to something not executable,
keeps today's classification: `CommandSpawnError`. It is a missing executable,
and `lading/runtime/subprocess_runner.py` already maps the launch `OSError` for
one to `CommandSpawnError`; reclassifying it as a preparation failure would
change the chained cause an existing caller sees, for no contract gain. Only
catalogue membership, scope permission and resolver failure are preparation
errors. The distinction this policy adds is the other one: a missing wrapper is
never a catalogue or scope denial, so it never produces the misleading
missing-Cargo diagnostic.

Cuprum's `bind_executable()` is the alternative and is not selected for 5.2. It
separates the logical program from the file executed and reports the file as
`CommandResult.resolved_path`. It does not widen authorization: binding a
program the scope does not allow still raises `ForbiddenProgramError` before
the resolver runs. It does not narrow it to the bound file either, and that
asymmetry is the part a future binding must not inherit. Measured: a scope
permitting only the logical `Program`, bound to an unrelated executable, ran
that executable with no check on it at all. Authorization named the program;
resolution named the file. Exact-path registration is preferred here precisely
because it ties the two together -- the path registered is the path authorized
-- and these callers have no stable logical name to preserve anyway. A future
binding must keep that tie by authorizing the bound path separately, exactly as
exact-path registration does, rather than assuming the binding vouches for the
file it names.

Resolution of a bare name against the effective child environment is cuprum's,
and its measured behaviour is part of this policy:

- A bare name resolves against the child's `PATH` and the requested `cwd`.
- A relative `PATH` entry is anchored to the child's `cwd` when one is given,
  and otherwise to the parent process's own working directory.
- An **absent** `PATH` falls back to the platform's default search path, so a
  bare name may still resolve even though the caller supplied no `PATH` at all.
- An **empty** `PATH` is not the same thing and is not a "search nothing"
  instruction. POSIX treats its single empty entry as the child's working
  directory, so a bare name resolves from `cwd` and fails only when the file is
  not there. Measured on the pinned cuprum, CPython 3.14: `PATH=""` with the
  executable in the child's `cwd` ran it, and the same `PATH` without it raised
  `FileNotFoundError`. Both directions match a plain `subprocess` control, so
  this is the platform's rule rather than a cuprum behaviour. The adapter never
  substitutes the parent's `PATH` for either case, and it does not treat an
  empty `PATH` as a way to forbid lookup.
- A relative target such as `./tool` resolves against `cwd` when one is given,
  and otherwise against the parent process's own working directory. That is a
  different mechanism from the `PATH` search a bare name uses, and it is not a
  substitute for one. The target succeeds when the file is there and fails at
  the operating system level when it is not; cuprum itself does not reject it.
- A bound relative path is rejected unless the binding opts into
  `allow_relative=True`. With it, a `cwd` anchors the binding and
  `resolved_path` reports the anchored file; without one the path is passed
  through unchanged, so the platform resolves it -- a bare name on `PATH`, a
  path with a separator against the parent's own directory. Prefer an absolute
  binding whenever the same file must run regardless of where execution starts.
- A symlink is reported as the configured path, not its target. Following the
  link is the operating system's job.

None of this is a sandbox. Validation happens before the launch, and the file
can be replaced between the check and the `exec`, so a check-and-use race
remains possible and must not be advertised as protection. Interpreter, `make`
and fixture executables stay in test-only catalogues and never enter the
production one.

#### 7.6.4. Text-first relay (R4)

The adapter selects `capture=True`, `echo_stdout` from the caller's
`echo_stdout`, `echo_stderr=True`, and `max_echo_line_bytes=None`. That
reproduces today's contract: both streams are captured completely, metadata
stdout stays silent, and every other command's stderr is echoed live. Omitting
`max_echo_line_bytes=None` would silently truncate a long line in the mirror,
because the default bounds each echoed line at 64 KiB.

Echo reaches a text facade the adapter supplies as
`ExecutionContext.stdout_sink` and `stderr_sink`. The facade satisfies only
`write` and `flush`, holds no `.buffer`, and delegates both to
`lading.runtime.stream_relay.write_to_relay_sink`, keeping the
`(active_sink, binary_sink)` state it returns for each stream. That is what
preserves the current policy:

- UTF-8 decoding with replacement is the capture path, so undecodable bytes
  never raise and never truncate what a caller receives.
- A parent text stream that rejects a payload with `UnicodeEncodeError` is
  flushed, then the exact UTF-8 bytes go to that stream's binary `buffer`, and
  the selection is sticky for later payloads. A text-only stream without a
  buffer is disabled for the rest of that stream, and capture continues.
- A `BrokenPipeError` disables mirroring for that stream and nothing else.
- Each of those decisions emits the ADR-007 event it emits today.

The facade must not expose `.buffer`. Cuprum prefers a sink attribute named
`buffer` over the text path and writes raw bytes to it, which would bypass the
helper and lose both the encoding-fallback decision and its event. Only the
helper may reach the underlying sink's binary buffer.

`RunOutputOptions(broken_pipe_policy=BrokenPipePolicy.BEST_EFFORT)` is selected
as a second-line guard. Because the facade absorbs `BrokenPipeError` itself and
returns normally, cuprum's policy should never fire in production; it is set so
that a path which bypasses the facade cannot abort the run and lose the result.
The division of records is fixed: the facade's ADR-007 event is the record of a
relay decision, and a `CommandResult.relay_fallbacks` entry means the facade
failed to absorb a sink failure. Emitting both for one decision is a defect.

Complete capture retains a stream's output in full, so a large output keeps its
current memory cost. That is the capture path, and it is where the cost lives.
The echo path is not additionally buffered: `max_echo_line_bytes=None` makes
cuprum build no line limiter at all and write each chunk straight to the
facade, so an unterminated line costs the facade nothing while it accumulates
and only the capture buffer grows. This policy makes no bounded-memory claim
and adds no truncation limit. It also promises no ordering between stdout and
stderr; each stream is ordered with itself, and the two are relayed
concurrently.

#### 7.6.5. Caller and cmd-mox boundary (R5)

Arguments stay positional. Command-specific construction stays in the caller;
cuprum's generic keyword builder would serialize `check=True` as
`--check=True`, which is wrong for Cargo, so the adapter never uses it.

Stdin keeps its three distinct meanings. No `stdin` argument inherits the
parent's. `StdinInput(text=...)` supplies text, `StdinInput(data=...)` supplies
bytes, and an empty string supplies empty input and closes the stream -- the
three are not interchangeable. Early child exit with unread input remains the
child's business, as it is today.

`cmd_mox_runner` keeps its IPC routing, cargo subcommand namespacing,
real-command overrides, `PATH` filtering, `PWD` handling, stdin, and result
reporting unchanged. Only its real passthrough invocation moves to the adapter,
so passthrough keeps live output without recursively re-entering the shim.

Synchronous and asynchronous execution keep their existing boundaries.
`run_sync()` calls `asyncio.run()`, so it raises `RuntimeError` inside a
running event loop; an async caller awaits `run()`. The adapter must not nest
`asyncio.run()` to paper over that, and any thread bridge that is later needed
must carry context-local scope and hooks with it.

Logging is unchanged: exactly one INFO record per external command invocation
from `log_command_invocation`, the existing redacted DEBUG environment record,
and the existing per-crate duration metrics. Cuprum's own observers stay unused
unless a change deliberately adds one, because a second invocation record
duplicates the first.

One adapter serves every caller. No second generic runner abstraction is
introduced, and the `CommandRunner` protocol remains the only seam a test
injects.

## 8. Release Publication

Every tagged build uses plain `uv build` to create one source distribution and
build all pure-Python wheels from it. Pull-request CI requires exactly one
sdist and at least one wheel; release collection repeats the sdist check before
publication. The artefact is published to PyPI through Trusted Publishing, then
attached to a GitHub Release as a second target.

Each target skips exact filenames it already contains, so rerunning a tag
completes without replacing assets. PyPI names are read from its PEP 691 simple
index, with `uv publish --check-url` as a second check during upload. GitHub
asset names are read through the cuprum `gh` allowlist, and the release remains
a draft until missing files are attached. This is filename idempotency; a
rebuilt distribution may have different bytes under an existing filename, and
this workflow does not reconcile those bytes across targets. The process
uploads no PEP 740 attestations.

[ADR-009](adrs/adr-0009-pypi-first-distribution-publication.md) records the
decision and trade-offs; [ADR-005](adrs/adr-0005-release-wheel-publication.md)
records the original GitHub-only flow it amends. Operational detail is in the
[developers' guide](developers-guide.md#publishing-a-release).
