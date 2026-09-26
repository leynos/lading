# ADR-007: Adopt the nose duplication gate

## Status

Accepted.

## Date

2026-09-26

## Context and problem statement

Duplication in `lading` was neither measured nor enforced. Nothing stopped a
second copy of a helper being written beside the first, and nothing recorded
when two similar-looking pieces of code were deliberately independent.
Reviewers noticed repetition by eye, inconsistently, and a codebase that
publishes release artefacts depends on exactly that kind of drift being caught
before publication.

The approach adopted here is not invented for `lading`. `leynos/episodic`
evaluated several duplication detectors and recorded the decision in its
ADR-021, settling on [nose](https://github.com/corca-ai/nose) and a wrapper
that turns its ranked report into a blocking gate with reasoned, reviewable
exceptions. That evaluation is adopted **by reference**: this repository does
not repeat the detector competition, and it makes no claim about episodic's
precision, recall, or run timings. The merged implementation at revision
`d9e5ac0d254f375e2986f52d91a3b88c117c833b` (PR
[leynos/episodic#276](https://github.com/leynos/episodic/pull/276)) is the
authoritative reference; where its opening description and its merged ADR-021
disagree, the merged ADR and code take precedence.

The problem this ADR addresses is narrower than "reduce duplication". The
objective is to **remove unjustified repeated logic** and to keep **intentional
parallels explicitly reviewable**. A low duplication count achieved by
indiscriminate abstraction, or by suppression, would defeat the purpose.

## Decision

Run the checked-in `nose` detector as a blocking gate over the maintained
first-party Python roots, and require that every reported family be either
extracted or recorded as a reasoned exception.

### Detector and provisioning

The gate pins `nose` at **0.20.0**, verified before every scan against
`[tool.nose].version`. The version is declared in three places -- the Makefile's
`NOSE_VERSION`, the `NOSE_VERSION` environment in `.github/workflows/ci.yml`,
and `[tool.nose].version` -- and
`tests/workflow_contracts/test_duplication_toolchain_contract.py` fails on
drift, because a detector that installs cleanly and then rejects its own
version is the failure mode drift produces.

`make install-nose` provisions the binary into `.tools/nose` from a trusted
prebuilt release via `cargo-binstall`, with
`--disable-strategies compile,quick-install`. A missing trusted binary is a
provisioning failure; it is never permission to start a costly source build, and
`cargo install`, floating `latest` installers, and curl-to-shell bootstraps
are excluded by the same rule. CI downloads `cargo-binstall` itself, verifies
its published SHA-256 digest, and only then unpacks it.

`NOSE_BIN` overrides the binary location, and a relative override resolves
against the repository root rather than the caller's working directory, so an
invocation from a subdirectory still runs the pinned detector.

### Scan scope

The scan covers `lading` and `scripts` over all three channels (`syntax`,
`semantic`, `near`) at a size floor of 24 intermediate-language tokens, with
`surface = "all"` and a ranked budget of `top = 30`.

`tests/` is deliberately out of scope. nose reports 190 families across that
tree, almost all of them parallel scenario scaffolding, and gating it would say
nothing about production duplication. The same tests-are-not-production
boundary applies to `scripts/tests/` through `exclude`.

That exclusion is written `**/tests/**`, and the form matters: nose anchors
`--exclude` globs to each `--root`, not to the repository root. The
repository-relative spelling `scripts/tests/**` matches nothing when the root is
`scripts`, so it silently excludes no files and the gate then reports families
from a tree the configuration claims to have skipped. A contract test now
rejects any exclusion glob that is not `**/`-prefixed or that names a directory
no configured root contains.

`surface = "all"` widens the view but does not make `top = 30` exhaustive.
Allowed families still occupy places in that surface, so the budget bounds the
adjudicated set rather than describing the whole scan. The threshold is not
raised and the budget is not reduced to obtain a green run.

### Gate and exception contracts

The wrapper separates detector execution, report validation, family and
exception matching, persistence, and the CLI. Detector commands are built as
structured argument vectors and run with an explicit repository working
directory, JSON output, and a bounded timeout. Exit status 0 (pass), 1
(unsuppressed families), and 2 (invalid input or configuration) are preserved.

Failures fail **closed**. A missing or wrong-version binary, a timeout, a
failed command, malformed JSON, and an invalid report shape all raise with
actionable diagnostics. None of them is converted into an empty findings list
or a skipped successful check, because a gate that passes when it could not run
is worse than no gate.

Exceptions are reasoned TOML entries:

```toml
[[tool.duplication_gate.allow]]
members = ["lading/example/a.py::operation", "lading/example/b.py::operation"]
reason = "A specific, reviewed explanation of why these must remain separate."
```

A `unit` entry supplies one key; `members` supplies at least two; exactly one
form is set. An entry silences a family only when **every** reported location
matches one of its keys, so a new member outside the entry's keys blocks again.
Keys are never line numbers or detector IDs, both of which are unstable. A
non-blank reason is required, and there is no repository-wide wildcard, no
mass-generated reason, and no automatic allowlisting of the initial scan.

Entries are edited only through `make duplication-allow`, which appends under
an advisory sidecar lock with an atomic read-modify-write, preserving TOML
comments and unrelated configuration and updating the same target idempotently.
The lock coordinates writers that participate in its protocol; it does not
coordinate unrelated editors that do not.

Stale-entry reporting observes absence from the capped ranked surface, so
"unmatched in this scan" is not proof that the duplication is gone: the family
may simply have fallen below the ranking bound. Removal is manual and
deliberate, and a deliberate `top=0` scan is the way to confirm a candidate.

### Adjudication of this repository

Adoption starts from **no copied exceptions**. The configured gate was run, and
each blocking family was inspected against its callers and its behaviour.

One family was genuinely repeated logic. The four dependency-index failure
paths in `lading/commands/publish_index_check.py` each built a message from the
shared formatter, logged the matching warning, and raised the caller's
exception class, varying only in prose. They now call one
`_raise_missing_dependency` helper with the wording still at each call site.
The 31 message snapshots are byte-identical before and after, which is what
makes the extraction behaviour-preserving rather than merely similar-looking.

The remaining ten families were adjudicated as intentional parallel structure
and recorded as reasoned exceptions in `[tool.duplication_gate]`. They fall
into two groups. Nine are shared *idioms*: one-statement optional-input guards,
dependency-injection fallbacks at four independent seams, type-checking import
blocks, the `absent table yields defaults` contract, two distinct exception
types that each record one diagnostic attribute, and the Windows and POSIX
bindings of a single lock primitive. Each would require a generic helper,
injected key sets, or a platform branch inside the primitive -- the
abstractions the adoption criteria exclude.

The tenth, the `toml_coerce` pair, is shared *declaration scaffolding*. The
region the detector reports is the module preamble, the mandated Numpydoc
parameter/returns/raises blocks, and parallel coercer signatures. Replacing
every function body in both modules with a distinct trivial statement still
leaves a 230-token match, so what is being reported is the documentation and
signature convention these sibling coercers must follow. Extracting it would
couple eight independent coercers to one docstring generator or signature
wrapper.

Exception keys are file-granular for the families nose reports as unnamed
fragments, because a `::name` key cannot match a location that carries no unit
name. Each such entry records that consequence, so a future family arising
inside a covered file is understood to be silenced and re-adjudicated.

Instrumentation reports 10 allowed and 0 blocking families, with 0 stale
entries.

## Consequences

`make lint` now runs the duplication gate after Skylos, and `make duplication`
runs it standalone. A clone past the size floor blocks the canonical pipeline
unless it is extracted or recorded with a reason a reviewer can weigh.
Reviewers gain a durable, versioned record of which parallels are intentional
and why, which is the outcome that matters: the exceptions are the reviewable
artefact, not an absence of findings.

The costs are real. The detector is a Rust binary, so a new contributor needs
`make install-nose` and a working `cargo-binstall`, and native Windows is not
supported by the unchanged helper, which uses `fcntl.flock` and POSIX directory
operations. The size floor and ranking budget mean lower-ranked duplication is
measured but not enforced, and the file-granular keys noted above make an
exception slightly wider than the family it was written for. Those limitations
are recorded here rather than resolved by widening the gate, because a gate
that reports everything is one nobody adjudicates.

## Alternatives considered

**Adopt a Python-native detector.** Rejected by reference. `leynos/episodic`
evaluated PyChase, pyscn, and others before settling on nose; this repository
adopts that outcome rather than repeating the competition. A Python-native
detector would also have to be pinned and provisioned with at least as much
care, without the evidence that already exists.

**Suppress the initial scan wholesale.** Rejected outright. Automatic
allowlisting of the baseline, mass-generated reasons, or a repository-wide
wildcard would all convert the gate into a formality that reports nothing and
enforces nothing.

**Gate `tests/` as well.** Rejected. The tree is dominated by deliberately
parallel scenario scaffolding, so gating it would generate findings that are
correct by construction while saying nothing about production duplication.

**Extract the `toml_coerce` scaffolding.** Rejected on evidence. The gutting
experiment above shows the match survives the removal of all behaviour, so an
extraction would share documentation machinery rather than logic and would
couple the coercers to it.

## References

- Reference PR:
  [leynos/episodic#276](https://github.com/leynos/episodic/pull/276)
- Reference revision: `d9e5ac0d254f375e2986f52d91a3b88c117c833b`
- Upstream detector: [corca-ai/nose](https://github.com/corca-ai/nose) at
  `v0.20.0`
- `[tool.nose]` and `[tool.duplication_gate]` in `pyproject.toml`
- `tests/workflow_contracts/test_duplication_toolchain_contract.py`
- [ADR-003](003-three-tier-python-linting.md) for the existing `make lint`
  stages
