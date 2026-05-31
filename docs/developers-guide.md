# Lading developer guide

This guide documents internal APIs, testing patterns, and development workflows
for contributors to `lading`. For the end-user CLI reference and `lading.toml`
configuration, see the [user guide](./users-guide.md).

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

## Workspace discovery helpers

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

## Publish command internals

`PublishOptions.allow_unpublished_workspace_deps` is a dry-run-only override
for release trains where one workspace crate depends on another crate version
that is part of the same publish plan but is not visible in the crates.io index
yet. When enabled, `lading publish` downgrades that specific index-lookup
failure to a warning and continues. The option is rejected at runtime when
`live=True`, so it cannot mask a real upload failure.

### Exception hierarchy (`publish_errors`)

`lading.commands.publish_errors` defines the public error boundary for
publish orchestration. Both classes inherit from `RuntimeError` and carry
their message through the standard `args` tuple.

| Exception | Raised when |
| --- | --- |
| `PublishPreflightError` | A local check fails before publication begins — dirty working tree, auxiliary build failure, failed `cargo check`/`cargo test` preflight, or an invalid option combination (e.g. `--live` combined with `--allow-unpublished-workspace-deps`). |
| `PublishError` | A `cargo publish` invocation fails after pre-flight checks have passed. Subclasses `PublishPreflightError`. |

Callers of `lading.commands.publish.run` may catch `PublishPreflightError`
to handle both validation and publish-phase failures through one `except`
clause, or catch `PublishError` first when publish-phase failures require
distinct handling.

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

### Pre-flight validation (`publish_preflight`)

`lading.commands.publish_preflight` performs workspace validation before
any crate is packaged or published. Its public entry point is:

```python
_run_preflight_checks(
    workspace_root: Path,
    *,
    allow_dirty: bool,
    configuration: LadingConfig,
    runner: _CommandRunner | None = None,
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
publication pipeline. Both accept a `CrateEntry` and a
`_PublicationPipelineContext` and execute exactly one `cargo` invocation
against the crate's staging root:

```python
_package_crate(crate: CrateEntry, context: _PublicationPipelineContext) -> None
_publish_crate(crate: CrateEntry, context: _PublicationPipelineContext) -> None
```

`_PublicationPipelineContext` is a frozen dataclass that groups the four
inputs shared across every per-crate call within a single `run()` invocation:

| Field | Type | Purpose |
| --- | --- | --- |
| `plan` | `PublishPlan` | Resolved publication plan including the `publishable` crate list. |
| `preparation` | `PublishPreparation` | Staging workspace metadata including `staging_root`. |
| `options` | `_PublishExecutionOptions` | Runtime flags (`live`, `allow_dirty`, `allow_unpublished_workspace_deps`). |
| `runner` | `_CommandRunner` | Injectable command runner; defaults to `_invoke` in production. |

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
