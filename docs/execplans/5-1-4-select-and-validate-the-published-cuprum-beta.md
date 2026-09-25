# Select and validate the published cuprum beta for both dependency paths

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & discoveries`, `Decision log`,
`Outcomes & retrospective`, `Conformance basis`, and `Verification plan` must
be kept up to date as work proceeds.

Status: DRAFT

Roadmap item: 5.1.4 in [the roadmap](../roadmap.md), step 5.1 "Establish the
beta contract and dependency boundary".

## Purpose / big picture

`lading` is a Python command-line tool that manages Rust workspaces. It depends
on `cuprum`, a library for running external programs through an allowlist
called a catalogue. Today the repository locks cuprum 0.1.0. Phase 5 of the
roadmap replaces lading's own `subprocess` and `plumbum` execution with one
cuprum adapter, and that work is planned against the cuprum 0.2.0 API, which is
only available in the published beta, `cuprum==0.2.0b1` (uploaded to PyPI on
2026-09-25).

Cuprum reaches lading along two independent dependency paths:

1. The repository path: `pyproject.toml` declares the dependency and `uv.lock`
   pins it. Every test, the `lading` package, and its doctests run here.
2. The standalone path: the release workflow runs
   `uv run --script scripts/upload_release_wheels.py`. That command reads the
   PEP 723 inline metadata block at the top of the script (a `# /// script`
   comment block that lists the script's own dependencies) and ignores both
   `pyproject.toml` and `uv.lock`.

The beta removed two keyword forms that lading still uses:
`scoped(allowlist=...)` and `run_sync(capture=...)`. Each now raises
`TypeError`. The release uploader's `run_gh` uses both, so a dependency-only
upgrade would break the release job the next time a tag is pushed.

After this change, both dependency paths select exactly `cuprum==0.2.0b1`, the
release uploader and the catalogue tests use the beta's `ScopeConfig` and
`RunOutputOptions` forms, and the suite proves that the installed beta works
through both paths with a stub `gh`. Nothing in validation can publish a GitHub
release. Success is observable by running:

```bash
make test
uv tree --script scripts/upload_release_wheels.py --depth 1 | grep cuprum
```

The suite passes, including a behavioural scenario that runs the uploader both
ways against a recording `gh` stub, and the second command prints
`cuprum v0.2.0b1`.

This task does not replace the production runner, migrate cmd-mox passthrough,
or remove `subprocess` from tests. Those are roadmap items 5.1.5, 5.2, and 5.3.

## Constraints

- Change only the dependency selection, the uploader's cuprum adapter
  (`scripts/release_wheel_upload.py:run_gh`), the catalogue call sites that the
  beta breaks, their tests, and documentation. Do not modify `lading/runtime/`
  (the `CommandRunner` protocol, `subprocess_runner.py`, `stream_relay.py`),
  `lading/testing/cmd_mox_runner.py`, or any production command path. Those
  belong to roadmap items 5.1.5 and 5.2.
- Keep the uploader's port stable. `upload_wheels(tag, wheels, *, run=run_gh)`
  receives its runner through the `UploadRunner` parameter; `run_gh` is the
  driven adapter behind it. Its signature
  `run_gh(arguments: Sequence[str]) -> CommandOutcome` and the `CommandOutcome`
  fields (`exit_code`, `stdout`, `stderr`) must not change. `discover_wheels`,
  `upload_wheels`, `report_outcome`, and the `Outcome` values must not change.
- Keep the release workflow invocation exactly
  `uv run --script scripts/upload_release_wheels.py --directory dist`. The
  contract test `tests/workflow_contracts/test_release_workflow.py` pins it.
- Keep the `gh` catalogue allowlist to `gh` alone (ADR-005).
- Keep `gh` output captured. The developers' guide records three tests that
  depend on `gh`'s stderr reaching the caller.
- No validation step may reach the real `gh` binary or hold GitHub
  credentials. Every test that runs the uploader must put a recording stub
  first on `PATH` and must remove `GH_TOKEN` and `GITHUB_TOKEN` from the child
  environment.
- Use the shared default uv and Cargo caches. Do not create an isolated cache
  and do not use `/tmp` as a build target.
- Do not grow `scripts/release_wheel_upload.py` (401 lines) or
  `tests/unit/test_upload_release_wheels.py` (417 lines). Both already exceed
  the 400-line limit in `AGENTS.md`; add new tests in new files.
- Do not introduce compatibility machinery. No wrapper accepts both the 0.1.0
  and 0.2.0 call forms, and no conditional import selects between them. Every
  caller moves to the beta form in the same commit as the pin.
- Prose and comments use en-GB-oxendict spelling (`-ize`, `-yse`, `-our`).

If satisfying the objective requires violating a constraint, stop, record the
conflict in `Decision log`, and escalate.

## Tolerances (exception triggers)

- Scope: stop and escalate if the work needs more than 16 files or more than
  700 net lines changed, excluding `uv.lock` and this plan.
- API surprise: stop if the beta breaks any cuprum usage other than the flat
  `scoped(allowlist=...)` and `run_sync(capture=...)` forms, for example
  `Program`, `ProjectSettings`, `ProgramCatalogue(projects=...)`, `sh.make`,
  `SafeCmd.argv`, or `UnknownProgramError`. The Stage A probe found no such
  break.
- Test surprise: stop if, after the pin, any test other than the 13 recorded
  in `Surprises & discoveries` fails for a reason other than the flat keyword
  forms.
- Resolution: stop if uv needs a `--prerelease` flag, a `[tool.uv]`
  `prerelease` setting, or an `exclude-newer` change to select the beta on
  either path. The Stage A probe resolved `cuprum==0.2.0b1` on both paths
  without any of them.
- Network: stop if the standalone scenario cannot reach the package index in
  CI (`ci.yml`) and the only remedy is to skip it.
- Interface: stop if any constraint above would need a signature change.
- Iterations: stop if a gate still fails after three fix attempts.
- Ambiguity: stop and present options if the beta is yanked or superseded by a
  later pre-release before merge.

## Risks

- Risk: the standalone scenario needs the package index, so it can fail on a
  machine without network access or during a PyPI outage. Severity: medium.
  Likelihood: low. Mitigation: the scenario uses the shared uv cache, so after
  one online run it resolves from cache. It has a generous per-test timeout
  (180 seconds). CI (`ci.yml`) already downloads packages for `make test`. Do
  not skip on failure; a skip would hide a broken standalone path.
- Risk: `cuprum==0.2.0b1` is yanked or replaced by `0.2.0b2` or `0.2.0`
  before this lands. Severity: medium. Likelihood: low. Mitigation: the exact
  pin plus the lockfile hash keeps selection deterministic. Tools install an
  exact `==` pin even when yanked. Moving to a later version is a deliberate
  follow-up edit to the same three sites, which the alignment contract test
  forces to stay in step.
- Risk: the published `lading` wheel will now require a pre-release. Pip only
  admits a pre-release when the specifier names one, and `==0.2.0b1` does, so
  `pip install lading-*.whl` still works. A user who pins a different cuprum in
  the same environment would conflict. Severity: low. Likelihood: low.
  Mitigation: say so in the users' guide installation section.
- Risk: the native wheel (`manylinux_2_28`) needs glibc 2.28 or newer. On
  older glibc or musl, installers fall back to the pure-Python wheel. Severity:
  low. Likelihood: low. Mitigation: validate the pure-Python wheel
  independently (EP-M3). Lading never builds cuprum pipelines, so the Rust
  stream backend is never on its execution path; capture always uses the Python
  pathway.
- Risk: the beta ships no `py.typed` marker, so `ty` may treat cuprum
  imports differently from annotated code. Severity: low. Likelihood: low.
  Mitigation: cuprum 0.1.0 had no marker either, and `make typecheck` already
  passes with it. Run `make typecheck` in EP-M2 and escalate on any new
  diagnostic rather than adding `# type: ignore`.
- Risk: Dependabot proposes a cuprum bump that edits only `pyproject.toml`
  and `uv.lock`, leaving the script metadata behind. Severity: low. Likelihood:
  medium once cuprum 0.2.0 ships. Mitigation: the alignment contract test fails
  on any such partial bump.

## Progress

- [x] (2026-09-25T10:30Z) Imported the cuprum 0.2.0-beta1 users' guide and
  migration guide as `docs/cuprum-users-guide.md` and
  `docs/cuprum-v0-2-0-migration-guide.md`, with absolute upstream links, and
  indexed both in `docs/contents.md` (commit `ace4778`).
- [x] (2026-09-25T11:30Z) Stage A reconnaissance and probes complete (see
  `Surprises & discoveries` and `Artefacts and notes`).
- [x] (2026-09-25T11:45Z) Drafted this ExecPlan.
- [ ] Design review by a community of experts, and plan revision.
- [ ] Approval of this plan.
- [ ] EP-M1: red tests committed under strict expected-failure markers.
- [ ] EP-M2: beta selected on both paths; callers migrated; markers removed;
  all gates green.
- [ ] EP-M3: distribution evidence recorded; documentation, ADR, and roadmap
  updated; all gates green.

## Surprises & discoveries

- Observation: the beta is published as `0.2.0b1` with five `cp312-abi3`
  native wheels (macOS x86-64 and arm64, manylinux 2.28 x86-64 and aarch64,
  Windows amd64), a `py3-none-any` pure-Python wheel, and an sdist. It requires
  Python 3.12 or newer and has no runtime dependencies. Evidence:
  `https://pypi.org/pypi/cuprum/0.2.0b1/json`, queried 2026-09-25. Impact: both
  distributions exist, so assessment gate 1 applies to both.
- Observation: uv 0.11.19 resolves `cuprum==0.2.0b1` and
  `cuprum>=0.2.0b1,<0.3` without any pre-release flag, on both paths. A plain
  `uv lock` changed only cuprum (`Updated cuprum v0.1.0 -> v0.2.0b1`; still 62
  packages). Evidence: scratch copies of `pyproject.toml`, `uv.lock`, and the
  two scripts under `/tmp`; transcripts in `Artefacts and notes`. Impact: no
  `[tool.uv]` pre-release configuration is needed.
- Observation: uv's documentation says that with inline script metadata "the
  project's dependencies will be ignored" even inside a project, and that
  `uv lock --script` writes an adjacent `<script>.lock` which `uv run --script`
  then uses automatically (confirmed locally: "Found existing lockfile for
  script"). Evidence: <https://docs.astral.sh/uv/guides/scripts/>, retrieved
  through Firecrawl; local `uv run --script -v` probe. Impact: the standalone
  path really is independent of `uv.lock`, and a script lockfile is an
  available option (see `Decision log`, D4).
- Observation: running the existing suite against the beta through an overlay
  (`uv run --with cuprum==0.2.0b1 pytest ...`) fails exactly 13 tests, every
  one with
  `TypeError: scoped() got an unexpected keyword argument 'allowlist'`: three in
  `tests/unit/utils/test_commands.py`, three BDD scenarios from
  `tests/bdd/steps/test_commands_catalogue_steps.py`, three in
  `tests/unit/test_upload_release_wheels.py`, and four in
  `tests/e2e/test_upload_release_wheels_cli.py`. The other 49 tests in those
  files pass. Evidence: transcript in `Artefacts and notes`. Impact: the
  migration surface is known and small. The roadmap bullet names only `run_gh`,
  but the developers' guide assigns the catalogue test call sites to this task
  too.
- Observation: beta signatures are
  `scoped(config: ScopeConfig | None = None, *, catalogue: ProgramCatalogue |
  None = None)`,
  `ScopeConfig(allowlist=None, before_hooks=(), after_hooks=(),
  observe_hooks=(), timeout=None, env_overlay=None)`,
  `RunOutputOptions(capture=True, echo=False, ...)`, and
  `SafeCmd.run_sync(self, *, output=None, timeout=None, context=None,
  stdin=None)`.
  With the new forms, a stub `gh` that prints to both streams and exits 3
  yields `CommandResult` with `exit_code == 3` and both streams captured as
  `str`. Evidence: `inspect.signature` probe in `Artefacts and notes`. Impact:
  the `run_gh` change is two lines plus an import.
- Observation: `scripts/release_wheel_upload.py` (401 lines) and
  `tests/unit/test_upload_release_wheels.py` (417 lines) already exceed the
  400-line limit. Evidence: `wc -l`. Impact: new tests go in new files;
  `run_gh` must not grow. Splitting these files is out of scope; note it for a
  later refactor.
- Observation: cuprum ships no `py.typed` marker in either 0.1.0 or the beta.
  Evidence: probe of the installed packages. Impact: no change in type-checking
  posture (see `Risks`).

## Decision log

- Decision D1: pin exactly, `cuprum==0.2.0b1`, in both `pyproject.toml` and
  the script metadata. Do not use a range such as `>=0.2.0b1,<0.3`. Rationale:
  the roadmap asks for an "explicit beta selection", and the assessment (§3.4)
  proposes "a trial pin to `cuprum==0.2.0b1`". Pre-release APIs may change
  between `b1`, later betas, and `0.2.0`, and the adapter work in 5.2 is
  planned against this exact surface. An exact pin makes the two paths select
  the same artefact without a script lockfile. A range would let the standalone
  path drift to a later beta whenever it resolves, while the lockfile held the
  repository path still. Moving to 0.2.0 final is a deliberate follow-up that
  edits the same three sites. Date/Author: 2026-09-25, planning agent (pending
  review).
- Decision D2: `run_gh` uses
  `scoped(ScopeConfig(allowlist=RELEASE_CATALOGUE.allowlist))` and
  `run_sync(output=RunOutputOptions(capture=True))`. It does not use the shorter
  `scoped(catalogue=RELEASE_CATALOGUE)` or rely on default capture. Rationale:
  the roadmap names `ScopeConfig` and `RunOutputOptions`, design §7.3 documents
  that form, and the 5.2 adapter will need `ScopeConfig` for hooks and
  environment overlays, so one form serves both. Explicit `capture=True` keeps
  the existing guarantee that `gh`'s diagnostic reaches the caller, and the
  developers' guide already explains why capture is stated. Date/Author:
  2026-09-25, planning agent (pending review).
- Decision D3: migrate the catalogue tests
  (`tests/unit/utils/test_commands.py`,
  `tests/bdd/steps/test_commands_catalogue_steps.py`) in the same commit as the
  pin. Rationale: they fail under the beta, and the developers' guide says they
  "must be corrected as part of task 5.1.4, before the dependency is upgraded".
  Changing callers and pin together is the atomic change; no shim is justified.
  Date/Author: 2026-09-25, planning agent (pending review).
- Decision D4: do not add a script lockfile
  (`scripts/upload_release_wheels.py.lock`) in this task. Rationale: with D1,
  cuprum selection on the standalone path is already deterministic, and the
  alignment contract test guards drift. A script lockfile would also pin
  `cyclopts` and its transitive dependencies for the release job. That is a
  sound supply-chain improvement, but it is a new artefact with its own refresh
  workflow and is not required by 5.1.4. Record it as a follow-up candidate.
  Date/Author: 2026-09-25, planning agent (pending review).
- Decision D5: prove "no GitHub release is published by validation" by
  construction, not by omission. Every scenario runs against a recording stub
  that is first on `PATH`, strips `GH_TOKEN` and `GITHUB_TOKEN` from the child
  environment, and asserts the full recorded `gh` call list. Rationale: a real
  `gh` without credentials cannot publish, and the stub records every call, so
  a stray `release edit` or `release create` would show up. Date/Author:
  2026-09-25, planning agent (pending review).
- Decision D6: record the dependency-selection policy (exact pin on both
  paths, alignment guarded by a contract test, deliberate upgrade to final) in
  design §7 and in a new ADR, `docs/adr/006-pin-the-cuprum-beta.md`. Rationale:
  shipping a runtime dependency on a pre-release is a hard-to-reverse release
  decision with user-visible effects. The repository keeps ADRs under
  `docs/adr/NNN-*.md`; follow that convention rather than the style guide's
  `docs/adr-NNN-*.md` pattern. Date/Author: 2026-09-25, planning agent (pending
  review).

## Outcomes & retrospective

Not started. Complete at each milestone and at completion. Before setting the
status to `COMPLETE`, reconcile every discovery with the artefacts in
`Conformance basis`: update the assessment's evidence boundary and design §7,
and mark roadmap item 5.1.4 done.

## Context and orientation

The repository root contains the `lading` package, `scripts/`, `tests/`, and
`docs/`. Run all commands from the repository root. `make` targets wrap `uv`.

The files this task touches:

- `pyproject.toml` line 19 declares `"cuprum>=0.1.0"` under
  `[project] dependencies`. `requires-python = ">=3.13"`. The only `[tool.uv]`
  setting is `package = true`, so uv's default pre-release handling applies.
- `uv.lock` pins `cuprum` 0.1.0 (one `py3-none-any` wheel and an sdist). Only
  `lading` depends on it.
- `scripts/upload_release_wheels.py` is the uploader's command-line edge and
  composition root. Its first lines are:

  ```python
  #!/usr/bin/env -S uv run python
  # /// script
  # requires-python = ">=3.13"
  # dependencies = ["cuprum>=0.1.0", "cyclopts>=3"]
  # ///
  ```

  It imports its sibling `scripts/release_wheel_upload.py`, which Python finds
  because a script's own directory is first on `sys.path`.
- `scripts/release_wheel_upload.py` holds the uploader logic. It builds
  `RELEASE_CATALOGUE`, a `ProgramCatalogue` whose only programme is
  `GH = Program("gh")`. `run_gh` (around line 226) is the only place the script
  starts a process:

  ```python
  with scoped(allowlist=RELEASE_CATALOGUE.allowlist):
      command = sh.make(GH, catalogue=RELEASE_CATALOGUE)(*arguments)
      result = command.run_sync(capture=True)
  ```

- `lading/utils/commands.py` defines `LADING_CATALOGUE` (cargo, git, sccache).
  It is not yet wired into execution. Its docstring already shows the beta form
  `scoped(ScopeConfig(allowlist=...))`.
- `tests/unit/utils/test_commands.py` (lines 88-120) and
  `tests/bdd/steps/test_commands_catalogue_steps.py` (lines 93-96 and 233-237)
  call `scoped(allowlist=LADING_CATALOGUE.allowlist)`. The feature file is
  `tests/bdd/features/commands_catalogue.feature`.
- `tests/unit/test_upload_release_wheels.py` unit-tests the uploader, faking
  `gh` with cmd-mox (`cmd_mox.mock("gh").with_args(...)`); the `cmd_mox`
  fixture comes from `cmd_mox.pytest_plugin`, registered in `tests/conftest.py`.
- `tests/e2e/test_upload_release_wheels_cli.py` runs
  `[sys.executable, scripts/upload_release_wheels.py, ...]` as a subprocess
  with a recording `gh` stub first on `PATH`. Because it uses the test
  interpreter, it exercises the repository path only.
- `tests/workflow_contracts/test_release_workflow.py` parses
  `.github/workflows/release.yml` and pins the upload command to
  `uv run --script scripts/upload_release_wheels.py`.
- `.github/workflows/release.yml` runs the uploader after creating a draft
  release, then publishes with `gh release edit --draft=false`. CI runs
  `make test`, `make lint`, and `make typecheck` on Python 3.13 in
  `.github/workflows/ci.yml`.

Terms used in this plan:

- Dependency path: one route by which an execution environment selects a
  cuprum version (repository or standalone, as defined above).
- Stub `gh`: an executable file named `gh`, written by a test into a
  temporary `bin/` directory placed first on `PATH`. It records its arguments
  to a JSON file and exits with a chosen status.
- Native and pure-Python distributions: the `cp312-abi3` wheels contain an
  optional Rust extension; the `py3-none-any` wheel does not.
  `cuprum.is_rust_available()` reports which one is installed.
- Red-Green-Refactor: write a failing test first (red), make it pass with the
  smallest change (green), then tidy without changing behaviour (refactor).

Documentation and skills to consult:

- `docs/roadmap.md` §5 and item 5.1.4; `docs/lading-design.md` §7, especially
  §7.2 item 6 and §7.3; `docs/cuprum-v0-2-0-beta1-adoption-assessment.md` §3.1,
  §3.4, and §7 gate 1.
- `docs/cuprum-users-guide.md` ("Apply a policy", "Control output",
  "Choosing a stream backend", "Checking the native extension") and
  `docs/cuprum-v0-2-0-migration-guide.md` ("Catalogue-backed scoped contexts").
- `docs/cmd-mox-usage-guide.md` for the cmd-mox fixture used by the unit
  tests.
- `docs/adr/005-release-wheel-publication.md` and
  `docs/developers-guide.md` ("Release workflow" and the `LADING_CATALOGUE`
  note near line 1606).
- `docs/scripting-standards.md` for PEP 723, cyclopts, and cuprum script
  conventions.
- `docs/documentation-style-guide.md` for the ADR and prose rules.
- Skills: `execplans` (this plan), `python-router` routing to
  `python-testing` (pytest-bdd scenarios, parametrization) and `hypothesis`
  (the property test), `hexagonal-architecture` (keep the change inside the
  `run_gh` adapter), `dependency-update` (pin change), `codegraph-mcp` (callers
  of `run_gh`, `scoped`), `firecrawl-mcp` (any further uv or PyPI lookups),
  `en-gb-oxendict`, `commit-message`, and `pr-creation`.

## Conformance basis

There are no Terms of Reference or technical-design identifiers for this work.
The upstream artefacts are these, at commit `02b9335` on this branch:

- `RM-5.1.4-SEL`: roadmap 5.1.4, first bullet: explicit beta selection in
  `pyproject.toml` and `uv.lock`, with aligned inline script metadata.
- `RM-5.1.4-API`: roadmap 5.1.4, second bullet: `run_gh` uses `ScopeConfig`
  and `RunOutputOptions`.
- `RM-5.1.4-OK`: roadmap 5.1.4, success bullet: the installed beta works
  through repository and standalone execution with a stub `gh`; validation
  publishes nothing.
- `ASM-3.1` and `ASM-3.4`: assessment §3.1 (legacy calls break) and §3.4
  (explicit selection, clean-environment install).
- `ASM-G1`: assessment §7 gate 1, including independent verification of
  pure-Python and native distributions.
- `DG-CAT`: developers' guide note assigning the catalogue test call sites to
  5.1.4.
- `DES-7.2.6` and `DES-7.3`: design §7.2 item 6 (update the uploader) and
  §7.3 (beta forms).
- `ADR-005`: the uploader stays a standalone PEP 723 script whose catalogue
  allowlists `gh` alone.

Trace links:

```plaintext
RM-5.1.4-SEL, ASM-3.4 -> EP-M2 -> tests/workflow_contracts/test_cuprum_selection.py (alignment)
RM-5.1.4-API, ASM-3.1, DES-7.2.6 -> EP-M2 -> tests/unit/test_upload_release_wheels.py (existing run_gh tests)
DG-CAT, DES-7.3 -> EP-M2 -> tests/unit/utils/test_commands.py, commands_catalogue.feature
RM-5.1.4-OK, ASM-G1 -> EP-M1/EP-M2 -> tests/bdd/features/release_wheel_upload.feature (both paths)
ASM-G1 (distributions) -> EP-M3 -> recorded pure-Python and native smoke transcripts
RM-5.1.4-API -> EP-M2 -> tests/unit/test_release_gh_adapter_properties.py (capture fidelity)
ADR-005 -> all milestones -> tests/workflow_contracts/test_release_workflow.py (unchanged, still green)
```

## Verification plan

This change introduces no new business logic. It introduces one configuration
invariant and relies on one adapter contract. The adapter contract is the
uploader's dependence on the beta's capture behaviour.

Obligation O1, selection alignment: the cuprum requirement in `pyproject.toml`,
the cuprum requirement in the PEP 723 block of
`scripts/upload_release_wheels.py`, and the `version` of the `cuprum` package in
`uv.lock` all select the same exact version, `0.2.0b1`.

- Method: parameterized contract test over the three sources. The domain is
  finite (three declarations), so enumeration is exhaustive.
- Artefact: `tests/workflow_contracts/test_cuprum_selection.py`. It reads
  `pyproject.toml` and `uv.lock` with `tomllib`, and extracts the PEP 723 block
  using the regular expression from the PEP 723 reference implementation, then
  parses its TOML. It compares whitespace-normalized requirement strings
  against the single expected string `cuprum==0.2.0b1`. `packaging` is only a
  transitive dependency here, so the test does not import it.
- Evidence: in EP-M1 the test fails because `pyproject.toml` still says
  `>=0.1.0`. In EP-M2 it passes.
- Non-vacuity: a helper unit test in the same file feeds the extractor a
  script whose metadata lists `cuprum==0.1.0` and asserts a mismatch is
  reported. A second feeds a script with no metadata block and asserts a clear
  failure rather than a silent pass. Both show the check can fail.

Obligation O2, both paths run the beta end to end: for each execution mode
(repository interpreter; `uv run --script`), the uploader discovers a wheel,
calls the stub `gh` exactly once with `release upload <tag> <wheel> --clobber`,
exits 0, and the environment it ran in has cuprum `0.2.0b1`.

- Method: pytest-bdd scenario outline over the two modes, plus a version
  check per mode: `importlib.metadata.version("cuprum")` in the repository
  interpreter, and
  `uv tree --script scripts/upload_release_wheels.py --depth 1` for the
  standalone mode.
- Artefact: `tests/bdd/features/release_wheel_upload.feature` and
  `tests/bdd/steps/test_release_wheel_upload_steps.py`.
- Evidence: in EP-M1, under `xfail(strict=True)`, the scenarios fail. The
  repository mode fails on the version assertion (`0.1.0`). The standalone mode
  fails on the version assertion because the metadata still resolves 0.1.0. In
  EP-M2 the markers are removed and both pass.
- Non-vacuity: the stub's record must exist and contain exactly one call, so
  a run that never reaches `gh` fails. The version assertion names the exact
  string, so 0.1.0 fails. The Stage A overlay probe showed that the current
  `run_gh` raises `TypeError` under the beta, so these scenarios would have
  caught a pin-only change. Re-confirm that in EP-M2 by temporarily reverting
  `run_gh` and observing the failure; do not commit the revert.

Obligation O3, no publication: during validation, every `gh` invocation reaches
the stub, and no child process holds `GH_TOKEN` or `GITHUB_TOKEN`.

- Method: the stub records its argv list and whether either token variable
  was present. The scenario asserts the recorded calls are exactly the one
  upload and that no token was visible.
- Artefact: the same feature file, scenario "Validation never publishes".
- Non-vacuity: a step-level self-check sets a dummy `GH_TOKEN` in the parent
  and asserts the child did not see it. That proves the scrubbing, not merely
  the absence of a token. Asserting the complete call list proves the stub was
  reached.

Obligation O4, capture fidelity of the migrated adapter: for any exit status in
0-255 and any UTF-8 text without carriage returns written to stdout and stderr
by `gh`, `run_gh` returns a `CommandOutcome` whose fields equal what `gh`
produced.

- Method: Hypothesis property test driving a real stub `gh` process through
  the real `run_gh`, with `max_examples=25` to bound process spawning. This
  checks repository-owned mapping against the real beta interface rather than
  cuprum's internals.
- Domain: `st.integers(0, 255)` for status. Text uses
  `st.text(st.characters(codec="utf-8", exclude_categories=("Cs",),
  exclude_characters="\r"), max_size=200)`
  for each stream. Include explicit `@example`s for empty streams, status 0,
  status 255, and non-ASCII text.
- Artefact: `tests/unit/test_release_gh_adapter_properties.py`.
- Evidence: passes in EP-M2. Record Hypothesis statistics
  (`--hypothesis-show-statistics`) to show that non-empty streams and non-zero
  statuses were both generated.
- Non-vacuity: seeded mutations, run locally and not committed. First,
  change `RunOutputOptions(capture=True)` to `capture=False`; the property must
  fail because both streams come back empty. Second, swap `stdout` and `stderr`
  in the `CommandOutcome` construction; the property must fail. Record both
  failures in `Artefacts and notes`.

Obligation O5, catalogue behaviour is unchanged under the beta: the existing
catalogue unit tests and the `commands_catalogue.feature` scenarios pass using
`scoped(ScopeConfig(allowlist=...))`, including rejection of an unregistered
programme with `UnknownProgramError`.

- Method: the existing named tests and scenarios. They are finite, named
  contract examples.
- Evidence: they fail under the beta overlay today (Stage A) and pass in
  EP-M2.

Axioms (trusted, not verified here):

- A1: uv resolves an explicit `==` pre-release pin without extra
  configuration, and `uv run --script` honours PEP 723 metadata while ignoring
  the project (uv documentation; locally confirmed with uv 0.11.19).
- A2: cuprum 0.2.0b1 behaves as its users' guide describes: capture is
  available through `RunOutputOptions`, `scoped` takes a `ScopeConfig`, and a
  catalogued programme is found on `PATH`.
- A3: PyPI serves the published artefacts with the hashes recorded in
  `uv.lock`.
- A4: `gh` without credentials cannot publish a release. This is defence in
  depth; the stub is the primary guarantee.

Rust, Kani, and Verus are not applicable: the repository has no Rust extension,
and the change has no arithmetic, memory, or protocol logic that a proof would
strengthen. CrossHair is not used because the only repository-owned logic, the
result mapping, is exercised against the real interface by O4. A symbolic run
over a mocked `CommandResult` would test the mock, not the contract.

No syrupy snapshot is added. The uploader's output format does not change, and
the existing end-to-end tests already assert exact outcome lines. A snapshot of
three version strings would duplicate O1's semantic assertion.

## Plan of work

Stage A (complete): reconnaissance and probes, recorded above. No code changes.

Stage B (EP-M1), red. Add the new tests, each marked
`@pytest.mark.xfail(strict=True, reason="5.1.4: cuprum beta not yet selected")`:

1. `tests/workflow_contracts/test_cuprum_selection.py` (O1), with its
   non-vacuity helper tests unmarked, because they test the checker and pass
   now.
2. `tests/bdd/features/release_wheel_upload.feature` and
   `tests/bdd/steps/test_release_wheel_upload_steps.py` (O2, O3). The step
   module reuses the stub pattern from
   `tests/e2e/test_upload_release_wheels_cli.py`: a stub `gh` written with
   `#!{sys.executable}`, recording argv and token presence to JSON. Because
   `pytest-bdd` generates the test functions, apply the marker by wrapping
   `scenarios(...)` in a module-level `pytestmark` list containing the `xfail`
   marker. Scenarios that already pass (the token self-check) sit in a second
   feature file, or are asserted as unmarked unit tests in the steps module. Do
   not mark them strict-xfail.
3. `tests/unit/test_release_gh_adapter_properties.py` (O4). Under 0.1.0,
   `from cuprum import RunOutputOptions` fails at collection, so guard the
   import to keep the red commit collectable: import inside the test body and
   mark the test strict-xfail. Remove the guard in EP-M2.

Run the focused red command (see `Concrete steps`) and confirm every marked
test is reported `XFAIL` for the stated reason, then run all four gates. Commit.

Stage C (EP-M2), green. In one commit:

1. `pyproject.toml`: `"cuprum>=0.1.0"` becomes `"cuprum==0.2.0b1"`.
2. `uv lock`, then confirm `git diff uv.lock` changes only the `cuprum`
   package block and the `lading` `requires-dist` specifier.
3. `scripts/upload_release_wheels.py` line 4:
   `# dependencies = ["cuprum==0.2.0b1", "cyclopts>=3"]`.
4. `scripts/release_wheel_upload.py`: import `RunOutputOptions` and
   `ScopeConfig` from `cuprum`, and change `run_gh` to:

   ```python
   with scoped(ScopeConfig(allowlist=RELEASE_CATALOGUE.allowlist)):
       # Capture is cuprum's default, but it is the point of this call:
       # without it a failed upload reports no reason.
       command = sh.make(GH, catalogue=RELEASE_CATALOGUE)(*arguments)
       result = command.run_sync(output=RunOutputOptions(capture=True))
   ```

   Shorten the existing three-line comment so the file does not grow past 401
   lines.
5. `tests/unit/utils/test_commands.py` and
   `tests/bdd/steps/test_commands_catalogue_steps.py`: replace
   `scoped(allowlist=LADING_CATALOGUE.allowlist)` with
   `scoped(ScopeConfig(allowlist=LADING_CATALOGUE.allowlist))` and import
   `ScopeConfig` beside `scoped`.
6. Remove every `xfail` marker added in EP-M1, and the import guard in the
   property test.
7. `uv sync` so the working environment matches the lock, then run the
   focused green command, the seeded mutations for O2 and O4 (reverting each
   afterwards), and all four gates.

Stage D (EP-M3), evidence and documentation:

1. Run the distribution smoke checks in `Concrete steps` for the native wheel
   and the pure-Python wheel, and paste the transcripts into
   `Artefacts and notes`.
2. Write `docs/adr/006-pin-the-cuprum-beta.md` (D1, D4, D6) and link it from
   `docs/contents.md` and design §7.
3. Update `docs/lading-design.md` §7: in §7.2 item 6, record that the uploader
   now uses the beta forms; in §7.3, state the selected version and the
   alignment contract; and add a short "Implementation notes (Step 5.1.4)".
4. Update `docs/developers-guide.md`: replace the paragraph that says the
   catalogue call sites "must be corrected as part of task 5.1.4" with the
   current state. Revise the release-workflow paragraph that says the `gh` call
   "states `capture=True`" to name `RunOutputOptions(capture=True)`. Add a
   short "Changing the cuprum version" note that lists the three sites, the
   `uv lock` step, and the contract test that enforces them.
5. Update `docs/users-guide.md` "Installation": lading depends on the cuprum
   0.2.0 beta; pip installs it automatically because the pin names the
   pre-release; environments that pin another cuprum will conflict.
6. Append a dated follow-up paragraph to the assessment's §1.1 evidence
   boundary saying that the published `0.2.0b1` artefact has been selected and
   validated for both dependency paths (gate 1), with a pointer to this plan.
   Leave the original snapshot text intact.
7. Mark roadmap item 5.1.4 done (`- [x]`) with a one-line evidence note.
8. Run `make fmt`, then `make markdownlint`, `make nixie`, and the four code
   gates. Commit.

Each stage ends with its validation. Do not start the next stage while any gate
is red.

## Milestones and plateaus

EP-M1, red specification committed.

- Outcome: the new tests exist and are strict-xfail; the repository still
  selects cuprum 0.1.0 and every gate is green.
- Requirements: specifies `RM-5.1.4-SEL`, `RM-5.1.4-OK`, and `ASM-G1`
  (paths).
- Acceptance: the red command reports each new marked test as `XFAIL`; gates
  green.
- Conformance check: no production file changed; no new dependency.
- Recovery: revert the single commit.
- Remaining gaps: selection, migration, documentation.
- Compatibility decision: none.

EP-M2, beta selected and callers migrated.

- Outcome: both paths select `cuprum==0.2.0b1`; `run_gh` and the catalogue
  tests use the beta forms; all new tests pass unmarked.
- Requirements: discharges `RM-5.1.4-SEL`, `RM-5.1.4-API`, `DG-CAT`,
  `DES-7.2.6`, and `RM-5.1.4-OK`, and obligations O1-O5.
- Acceptance: the green command passes; the seeded mutations fail as
  predicted; `uv tree --script ... | grep cuprum` prints `cuprum v0.2.0b1`; all
  four gates are green.
- Conformance check: `run_gh`'s signature, `CommandOutcome`, `UploadRunner`,
  the allowlist, and the workflow command are unchanged. No file under
  `lading/runtime/` or `lading/testing/` changed. No compatibility shim.
  `uv.lock` changed only for cuprum.
- Recovery: revert the commit. The repository returns to the EP-M1 plateau,
  because the xfail markers come back with the revert.
- Remaining gaps: distribution evidence and documentation.
- Compatibility decision: none. The flat forms are private call sites and
  test code, updated atomically with the pin.

EP-M3, evidence and documentation.

- Outcome: native and pure-Python smoke transcripts are recorded; design,
  ADR-006, developers' guide, users' guide, assessment, and roadmap reflect the
  new state.
- Requirements: discharges `ASM-G1` (distributions) and closes 5.1.4.
- Acceptance: Markdown gates and code gates green; roadmap 5.1.4 checked.
- Conformance check: the documentation states the pin policy exactly as
  implemented; no claim beyond gate 1 (the runner migration remains open).
- Recovery: documentation-only commit; revert freely.
- Remaining gaps: roadmap 5.1.5 onward; the script lockfile follow-up (D4).
- Compatibility decision: none.

## Concrete steps

Run everything from the repository root. Capture gate output with `tee` using
the template `/tmp/$ACTION-lading-$(git branch --show-current).out`. Run gates
one at a time, never in parallel.

Red (EP-M1):

```bash
uv run pytest -q tests/workflow_contracts/test_cuprum_selection.py \
  tests/bdd/steps/test_release_wheel_upload_steps.py \
  tests/unit/test_release_gh_adapter_properties.py \
  | tee /tmp/red-lading-$(git branch --show-current).out
```

Expected: the checker's own helper tests pass, and every marked test is
reported as `x` (xfailed). No test is reported `XPASS`; strict mode would make
that a failure.

Green (EP-M2):

```bash
uv lock
git diff --stat uv.lock          # expect a small diff limited to cuprum
uv sync
uv run python -c "import importlib.metadata as m; print(m.version('cuprum'))"
# 0.2.0b1
uv tree --script scripts/upload_release_wheels.py --depth 1 | grep cuprum
# cuprum v0.2.0b1
uv run pytest -q tests/workflow_contracts/test_cuprum_selection.py \
  tests/bdd/steps/test_release_wheel_upload_steps.py \
  tests/unit/test_release_gh_adapter_properties.py \
  tests/unit/utils/test_commands.py \
  tests/bdd/steps/test_commands_catalogue_steps.py \
  tests/unit/test_upload_release_wheels.py \
  tests/e2e/test_upload_release_wheels_cli.py \
  --hypothesis-show-statistics \
  | tee /tmp/green-lading-$(git branch --show-current).out
```

Expected: all pass, with no `xfail` or `xpass` entries.

Gates, sequentially, after each milestone:

```bash
make check-fmt | tee /tmp/check-fmt-lading-$(git branch --show-current).out
make typecheck | tee /tmp/typecheck-lading-$(git branch --show-current).out
make lint      | tee /tmp/lint-lading-$(git branch --show-current).out
make test      | tee /tmp/test-lading-$(git branch --show-current).out
make markdownlint | tee /tmp/markdownlint-lading-$(git branch --show-current).out
make nixie     | tee /tmp/nixie-lading-$(git branch --show-current).out
```

Prefer delegating the gate run to the `scrutineer` agent, which runs these
sequentially and reports log paths.

Distribution smoke (EP-M3). The stub directory can be any scratch directory
under `/tmp`; it holds only the stub. Each command runs `run_gh` against a stub
`gh` that prints to both streams and exits 3:

```bash
STUB=$(mktemp -d); printf '#!/bin/sh\necho "stub $*"; echo diag >&2; exit 3\n' > "$STUB/gh"
chmod +x "$STUB/gh"
SMOKE='import sys, cuprum; sys.path.insert(0, "scripts"); import release_wheel_upload as r
print(cuprum.is_rust_available(), r.run_gh(["release", "view"]))'

# Native wheel (default selection on manylinux x86-64):
env -u GH_TOKEN -u GITHUB_TOKEN PATH="$STUB:$PATH" \
  uv run --no-project --with cuprum==0.2.0b1 python -c "$SMOKE"
# True CommandOutcome(exit_code=3, stdout='stub release view\n', stderr='diag\n')

# Pure-Python wheel, selected by URL (copy the URL and hash from uv.lock):
env -u GH_TOKEN -u GITHUB_TOKEN PATH="$STUB:$PATH" \
  uv run --no-project --with "cuprum @ <py3-none-any wheel URL from uv.lock>" python -c "$SMOKE"
# False CommandOutcome(exit_code=3, stdout='stub release view\n', stderr='diag\n')
```

## Validation and acceptance

Acceptance is behavioural:

- `make test` passes. The new scenario "The uploader attaches a wheel through
  the cuprum beta" passes in both modes, and the new tests fail before EP-M2
  (recorded as strict `XFAIL` in EP-M1) and pass after.
- `uv tree --script scripts/upload_release_wheels.py --depth 1` lists
  `cuprum v0.2.0b1`;
  `uv run python -c "import importlib.metadata as m;
  print(m.version('cuprum'))"`
  prints `0.2.0b1`.
- The distribution smoke prints `True ...` for the native wheel and
  `False ...` for the pure-Python wheel, each with `exit_code=3` and both
  streams captured.
- No test or smoke step invokes a real `gh`. The stub records show every call,
  and the token self-check passes.

The BDD specification that drives EP-M1 and EP-M2, in
`tests/bdd/features/release_wheel_upload.feature`:

```gherkin
Feature: Release wheel upload through the cuprum beta
  The release uploader runs in two environments: the repository environment
  locked by uv.lock, and the standalone environment built by
  `uv run --script` from the script's inline metadata. Both must select the
  cuprum 0.2.0 beta and reach gh only through the recording stub.

  Scenario Outline: The uploader attaches a wheel through the cuprum beta
    Given a dist directory containing "lading-1.2.3-py3-none-any.whl"
    And a recording gh stub that exits 0
    When the uploader runs in <mode> mode for tag "v1.2.3"
    Then the uploader exits 0
    And gh was called exactly once with "release upload v1.2.3" and the wheel and "--clobber"
    And the <mode> environment resolves cuprum "0.2.0b1"

    Examples:
      | mode       |
      | repository |
      | standalone |

  Scenario Outline: A rejected upload reports gh's diagnostic
    Given a dist directory containing "lading-1.2.3-py3-none-any.whl"
    And a recording gh stub that writes "HTTP 422: asset exists" to stderr and exits 1
    When the uploader runs in <mode> mode for tag "v1.2.3"
    Then the uploader exits 1
    And the uploader's error line contains "HTTP 422: asset exists"

    Examples:
      | mode       |
      | repository |
      | standalone |

  Scenario: Validation never publishes
    Given a dist directory containing "lading-1.2.3-py3-none-any.whl"
    And a recording gh stub that exits 0
    And the parent environment holds a GH_TOKEN
    When the uploader runs in standalone mode for tag "v1.2.3"
    Then gh saw no GH_TOKEN or GITHUB_TOKEN
    And no recorded gh call is "release create" or "release edit"
```

Quality criteria:

- Tests: `make test` green; the targeted green command green with no xfail
  entries.
- Verification: O1-O5 discharged as described, with the O2 and O4 seeded
  mutations observed failing and then reverted.
- Lint, type check, format: `make lint`, `make typecheck`, `make check-fmt`
  green; `make markdownlint` and `make nixie` green after documentation.
- Security: no credentials reach any child process in validation; the
  catalogue still allowlists only `gh`.

## Idempotence and recovery

Every step is repeatable. `uv lock` and `uv sync` are idempotent for a fixed
`pyproject.toml`. The stub directories live under pytest's `tmp_path` or a
`mktemp -d` scratch directory. Seeded mutations are temporary edits; restore
with `git checkout -- scripts/release_wheel_upload.py` and confirm `git status`
is clean before committing. If the standalone scenario fails with a network
error, rerun once; if it fails again, stop under the network tolerance. To
abandon the work, revert the milestone commits in reverse order; each milestone
is a coherent plateau.

## Artefacts and notes

Published files (PyPI JSON API, 2026-09-25):

```plaintext
cuprum-0.2.0b1-cp312-abi3-macosx_10_12_x86_64.whl   >=3.12
cuprum-0.2.0b1-cp312-abi3-macosx_11_0_arm64.whl     >=3.12
cuprum-0.2.0b1-cp312-abi3-manylinux_2_28_aarch64.whl >=3.12
cuprum-0.2.0b1-cp312-abi3-manylinux_2_28_x86_64.whl >=3.12
cuprum-0.2.0b1-cp312-abi3-win_amd64.whl             >=3.12
cuprum-0.2.0b1-py3-none-any.whl                     >=3.12
cuprum-0.2.0b1.tar.gz                               >=3.12
```

Scratch lock probe (copy of `pyproject.toml` and `uv.lock` with
`"cuprum==0.2.0b1"`):

```plaintext
$ uv lock
Resolved 62 packages in 557ms
Updated cuprum v0.1.0 -> v0.2.0b1
```

Scratch standalone probe (copy of both scripts with the inline pin changed):

```plaintext
$ uv tree --script upload_release_wheels.py --depth 1 | tail -1
cuprum v0.2.0b1
$ uv run --script -v upload_release_wheels.py --help   # after uv lock --script
DEBUG Found existing lockfile for script
```

Current `run_gh` under the beta, and the beta forms, against a stub `gh` that
prints `gh stub $*` to stdout and `warn` to stderr and exits 3:

```plaintext
TypeError scoped() got an unexpected keyword argument 'allowlist'
scoped(config: ScopeConfig | None = None, *, catalogue: ProgramCatalogue | None = None)
SafeCmd.run_sync(self, *, output=None, timeout=None, context=None, stdin=None)
new forms -> 3 'gh stub release view\n' 'warn\n' CommandResult
```

Existing suite against the beta overlay
(`uv run --with cuprum==0.2.0b1 pytest -q tests/unit/utils/test_commands.py
tests/bdd/steps/test_commands_catalogue_steps.py
tests/unit/test_upload_release_wheels.py
tests/e2e/test_upload_release_wheels_cli.py`):

```plaintext
FAILED tests/unit/utils/test_commands.py::TestScopedContext::test_catalogue_can_be_used_in_scoped_context
FAILED tests/unit/utils/test_commands.py::TestScopedContext::test_scoped_context_allows_command_construction
FAILED tests/unit/utils/test_commands.py::TestScopedContext::test_scoped_context_rejects_unregistered_program
FAILED tests/bdd/steps/test_commands_catalogue_steps.py::test_constructing_a_cargo_command_within_the_catalogue_scope
FAILED tests/bdd/steps/test_commands_catalogue_steps.py::test_constructing_a_git_command_within_the_catalogue_scope
FAILED tests/bdd/steps/test_commands_catalogue_steps.py::test_rejecting_an_unregistered_program
FAILED tests/unit/test_upload_release_wheels.py::test_upload_invokes_gh_with_the_tag_and_wheels
FAILED tests/unit/test_upload_release_wheels.py::test_failed_upload_raises
FAILED tests/unit/test_upload_release_wheels.py::test_run_gh_returns_the_diagnostic_it_captured
FAILED tests/e2e/test_upload_release_wheels_cli.py::test_every_wheel_reaches_gh_with_the_tag_from_the_environment
FAILED tests/e2e/test_upload_release_wheels_cli.py::test_a_failing_gh_fails_the_step
FAILED tests/e2e/test_upload_release_wheels_cli.py::test_a_successful_step_reports_its_outcome
FAILED tests/e2e/test_upload_release_wheels_cli.py::test_each_failure_reports_its_own_outcome[rejected-upload-failed]
13 failed, 49 passed
```

## Interfaces and dependencies

Dependency after EP-M2, identical on both paths:

```toml
# pyproject.toml, [project] dependencies
"cuprum==0.2.0b1",
```

```python
# scripts/upload_release_wheels.py, PEP 723 block
# dependencies = ["cuprum==0.2.0b1", "cyclopts>=3"]
```

Adapter after EP-M2, in `scripts/release_wheel_upload.py` (signature unchanged):

```python
from cuprum import (
    Program,
    ProgramCatalogue,
    ProjectSettings,
    RunOutputOptions,
    ScopeConfig,
    scoped,
    sh,
)

def run_gh(arguments: cabc.Sequence[str]) -> CommandOutcome: ...
```

If the multi-line import pushes the file past 401 lines, keep the import on one
line where `ruff format` allows it, or trim the `run_gh` comment. Do not split
the module in this task.

New test modules:

- `tests/workflow_contracts/test_cuprum_selection.py`: the selection contract
  (O1).
- `tests/bdd/features/release_wheel_upload.feature` and
  `tests/bdd/steps/test_release_wheel_upload_steps.py`: both-path behaviour and
  no-publication (O2, O3).
- `tests/unit/test_release_gh_adapter_properties.py`: capture fidelity (O4).

No new runtime or development dependency. `hypothesis`, `pytest-bdd`,
`cmd-mox`, and `pyyaml` are already development dependencies.

## Revision note

2026-09-25: initial draft from Wyvern reconnaissance, PyPI and uv documentation
research through Firecrawl, and local probes. Pending the community-of-experts
design review.
