# Lading user guide

`lading` is a command-line tool for managing release workflows in Rust
workspaces. It can:

- Bump versions across the workspace (`Cargo.toml` files) and keep internal
  dependency requirements in sync.
- Update version references inside TOML code fences in Markdown documentation.
- Plan and execute publication (`cargo package` + `cargo publish`) in dependency
  order, with pre-flight `cargo check`/`cargo test` validation.

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

This tutorial assumes a Rust workspace with a root `Cargo.toml` and one or
more member crates.

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

If `bump.documentation.globs` is configured, `lading` also searches those
Markdown files for TOML code fences and updates version values that refer to
workspace crates.

### 3. Publish in dry-run mode

By default, `publish` runs `cargo publish --dry-run` so the full pipeline can
be validated without uploading crates.

```bash
lading publish
```

To require a clean working tree before running the pre-flight checks, pass
`--forbid-dirty`:

```bash
lading publish --forbid-dirty
```

To perform a real publish (no `--dry-run`), pass `--live`:

```bash
lading publish --live
```

`publish` stages the workspace into a temporary directory before packaging. If
any member crate sets `readme.workspace = true`, `lading` copies the workspace
`README.md` into that crate in the staged workspace so `cargo package` can
include it.

## Configuration reference (`lading.toml`)

`lading` looks for `lading.toml` in the workspace root. The file must be a TOML
table at the top level. Unknown keys are rejected with a configuration error.

All paths and globs are interpreted relative to the workspace root.

### Complete example

```toml
[bump]
exclude = ["some-private-crate"]

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
compiletest_extern = { ui_test_helpers = "target/debug/deps/libui_test_helpers.so" }
env = { DYLINT_LOCALE = "en_GB" }
stderr_tail_lines = 40
```

### `[bump]`

| Key       | Type            | Default | Meaning |
| --------- | --------------- | ------- | ------- |
| `exclude` | array of strings | `[]`    | Crate names to exclude from manifest updates. |

### `[bump.documentation]`

| Key     | Type            | Default | Meaning |
| ------- | --------------- | ------- | ------- |
| `globs` | array of strings | `[]`    | Glob patterns for Markdown files whose TOML code fences should be updated. |

### `[publish]`

| Key            | Type                       | Default     | Meaning |
| -------------- | -------------------------- | ----------- | ------- |
| `exclude`      | array of strings            | `[]`        | Crate names to exclude from publication. |
| `order`        | array of strings            | `[]`        | Explicit publish order; overrides dependency-derived ordering when present. |
| `strip_patches`| `"all"` \| `"per-crate"` \| `false` | `"per-crate"` | How to edit `[patch.crates-io]` in the staged workspace before packaging. |

### `[preflight]`

| Key                 | Type                     | Default | Meaning |
| ------------------- | ------------------------ | ------- | ------- |
| `test_exclude`      | array of strings          | `[]`    | Crate names to exclude from `cargo test` by passing `--exclude`. |
| `unit_tests_only`   | boolean                  | `false` | Append `--lib --bins` to the pre-flight `cargo test` invocation. |
| `aux_build`         | array of array of strings | `[]`    | Extra commands (tokenised) to run before cargo pre-flight checks. |
| `compiletest_extern`| table (string → string)  | `{}`    | Extra `--extern` entries to append to `RUSTFLAGS` for compiletest-style suites. |
| `env`               | table (string → string)  | `{}`    | Environment overrides applied to git/cargo invocations run by `publish`. |
| `stderr_tail_lines` | integer (≥ 0)            | `40`    | Number of lines to tail from referenced `*.stderr` files when tests fail. |

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
