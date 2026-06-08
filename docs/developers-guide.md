# Lading developer guide

This guide documents internal APIs, testing patterns, and development workflows
for contributors to `lading`. For the end-user CLI reference and `lading.toml`
configuration, see the [user guide](./users-guide.md). For repository operating
rules and required quality gates, see the [agent instructions](../AGENTS.md).

## Development invocation

The console script resolves to `lading.cli.main`. During development, the
implementation module may be invoked directly:

```bash
uv run python -m lading.cli --help
```

## Build environment

The Makefile resolves the `uv` executable through the `UV` variable:

```make
UV ?= $(shell command -v uv 2>/dev/null || printf '%s/.local/bin/uv' "$$HOME")
```

When `uv` is available on `PATH`, `command -v uv` supplies the executable path.
If it is not on `PATH`, the Makefile falls back to `$HOME/.local/bin/uv`, which
matches the default user-local installation path used by the project
environment. Targets that create the virtual environment, sync dependencies,
run builds, or execute tests should depend on and invoke `$(UV)` rather than a
literal `uv` command, so Makefile validation and command execution use the same
resolved executable.

## Linting workflow

Run the Python lint gate with:

```bash
make lint
```

The target is deliberately two-tiered. Ruff runs first because it is fast,
handles broad style and correctness checks, and imports the stricter lint
policy used by `leynos/episodic`. If Ruff passes, the target then runs Pylint
through the pinned `pylint-pypy-shim` tool under PyPy. This second tier is
focused on rule families that complement Ruff, especially logging format
safety, pattern matching checks, selected simplification checks, deprecated
standard-library usage, file hygiene, and design-size limits.

The relevant Makefile variables are:

- `PYLINT_PYTHON` — Python executable used by `uv tool run`; defaults to `pypy`.
- `PYLINT_TARGETS` — directories passed to Pylint; defaults to `lading scripts
  tests`.
- `PYLINT_PYPY_SHIM_REF` — pinned `pylint-pypy-shim` revision.
- `PYLINT_PYPY_SHIM` — Git URL assembled from the pinned shim revision.
- `PYLINT` — full `uv tool run --python $(PYLINT_PYTHON)` invocation for the
  shimmed Pylint command.

The `lint` target depends on both `ruff` and `uv`, so it fails during tool
checks if either command is unavailable. Keep any future lint additions wired
through Makefile prerequisites as well as command invocations, so local
failures remain early and clear.

Ruff and Pylint policy live in `pyproject.toml`. The Ruff configuration enables
preview rules, targets Python 3.13, imports the selected `episodic` rule set,
and bans deprecated `typing` aliases in favour of built-in collection types,
`collections.abc`, `collections`, `contextlib`, or `re` as appropriate. The
Pylint configuration keeps the pass opt-in by disabling all messages first and
then enabling only the chosen second-tier checks. Local ignores and thresholds
document existing codebase constraints that should be addressed as focused
cleanup work rather than incidental lint-gate churn.

## Testing hooks

Behavioural tests invoke the CLI as an external process and spy on the `python`
executable with [`cmd-mox`](./cmd-mox-usage-guide.md). Setting
`LADING_USE_CMD_MOX_STUB` to a truthy value such as `1` or `true` forces
publish pre-flight checks to be proxied through the cmd-mox inter-process
communication (IPC) server so that the suite can assert on
`cargo::<subcommand>` invocations without launching real tools. This pattern
keeps the tests faithful to real user interactions while still providing strict
control over command invocations. Use the same approach when adding new
end-to-end scenarios.

The end-to-end suite in `tests/e2e/` keeps git interactions real while stubbing
only `cargo` operations, using cmd-mox passthrough spies for `git status` when
publish runs with stub mode enabled.

## Bump command internals

`lading.commands.bump` coordinates manifest updates, documentation updates, and
lockfile reporting. Keep user-facing summary construction in
`lading.commands.bump_output` rather than formatting messages inline in the
workflow. The `BumpChanges` value groups changed manifests, documentation files,
and lockfiles so tests can snapshot the complete CLI message contract.

`lading.commands.bump_lockfiles` owns Cargo lockfile discovery and regeneration
after a version bump changes manifest content. It always includes the workspace
root `Cargo.toml`, validates configured nested manifests before invoking Cargo,
and de-duplicates resolved manifest paths. Invalid configured manifests and
failed `cargo update --workspace` commands raise `LockfileRegenerationError`,
which keeps bump failures in bump domain language rather than reusing
publish-specific errors.

Dry-run bump output uses `bump_lockfiles.resolve_lockfile_paths()` to report
which lockfiles would be regenerated without invoking Cargo. Live bump runs use
`bump_lockfiles.regenerate_lockfiles()` after manifest and documentation
processing, and only when at least one manifest changed.

## Workspace discovery helpers

### Lockfile helpers (`lading/commands/lockfile.py`)

`discover_tracked_lockfiles(workspace_root, runner)` filters git-tracked
`Cargo.lock` files outside `target/` with adjacent `Cargo.toml` manifests.
Private helpers `_handle_git_ls_files_failure` and
`_lockfiles_with_manifests` perform the error-handling and path-filtering
passes respectively.

`refresh_lockfile(manifest_path, runner)` runs
`cargo generate-lockfile --manifest-path` for the supplied manifest and raises
`LockfileRefreshError` on non-zero exit. `_refresh_lockfiles` in `bump.py`
calls it after manifest rewrites. The refresh loop is intentionally not
transactional: if a later lockfile refresh fails, previously rewritten
manifests and refreshed lockfiles remain on disk, and the operator should fix
the Cargo error, then run
`cargo generate-lockfile --manifest-path <path>/Cargo.toml` for each affected
crate manifest. Rerunning `lading bump` will not refresh lockfiles once the
manifests are already rewritten.

`validate_lockfile_freshness(manifest_path, runner)` runs
`cargo metadata --locked --manifest-path ... --format-version=1`. It returns
`True` on success and `False` otherwise. `_validate_lockfile_freshness` in
`publish_preflight.py` calls it before the cargo check/test pre-flight.

`LockfileRefreshError` inherits `LadingError`; its message includes Cargo
stderr.

### `load_cargo_metadata`

Import `lading.workspace.load_cargo_metadata` to execute `cargo metadata` with
the current or explicitly provided workspace root:

```python
from pathlib import Path

from lading.workspace import load_cargo_metadata

metadata = load_cargo_metadata(Path("/path/to/workspace"))
print(metadata["workspace_root"])
```

The helper normalizes the workspace path, invokes
`cargo metadata --format-version 1` using `plumbum`, and returns the parsed
JSON mapping. Any execution errors or invalid output raise `CargoMetadataError`
with a descriptive message, so callers can present actionable feedback to users.

### Workspace graph model

`load_workspace` converts the raw metadata into a strongly typed
`WorkspaceGraph` model backed by `msgspec.Struct` definitions. The graph lists
each crate, its manifest path, publication status, and any dependencies on
other workspace members.

```python
from pathlib import Path

from lading.workspace import load_workspace

workspace = load_workspace(Path("/path/to/workspace"))
print([crate.name for crate in workspace.crates])
```

The builder reads each crate manifest with `tomlkit` to detect
`readme.workspace = true` directives while preserving document structure for
future round-tripping.

## Programmatic publish options

When invoking `lading.commands.publish.prepare_workspace` programmatically,
callers can customize behaviour via `PublishOptions`. The defaults are:

- `allow_dirty=True` — skip the git cleanliness guard. **Security note:** this
  means uncommitted changes are permitted by default; pass `allow_dirty=False`
  to enforce a clean working tree before staging.
- `live=False` — run `cargo publish --dry-run` rather than uploading crates.
- `build_directory=None` — create a fresh temporary directory for staging.
- `preserve_symlinks=True` — preserve symbolic links in the staged workspace.
- `cleanup=False` — leave the staging directory intact for inspection.

Additional parameters `configuration`, `workspace`, and `command_runner` allow
dependency injection for testing and are typically left unset.

Examples:

- `PublishOptions(preserve_symlinks=False)` — disable symlink preservation when
  staging the workspace (useful when external assets need to be copied rather
  than linked).
- `PublishOptions(cleanup=True)` — remove the temporary staging directory
  automatically at process exit instead of leaving it for inspection.
- `PublishOptions(allow_dirty=False)` — require a clean git working tree before
  proceeding with publish preparation.

## Bump command internals

`BumpOptions` carries the dependency-injection points used by
`lading.commands.bump.run`. The `runner` field accepts an optional
`_CommandRunner`, matching the command-runner protocol used by publish
execution. When `runner` is `None`, bump falls back to the default subprocess
runner. Tests pass a runner explicitly so lockfile refresh commands can be
observed without invoking real Cargo processes.

`BumpChanges` records the user-visible files touched by a bump run. Its
`lockfiles` field contains the git-tracked `Cargo.lock` files refreshed after
manifest rewrites. The output formatter treats these paths like manifests and
documentation files, listing each refreshed lockfile with a `(lockfile)` suffix
so operators can see which generated files need review and commit.

## Publish command internals

`PublishOptions.allow_unpublished_workspace_deps` is a dry-run-only override
for release trains where one workspace crate depends on another crate version
that is part of the same publish plan but is not visible in the crates.io index
yet. When enabled, `lading publish` downgrades that specific index-lookup
failure to a warning and continues. The option is rejected at runtime when
`live=True`, so it cannot mask a real upload failure.

### Exception hierarchy (`lading.exceptions`)

`lading.exceptions.LadingError` is the package-level base class for domain
failures raised by lading itself. It extends `Exception` directly and gives
callers one stable type to catch when they want to handle expected lading
failures without also catching unrelated runtime errors from Python, Cargo, git
wrappers, or test doubles.

Every root domain exception should inherit from `LadingError`. More specific
exceptions should continue to inherit from the local root for their feature
area so existing handling remains precise:

| Root exception | Module | Notes |
| --- | --- | --- |
| `ConfigurationError` | `lading.config` | Base for configuration loading and validation failures. |
| `WorkspaceModelError` | `lading.workspace.models` | Base for workspace graph/model validation failures. |
| `CargoMetadataError` | `lading.workspace.metadata` | Base for cargo metadata execution and parsing failures. |
| `LockfileRefreshError` | `lading.commands.lockfile` | Raised when `cargo generate-lockfile` fails. |
| `PublishPlanError` | `lading.commands.publish_plan` | Raised when a publish plan cannot be constructed. |
| `PublishPreparationError` | `lading.commands.publish_manifest` | Raised when staged publish manifests or workspace assets cannot be prepared. |
| `PublishPreflightError` | `lading.commands.publish_errors` | Raised for local publish validation and pre-flight failures. |

Reuse plan:

- New command, workspace, and configuration modules should define exactly one
  local root exception that subclasses `LadingError` when they introduce a new
  failure family.
- Subclasses should inherit from that local root, not from `LadingError`
  directly, unless they are themselves the root of a new family.
- Do not inherit lading domain exceptions from `RuntimeError`; reserve
  `RuntimeError` for unexpected programming errors or third-party APIs that
  already expose it.
- Keep messages useful at the boundary where they are raised. Include the
  relevant path, crate name, command, or configuration key so CLI handlers can
  report the exception without reconstructing context.

Usage guidance:

- CLI and integration boundaries may catch `LadingError` to render expected
  lading failures as user-facing diagnostics.
- Feature code should catch the narrowest local exception it can handle, such
  as `PublishPreflightError` or `LockfileRefreshError`, and let unrelated
  `LadingError` subclasses propagate.
- Tests should assert the specific exception type for the behaviour under test,
  then use `LadingError` only when verifying common boundary handling.

`lading.commands.publish_errors` defines the public error boundary for publish
orchestration. Both publish exceptions inherit from the package-level
`LadingError` base and carry their message through the standard `args` tuple.

| Exception | Raised when |
| --- | --- |
| `PublishPreflightError` | A local check fails before publication begins — dirty working tree, auxiliary build failure, failed `cargo check`/`cargo test` preflight, or an invalid option combination (e.g. `--live` combined with `--allow-unpublished-workspace-deps`). |
| `PublishError` | A `cargo publish` invocation fails after pre-flight checks have passed. Subclasses `PublishPreflightError`. |

Callers of `lading.commands.publish.run` may catch `PublishPreflightError`
to handle both validation and publish-phase failures through one `except`
clause, or catch `PublishError` first when publish-phase failures require
distinct handling.

### Extracted publish modules

`publish_plan.py` owns publication planning and plan rendering. Its
`PublishPlan` dataclass is the immutable boundary between workspace analysis
and execution: it stores the workspace root, publishable crates in the resolved
order, crates skipped by manifest/configuration, and configured exclusions
that did not match a workspace crate. `plan_publication()` builds that object
by filtering non-publishable crates, applying `publish.exclude`, validating
`publish.order` when present, or deriving a deterministic dependency order.

`publish_manifest.py` owns staging-time manifest mutations. It contains the
workspace preparation types and helpers that copy the workspace tree, stage
workspace README files for crates that opt in, and apply the
`publish.strip_patches` strategy to the staged `Cargo.toml`. These operations
run before any `cargo package` or `cargo publish` command, so the command runner
works against a prepared snapshot rather than the source workspace.

`publish_diagnostics.py` owns compiletest failure enrichment. When a cargo
pre-flight test failure mentions compiletest-style `*.stderr` artefacts, the
diagnostic helper locates the referenced files, tails a bounded number of
lines, and appends those snippets to the `PublishPreflightError` message. The
module is deliberately read-only: missing artefacts or unreadable files produce
diagnostic notes rather than replacing the original cargo failure.

`publish_index_check.py` owns crates.io index-lookup classification. It
contains `_CargoInvocation`, the predicates and parsers that recognize Cargo's
"no matching package/version" diagnostics, crate-name canonicalization, and
`_handle_index_missing_version()`. That handler decides whether an index miss
is out-of-plan and fatal, in-plan but still fatal, or in-plan and downgraded by
`allow_unpublished_workspace_deps` during dry-run publication.

### `_PublishExecutionOptions`

`_PublishExecutionOptions` is a frozen dataclass that carries the runtime flags
forwarded to every `cargo package` and `cargo publish` invocation within a
single `lading publish` run. Its fields are:

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `live` | `bool` | — | When `True`, omits `--dry-run` from `cargo publish`. |
| `allow_dirty` | `bool` | — | Passes `--allow-dirty` to both cargo subcommands. |
| `allow_unpublished_workspace_deps` | `bool` | `False` | Dry-run-only override; see `allow_unpublished_workspace_deps` above. |

The dataclass is an internal implementation detail; callers interact with the
public `PublishOptions` dataclass, which `run()` converts before dispatching.

### Publication orchestration helpers

`_validate_publication_options(options)` is the first publish-specific guard in
`run()`. It rejects invalid option combinations before workspace loading or
staging begins. Today that means `live=True` cannot be combined with
`allow_unpublished_workspace_deps=True`, because the sibling-dependency
index-lookup downgrade is only valid for dry-run workflows.

`_execute_live_publication_pipeline(plan, preparation, *, options, runner)` is
the live-mode dispatcher. It walks `PublishPlan.publishable` in order and runs
`cargo package` followed by `cargo publish` for each crate before advancing to
the next crate. It logs per-crate progress, records completed crates for abort
diagnostics, and normalizes staging/preparation failures into
`PublishPreflightError` so callers receive the same publish command error
boundary.

`_handle_publish_result(invocation, crate, plan, options)` owns the result
classification for a completed `cargo publish` command. It logs success,
skips already-published crate versions, delegates in-plan crates.io index
visibility failures to `_handle_index_missing_version`, and raises
`PublishError` for all other non-zero publish exits after formatting the cargo
failure message.

`_CargoPreflightOptions` lives in `publish_preflight.py` and carries the
per-invocation settings for cargo pre-flight commands: extra cargo arguments,
test exclusions, unit-test-only narrowing, environment overrides, and optional
stderr-tail diagnostics. `_run_preflight_checks` builds these option objects
for `cargo check` and `cargo test` so command construction stays explicit and
testable.

Publication dispatch deliberately differs by mode. Dry-run mode keeps the
historical two-phase pipeline: package every publishable crate, then run
`cargo publish --dry-run` for every crate. Live mode interleaves the pipeline
per crate: package the next crate, publish it, then advance to the next entry
in `PublishPlan.publishable`. That ordering lets dependent crates resolve newly
uploaded in-plan dependencies during a single live release train. The live
pipeline does not roll back earlier uploads if a later crate fails; reruns rely
on the already-published detection path to log and skip versions already
visible in the registry.

The index-lookup handling is split across three helpers:

- `_is_index_missing_version_error(exit_code, stdout, stderr) -> bool` checks
  for both Cargo's version-selection failure marker and the crates.io index
  marker after confirming the command failed. Requiring both markers minimizes
  false positives from unrelated resolver, registry, or command failures.
- `_extract_missing_dependency_name(stdout, stderr) -> str | None` parses the
  missing crate name from Cargo's requirement line. The regex accepts Cargo's
  backtick, single-quote, and double-quote delimiters around the requirement,
  captures the dependency name before `=`, and searches `stderr` before
  `stdout` because Cargo normally reports this failure on the error stream.
- `_handle_index_missing_version(_CargoInvocation, *, plan, options)` applies
  the decision tree. If name extraction fails, the original Cargo failure stays
  fatal. If the parsed name is not in the publish plan, the failure is fatal
  with guidance to publish or index that dependency first. If the parsed name
  is in the plan and `allow_unpublished_workspace_deps` is set, the helper logs
  a warning and continues; otherwise it raises with guidance to use the flag in
  dry-run mode or follow the staged-publish workaround.

#### Crate-name canonicalization

`_canonical_crate_name(name)` normalizes a crate name by replacing every
hyphen with an underscore. It is applied to both sides of the
`publishable_names` membership check inside `_handle_index_missing_version`:

```python
publishable_names = {_canonical_crate_name(entry.name) for entry in plan.publishable}
if _canonical_crate_name(missing_name) not in publishable_names:
```

This is necessary because Cargo error diagnostics may report a missing
dependency using hyphens (e.g. `my-crate`), while the corresponding
`Cargo.toml` entry and the `PublishPlan` store the same package name with
underscores (e.g. `my_crate`). Without normalization, a hyphenated cargo
diagnostic would be incorrectly classified as an out-of-plan dependency and
raise a fatal error instead of triggering the downgrade path.

`_format_cargo_failure_message(command, crate_name, exit_code, output)` assembles
the human-readable error string that is embedded in every `PublishPreflightError`
or `PublishError` raised on a non-zero cargo exit. It is a pure function with no
side effects: given the cargo subcommand string, the crate name, the numeric
exit code, and the `(stdout, stderr)` pair, it returns a formatted message that
includes all four values. Using a single function for message construction keeps
the error format consistent across the packaging and publish phases and makes
snapshot testing straightforward.

### Command runners (`lading.runtime`)

`lading.runtime` owns the shared `CommandRunner` protocol and the production
`subprocess_runner` adapter. Command modules type against this protocol so tests
can inject cmd-mox or recording runners without depending on publish-specific
infrastructure.

`lading.commands.publish_execution` still owns publish-specific error mapping
around command execution. `lading bump` uses the runtime runner directly for
lockfile refreshes, while `lading publish` uses `_invoke` where failures should
surface as `PublishPreflightError`.

### Pre-flight validation (`publish_preflight`)

`lading.commands.publish_preflight` performs workspace validation before
any crate is packaged or published. Its public entry point is:

```python
_run_preflight_checks(
    workspace_root: Path,
    *,
    allow_dirty: bool,
    configuration: LadingConfig,
    runner: CommandRunner | None = None,
) -> None
```

The function verifies the git working tree is clean (unless `allow_dirty`
is set), then executes `cargo check` and `cargo test` in a temporary
`--target-dir` to keep preflight artefacts separate from the workspace's
own target directory. A non-zero exit from any step raises
`PublishPreflightError` with a descriptive message.

| Helper | Purpose |
| --- | --- |
| `_compose_preflight_arguments` | Builds the base `cargo` argument tuple for a given target directory and `--all-targets` flag. |
| `_preflight_argument_sets` | Returns `(check_args, test_args)` tuples adapted for unit-test-only mode. |
| `_run_cargo_preflight` | Executes a single `cargo check` or `cargo test` invocation and raises on failure. |
| `_verify_clean_working_tree` | Runs `git status --porcelain` and raises if the tree is dirty and `allow_dirty` is `False`. |

### Per-crate publication helpers

`_package_crate` and `_publish_crate` are the atomic units of the
publication pipeline. Both accept the crate entry, publication state, and
command runner explicitly, then execute exactly one `cargo` invocation against
the crate's staging root:

```python
_package_crate(
    crate: WorkspaceCrate,
    state: _PublicationPipelineState,
    *,
    runner: _CommandRunner,
) -> None
_publish_crate(
    crate: WorkspaceCrate,
    state: _PublicationPipelineState,
    *,
    runner: _CommandRunner,
) -> None
```

`_PublicationPipelineState` carries only publish-domain state: the resolved
`PublishPlan`, the `PublishPreparation`, and `_PublishExecutionOptions`.
Infrastructure stays at the call boundary: `_CommandRunner` is passed directly
to each pipeline/helper function rather than being bundled into the state.

`_dispatch_publication` selects the live or dry-run pipeline and delegates
accordingly. It is the sole branch that decides between the interleaved
per-crate flow and the historical two-phase batch flow, keeping `run()`
free of that decision.

`lading.commands.publish_execution` loads the optional `cmd_mox` command-runner
module with `importlib.import_module("cmd_mox.command_runner")`. Keeping the
module in an `object | None` variable avoids relying on
`from cmd_mox import ...  # type: ignore` when the package is absent, and it
prevents conflicting type declarations when `cmd_mox` is present in the type
checker environment.
