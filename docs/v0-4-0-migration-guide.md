# Lading 0.4.0 migration guide

Lading 0.4.0 raises the interpreter floor from CPython 3.13 to CPython 3.14 and
adds `publish.stage_exclude`, which changes what a staged publish tree contains
and therefore which files `cargo package` may publish. The command-line
interface and the published Python surface are otherwise unchanged from 0.3.x.
An environment already running 3.14 needs no action beyond the version bump
itself, unless a published crate relies on the old Git-aware file selection; see
[Publish staging exclusions](#publish-staging-exclusions).

When 0.4.0 is released, its tagged build will also be published to PyPI as a
source distribution and pure-Python wheel. GitHub Releases will retain both
files for direct download. Use PyPI for a normal package installation; choose
the matching GitHub release to download an artefact directly. No project
configuration changes are needed to move an existing install to PyPI.

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

_Table 1: Required action by interpreter version._

Only the interpreter-floor change forces an environment migration. The staging
default is a behaviour change instead: a project whose packaged files must keep
Git-aware selection sets `stage_exclude = []`. A project pinned to a Lading
version through a lockfile keeps that version until the lockfile is
regenerated, so the floor bites when the pin is moved rather than when this
release is published.

## What changed

The requirement in the package metadata moved:

```text
# 0.3.1
Requires-Python: >=3.13

# 0.4.0
Requires-Python: >=3.14
```

Because `Requires-Python` is enforced by the installer, an attempt to install
the 0.4.0 wheel under 3.13 fails before any file is written:

```console
$ python3.13 -m pip install lading-0.4.0-py3-none-any.whl
ERROR: Package 'lading' requires a different Python: 3.13.x not in '>=3.14'
```

That refusal is the intended behaviour rather than a packaging accident. It
replaces the alternative — installing successfully and then failing at import
or during a run — with a message that names the constraint.

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

## Publish staging exclusions

`lading publish` copies the workspace into a staging directory before
packaging. 0.4.0 adds the optional `publish.stage_exclude` key: an array of
workspace-relative glob patterns naming entries the copy leaves out. Its
default is `[".git"]`, so the staged tree is no longer a Git checkout.

Leaving `.git` behind removes most of the cost of staging a large repository,
but it also changes how Cargo selects files for a package. With no Git metadata,
`cargo package` skips every dot-named entry, does not consult the repository's
`.gitignore`, and writes no `.cargo_vcs_info.json`. A file that Git ignores
without a leading dot — a local database, or a settings module — is therefore
packaged unless the crate names it in `include` or the configuration excludes
it. Audit any such file that sits inside a publishable crate before relying on
the default.

Projects that do not publish are unaffected. To keep the 0.3.x behaviour for a
workspace that depends on Git-aware selection, set:

```toml
[publish]
stage_exclude = []
```

An empty list copies `.git` and every other file again, restoring Git-based
file selection and VCS provenance, at the cost of the copy time the default
avoids. Excluding a path that would remove a required manifest is rejected
before staging begins.

See [Staging exclusions](users-guide.md#staging-exclusions) for the pattern
rules.

## Upgrading

Upgrade the interpreter first, then the package, so that the installer's
constraint is satisfied at the moment it is checked.

```bash
# Substitute the tool that manages your interpreter.
uv python install 3.14
uv python pin 3.14

# Then move the package itself.
uv add lading==0.4.0
```

A project using `pip` and a virtual environment follows the same order:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install lading==0.4.0
```

If the interpreter cannot be moved yet, pin Lading below the floor and defer
the upgrade. Nothing in 0.4.0 fixes a defect present in 0.3.1, so there is no
correctness pressure to migrate before the interpreter is ready:

```bash
uv add 'lading<0.4.0'
```

## Staying on 0.3.1

The 0.3.1 wheel remains available from its tagged release and continues to work
on 3.13. This release does not withdraw it, and no part of 0.4.0 changes its
behaviour retroactively. Treat 0.3.1 as the supported version for a 3.13
environment, and migrate when the interpreter moves.
