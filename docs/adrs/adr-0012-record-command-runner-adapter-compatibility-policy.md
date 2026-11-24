# ADR-012: Preserve the command runner contract in the cuprum adapter

## Status

Accepted, 2026-11-24. One cuprum adapter behind `CommandRunner` preserves the
existing tuple result, the existing failure classification, exact environment
replacement, exact-executable authorization, and the text-first relay, without
mutating `os.environ` and without retaining a subprocess backend.

This is a policy decision; the adapter itself is roadmap 5.2. Section 7.6 of the
[design document](../lading-design.md#76-adapter-compatibility-policy-step-515)
is the implementation-facing statement of the same decision.

## Date

2026-11-24

## Context and problem statement

Phase 5 of the roadmap replaces lading's own process execution with one cuprum
adapter behind the existing `CommandRunner` port. The port is small and its
contract is load-bearing:

```python
def __call__(
    self,
    command: cabc.Sequence[str],
    *,
    cwd: Path | None = None,
    env: cabc.Mapping[str, str] | None = None,
    echo_stdout: bool = True,
) -> tuple[int, str, str]:
```

Everything that depends on it -- workspace metadata, publish preflight, the
publish pipeline, lockfile discovery and regeneration, sccache statistics, and
the cmd-mox test adapter -- is written against that signature, its
`(exit_code, stdout, stderr)` result, the rule that a non-zero exit is data,
and the rule that a failure to launch raises `CommandSpawnError`.

The current backend is `lading/runtime/subprocess_runner.py`. It contains the
only production `subprocess.Popen` call, it applies
`c_locale_env(normalize_environment(env))` and passes the result as
`Popen(env=...)`, and it relays both pipes through threads whose policy lives in
`lading/runtime/stream_relay.py`: UTF-8 replacement decoding, a binary
fallback when the parent text stream cannot encode, and suppression of a broken
pipe without losing captured output. Each relay decision emits an
[ADR-007](adr-0007-relay-observability-events.md) event.

The migration is therefore not a swap of one call for another. Cuprum has its
own result object, its own exception hierarchy, an environment model that
defaults to overlaying the parent, an allowlist that is a separate policy
surface from the catalogue, and an echo path that is byte-first when a sink
exposes a buffer. Each of those can change observable behaviour, and the beta1
assessment recorded two of them as blockers: exact replacement was not
expressible, and a broken text sink aborted the run.

The question this ADR answers is what the adapter must guarantee, so that 5.2
can be implemented without re-deciding these questions per call site.
`cuprum==0.2.0b2` is now pinned on every dependency path, and both beta1
blockers have native answers, so the decision is no longer hypothetical.

## Decision Drivers

- The port's contract is what every caller and every test already depends on;
  a migration should not change it, and a caller should never import a cuprum
  type.
- A policy rejection must never be reported as a missing executable. Missing
  Cargo is a distinct, actionable operator diagnostic, and collapsing the two
  produces a wrong remedy.
- The child environment must be exactly what the caller composed. Callers
  deliberately omit credentials and CI variables, and an inherited value that
  reappears silently is a correctness and a security problem.
- Authorization must name the file that runs. A configured `RUSTC_WRAPPER` and
  a cmd-mox-resolved override are absolute paths, and a catalogue that
  authorized a name rather than a path would authorize the wrong binary.
- Live output, complete capture, and a failed parent sink must remain
  independent of each other, because an operator watching a build and a machine
  reading its result need different guarantees from the same run.
- Process-global mutation of `os.environ` is unavailable: it races every other
  command in the process and every thread.
- A second compatibility layer is a cost, not a feature. Machinery is adopted
  only where the pinned dependency cannot express the contract.

## Options considered

### Option A: overlay the caller's environment onto the parent's

Pass the caller's mapping as `ExecutionContext.env` and leave the default
`EnvMode.OVERLAY`. This is the smallest adapter: no mode to select, no composed
environment to reason about.

It fails the contract. `env={}` and `env=None` would mean the same thing, and a
parent variable the caller deliberately omitted -- `GITHUB_REF_NAME`,
`GITHUB_OUTPUT`, a proxy setting, a compiler configuration -- would reappear in
the child. The uploader's end-to-end test removes exactly those variables when
they are not supplied, so the test would pass for the wrong reason.

### Option B: mutate `os.environ`, run, and restore

Set the composed environment into `os.environ`, run the command, and put the
old values back.

This is rejected outright. It is process-global, so any concurrent command,
thread, or test observes the mutated environment, and a failure between the
write and the restore leaves the process wrong. It also cannot express omission
without a sweep of every key, which widens the window.

### Option C: an exec shim through an isolated interpreter bootstrap

Launch a bootstrap that selects the permitted keys and calls `os.execve` on the
validated target. This was the beta1 recommendation for replacement, and it was
measured to work: the final child saw no sentinel.

It is not selected. `EnvMode.REPLACE` now expresses the same contract natively,
and a shim would add a process boundary: the actual target's exit status
arrives through the bootstrap, its spawn failure has to be distinguished from
the bootstrap's, and the target's identity has to be carried in telemetry that
the caller can trust. It remains the fallback shape if replacement is ever
lost, under the conditions the design records.

### Option D: native replacement, native broken-pipe policy, text facade relay

Compose the environment with the existing helpers, pass it with
`EnvMode.REPLACE`, select `BrokenPipePolicy.BEST_EFFORT`, and echo through a
text-only facade that delegates to `write_to_relay_sink`. Map cuprum's
preparation errors and launch `OSError` explicitly onto lading's exceptions.

This is the selected option.

| Topic            | A: overlay   | C: exec shim    | D: native policy     |
| ---------------- | ------------ | --------------- | -------------------- |
| Omission         | Not possible | Exact           | Exact                |
| Extra boundaries | None         | Process + error | None                 |
| Global mutation  | None         | None            | None                 |
| Relay policy     | Unchanged    | Unchanged       | Unchanged via facade |
| Relies on beta2  | No           | No              | Yes, measured        |

_Table 1: Comparison of the environment strategies._

## Decision

The adapter is a compatibility layer with five fixed obligations. Design §7.6
states each in implementation terms; this section states the decision.

### Result and failure classification

The adapter keeps the `CommandRunner` signature and the `(int, str, str)`
result, always captures both streams, and treats a non-zero exit as data. It
maps failures as follows, in this order, before any caller sees them:

| Situation                   | Becomes                                   |
| --------------------------- | ----------------------------------------- |
| Empty argument vector       | `ValueError`                              |
| `UnknownProgramError`       | `CommandPreparationError`, cause retained |
| `ForbiddenProgramError`     | `CommandPreparationError`, cause retained |
| `ExecutableResolutionError` | `CommandPreparationError`, cause retained |
| Launch `OSError`            | `CommandSpawnError`, cause retained       |
| Non-zero child exit         | Result data, no exception                 |

The order matters because `ForbiddenProgramError` is a `PermissionError` and so
an `OSError`; a blanket `except OSError` would report a policy denial as a
missing binary, and would also miss `ExecutableResolutionError`, which is a
`RuntimeError`. `CommandPreparationError` is added by 5.2.1 as a `LadingError`
so that preparation failures cannot reach the existing translations that turn a
spawn failure into `CargoExecutableNotFoundError` or `PublishPreflightError`.
No domain caller imports a cuprum exception.

### Environment

`env=None` inherits with C-locale normalization. Any supplied mapping, including
`{}`, replaces inheritance. Replacement is
`ExecutionContext(env=composed, env_mode=EnvMode.REPLACE)`, which starts from
an empty environment, discards ambient scope overlays, and leaves `os.environ`
untouched. `None` is converted by the adapter, because cuprum treats `env=None`
under `REPLACE` as the empty environment too. `cmd_mox_runner` keeps composing
its own inherited, filtered environment first and hands the final mapping to
the adapter as a replacement.

### Authorization

Catalogue membership (at `sh.make`) and allowlist permission (at run) stay
separate checks, and a per-call scope narrows rather than widens. A configured
sccache path and a cmd-mox-resolved path are registered as exact-path `Program`
entries in a local catalogue for that invocation; a matching basename does not
authorize. `bind_executable()` is the documented alternative and does not widen
authorization; it does not authorize the bound file either, so authorization
and resolution are not interchangeable. It is not selected because these
callers have no stable logical name to preserve. Validation is not a sandbox,
and the check-and-use race is documented rather than denied.

### Relay

Capture with
`RunOutputOptions(capture=True, echo_stderr=True, max_echo_line_bytes=None)`,
echo via a text-only facade that owns `write_to_relay_sink`'s state and exposes
no `.buffer`, and select `BrokenPipePolicy.BEST_EFFORT` as a guard that should
not fire while the facade is absorbing. The facade's ADR-007 event records a
relay decision; a `CommandResult.relay_fallbacks` entry records that the facade
did not absorb a sink failure. Emitting both for one decision is a defect.
Capture stays complete and echo lines stay unbounded, which retains the
existing memory cost and adds no new limit.

### Boundary

Positional arguments, caller-owned command construction, three distinct stdin
meanings, unchanged cmd-mox IPC and namespacing, no nested `asyncio.run()`, one
invocation log record, one adapter, and no second generic runner abstraction.

## Consequences

- Every caller and test keeps its current contract, so the migration is
  invisible at the port. A domain module never imports cuprum.
- Policy denial, missing executable, and non-zero exit stay three distinct
  outcomes, and the missing-Cargo diagnostic keeps its meaning.
- The environment contract is exact, so a caller that omits a variable can
  rely on it being absent. Requesting inheritance remains an explicit choice.
- The adapter depends on two beta2 opt-ins -- `EnvMode.REPLACE` and
  `BrokenPipePolicy.BEST_EFFORT` -- whose behaviour is pinned by
  `tests/unit/test_cuprum_policies.py`. A later beta that changes either is a
  design deviation, not an implementation detail.
- The relay has a deliberate no-buffer rule. A sink that exposes `.buffer`
  would silently move echo onto cuprum's byte path and lose the encoding
  fallback and its event, so the rule is a tested invariant rather than a
  stylistic preference.
- `bump_lockfile_regeneration`'s cause labels and the developers' guide metric
  contract must gain the preparation cause in the same change that introduces
  the exception, or a policy denial is recorded as a cargo exit.
- The adapter retains the existing memory profile. Complete capture and
  newline-free echo lines are still unbounded, and this decision does not claim
  otherwise.
- Validation of executable identity remains advisory. The policy documents the
  race instead of implying protection the operating system does not provide.

## References

- Design section
  [7.6](../lading-design.md#76-adapter-compatibility-policy-step-515) and its
  requirement identifiers R1 to R5.
- [Cuprum beta adoption assessment](../cuprum-v0-2-0-beta1-adoption-assessment.md),
  sections 3, 4, 7, and 8.
- [ADR-006](adr-0006-align-cuprum-selection-across-dependency-paths.md) for the
  pin and lock policy this decision relies on.
- [ADR-007](adr-0007-relay-observability-events.md) for the relay event
  contract the facade continues to emit.
- [Roadmap](../roadmap.md) step 5.1.5 and step 5.2.
