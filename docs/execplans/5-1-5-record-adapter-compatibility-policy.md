# Record the adapter compatibility policy (5.1.5)

Status: DRAFT — awaiting approval for implementation.

This ExecPlan is a living document. Keep Constraints, Tolerances, Risks,
Progress, Surprises & discoveries, Decision log, Outcomes & retrospective,
Conformance basis, and Verification plan current at each milestone.

## Purpose / big picture

Record a sufficiently precise policy for one cuprum execution adapter behind
`CommandRunner`, targeting **cuprum v0.2.0-beta2 (`0.2.0b2`)**. A maintainer
must be able to implement the later execution migration without guessing how
results, failures, environments, executable authorization, or relay behave.
Success for 5.1.5 is a coherent design §7, a substantive architectural decision
record (ADR), and maintainer guidance. It is not an implemented adapter or a
certified release artefact. This pull request supplies the plan only.

## Constraints

Preserve the existing callable signature and `(exit_code, stdout, stderr)`
result, non-zero exits as data, and domain exception translations. Keep cuprum
objects inside the adapter; callers continue to depend on the runner port.
Preserve positional argument vectors, C-locale normalization, text-first relay,
and cmd-mox isolation. Never implement environment replacement by mutating
process-global `os.environ`, and never retain a subprocess execution fallback.
The existing cmd-mox response-environment protocol is a separate concern; this
prohibition concerns composing child environments, not silently removing that
protocol's existing behaviour.

This implementation is documentation-only. Do not change Python, Rust,
dependency pins, lockfiles, runtime interfaces, tests, or release workflows. Do
not perform the production migration, the six direct-subprocess test
migrations, the two plumbum helper migrations, or release certification.
Dependency-internal subprocess use and the cargo shim's `os.execvp` replacement
remain outside phase 5. Existing upstream tracking in assessment §6.7 remains
the index; no duplicate upstream issues are needed.

## Tolerances (exception triggers)

The expected implementation touches at most seven documentation files: design,
new ADR, developer guide, contents, roadmap, assessment addendum, and this
plan. A user-guide clarification may replace the addendum within that bound or
be recorded as a justified eighth file. Any runtime change, new dependency,
changed public signature, weakened contract, or required second execution
backend is outside tolerance. Record the proposed deviation, affected
requirements, and options here, set status BLOCKED, and obtain direction before
proceeding. There is no elapsed-time limit. Stop on disk exhaustion or
unavailable compiler admission; do not clear caches or alter shared services.
Report failing gates with their logs rather than treating partial validation as
success.

## Risks

The main risk is applying beta1 conclusions to beta2 (high impact, observed).
The tagged beta2 guide documents native environment replacement and best-effort
broken-pipe handling. Revalidate against that exact version before recording
which compatibility machinery is necessary. Repository pins remain beta1 at the
planning baseline; another worktree is adopting beta2 independently.

A second risk is confusing a spawn failure with policy rejection (high impact).
Catalogue rejection must never become a misleading missing-Cargo diagnostic. An
exec shim would also move target failure into a child process, requiring a real
error channel rather than interpreting exit 127 or parsing stderr. Native
replacement avoids that extra failure boundary.

A third risk is overstating validation (medium impact, observed). Existing
roadmap prose calls a release artefact certified, whereas the supplied task
explicitly withholds certification. Preserve historical evidence with dates; do
not extrapolate dependency or stub-uploader smoke tests to migration or
cross-platform certification.

## Progress

- [x] (2026-10-20) Inspected branch ownership and renamed the working branch.
- [x] (2026-10-20) Completed two Wyvern reconnaissance assignments and inspected
  their source evidence in context pack `pk_au7yfut7`.
- [x] (2026-10-20) Researched tagged beta2 documentation using Firecrawl.
- [x] (2026-10-20) Completed six-perspective community-of-experts review;
  reconciled path resolution, exception ownership, gate safety, and memory
  costs.
- [x] (2026-10-20) Passed formatting, typechecking, lint, tests, Markdown
  spelling/lint, and Mermaid validation for the planning documents.
- [ ] Commit, push, and open the draft planning pull request.
- [ ] Obtain approval to execute this plan.
- [ ] M1: reconcile evidence and record design policy plus ADR.
- [ ] M2: synchronize maintainer guidance, validate, and mark 5.1.5 done.

## Surprises & discoveries

The local dependency is `cuprum==0.2.0b1`, despite the requested beta2 target.
The local assessment is a historical beta1/source-level assessment, not a beta2
API specification. The beta2 guide explicitly supports
`ExecutionContext(env=..., env_mode=EnvMode.REPLACE)`, with `EnvMode` imported
from `cuprum.context`. Its default is `EnvMode.OVERLAY`. This removes the
assessment's principal reason for an environment exec shim.

Beta2 also documents `BrokenPipePolicy.BEST_EFFORT`; this alone does not prove
lading's text-first encoding fallback and relay-observability policy. The
existing relay helpers remain the policy reference. The runner protocol calls a
supplied environment “overrides”, but the concrete production runner uses exact
replacement after normalization. Cmd-mox intentionally composes an inherited
environment before real passthrough; these are distinct boundaries.

## Decision log

- (2026-10-20) Plan a documentation-only implementation of 5.1.5. Runtime
  migration remains 5.2 onward, and dependency adoption remains separate.
- (2026-10-20) Prefer beta2 native replacement over an exec shim. Describe the
  shim's conditions and obligations, but do not require one when the target API
  already preserves the contract. This follows the user requirement “where
  necessary” and avoids an unnecessary process/error boundary.
- (2026-10-20) Retain a narrow text sink policy where native options do not
  provide text-first compatibility. The concrete compatibility commitment is
  the task's existing relay contract, not an incremental migration convenience.
- (2026-10-20) Reconcile certification wording during implementation without
  erasing dated 5.1.4 evidence or marking downstream migration complete.

## Outcomes & retrospective

Planning has established a beta2-specific direction and identified stale beta1
assumptions. No policy implementation, runtime migration, dependency bump, or
release validation has been performed by this plan. The six expert perspectives
completed review and their findings were incorporated. Planning gates passed on
2026-10-20: `make fmt`, `make check-fmt`, `make typecheck`, `make lint`,
`make test`, `make markdownlint`, and `make nixie`. Tests reported 1,332 passed
and 30 skipped, with 106 snapshots passing. Logs are under
`/tmp/<gate>-lading-5-1-5.out` (the final formatting log uses
`/tmp/fmt-lading-5-1-5-2.out`). These are baseline regression results, not
beta2 adapter integration evidence. Implementation status remains DRAFT until
explicit approval.

## Context and orientation

Start with `docs/contents.md`, `docs/repository-layout.md`, and
`docs/documentation-style-guide.md`. The architectural boundary is
`lading/runtime/runner.py:CommandRunner`. The current implementation is
`lading/runtime/subprocess_runner.py`, including `CommandSpawnError`,
`normalize_environment`, and `invoke_via_subprocess`. Locale policy lives in
`lading/utils/process.py`; relay policy lives in
`lading/runtime/stream_relay.py` and `lading/runtime/relay_events.py`.
`lading/utils/commands.py` owns `LADING_CATALOGUE` for cargo, git, and sccache.

`lading/cli.py:_select_runner` selects the production or cmd-mox runner.
`lading/testing/cmd_mox_runner.py` owns inter-process communication (IPC),
cargo namespacing, filtered PATH, PWD, input, and result reporting. Metadata
and publishing callers in `lading/workspace/metadata.py` and
`lading/commands/publish_execution.py` own domain-specific error translation.
The uploader already has a separate process edge in `scripts/release_gh.py`;
this task does not move it.

Read `docs/lading-design.md` §7, the assessment §§3–4, §6.7, and §7,
`docs/cuprum-users-guide.md`, and `docs/cmd-mox-usage-guide.md`. The imported
cuprum guide is beta1-era context; use the tagged beta2 guide below for target
API facts. Read ADR-006 for dependency ownership and ADR-007 for relay events.

Required skills are `execplans`, `codegraph-mcp`, `hexagonal-architecture`, and
`python-router`. Use CodeGraph for semantic relationships and `context_pack`
for agent evidence exchange. Use `firecrawl-mcp` for remote documentation and
`logisphere-experts` for design review. Follow `python-router` to testing or
verification skills only if later runtime work is explicitly authorized. Use
`commit-message` and `pr-creation` for delivery.

## Conformance basis

The repository baseline is commit `16b2c1a7698971bba24adbc8b7377277746121b5`.
There is no separate Terms of Reference for this task. Governing inputs are the
user-supplied roadmap 5.1.5, the checked-in roadmap §5.1, design §7, assessment
§§3–4 and §7, ADR-006, ADR-007, and the documentation style guide at that
revision. Where completion claims conflict, the task's no-certification scope
governs this work.

Use these selective trace identifiers in review notes:

- R1: tuple, non-zero result, and exception parity → M1 policy → A1 review.
- R2: inheritance/replacement, locale, and no global mutation → M1 → A2.
- R3: validated exact executable registration and scope → M1 → A3.
- R4: text-first relay and capture after sink failure → M1 → A4.
- R5: one adapter, cmd-mox isolation, and explicit downstream scope → M1/M2 →
  A5.
- R6: durable design/ADR, synchronized guidance, roadmap completion → M2 → A6.

External evidence, consulted through Firecrawl on 2026-10-20:

- [Tagged beta2 users' guide][beta2-guide]: environment modes, catalogue
  rejection versus missing executable, capture/echo, and broken-pipe options.
- [Beta2 release][beta2-release]: target release identity; release notes alone
  are not proof that lading integrates its APIs correctly.
- [Python 3.14 exec documentation][python-exec]: process replacement and
  explicit environment semantics. `execve` requires a resolved path; PATH
  lookup belongs to the `p` variants. This only supports the conditional shim
  discussion, not a claim of cross-platform parity.

## Intended policy and interfaces

R1: leave `CommandRunner(command, *, cwd=None, env=None, echo_stdout=True)`
returning `tuple[int, str, str]`. Always capture both streams; a non-zero child
exit is data. Empty argv remains `ValueError`. Actual launch `OSError` becomes
`CommandSpawnError` with the requested program, original reason, and chained
cause, preserving metadata/publish translations. `UnknownProgramError` and
`ForbiddenProgramError` remain preparation/authorization failures, not missing
binary errors. Resolver failures must remain distinguishable from actual spawn
failures. Do not add cuprum result objects to domain callers. M1 must include
an exception mapping table: empty input to `ValueError`; catalogue, scope, and
resolver failures to existing `LadingError` with the original cause; actual
spawn `OSError` to `CommandSpawnError`; non-zero exit to tuple data. Preserve
existing metadata/publish translations only for their existing spawn-failure
cases. No domain caller needs a cuprum exception import. Document any narrower
existing lading preparation error discovered during M1 rather than adding a new
hierarchy without need.

R2: production `env=None` inherits the parent, with the existing C-locale
normalization. A supplied mapping, including `{}`, replaces inheritance after
string conversion and locale normalization (`LC_ALL=C`, `LANG=C`,
`LANGUAGE=''`). Use beta2 `EnvMode.REPLACE` for the fully composed mapping;
ensure ambient cuprum scope overlays cannot reintroduce omitted keys. Cmd-mox
continues to compose its own inherited/filtered invocation environment first,
then passes that final mapping as exact replacement for real execution. No
parent dictionary is changed by adapter environment composition.

An exec shim is not selected for beta2's documented replacement API. If a
verified residual gap requires one, stop for a design deviation: specify an
isolated interpreter bootstrap, validated target resolved against intended
PATH/cwd, environment-carried values with no secret argv, inherited stdin or
explicit payload, signal/exit parity, target identity in logs, and a separate
unambiguous spawn-error channel. The bootstrap inherits the parent and is not
an isolation boundary. Never infer spawn failure from a target exit code.

R3: catalogue membership and execution scope are separate authorization checks.
Pass the intended catalogue explicitly to `sh.make`, and use an appropriately
narrow scope. Validate configured sccache and cmd-mox-resolved paths against
their authorized source, then register the exact executable identity in a local
catalogue. A matching basename alone is insufficient. Do not add arbitrary
discovered paths to the global catalogue or permit all programs. Separate
missing executable failures from unapproved identities; check-and-use
filesystem races remain possible and must not be advertised as sandbox
protection. Interpreter and fixture catalogues remain test-only.

Native execution, not only the conditional shim, must resolve bare names and
relative paths against the effective child PATH and requested cwd. Document
absent versus empty PATH behaviour without inventing a fallback to the parent's
PATH. Cover relative configured paths and symlink policy; lexical validation
alone does not establish authority. The configured `RUSTC_WRAPPER` and the
cmd-mox passthrough directive after existing PATH filtering are the respective
sources of authority, subject to existing validation. Beta2's scoped
`bind_executable` can retain logical identity and report `resolved_path`, but
it does not widen authorization. M1 must explain why exact-path registration is
selected here and how any future binding would retain the same trust rule.

R4: retain UTF-8 replacement decoding, stdout echo controlled by the caller,
always-on stderr echo, complete capture, and current unbounded echo-line
compatibility (`max_echo_line_bytes=None`). Prefer `RunOutputOptions` native
settings where equivalent. A text facade delegates writes and flush policy to
`write_to_relay_sink`, preserving encoding fallback, broken-pipe suppression,
and ADR-007 events. Expose no `.buffer` that lets cuprum bypass that facade;
only the existing helper may use the underlying sink's binary fallback. Inspect
`BrokenPipePolicy.BEST_EFFORT` for overlap so failure events are not emitted
twice. Do not retain process spawning or pipe-draining threads merely to
preserve relay. Make no total ordering promise across stdout and stderr.
Complete capture and unbounded echo lines retain potentially unbounded memory
costs for output and newline-free buffering; this policy makes no
bounded-memory claim and introduces no new truncation limit.

R5: keep arguments positional, leave command-specific construction in callers,
and preserve stdin distinctions (inheritance, empty text, and supplied data).
Keep cmd-mox IPC, namespace, PATH filtering, PWD, and reporting unchanged by
this policy. Synchronous and async execution must respect their existing
boundaries; an active event loop is not permission to nest `asyncio.run()`.
Avoid duplicate invocation logs and retain existing environment redaction and
duration metrics. No second generic runner abstraction is needed.

## Milestones and plateaus

### M1: record the policy and substantive decision

After approval, reread the current branch and beta2 dependency state. Sweep
existing helpers before describing any new abstraction. Reconcile the beta1
assessment with tagged beta2 behaviour in a dated addendum without rewriting
historical probe results. Record R1–R5 in design §7, preferably a new §7.6, and
correct adjacent examples or diagrams that imply incompatible contracts. Create
`docs/adr/010-command-runner-adapter-compatibility.md` if 010 is still free;
otherwise use the next available number and update all links.

Follow the existing ADR structure: context and problem statement, decision
drivers, decision, and consequences. Explain alternatives: direct overlay fails
omission semantics; native replacement is preferred; an exec shim is
conditional; global environment mutation and a retained subprocess backend are
rejected. Link the ADR from §7 and `docs/contents.md` immediately.

Plateau: coherent policy documents, with R1–R5 mapped to A1–A5 review evidence.
Run all gates below and commit this atomic documentation change. Conformance
review must confirm no runtime/dependency modifications and no unsupported
beta2 or release claims. On failure, repair only the affected documentation;
leave the milestone open rather than committing failed gates.

### M2: synchronize guidance and record completion

Update `docs/developers-guide.md` with a concise internal adapter policy and
links to §7/ADR, clearly distinguishing intended implementation from current
execution. Review `docs/users-guide.md`; no consumer behaviour changes in this
documentation-only task, so record that no user-guide change is necessary
unless a misleading existing guarantee needs correction. Keep component
interface details in design §7 rather than inventing another architecture doc.

Reconcile roadmap §5 certification language against dated evidence and the
user's scope, retaining independent completed 5.1.4 work. Mark only 5.1.5 `[x]`
(done) after A1–A6 and gates pass; do not mark 5.2, later migrations, or
release acceptance done. Update this plan's progress and retrospective, and
commit the coherent documentation milestone. Recovery is a focused correction
or revert of that commit; never reset another checkout's work.

## Verification plan

This task introduces documentation, not executable logic or new axioms to prove
in a Rust extension. Its acceptance evidence is a traceable policy review plus
existing regression gates. Red-Green-Refactor is inapplicable to prose; the
observable substitute is reviewing each missing policy requirement before M1
and finding its explicit, consistent statement after M1. Deleting the
environment-mode decision or conflating catalogue and spawn errors must fail
that review. No string-presence test should impersonate semantic validation.

A1 checks concrete tuple/error examples: `(7, 'out', 'err')` stays data,
unregistered program is policy rejection, and registered missing executable
retains chained spawn failure. A2 checks `{}` versus `None`, absent versus
empty values, the C locale, scope overlays, and cmd-mox's composition boundary.
A3 checks configured absolute paths, same-basename rejection, narrow scope,
different parent/child PATH values, omitted versus empty PATH, relative targets
under cwd, and symlinks. A1 also verifies each policy/resolution error maps to
a lading-owned failure with a retained cause. A4 checks complete capture with
failed write/flush, encoding fallback, and no raw buffer. A5 checks domain
ownership and cmd-mox responsibilities. A6 checks all links, target version,
completion wording, and the roadmap checkbox. Discharge these with reviewer
notes identifying the design/ADR paragraphs; any missing or contradictory case
leaves the corresponding item open.

External assumptions are beta2's documented modes/result/error interfaces,
Python environment/exec semantics, and the operating system's process API. They
are not formally proved by lading. Later runtime work must exercise the real
dependency boundary; source documentation and a mocked call are not integration
evidence. Native and pure-Python artefacts and supported platforms need their
own acceptance evidence before certification.

The following downstream test specification belongs to 5.2 and later work, not
to this documentation implementation. Reuse existing pytest seams in
`tests/unit/test_subprocess_runner_encoding.py`,
`tests/unit/test_subprocess_runner_logging.py`,
`tests/unit/test_cmd_mox_integration.py`, and
`tests/unit/utils/test_commands.py`. Before production changes, add the
smallest failing case, observe the intended failure, implement, and rerun it
plus the full gates. Cover happy, unhappy, and boundary cases above. Do not
leave expected-failure markers as acceptance evidence.

Use pytest-bdd for a future
`tests/bdd/features/command_runner_compatibility.feature`, with step bindings
under `tests/bdd/steps/test_command_runner_compatibility_steps.py`:

```gherkin
Feature: Preserve the command runner boundary
  Scenario: An explicit environment excludes inherited variables
    Given the parent has a sentinel absent from the supplied environment
    When a permitted real child runs with that explicit environment
    Then the child cannot see the sentinel
    And the parent environment is unchanged

  Scenario: A closed relay does not discard capture
    Given a permitted child writes stdout and stderr
    And the stdout relay raises BrokenPipeError
    When the runner completes
    Then both captured streams and the exit code remain available

  Scenario: Catalogue denial is not a missing executable
    Given a program is absent from the execution catalogue
    When the runner prepares the command
    Then preparation fails without spawning a process
    And no missing-Cargo diagnostic is produced
```

Run the future feature through
`uv run pytest tests/bdd/steps/test_command_runner_compatibility_steps.py` (one
command on one line). Reuse existing relay-observability feature coverage for
event semantics. End-to-end tests must exercise real cmd-mox passthrough and a
real child's env/cwd/stdin, including omitted `GITHUB_REF_NAME` and
`GITHUB_OUTPUT` in the uploader CLI boundary. Signal tests require bounded
cleanup and reaping.

For generated environment maps and positional argv, use Hypothesis to check
exact key/value preservation modulo documented locale normalization, omission,
and no mutation. Generate valid OS strings without NUL, include empty and
non-empty maps, classify collisions/omissions, and include explicit witnesses.
A seeded overlay-for-replacement fault must fail the sentinel test. Exercise
split UTF-8, invalid bytes, CRLF, and final unterminated output in relay cases;
assert per-stream order, not cross-stream order. Syrupy snapshots are suitable
for stable diagnostics and ADR-007 events alongside semantic assertions, with
paths/timestamps redacted; ordinary tuples do not need snapshots.

No Rust changes or new contractual business logic are introduced here, so
rstest, rstest-bdd, insta, proptest, kani, and verus are not dependencies of
5.1.5. If later scope introduces a pure contractual algorithm warranting proof,
revisit the plan and prefer a provable Rust boundary only with substantive,
satisfiable assumptions and a non-vacuous proof. Process integration itself
still requires real-process evidence.

## Concrete steps and validation

Run from the repository worktree root. Read `AGENTS.md` and verify
`git branch --show-current` before editing. Use the standard GitHub client as
`leynos`, clean command environments as required by AGENTS, and SSH for remote
Git operations. Never alter the other beta2-adoption worktree.

After each major milestone, stage intended Markdown files so the tracked-file
Markdown gate includes new documents. Delegate gates to one `scrutineer`, with
no overlapping gate in this worktree. Run these sequentially, each through
`tee` with Bash `pipefail` and a separate `/tmp` log:

```bash
set -e -o pipefail
make fmt 2>&1 | tee /tmp/fmt-lading-5-1-5.out
make check-fmt 2>&1 | tee /tmp/check-fmt-lading-5-1-5.out
make typecheck 2>&1 | tee /tmp/typecheck-lading-5-1-5.out
make lint 2>&1 | tee /tmp/lint-lading-5-1-5.out
make test 2>&1 | tee /tmp/test-lading-5-1-5.out
make markdownlint 2>&1 | tee /tmp/markdownlint-lading-5-1-5.out
make nixie 2>&1 | tee /tmp/nixie-lading-5-1-5.out
```

Each command must exit zero; pytest must report no unexpected failures, and
Markdown/diagram validation must finish successfully. Stop progression on a
failed gate, inspect its saved log, and rerun only after a justified fix.
Inspect formatter churn and exclude unrelated changes. Never hand-edit the
generated `typos.toml`; follow repository policy if its regeneration differs.
After formatting and final evidence updates, re-stage only the intended files.
Inspect both `git diff` and `git diff --cached`; the committed versions must
match the gated content. Recheck Markdown after final prose changes. Run
`git diff --check`, review the staged diff, and commit with a temporary message
file and `git commit -F`. Record gate outcomes here, not just commands.

## Idempotence and recovery

Documentation edits and checks can be repeated. Recheck ADR numbering and
existing paragraphs before adding duplicates. Gates may update local tool
caches and lockfiles; inspect status and never include unrelated generated
changes. A failed gate does not authorize changing runtime code in this plan.
Preserve logs and report baseline/tooling failures precisely. Do not certify
unrun or failed checks, bypass compiler admission, or use `/tmp` for builds.

## Artefacts and notes

The planning branch is `5-1-5-record-adapter-compatibility-policy`; its
upstream must be `origin/5-1-5-record-adapter-compatibility-policy` after
publication. The draft PR title is
`Plan: Record adapter compatibility policy (5.1.5)`. Link this plan through
that branch in the PR summary and append the session link in a final
`## References` section. Preserve draft state. The roadmap checkbox remains
open in the planning PR.

Revision note (2026-10-20): initial plan incorporates repository reconnaissance
and beta2 research. Native replacement supersedes the beta1 shim assumption;
implementation and release validation remain explicitly open.

[beta2-guide]: https://github.com/leynos/cuprum/blob/v0.2.0-beta2/docs/users-guide.md
[beta2-release]: https://github.com/leynos/cuprum/releases/tag/v0.2.0-beta2
[python-exec]: https://docs.python.org/3.14/library/os.html#os.execve

Revision note (2026-10-20, expert review): Pandalump and Telefono required
native PATH/cwd witnesses and explicit lading-facing exception mapping.
Doggylump reinforced spawn-versus-policy diagnostics. Dinolump required
fail-fast gates and restaging; Buzzy Bee requested explicit memory costs.
Wafflecat supported native replacement over a needless shim. All findings are
incorporated; downstream runtime validation remains open.
