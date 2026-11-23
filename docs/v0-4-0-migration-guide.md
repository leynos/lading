# Lading 0.4.0 migration guide

Lading 0.4.0 raises the interpreter floor from CPython 3.13 to CPython 3.14. It
also makes the command-line boundary explicit: diagnostics that an earlier
dependency rendered on stdout are now written to stderr, and command summaries
are printed once, unwrapped and without colour. The release adds the opt-in
`bump.manifest_rewrites` setting for updating configured non-member manifests
during `lading bump`. It defaults to `[]`; configured non-member manifests are
rewritten only when this setting is enabled. See the
[bump configuration reference](users-guide.md#bump) for its fields and
behaviour.

The command-line flags and existing `lading.toml` key syntax remain compatible
with 0.3.x; this release adds the opt-in `bump.manifest_rewrites` key described
above. Compound dependency requirement rewriting changes for workspace-member
manifests and for non-member manifests selected by `bump.manifest_rewrites`.
The documented Python API additions are additive.
`BumpConfig.manifest_rewrites` is new, and `lading.config_manifest_rewrites` and
`lading.config_validation` support its configuration. `BumpOptions.clock` adds
an injectable rewrite timer, and
`lading.commands.manifest_paths.resolve_manifest_candidate` is available to
manifest-path consumers. `lading.commands.bump_manifest_rewrites` exports
`ManifestRewriteError`, `ManifestRewritePlan` and `ManifestRewriteVersions`.
The internal `parse_manifest` and `write_atomic_text` helpers moved from
`lading.commands.bump_toml` to `lading.commands.bump_toml_files`. The
`DEPENDENCY_SECTIONS`, `assign_dependency_version_field`,
`update_dependency_entry`, `update_dependency_sections`,
`update_dependency_table` and `update_section` helpers moved to
`lading.commands.bump_toml_dependencies`. Update imports if your tooling uses
them.

## Impact at a glance

| Change                   | Obligation         | Action                                                      |
| ------------------------ | ------------------ | ----------------------------------------------------------- |
| Python interpreter floor | Required           | Use Python 3.14 or later to install 0.4.0.                  |
| Moved helper imports     | Required if called | Update imports if your code uses the moved helpers.         |
| Diagnostic output stream | Required if called | Read stderr if scripts expect diagnostics on stdout.        |
| Compound requirements    | Recommended        | Review and resolve warnings when bounds exclude the target. |
| `bump.manifest_rewrites` | Optional           | Enable it for configured non-member manifests.              |

_Table 1: Migration actions by change._

## Import migration

### File helpers

#### Before: file helpers

Before 0.4.0, import these helpers from `lading.commands.bump_toml`. This
historical import is expected to raise `ImportError` on 0.4.0.

<!-- tested-example: v0-4-0-helper-import-before -->

```python
from lading.commands.bump_toml import parse_manifest, write_atomic_text
```

#### After: file helpers

<!-- tested-example: v0-4-0-helper-import-after -->

```python
from lading.commands.bump_toml_files import parse_manifest, write_atomic_text
```

### Dependency helpers

#### Before: dependency helpers

Before 0.4.0, import these dependency helpers from `lading.commands.bump_toml`.
This historical import is expected to raise `ImportError` on 0.4.0.

<!-- tested-example: v0-4-0-dependency-import-before -->

```python
from lading.commands.bump_toml import (
    DEPENDENCY_SECTIONS,
    assign_dependency_version_field,
    update_dependency_entry,
    update_dependency_sections,
    update_dependency_table,
    update_section,
)
```

#### After: dependency helpers

<!-- tested-example: v0-4-0-dependency-import-after -->

```python
from lading.commands.bump_toml_dependencies import (
    DEPENDENCY_SECTIONS,
    assign_dependency_version_field,
    update_dependency_entry,
    update_dependency_sections,
    update_dependency_table,
    update_section,
)
```

`ConfigurationError` and `ConfigurationNotLoadedError` now live in
`lading.exceptions` and remain importable from `lading.config`. An environment
already running 3.14 that only reads successful output needs no action beyond
the version bump itself.

When 0.4.0 is released, its tagged build will also be published to PyPI as a
source distribution and pure-Python wheel. GitHub Releases will retain both
files for direct download. Use PyPI for a normal package installation; choose
the matching GitHub release to download an artefact directly. No project
configuration changes are needed to move an existing install to PyPI.

The release also changes how `lading bump` rewrites compound dependency
requirements in workspace-member manifests and explicitly configured non-member
manifests: the first comparator takes the target version, and later comparators
remain verbatim. It warns when the rewritten first comparator is strict (`<` or
`>`) or a later recognized comparison bound (`<`, `<=`, `=`, `>=` or `>`)
excludes the target. Retained caret and tilde clauses, and unsupported forms,
are not evaluated by this warning. See the
[bump configuration reference](users-guide.md#bump).

Subprocess output relays also emit additive, privacy-safe `INFO` decision
events with the prefix `relay observability event:`. They describe relay
fallback and disablement decisions without including child-process output. This
adds no configuration or invocation change; see
[Observability](users-guide.md#observability) for the event contract.

The number is derived rather than announced: 0.3.1 is the most recent release,
and a breaking change under pre-1.0 semantic versioning advances the minor
component, so the next tag from this tree is 0.4.0. If the floor change is
scheduled into a later release, the content below applies to that release
unchanged.

The release is the first whose wheel is built under 3.14, and the first whose
package metadata refuses an older interpreter. Both follow from the same
decision, recorded below.

## Who is affected

| Your environment        | Action required                                    |
| ----------------------- | -------------------------------------------------- |
| CPython 3.14 or later   | None; upgrade as usual.                            |
| CPython 3.13            | Upgrade the interpreter, or remain on 0.3.1.       |
| CPython 3.12 or earlier | Upgrade the interpreter; 0.3.1 also required 3.13. |

_Table 2: Required action by interpreter version._

Only the interpreter-floor change requires an environment migration. The
command-line stream change described below needs no environment work, though a
script that reads diagnostics from stdout must read stderr instead. A project
pinned to a Lading version through a lockfile keeps that version until the
lockfile is regenerated, so the floor bites when the pin is moved rather than
when this release is published.

## What changed

The requirement in the package metadata moved:

<!-- tested-example: v0-4-0-requires-python -->

```text
# 0.3.1
Requires-Python: >=3.13

# 0.4.0
Requires-Python: >=3.14
```

Because `Requires-Python` is enforced by the installer, an attempt to install
the 0.4.0 wheel under 3.13 fails before any file is written:

<!-- tested-example: v0-4-0-python313-wheel-rejection -->

```console
$ python3.13 -m pip install lading-0.4.0-py3-none-any.whl
ERROR: Package 'lading' requires a different Python: 3.13.x not in '>=3.14'
```

That refusal is the intended behaviour rather than a packaging accident. It
replaces the alternative — installing successfully and then failing at import
or during a run — with a message that names the constraint.

## Command-line output streams

Lading constructs its command-line application with an explicit result action,
so its own dispatcher owns printing and exit codes instead of delegating them
to the argument parser. Two observable consequences matter to scripts:

- Error diagnostics — an unknown subcommand, an invalid argument value, or a
  missing required option — are written to **stderr**. Earlier releases
  rendered these panels on stdout.
- A command summary stays on **stdout** and is printed once as plain text,
  unwrapped by the terminal width and without colour codes.

A script that captured both streams together sees no change in meaning. A
script that read error text from stdout must read stderr instead. The complete
contract, including the exit-status table, is in
[Output streams and exit codes](users-guide.md#output-streams-and-exit-codes);
the decision is recorded in
[ADR-011](adrs/adr-0011-cyclopts-4-cli-result-and-diagnostic-boundary.md).

## Why the floor moved

Lading's lint and typecheck gateways evaluate the source against a single
declared baseline: Ruff's `target-version`, Pylint's `py-version`, the managed
interpreters `uv tool run` provisions, and the type checker's
`--python-version` all derive from one `PYTHON_BASELINE` value in the
`Makefile`. The gateways parse and analyse the code as 3.14, and the toolchain
runs on a 3.14 interpreter.

Supporting 3.13 in the metadata while checking against 3.14 would advertise a
version nothing in the project verifies.
[Linting workflow](developers-guide.md#linting-workflow) in the developer's
guide records how the baseline is declared and mirrored;
[Supported Python versions](users-guide.md#supported-python-versions) states
the floor for users.

## Upgrading

Upgrade the interpreter first, then the package, so that the installer's
constraint is satisfied at the moment it is checked.

<!-- tested-example: v0-4-0-upgrade-uv -->

```bash
# Substitute the tool that manages your interpreter.
uv python install 3.14
uv python pin 3.14

# In the existing [project] table in pyproject.toml, set:
# requires-python = ">=3.14"

# Then move the package itself.
uv add lading==0.4.0
```

A project using `pip` and a virtual environment follows the same order:

<!-- tested-example: v0-4-0-upgrade-venv -->

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install lading==0.4.0
```

If the interpreter cannot be moved yet, pin Lading below the floor and defer
the upgrade. Nothing in 0.4.0 fixes a defect present in 0.3.1, so there is no
correctness pressure to migrate before the interpreter is ready:

<!-- tested-example: v0-4-0-pin-before-floor -->

```bash
uv add 'lading<0.4.0'
```

## Staying on 0.3.1

The 0.3.1 wheel remains available from its tagged release and continues to work
on 3.13. This release does not withdraw it, and no part of 0.4.0 changes its
behaviour retroactively. Treat 0.3.1 as the supported version for a 3.13
environment, and migrate when the interpreter moves.
