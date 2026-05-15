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

## Linting workflow

Run the Python lint gate with:

```bash
make lint
```

The target is deliberately two-tiered. Ruff runs first because it is fast,
handles broad style and correctness checks, and imports the stricter lint policy
used by `leynos/episodic`. If Ruff passes, the target then runs Pylint through
the pinned `pylint-pypy-shim` tool under PyPy. This second tier is focused on
rule families that complement Ruff, especially logging format safety, pattern
matching checks, selected simplification checks, deprecated standard-library
usage, file hygiene, and design-size limits.

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
through Makefile prerequisites as well as command invocations so local failures
remain early and clear.

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
