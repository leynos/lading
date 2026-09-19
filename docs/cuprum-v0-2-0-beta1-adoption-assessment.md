# Cuprum v0.2.0-beta1 adoption assessment

## 1. Recommendation and scope

**Proceed with phase 5 using a compatibility adapter, subject to the acceptance
gates below.** Cuprum already provides live stdout/stderr relay, simultaneous
capture, synchronous and asynchronous execution, stdin input, cancellation,
timeouts, and structured execution observation. Waiting for a line-iteration
API is unnecessary for lading's publish streaming requirement.

There is no demonstrated unavoidable upstream blocker to replacing lading's own
`subprocess` usage when wrappers and test-fixture changes are permitted. There
are, however, incompatible existing cuprum call sites, plus two demonstrated
blockers to a *direct, behaviour-preserving replacement*: environment
replacement semantics and broken-pipe handling. The environment difference
needs a substantially heavier shim than relay failures. Adoption must not
silently change either contract.

This assessment treats the inspected implementation as the final 0.2.0 surface
for workaround planning. Open pull requests (PRs) are assessed separately; no
unmerged functionality is required by the proposed migration. General-purpose
features remaining after those PRs are listed in section 6. This is an
assessment and proposed direction, not an accepted architectural decision or an
implementation of phase 5.

### 1.1. Evidence boundary

- Assessment date: 2026-09-19.
- Lading: `c0dda6821de1ac097ce89be96003a051aa11ac99`, initially clean.
- Cuprum: `861fe2f053645311482141f155baeaa70dca0299`, initially clean;
  the GitHub `main` commit matched the sibling checkout during inspection.
- The prospective release name is supplied by the release assumption. Cuprum's
  source metadata still says `0.1.0`; no published beta artefact was tested.
- Runtime probes used Python 3.13 on Linux, importing the sibling source tree
  through `uv run --no-project --python 3.13 python`. They exercise the Python
  execution path, not a newly built native wheel or a merged PR combination.
- All 16 open cuprum PRs were inventoried through GitHub. The public API patches
  of the three relevant feature PRs were inspected as well as their
  descriptions.

The source and runtime observations below describe those exact revisions. They
do not certify Windows/macOS behaviour, packaging, all native backends, or the
future beta's complete test suite.[^1]

## 2. Actual migration scope

The [phase 5 roadmap](roadmap.md#5-command-execution-modernization) predates
some implementation changes. Metadata already calls the injected
`CommandRunner`; `_ensure_command()` no longer exists. The concrete spawning
code is now in `lading/runtime/subprocess_runner.py`, not
`publish_execution.py`. Production metadata no longer imports plumbum. The
staged catalogue already includes `sccache` alongside `cargo` and `git`. These
discrepancies should be corrected during implementation rather than following
the older symbol names literally.

| Boundary                                                             | Current requirement                                                                                                      | Migration consequence                                            |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------- |
| `lading/runtime/subprocess_runner.py`                                | One `Popen` boundary; capture both pipes, relay incrementally, optional stdin, cwd, environment, spawn-error translation | Replace the backend behind the existing `CommandRunner` protocol |
| `workspace/metadata.py` and publish/preflight callers                | Tuple result; metadata stdout remains silent; stderr remains live; domain-specific errors                                | Preserve the tuple and exception contracts                       |
| Lockfile discovery/regeneration and sccache statistics               | Git/Cargo execution and the exact compiler-wrapper executable                                                            | Include these callers, not just publish and metadata             |
| `lading/testing/cmd_mox_runner.py`                                   | Stub responses plus local passthrough, resolved executable paths, stdin, filtered PATH, PWD, result reporting            | Route real passthrough through the same cuprum adapter           |
| `tests/e2e/helpers/{git_helpers,e2e_steps_helpers}.py`               | Remaining plumbum execution and Git exception handling                                                                   | Replace both helpers and remove the test dependency              |
| `tests/bdd/steps/test_common_steps.py`                               | Real Python CLI child and captured status/output                                                                         | Use a test-only catalogue for the interpreter                    |
| `tests/e2e/test_upload_release_wheels_cli.py`                        | Real helper-script CLI, captured output and deliberately omitted environment keys                                        | Adapt result fields and preserve absent environment variables    |
| `tests/integration/{test_cargo_shim_cli,test_lockfile_discovery}.py` | Direct shim and Git fixture execution                                                                                    | Register exact fixture executables and preserve exit checks      |
| `tests/workflow_contracts/test_lint_target.py`                       | `make -n lint`, with checked exit status                                                                                 | Use a test-only make entry and explicit result checking          |
| `tests/e2e/test_staging_cleanup_on_termination.py`                   | Start child, wait for readiness, send SIGTERM, wait/kill, assert negative signal status                                  | Use an async run plus public observation and a POSIX signal shim |

*Table 1: Current execution boundaries within the requested scope.*

There are seven Python files importing the standard-library `subprocess`
module: one production runner and six test files. Adapter imports whose module
name contains `subprocess_runner` are additional migration touchpoints, not
additional process-spawning implementations.

The release uploader already uses cuprum, but its old API calls also need
migration as described in section 3.1. The extensionless
`scripts/publish-check/bin/cargo` uses `os.execvp`, which replaces the current
process rather than creating a managed child. Removing that exec handoff is not
required to remove `subprocess`; replacing it would need a separate assessment
of signal and exit semantics. Third-party dependencies' internal process
implementations are likewise outside this repository migration.

## 3. Blockers and release gates

### 3.1. Existing cuprum calls break on a dependency-only upgrade

`scripts/release_wheel_upload.py:run_gh` uses
`scoped(allowlist=RELEASE_CATALOGUE.allowlist)` and
`command.run_sync(capture=True)`. Both forms raised `TypeError` in source-tree
probes. The current API requires
`scoped(ScopeConfig(allowlist=RELEASE_CATALOGUE.allowlist))` and
`run_sync(output=RunOutputOptions(capture=True))`, or the default capture
behaviour with no output argument.

This is an outright blocker to upgrading the dependency without changing
lading, not an upstream feature request. Correct both call sites or introduce a
local version adapter if dual-version support is actually required. Update the
obsolete scope examples in lading's catalogue documentation too. The standalone
entry point `scripts/upload_release_wheels.py` declares its own `cuprum>=0.1.0`
dependency in inline script metadata: changing only `pyproject.toml` and
`uv.lock` does not select the beta for that execution mode. Exercise the real
uploader boundary with a stub `gh`, without publishing.

### 3.2. Exact environment replacement is not directly expressible

Lading's implementation passes a supplied environment, after string conversion
and C-locale normalization, directly to `Popen(env=...)`. Despite the runner
protocol's wording about overrides, this replaces the inherited environment.
Cuprum's `ExecutionContext.env` always overlays the live parent environment;
`env={}` means inheritance. Passing a complete dictionary does not remove
unlisted inherited variables.[^2]

A probe placed `CUPRUM_ADOPTION_SENTINEL=inherited` in the parent and ran a
child with `ExecutionContext(env={})`: the child printed `inherited`. This
matters for reproducibility and for callers that intentionally omit
credentials, proxy settings, or compiler configuration. Setting a variable to
an empty string does not generally reproduce its absence.

This blocks a direct replacement preserving the existing concrete contract. It
does **not** require an upstream feature if an exec shim is acceptable: cuprum
can launch an isolated Python bootstrap, which selects the permitted
environment keys and calls `os.execve` for the validated target. A Linux probe
using `python -I -S` and `os.execve` confirmed that the final child no longer
received the sentinel. No `subprocess` call or global environment mutation is
needed.

There is a concrete caller for omission semantics:
`tests/e2e/test_upload_release_wheels_cli.py` removes `GITHUB_REF_NAME` and
`GITHUB_OUTPUT` when they are not supplied. A direct overlay would restore
those values from a CI parent, invalidating that test setup.

That workaround is significant engineering, not a completed solution. It must
preserve target resolution against the intended PATH/cwd, stdin, error
translation, signal behaviour, and command identity in telemetry. Keep secret
values out of bootstrap arguments; pass values through the environment and only
selection metadata through arguments. The bootstrap itself still starts with
the inherited environment: this is not an isolation boundary protecting the
bootstrap from that environment. Platform parity needs separate tests.

Recommendation: use direct overlays only where the existing caller explicitly
requires inheritance. Preserve exact replacement elsewhere with a tested shim,
or make an explicit, reviewed change to the runner contract. Do not temporarily
clear `os.environ`; that changes process-global state and races other work.
Native replacement/unset support is the highest-priority feature proposal.

### 3.3. Broken-pipe relay requires a compatibility sink

Lading's `write_to_relay_sink` disables a failed relay on `BrokenPipeError`
while preserving capture. Cuprum's echo handler specifically recovers from
`UnicodeEncodeError`; an otherwise equivalent broken text sink raised
`BrokenPipeError` from `run_sync()` in the probe.[^3]

This blocks unwrapped substitution for existing relay semantics, but a small
stateful sink can suppress broken-pipe writes and flushes after the first
failure. Reuse lading's existing relay policy through that sink rather than
retaining its process-spawning or pipe-draining threads. The sink must not
expose a raw `.buffer` that lets cuprum bypass the wrapper.

### 3.4. Publication and integration gates remain outstanding

Python compatibility is not a blocker: lading requires Python 3.13 or newer;
cuprum requires 3.12 or newer. Lading currently locks cuprum 0.1.0, however.
The beta must be explicitly selected, for example with a trial pin to
`cuprum==0.2.0b1`, and the lockfile regenerated. Merely retaining
`cuprum>=0.1.0` neither selects nor proves the beta implementation.

Before declaring adoption ready to ship, install the actual beta distribution
in a clean environment and run the lading acceptance gates in section 7. No
full lading migration suite, release-wheel installation, or cross-platform
certification has been performed by this documentation change. This is a
validation gate, not evidence of a cuprum defect.

## 4. Workarounds against the final 0.2.0 surface

The intended scope is the existing runtime boundary and test support. Reuse
`CommandRunner`, the catalogue, locale normalization, relay helpers, and
cmd-mox adapter; a second general-purpose process framework is unnecessary.
Record the eventual adapter decisions in the
[design document](lading-design.md#command-execution-migration) and its
maintainer contract in the [developer guide](developers-guide.md).

| Gap or difference                                  | Workaround and limits                                                                                                                                                             | Ownership                                          |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| Tuple API versus `CommandResult`                   | Always capture, assert non-`None` streams, return `(exit_code, stdout, stderr)`; keep non-zero exits as data                                                                      | Lading adapter                                     |
| Catalogue and execution authorization are separate | Use `sh.make(program, catalogue=LADING_CATALOGUE)` and `scoped(ScopeConfig(allowlist=...))`; a scope does not replace `make`'s default catalogue                                  | Lading adapter                                     |
| Executable paths differ from nominal names         | Validate configured sccache and resolved cmd-mox targets, then register the exact path in a narrowly scoped catalogue; keep interpreter, make, and fixture entries test-only      | Lading policy; general identity issue in section 6 |
| Missing binary versus unknown programme            | Wrap actual spawn `OSError` in `CommandSpawnError`; preserve metadata and publish translations. Treat unknown catalogue entries and forbidden execution as policy errors          | Lading adapter                                     |
| Default echo is off and bounded to 64 KiB per line | Set `capture=True`, `echo_stdout` from the caller, `echo_stderr=True`, and `max_echo_line_bytes=None` for existing relay parity                                                   | Configuration, not missing streaming               |
| Binary-buffer-first echo versus text-first relay   | Pass a text facade around `write_to_relay_sink`; retain UTF-8 replacement decoding, binary fallback, and broken-pipe suppression                                                  | Lading compatibility sink                          |
| Optional passthrough stdin                         | Convert the existing input to `StdinInput(text=...)` or `StdinInput(data=...)`; preserve `None` as inherited stdin                                                                | Lading adapter                                     |
| Environment conversion and C locale                | Reuse normalization and `LC_ALL=C`, `LANG=C`, `LANGUAGE=''`; separately handle replacement semantics from section 3.2                                                             | Lading policy                                      |
| cmd-mox bypasses the normal runner                 | Retain IPC, cargo namespacing, real-command overrides, PATH filtering, PWD, stdin, and reporting; replace only its real invocation                                                | Lading testing adapter                             |
| Running-process test fixtures                      | Create an async `run()` task; capture PID from a synchronous `start` hook, await readiness from stdout observation, signal via `os.kill`, and await the task with bounded cleanup | Test-only lifecycle shim                           |
| Synchronous callers inside an existing event loop  | `run_sync()` uses `asyncio.run()`; use `await run()` in async fixtures. A necessary thread bridge must propagate context-local scope/hooks                                        | Integration constraint, not a CLI blocker          |
| Checked exits and `CompletedProcess` consumers     | Read `exit_code`, explicitly enforce success where required, and use a project-owned result shape instead of importing subprocess result/exception classes                        | Test helper migration                              |
| Existing logging, timing, and metrics              | Keep one lading invocation log, current environment redaction, and domain duration metrics; install cuprum observers deliberately to avoid duplicate records                      | Lading observability integration                   |

*Table 2: Compatibility work that does not require changes to cuprum 0.2.0.*

The missing-binary distinction is particularly important. A registered but
absent executable raised `FileNotFoundError` in the probe.
`UnknownProgramError` reports catalogue lookup failure, not executable
discovery failure. The roadmap's proposed missing-Cargo mapping through
`UnknownProgramError` is therefore incorrect for the current API.[^4]

Preserve existing argument vectors as positional tokens: `builder(*argv)`. The
generic keyword builder serializes `check=True, locked=False` as
`('--check=True', '--locked=False')`. It does not know Cargo's boolean flags,
repeated options, subcommands, or `--` separator rules. Lading should keep
those domain-specific constructions and introduce typed local builders only
where they improve a real contract.

The SIGTERM probe used public `sh.observe()` start/stdout events, waited for
`ready`, sent SIGTERM to the owned PID, and received `exit_code == -15` with
the readiness output captured. This demonstrates feasibility for the current
POSIX-only termination tests. A full fixture must also handle failed startup,
early exit, readiness timeout, hook removal, task cancellation, and final
reaping; PID observation alone is not a reusable process handle.

## 5. Open PR coverage

These are proposals at the inspected heads, not claims about the released beta.
The three feature changes are useful independently of phase 5.[^5]

| PR and inspected head                                                                         | Proposed capability                                                                                                                  | Effect on this assessment                                                                                                                                               |
| --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [#365](https://github.com/leynos/cuprum/pull/365), `99d8920e6f1744ccb8292b4d9d8e390aed72c0a9` | `SafeCmd.lines()`, `LineEvent`, synchronous `on_line`, monotonic arrival times, line-stream ownership and observation                | Covers convenient line iteration and timestamped progress; current echo already satisfies live relay. Does not supply a public process-control handle                   |
| [#371](https://github.com/leynos/cuprum/pull/371), `6a0b591536369eedd783ab314bbada77028f8fda` | Result timing, child CPU and peak resident-memory measurements, observation projections and acquisition mode                         | Covers basic result measurements. Resource values remain unavailable for pipelines, Windows, and timeout paths; aggregate CPU fallback is approximate under concurrency |
| [#376](https://github.com/leynos/cuprum/pull/376), `d74ec2a0374cfd11ad70e22ae2a75f50d5b17f61` | Presentation sessions and opt-in GitHub Actions groups/annotations, terminal session outcomes, workflow-command injection protection | Covers CI presentation and sink finalization. Does not establish global argv redaction, non-blocking sink writes, or complete terminal `ExecEvent` coverage             |
| [#432](https://github.com/leynos/cuprum/pull/432), `7c69a5091d2ff14e7de6f4c9da831f542d18c46b` | Plan for `RustStreamError` at the PyO3 boundary                                                                                      | Planning only; not delivered runtime error handling                                                                                                                     |
| [#433](https://github.com/leynos/cuprum/pull/433), `2275fc69b9cddbfffa8424a07abbfa846fc3fc37` | Plan to hoist invariant execution-event fields                                                                                       | Planning only; not a command-builder type-safety feature                                                                                                                |

*Table 3: Open PRs relevant to execution APIs or their planned evolution.*

The remaining open PRs were `#401`, `#405`, `#406`, `#408`, `#413`, `#414`,
`#418`, `#420`, `#422`, `#423`, and `#426`. Their changed-file inventories
concern dependency updates, native formatting, Loom lifecycle validation,
benchmark infrastructure, or CI placement/timeouts. They do not supply the
public capabilities proposed in section 6. In particular, benchmark telemetry
in #418 is not an application subprocess-observability API.

Do not open duplicate feature requests for line iteration, ordinary result
timing/resource reporting, Actions grouping, or the planned native error enum.
Reassess the integrated heads after merging: inspection of separate PRs is not
evidence that their combined API and tests pass.

## 6. General-purpose features remaining after the open PRs

The following are feature candidates, not conditions imposed on the final 0.2.0
surface. Each removes a reusable limitation in process execution,
command-interface type safety, or observability. Cargo diagnostic parsing,
crate metrics, locale choices, cmd-mox routing, and sccache policy stay in
lading.

### 6.1. Explicit environment inheritance, replacement, and unset

**Priority: high; directly relevant.** Add a typed environment policy with
distinct inherit/overlay/replace modes and explicit deletion. Preserve today's
overlay default. Test empty replacement, missing-versus-empty variables, PATH
resolution, nested scopes, and concurrent invocations without mutating the
parent. No inspected open PR adds this. It removes the exec bootstrap and its
command-identity and isolation limitations.

### 6.2. Configurable, non-disruptive output delivery

**Priority: high; directly relevant.** Add an explicit echo-failure policy that
can disable a failed stream while continuing capture, covering broken pipes as
well as encoding failures. Report a bounded categorical diagnostic once per
affected stream. Preserve a strict mode for applications that require delivery
failure to fail execution.

Also provide a supported off-loop delivery mechanism with explicit queue,
overflow/backpressure, and shutdown rules. Current sink writes/flushes are
synchronous on the execution loop; a blocked destination can delay both pipe
draining and timeout/cancellation handling. A local worker/queue wrapper is
possible, but must choose between blocking, dropping, spooling, and memory
growth. PR #376 supplies presentation sessions, not that delivery policy.[^3]

### 6.3. Owned process handles and process-tree control

**Priority: medium; test migration benefits immediately.** Provide a public
managed execution handle with readiness-independent PID access, wait, signal,
terminate, kill, and deterministic cleanup. Define signalling-after-exit and
ownership semantics so callers do not coordinate raw PIDs themselves.

Separately support opt-in process groups/sessions and platform-appropriate
descendant cleanup. Current spawning does not request a new session/group and
teardown calls `terminate()`/`kill()` on the direct child. Cargo can start
compilers and build scripts; terminating Cargo is not a guarantee that those
descendants end. This is also a limitation of lading's current runner, not a
new adoption regression. PR #365 adds output iteration, not process-tree
ownership. Acceptance should include a grandchild holding an output pipe and
repeated cancellation during teardown.[^6]

### 6.4. Type-safe command construction and executable identity

**Priority: medium; directly relevant.** `Program` is a nominal string type,
while `SafeCmdBuilder` is `Callable[..., SafeCmd]`. That return type does not
give a type checker a meaningful argument contract for the generic builder.
Offer a public argument type and callable protocol, plus reusable composition
for flags, repeated options, option values, and positional separators. Keep
tool-specific schemas optional; a Cargo publishing DSL is not required.[^4]

Distinguish the logical programme identity from a validated executable path.
Today registering `/path/to/sccache` works, but creates a different catalogue
identity from `sccache`; naïvely accepting any matching basename weakens the
allowlist. A typed binding between a catalogue entry and its executable would
support wrappers, virtual environments, and controlled test substitution while
keeping telemetry labels stable. Acceptance should reject an unapproved path
despite a matching basename. None of the open PRs supplies these interfaces.

### 6.5. Complete execution outcomes and safe telemetry projection

**Priority: high for general observability; locally wrappable.** In a probe, a
missing executable emitted only `plan`; a cancelled run emitted `plan` and
`start` with no terminal execution event. Provide an exactly-once terminal
outcome for executions that entered observation, including spawn failure,
cancellation, non-zero exit, timeout, and success, correlated by `exec_id`.
Define preparation failures separately. This lets adapters retire per-run state
and account for failed attempts without application `try/finally` wrappers. PR
`#376` supplies terminal *presentation-session* outcomes, not this general
execution-event contract; #371 enriches existing exit measurements.

Add opt-in redaction/projection rules shared by logs, tracing, presentation
labels, and custom observation adapters. Existing log hooks project argv
verbatim; `ExecEvent` can also carry environment values and output lines.
Secrets in arguments or output therefore require consumer policy today. Apply
redaction to telemetry copies without altering executed argv or captured
results. PR #376's workflow-command escaping addresses injection, not secret
redaction. Test representative sensitive values across every enabled adapter
and retain bounded metric labels.[^7]

### 6.6. Bounded capture and explicit binary/stream I/O

**Priority: medium; broader subprocess parity rather than a phase 5 blocker.**
Echo bounds do not bound captured output. Offer capture-to-file/spooling or
bounded-tail results with explicit truncation metadata, independent stdout and
stderr capture policies, and a typed bytes-result mode. Existing lading
captures text and can retain that behaviour, but long-running command clients
should not have to choose between complete in-memory capture and no result.

Also consider streaming stdin from an async source and supported
file/descriptor redirection for commands that cannot buffer all input.
`StdinInput` currently accepts a complete text or bytes payload;
`CommandResult` exposes decoded strings. A custom binary sink or helper
executable can work around parts of this, but is not a coherent typed binary
execution API. PR #365's bounded line-delivery queue does not bound full
capture or add binary results.

## 7. Adoption acceptance gates

1. Select and install the actual beta artefact, regenerate the lockfile, and
   test the supported Python/platform combinations. Verify relevant pure-Python
   and native distributions independently where both are supported. Update the
   uploader's old cuprum calls and standalone dependency metadata; exercise
   both repository and standalone script execution with a stub `gh`.
2. Replace the central runner and real cmd-mox passthrough. Exercise metadata,
   package/publish, preflight, Git lockfile discovery, lockfile regeneration,
   and configured sccache paths through the new boundary.
3. Preserve live relay before process exit, output without trailing newlines,
   quiet metadata stdout, concurrent stdout/stderr draining, split UTF-8,
   narrow encodings, text-only sinks, broken pipes, and lines over 64 KiB.
   Compare capture and display separately.
4. Test missing/forbidden programmes, bad cwd, explicit environment omission,
   C locale, stdin/early stdin closure, timeout partial output, cancellation,
   and negative signal status. Assert that unrelated parent variables are
   absent when replacement was requested.
5. Run cmd-mox stub and real-passthrough cases, including PATH recursion
   avoidance, resolved executable overrides, PWD, input, reporting, and exactly
   one invocation log. Preserve domain errors and per-crate timing metrics.
6. Migrate all six direct-subprocess test files and both plumbum helpers.
   Preserve readiness-before-SIGTERM and cleanup-on-copy tests. Check tracked
   Python and extensionless scripts for remaining subprocess/plumbum execution,
   including result types and exceptions, rather than merely renaming imports.
7. Run `make test`, `make lint`, `make check-fmt`, and `make typecheck` for the
   implementation; run `make fmt`, `make markdownlint`, and `make nixie` for
   the documentation. Correct the stale roadmap/design descriptions and record
   the approved adapter policy before marking phase 5 complete.

### 7.1. Executed probe results

| Probe against inspected cuprum source                            | Observed result                                                                 |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Capture plus stdin plus independent echo, child exits 7          | Status 7; stdout `hello\n`; stderr `err\n`; only stderr echoed                  |
| Unbounded partial-line echo followed by a 0.3-second child sleep | First sink write at approximately 0.011 seconds; run completed at 0.315 seconds |
| Registered absent executable                                     | `FileNotFoundError`; observation phases `['plan']`                              |
| Empty environment overlay with parent sentinel                   | Sentinel inherited                                                              |
| Isolated Python exec bootstrap selecting environment keys        | Sentinel absent in final child                                                  |
| Text sink raising `BrokenPipeError`                              | Error propagated from `run_sync()`                                              |
| Public start/readiness hooks followed by SIGTERM                 | Captured `ready\n`; exit status -15                                             |
| Async cancellation after start                                   | Observation phases `['plan', 'start']`                                          |
| Generic keyword flags `check=True, locked=False`                 | `('--check=True', '--locked=False')`                                            |
| Legacy `scoped(allowlist=...)` and `run_sync(capture=True)`      | Each raised `TypeError` for an unexpected keyword argument                      |

*Table 4: Focused runtime evidence; timings are illustrative, not benchmarks.*

[^1]: Inspected
      [lading revision](https://github.com/leynos/lading/tree/c0dda6821de1ac097ce89be96003a051aa11ac99)
    and [cuprum revision](https://github.com/leynos/cuprum/tree/861fe2f053645311482141f155baeaa70dca0299).
    Release assumptions must be checked against the eventual tagged artefact.

[^2]: Lading's [runner](../lading/runtime/subprocess_runner.py) and
    [locale helper](../lading/utils/process.py); cuprum's
    [environment resolution](https://github.com/leynos/cuprum/blob/861fe2f053645311482141f155baeaa70dca0299/cuprum/context/env_overlay.py).

[^3]: Lading's [relay policy](../lading/runtime/stream_relay.py); cuprum's
    [echo implementation](https://github.com/leynos/cuprum/blob/861fe2f053645311482141f155baeaa70dca0299/cuprum/_stream_echo.py).

[^4]: Cuprum's
      [command API](https://github.com/leynos/cuprum/blob/861fe2f053645311482141f155baeaa70dca0299/cuprum/sh.py)
    and [programme type](https://github.com/leynos/cuprum/blob/861fe2f053645311482141f155baeaa70dca0299/cuprum/program.py).

[^5]: Live
      [open PR inventory](https://github.com/leynos/cuprum/pulls?q=is%3Apr+is%3Aopen)
    retrieved on the assessment date. Table 3 records the inspected feature
    heads so later changes can be distinguished from this snapshot.

[^6]: Cuprum's
      [spawn configuration](https://github.com/leynos/cuprum/blob/861fe2f053645311482141f155baeaa70dca0299/cuprum/_subprocess_execution.py)
    and [termination implementation](https://github.com/leynos/cuprum/blob/861fe2f053645311482141f155baeaa70dca0299/cuprum/_process_lifecycle.py).

[^7]: Cuprum's
      [execution events](https://github.com/leynos/cuprum/blob/861fe2f053645311482141f155baeaa70dca0299/cuprum/events.py)
    and [observability and argument-logging guidance](https://github.com/leynos/cuprum/blob/861fe2f053645311482141f155baeaa70dca0299/docs/users-guide.md#structured-logging-adapter).
