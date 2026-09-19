# Lading Tool Development Roadmap

## 1. Foundation and Core Abstraction

**Objective:** Establish the foundational structure of the `lading` tool,
including the CLI, configuration management, and workspace discovery. This
phase decouples the logic from the old repository-specific scripts and creates
a solid base for the new, generalized functionality.

______________________________________________________________________

### 1.1. Project Scaffolding and CLI Structure

**Description:** Create the new Python project structure for `lading` and
implement the basic command-line interface using `cyclopts`.

**Tasks:**

- [x] **Initialize New Project:**

  - **Outcome:** A new Python package named `lading` is created with a
    `pyproject.toml` file.
  - **Completion Criteria:** The project is configured with `uv`, `ruff`, and
    `pytest`. The directory structure matches the one outlined in the design
    document.

- [x] **Implement `lading` CLI Shell:**

  - **Outcome:** A `cyclopts`-based CLI application is implemented with `bump`
    and `publish` subcommands.
  - **Completion Criteria:** The CLI runs and correctly dispatches to
    placeholder functions for each subcommand. It accepts the global
    `--workspace-root` option.

- [x] **Configuration Loading:**

  - **Outcome:** The tool can locate and parse a `lading.toml` file from the
    workspace root.
  - **Completion Criteria:** A Pydantic or dataclass model for `lading.toml` is
    defined. The CLI successfully loads and validates the configuration file,
    making its values accessible to the application.

______________________________________________________________________

### 1.2. Workspace Discovery and Modelling

**Description:** Implement the core logic for inspecting a Rust workspace using
`cargo metadata`.

**Tasks:**

- [x] **Implement `cargo metadata` Wrapper:**

  - **Outcome:** A Python function exists that executes
    `cargo metadata --format-version 1` as a subprocess and captures its JSON
    output.
  - **Completion Criteria:** The function correctly handles command execution
    errors and returns the parsed JSON data. It is covered by unit tests using
    a mocked subprocess.

- [x] **Develop Workspace Data Model:**

  - **Outcome:** A set of `msgspec.Struct` models represents the workspace
    graph, including crates, dependencies, and manifest paths.
  - **Completion Criteria:** The models can be successfully instantiated from
    the JSON output of `cargo metadata` for a representative test workspace.

- [x] **Integrate Discovery into CLI:**

  - **Outcome:** The `lading` CLI builds the workspace model upon invocation.
  - **Completion Criteria:** Both `bump` and `publish` commands have access to
    the complete, populated workspace model.

## 2. `lading bump` Subcommand Implementation

**Objective:** Deliver a fully functional, configuration-driven `bump` command
that correctly propagates version changes across all relevant files in a
generic Rust workspace.

______________________________________________________________________

### 2.1. Version Propagation in `Cargo.toml`

**Description:** Implement the core logic for updating version numbers in the
workspace and member crate manifests.

**Tasks:**

- [x] **Update Workspace and Member Versions:**

  - **Outcome:** The `bump` command can modify the `version` field in the main
    `Cargo.toml` and all member `Cargo.toml` files.
  - **Completion Criteria:** An integration test confirms that running
    `lading bump 1.2.3` on a fixture workspace results in all `package.version`
    fields being updated to `1.2.3`.

- [x] **Update Internal Dependency Versions:**

  - **Outcome:** The command updates the version specifiers for all
    dependencies that are internal to the workspace.
  - **Completion Criteria:** An integration test verifies that
    `[dependencies]`, `[dev-dependencies]`, and `[build-dependencies]` sections
    are correctly updated to point to the new workspace version, preserving
    operators like `^` or `~`.

- [x] **Implement `--dry-run` Mode for Bumping:**

  - **Outcome:** The `--dry-run` flag prevents any file modifications and
    instead prints a summary of intended changes. Tracked `Cargo.lock` files
    are refreshed after manifest rewrites in live mode; in dry-run mode they
    are reported but not modified (closes `#61`).
    Configurable lockfile rebuild (closes `#65`): `lading bump` regenerates
    `Cargo.lock` files for the workspace root and any `lockfile_manifests`
    listed in `lading.toml` by default; regeneration is suppressed by setting
    `rebuild_lockfiles = false` in configuration or passing
    `--no-rebuild-lockfiles` on the command line.
  - **Completion Criteria:** Running `lading bump 1.2.3 --dry-run` produces a
    report of all files that would be changed, and a subsequent check confirms
    no files were actually modified.

______________________________________________________________________

### 2.2. Documentation and README Synchronization

**Description:** Implement the logic for updating documentation files and
handling workspace READMEs.

**Tasks:**

- [x] **Implement Configurable Documentation Updates:**

  - **Outcome:** The `bump` command updates version numbers within TOML code
    fences in documentation files, using new configuration introduced alongside
    this feature.
  - **Completion Criteria:** A test case with a fixture workspace containing a
    `README.md` with a TOML snippet confirms the version inside the snippet is
    correctly updated.

- [x] **Implement Workspace README Handling:**

  - **Outcome:** The `bump` command copies the workspace `README.md` to member
    crates that have `package.readme.workspace = true` set, rewriting relative
    Markdown links for the crate directory.
  - **Completion Criteria:** End-to-end tests verify that bump creates adopted
    crate README files before publish runs.

## 3. `lading publish` Subcommand Implementation

**Objective:** Deliver a robust `publish` command that safely and correctly
publishes workspace crates in the right order, with comprehensive pre-flight
checks.

______________________________________________________________________

### 3.1. Publication Planning and Pre-flight Checks

**Description:** Implement the logic to determine which crates to publish, in
what order, and to validate the workspace state before any publication actions
occur.

**Tasks:**

- [x] **Determine Publishable Crates:**

  - **Outcome:** The `publish` command can identify all publishable crates from
    the workspace model.
  - **Completion Criteria:** The logic correctly filters out crates with
    `publish = false` and those listed in the `publish.exclude` configuration.

- [x] **Implement Topological Sort for Publish Order:**

  - **Outcome:** The command can generate a valid publication order based on
    the workspace dependency graph.
  - **Completion Criteria:** The implementation correctly sorts a test case
    with a multi-level dependency chain and correctly reports an error for a
    workspace with a dependency cycle. The explicit `publish.order`
    configuration is also honoured when present.

- [x] **Implement Pre-Publish Checks:**

- **Outcome:** The command executes `cargo check` and `cargo test` against the
    workspace after verifying the working tree is clean before proceeding.
  - **Completion Criteria:** A test confirms that the publish command fails if
    either of the pre-flight checks fails. The `--forbid-dirty` flag restores
    the cleanliness guard when operators need to enforce it.
    - Tracked `Cargo.lock` files are validated for freshness under `--locked`
      before the `cargo check`/`cargo test` pre-flight; stale lockfiles surface
      an actionable `cargo generate-lockfile` repair command (closes `#61`).

______________________________________________________________________

### 3.2. Crate Packaging and Publication

**Description:** Implement the final steps of packaging each crate and
interacting with the `cargo publish` command.

**Tasks:**

- [x] **Implement Configurable Patch Stripping:**

  - **Outcome:** The command correctly modifies the workspace `Cargo.toml` in
    the temporary clone according to the `publish.strip_patches` strategy
    (`all`, `per-crate`, or `false`).
  - **Completion Criteria:** Unit tests verify that the manifest is correctly
    manipulated for each of the three strategies.

- [x] **Implement Crate Packaging Loop:**

  - **Outcome:** The command iterates through the publish list, executing
    `cargo package` for each crate.
  - **Completion Criteria:** A dry-run test confirms that `cargo package` is
    called for each publishable crate in the correct order.

- [x] **Implement `cargo publish` Execution:**

  - **Outcome:** The command executes `cargo publish` for each crate,
    supporting both batched dry-run mode and interleaved per-crate live mode
    for internal dependency release trains (closes `#59`). The
    `--allow-unpublished-workspace-deps` dry-run-only flag (closes `#60`)
    downgrades index-lookup failures for in-plan workspace dependencies to
    warnings, enabling CI dry-run workflows on multi-crate release trains.
    Issue `#89` tightens this dry-run path so the downgrade applies only when
    the missing dependency is projected to have been published earlier in the
    current publish order, and makes that behaviour the dry-run CLI default
    with an explicit opt-out flag.
  - **Completion Criteria:** A dry-run test verifies that
    `cargo publish --dry-run` is called correctly. The logic gracefully handles
    and logs when a crate version is already published, then continues to the
    next crate.

## 4. Stabilization and Documentation

**Objective:** Ensure the `lading` tool is robust, well-tested, and has clear
documentation for end-users.

______________________________________________________________________

### 4.1. Testing and Quality Assurance

**Description:** Finalize the test suite, focusing on edge cases and end-to-end
behaviours.

**Tasks:**

- [x] **Achieve High Test Coverage:**

  - **Outcome:** The entire `lading` codebase has a high level of unit and
    integration test coverage.
  - **Completion Criteria:** Code coverage reports show over 90% line coverage
    for all new modules.

- [x] **Create End-to-End Test Suite:**

  - **Outcome:** A suite of tests exists that covers the full user workflow in
    a temporary Git repository.
  - **Completion Criteria:** At least one end-to-end test exists for `bump` and
    one for `publish --dry-run` that validates the full sequence of operations
    on a non-trivial fixture workspace.

- [x] **Stop double-logging external command invocations (`#104`):**

  - **Outcome:** `subprocess_runner` emits exactly one INFO-level invocation
    log per external command; the redundant DEBUG spawn log is removed.
  - **Completion Criteria:** Regression tests in
    `tests/unit/test_subprocess_runner_logging.py` assert a single INFO record
    and the absence of any "Spawning subprocess:" record.

______________________________________________________________________

### 4.2. User Documentation and Release

**Description:** Prepare user-facing documentation and package the tool for
distribution.

**Tasks:**

- [x] **Write User Guide:**

  - **Outcome:** A comprehensive `README.md` or set of documentation files
    exists for the `lading` project.
  - **Completion Criteria:** The documentation includes an installation guide,
    a tutorial, and a complete reference for the `lading.toml` configuration
    file.

- [x] **Package for PyPI:**

  - **Outcome:** The `lading` tool is packaged as a standard Python wheel.
  - **Completion Criteria:** The `pyproject.toml` is fully configured for
    building a distributable package, and a successful build can be triggered.

## 5. Command execution modernization

Idea (re-architecting): one cuprum execution adapter behind `CommandRunner` can
remove lading-owned subprocess and plumbum execution while preserving command
results, real-time relay, domain errors, and cmd-mox isolation.

The [beta adoption assessment](cuprum-v0-2-0-beta1-adoption-assessment.md)
defines the compatibility requirements and acceptance gates. The current work
delivers that assessment, this reconciled plan, and deduplicated upstream
tracking. It does not implement the migration or certify a release artefact.
Only the catalogue, pathlib conversion, and source-level assessment are
complete; all execution migration and release validation below remain open.

Scope includes the production runner and every caller, real cmd-mox
passthrough, six test files importing `subprocess`, two plumbum test helpers,
and the existing cuprum release uploader. Dependency-internal subprocess use
and the cargo shim's `os.execvp` process replacement are outside this phase.
General-purpose upstream extensions are tracked separately in
[assessment §6.7](cuprum-v0-2-0-beta1-adoption-assessment.md#67-upstream-tracking-after-issue-and-roadmap-reconciliation);
they are not mandatory additions to the final 0.2.0 surface or prerequisites
when the documented compatibility shims preserve the required behaviour.

### 5.1. Establish the beta contract and dependency boundary

This step settles whether the actual beta and the existing command catalogue
can support one adapter. It fixes known API incompatibilities before changing
production execution. See the assessment §§1-3 and §7.

- [x] 5.1.1. Define the lading programme catalogue.
  - `lading/utils/commands.py` registers `cargo`, `git`, and `sccache` in
    `LADING_CATALOGUE`; enforcement is not yet wired into the production runner.
- [x] 5.1.2. Normalize workspace paths with `pathlib`.
  - `lading/utils/path.py` no longer depends on `plumbum.local.path()`.
- [x] 5.1.3. Record source-level beta compatibility and streaming evidence.
  - The assessment records live capture/relay probes, environment and
    broken-pipe differences, API incompatibilities, and upstream PR coverage.
    This completes the streaming evaluation, not the adapter implementation.
  - Remaining upstream gaps have deduplicated issue links in assessment §6.7;
    completed and already-planned capabilities were not filed again.
- [ ] 5.1.4. Select and validate the published beta for both dependency paths.
  - Replace the current cuprum 0.1.0 lock with an explicit beta selection in
    `pyproject.toml` and `uv.lock`; align inline dependency metadata in
    `scripts/upload_release_wheels.py`.
  - Update `scripts/release_wheel_upload.py:run_gh` to use `ScopeConfig` and
    `RunOutputOptions`, replacing the removed flat keyword forms.
  - Success: the installed beta works through repository and standalone script
    execution with a stub `gh`; no GitHub release is published by validation.
- [ ] 5.1.5. Record the adapter compatibility policy in the design document.
  - Preserve the existing `CommandRunner` tuple and exception contracts;
    separate catalogue rejection from actual spawn `OSError`.
  - Specify environment inheritance versus exact replacement, an exec shim
    where necessary, validated executable-path registration, and text-first
    relay compatibility without exposing a bypassing raw buffer.
  - Success: the decision is recorded in `docs/lading-design.md` §7 and an
    ADR where substantive; no global `os.environ` mutation or retained
    subprocess backend is needed to implement it.

### 5.2. Route production and passthrough through one adapter

This step proves that the cuprum boundary preserves the current execution
contract across metadata, publishing, lockfiles, and compiler-cache queries. It
reuses the existing runner protocol and relay policy. See the assessment §§3-4
and §7.

- [ ] 5.2.1. Replace the concrete production subprocess backend with cuprum.
  - Requires 5.1.4 and 5.1.5.
  - Replace `lading/runtime/subprocess_runner.py` spawning behind
    `CommandRunner`; use the explicit catalogue plus scoped allowlist and
    preserve positional argument vectors and `(exit_code, stdout, stderr)`.
  - Retain C-locale normalization, cwd, input handling, and exact environment
    omission where requested; validate configured sccache and resolved paths.
  - Success: non-zero exits remain data, spawn failures remain
    `CommandSpawnError`, and missing Cargo still becomes
    `CargoExecutableNotFoundError`; `UnknownProgramError` remains a catalogue
    error, not executable discovery.
- [ ] 5.2.2. Preserve incremental relay and capture through the adapter.
  - Requires 5.2.1.
  - Capture both streams, keep metadata stdout silent and stderr live, and
    select `max_echo_line_bytes=None` for current unbounded relay parity.
  - Reuse the relay policy in a sink facade for UTF-8 boundaries, narrow
    encodings, binary fallback, and broken-pipe suppression; remove redundant
    pipe-draining threads once cuprum owns draining.
  - Success: output arrives before exit, including partial lines; capture
    survives rejected sinks and lines over 64 KiB without truncation.
- [ ] 5.2.3. Route every production caller through the cuprum adapter.
  - Requires 5.2.2.
  - Update runner selection and defaults in the CLI, workspace metadata,
    publish/preflight, lockfile discovery/regeneration, and sccache statistics.
    Metadata already uses an injected runner; `_ensure_command()` is obsolete.
  - Preserve one invocation log, environment redaction, per-crate timing, and
    domain error translation; make additional cuprum telemetry opt-in.
  - Success: these workflows pass through the installed beta while their
    existing externally observable contracts remain covered.
- [ ] 5.2.4. Migrate real cmd-mox passthrough to the same adapter.
  - Requires 5.2.2.
  - Preserve `lading/testing/cmd_mox_runner.py` IPC, cargo namespacing,
    real-command overrides, PATH filtering, PWD, stdin, and result reporting.
  - Success: stub and real-passthrough cases preserve streaming and one
    invocation log without recursively executing the command shim.

### 5.3. Remove subprocess and plumbum from test execution

This step proves that fixture setup and real CLI tests can use the same beta
without losing independent process-lifecycle checks. Keep interpreter, make,
and fixture executables in test-only catalogues. See the assessment §2, §4, and
§7.

- [ ] 5.3.1. Migrate both plumbum end-to-end helpers.
  - Requires 5.1.4.
  - Update `tests/e2e/helpers/git_helpers.py` and
    `tests/e2e/helpers/e2e_steps_helpers.py`; preserve `GitCommandError`.
- [ ] 5.3.2. Migrate capture-oriented direct-subprocess test invocations.
  - Requires 5.2.1.
  - Cover `tests/bdd/steps/test_common_steps.py`,
    `tests/e2e/test_upload_release_wheels_cli.py`,
    `tests/integration/test_cargo_shim_cli.py`,
    `tests/integration/test_lockfile_discovery.py`, and
    `tests/workflow_contracts/test_lint_target.py`.
  - Success: checked exits and captured output retain their semantics,
    deliberately omitted GitHub environment variables stay absent, and no
    subprocess result or exception types remain in these helpers.
- [ ] 5.3.3. Migrate the SIGTERM staging-cleanup process fixture.
  - Requires 5.1.4.
  - Update `tests/e2e/test_staging_cleanup_on_termination.py` using async
    cuprum execution, public start/readiness observation, and POSIX signalling.
  - Success: readiness precedes SIGTERM; cleanup during and after copy,
    negative signal status, early exit, readiness timeout, and bounded final
    reaping remain covered without `Popen` or private cuprum process handles.

### 5.4. Complete the migration contract and dependency cleanup

This step establishes whether the integrated beta satisfies phase 5 across real
workflows and whether documentation describes the implemented boundary. See the
assessment §7; individual task tests remain part of implementation.

- [ ] 5.4.1. Validate the integrated command-boundary behaviour matrix.
  - Requires 5.2.3, 5.2.4, 5.3.1, 5.3.2, and 5.3.3.
  - Exercise real and stub execution across relay/capture combinations,
    environment omission, stdin closure, missing/forbidden programmes, bad cwd,
    timeout partial output, cancellation, and signal termination.
  - Success: supported Python/platform and applicable wheel/backend paths pass
    the acceptance gates; record any unvalidated platform explicitly.
- [ ] 5.4.2. Remove obsolete execution code and the plumbum dependency.
  - Requires 5.4.1.
  - Remove plumbum from development dependencies and regenerate the lockfile;
    retire old runner imports and obsolete relay/thread utilities after moving
    any still-required policy into the canonical adapter.
  - Success: tracked Python and extensionless scripts contain no lading-owned
    subprocess/plumbum execution or associated result/exception imports;
    `make test`, `make lint`, `make check-fmt`, and `make typecheck` pass.
- [ ] 5.4.3. Publish the implemented migration guidance.
  - Requires 5.4.2.
  - Update design §7, developer conventions, scripting examples, and any
    user-visible compatibility changes in the users' guide. Scripting standards
    already prescribe cuprum; correct their obsolete API examples.
  - Success: examples use real beta APIs, `make fmt`, `make markdownlint`, and
    `make nixie` pass, and the assessment's proposed shims are distinguished
    from the implementation actually delivered.
