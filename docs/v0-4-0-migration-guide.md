# Lading 0.4.0 migration guide

Lading 0.4.0 raises the interpreter floor from CPython 3.13 to CPython 3.14.
Nothing else in this release changes how Lading is invoked, configured, or
extended: the command-line interface, `lading.toml` keys, and the published
Python surface are unchanged from 0.3.x. An environment already running 3.14
needs no action beyond the version bump itself.

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

Only the interpreter-floor change requires an environment migration. A project
pinned to a Lading version through a lockfile keeps that version until the
lockfile is regenerated, so the floor bites when the pin is moved rather than
when this release is published.

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
