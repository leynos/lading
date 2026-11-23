# Migration guide: v0.3.1 to v0.4.0

Lading 0.4.0 raises the interpreter floor from CPython 3.13 to CPython 3.14. It
also makes the command-line boundary explicit: diagnostics that an earlier
dependency rendered on stdout are now written to stderr, and command summaries
are printed once, unwrapped and without colour. The flags, `lading.toml` keys,
and published Python surface are unchanged from 0.3.x. An environment already
running 3.14 that only reads successful output needs no action beyond the
version bump itself.

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
component, so the next final tag from this tree is 0.4.0. The documented
release procedure defers a final 0.x release until cuprum 0.2.0 final is
available, so the bump confinement change ships first in the `0.4.0b1`
pre-release; the migration content below applies to both. If the floor change
is scheduled into a later release, the content below applies to that release
unchanged.

The release is the first whose wheel is built under 3.14, and the first whose
package metadata refuses an older interpreter. Both follow from the same
decision, recorded below.

## Bump confinement preflight

`lading bump` now validates the complete write plan before changing anything. A
workspace that relied on any of the following now fails with exit status 1 and a
`Preflight error:` diagnostic:

- A member reachable only through a symlinked directory below the workspace
  root. Make the member path real, or move the member into the workspace.
- A documentation glob that escapes the workspace, such as
  `../outside/README.md`. Narrow the glob to the workspace.
- A selected documentation file that cannot be read or decoded. Previously it
  was skipped with a warning.
- A workspace README or adopted crate README that is a symlink or a
  non-regular file.
- A manifest, documentation file, README, or lockfile target that is a hard
  link (a file with more than one directory entry), even when every link stays
  inside the workspace. A write through one name would leave the others stale,
  so replace the target with an independent copy of the file.
- A path dependency inside the workspace whose `Cargo.toml` is a symlink,
  missing, or not a regular file. Cargo treats such a dependency as an implicit
  workspace member; make the path real and give it a regular `Cargo.toml`.
- A configured `bump.lockfile_manifests` entry that is absolute, traverses
  upwards, is missing, is a symlink, or is not named `Cargo.toml`. This
  validation applies when lockfile regeneration is enabled; a run with
  `--no-rebuild-lockfiles` ignores configured entries.
- A non-empty `CARGO_RESOLVER_LOCKFILE_PATH` in the environment, or a
  `resolver.lockfile-path` in a Cargo configuration file, including a file
  pulled in through that configuration's `include` entries. Unset the variable
  or remove the setting.

Configured lockfile manifests are resolved to the effective workspace root
Cargo reports. A manifest that belongs to the outer workspace now reports the
outer `Cargo.lock` instead of an adjacent file; only a standalone nested
workspace keeps its own adjacent lockfile. A dry run still writes nothing, and
discovery now runs `cargo metadata --no-deps`, which leaves a missing or stale
lockfile untouched.

A same-version rerun makes no manifest changes and therefore skips lockfile
regeneration. Repair a single failed manifest with
`cargo update --workspace --manifest-path <manifest>`; a multi-manifest failure
also prints that repair command for each listed manifest.

The guarantee is validation before mutation. A failure during an actual write
or Cargo invocation is not rolled back, and a concurrent process that changes a
path after validation is not defended against.

## Who is affected

| Your environment                                                       | Action required                                             |
| ---------------------------------------------------------------------- | ----------------------------------------------------------- |
| CPython 3.14 or later, confined workspace                              | None; upgrade as usual.                                     |
| CPython 3.14 or later, script reads diagnostics from stdout            | Required: read diagnostics from stderr instead.             |
| CPython 3.14 or later, symlinked member or escaping documentation glob | Required: make the member or glob real, or remain on 0.3.1. |
| CPython 3.14 or later, unreadable documentation file                   | Required: make the file readable and decodable.             |
| CPython 3.14 or later, symlinked or non-regular README target          | Required: replace it with a regular file.                   |
| CPython 3.14 or later, hard-linked write target                        | Required: replace it with an independent copy of the file.  |
| CPython 3.14 or later, invalid configured lockfile manifest            | Required: make it an existing, confined `Cargo.toml`.       |
| CPython 3.14 or later, invalid implicit member manifest                | Required: make the path dependency real and regular.        |
| CPython 3.14 or later, Cargo lockfile-path override                    | Required: unset the variable or remove the setting.         |
| CPython 3.13                                                           | Required: upgrade the interpreter, or remain on 0.3.1.      |
| CPython 3.12 or earlier                                                | Required: upgrade the interpreter; 0.3.1 required 3.13.     |

_Table 1: Required action by interpreter version and workspace shape._

Three changes require action. The interpreter floor requires an environment
migration. A workspace that relies on a symlinked member or an escaping
documentation glob must be corrected, as described under
[Bump confinement preflight](#bump-confinement-preflight); such a workspace
fails the same way on any interpreter. A script that reads diagnostics from
stdout should read stderr instead, because diagnostics moved there. A project
pinned to a Lading version through a lockfile keeps that version until the
lockfile is regenerated, so the floor bites when the pin is moved rather than
when this release is published.

## What changed

The requirement in the package metadata moved:

<!-- tested-example: migration-requires-python-floor -->
```text
# 0.3.1
Requires-Python: >=3.13

# 0.4.0
Requires-Python: >=3.14
```

Because `Requires-Python` is enforced by the installer, an attempt to install
the 0.4.0 wheel under 3.13 fails before any file is written:

<!-- tested-example: migration-old-interpreter-error -->
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

The first release carrying the bump confinement change is the `0.4.0b1`
pre-release. Install it explicitly, because a plain version specifier does not
select a pre-release:

<!-- tested-example: migration-upgrade-uv-prerelease -->
```bash
# Substitute the tool that manages your interpreter.
uv python install 3.14
uv python pin 3.14

# Then move the package itself, selecting the pre-release.
uv add lading==0.4.0b1
```

A project using `pip` and a virtual environment follows the same order:

<!-- tested-example: migration-upgrade-pip-prerelease -->
```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install lading==0.4.0b1
```

On Windows, create the environment with the launcher:

<!-- tested-example: migration-create-venv-windows -->
```text
py -3.14 -m venv .venv
```

Then run the environment's interpreter, which is `.venv\Scripts\python`:
`.venv\Scripts\python -m pip install lading==0.4.0b1`.

Once the final `0.4.0` release is available, the same commands with
`lading==0.4.0` move an installation to it; the final release carries the same
bump confinement behaviour.

If the interpreter cannot be moved yet, pin Lading below the floor and defer
the upgrade. That defers the confinement fix too: on 0.3.1 a symlinked member,
a symlinked manifest, or an escaping documentation glob can still make a bump
write outside the workspace, and an invalid configured lockfile manifest can
still fail after manifests were rewritten. There is no scheduled defect fix
waiting in 0.4.0 beyond that safety boundary, but a workspace in one of those
shapes should be corrected, or migrated, before the interpreter moves:

<!-- tested-example: migration-defer-upgrade -->
```bash
uv add 'lading<0.4.0'
```

## Staying on 0.3.1

The 0.3.1 wheel remains available from its tagged release and continues to work
on 3.13. This release does not withdraw it, and no part of 0.4.0 changes its
behaviour retroactively. Treat 0.3.1 as the supported version for a 3.13
environment, and migrate when the interpreter moves.
