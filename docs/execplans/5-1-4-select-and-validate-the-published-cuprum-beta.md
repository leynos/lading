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
on `cuprum`, a library that runs external programmes only when they are listed
in an allowlist called a catalogue. The repository locks cuprum 0.1.0 today.
Phase 5 of the roadmap will replace lading's own `subprocess` and `plumbum`
execution with a single cuprum adapter. That work is planned against the cuprum
0.2.0 API, which exists only in the published beta, `cuprum==0.2.0b1` (uploaded
to PyPI on 2026-09-25).

Cuprum reaches lading along two independent dependency paths:

1. The repository path: `pyproject.toml` declares the dependency and `uv.lock`
   pins it. Every test, the `lading` package, and its doctests run here.
2. The standalone path: the release workflow runs
   `uv run --script scripts/upload_release_wheels.py`. That command reads the
   PEP 723 inline metadata block at the top of the script (a `# /// script`
   comment block that lists the script's own dependencies) and ignores both
   `pyproject.toml` and `uv.lock`. If a lockfile named
   `scripts/upload_release_wheels.py.lock` sits beside the script, uv uses that
   instead of resolving afresh.

The beta removed two keyword forms that lading still uses:
`scoped(allowlist=...)` and `run_sync(capture=...)`. Both now raise
`TypeError`. The release uploader's `run_gh` uses both, so upgrading the
dependency alone would break the release job on the next tag push.

After this change:

- Both paths select exactly `cuprum==0.2.0b1`, and each is locked by hash.
- The uploader's cuprum boundary sits in one small module,
  `scripts/release_gh.py`. It uses the beta's catalogue-backed scope,
  `scoped(catalogue=...)`, and `RunOutputOptions`.
- The catalogue tests use the same catalogue-backed scope.
- The suite shows that the installed beta works through both paths with a stub
  `gh`, and no validation step can publish a GitHub release.

Success is observable by running:

```bash
make test
uv tree --script scripts/upload_release_wheels.py --depth 1 | grep cuprum
```

The suite passes. It includes a behavioural scenario that runs the uploader
standalone against a recording `gh` stub and checks the cuprum version in the
environment that actually ran. The second command prints `cuprum v0.2.0b1`.

This task does not replace the production runner, migrate cmd-mox passthrough,
or remove `subprocess` from tests. Those are roadmap items 5.1.5, 5.2, and 5.3.

## Constraints

- Change only these things:
  - the dependency selection;
  - the uploader's cuprum adapter;
  - the catalogue call sites that the beta breaks;
  - their tests and test support;
  - two configuration lines: the mutmut `also_copy` list and the typos local
    overlay;
  - documentation.

  Do not modify `lading/runtime/` (the `CommandRunner` protocol,
  `subprocess_runner.py`, `stream_relay.py`),
  `lading/testing/cmd_mox_runner.py`, or any production command path in the
  `lading` package. Those belong to roadmap items 5.1.5 and 5.2.
- Keep the uploader's behaviour stable. The `UploadRunner` protocol and the
  signature `run_gh(arguments: Sequence[str]) -> CommandOutcome` must not
  change. Neither must the `CommandOutcome` fields (`exit_code`, `stdout`,
  `stderr`), the `Outcome` values, or the observable output of
  `discover_wheels`, `upload_wheels`, `attach_wheels`, and `report_outcome`.
  `run_gh`, `CommandOutcome`, `GH`, and `RELEASE_CATALOGUE` may move to a new
  module (EP-M0). If they do, every importer is updated, and no alias is left
  behind.
- Keep the release workflow invocation exactly
  `uv run --script scripts/upload_release_wheels.py --directory dist`. The
  contract test `tests/workflow_contracts/test_release_workflow.py` pins it.
- Keep the `gh` catalogue allowlist to `gh` alone (ADR-005).
- Keep `gh` output captured. The developers' guide records three tests that
  depend on `gh`'s stderr reaching the caller.
- No validation step may reach a real `gh` or a real GitHub account. Every test
  that runs the uploader in a child process must use the shared stub helper (see
  `Interfaces and dependencies`). That helper:
  - puts the stub first on `PATH` and checks this with `shutil.which`;
  - removes `GH_TOKEN` and `GITHUB_TOKEN`;
  - points `GH_CONFIG_DIR` at an empty directory, so a stored `gh` login cannot
    be used;
  - sets `GH_HOST=stub.invalid` and `GH_PROMPT_DISABLED=1`;
  - uses the tag `v0.0.0-stub`, which cannot exist.
- Use the shared default uv and Cargo caches. Do not create an isolated cache,
  and do not use `/tmp` as a build target.
- Do not grow any file that already exceeds the 400-line limit in `AGENTS.md`
  (`scripts/release_wheel_upload.py`, 401 lines;
  `tests/unit/test_upload_release_wheels.py`, 417 lines). EP-M0 shrinks both.
- Do not introduce compatibility machinery. That means no wrapper that accepts
  both the 0.1.0 and 0.2.0 call forms, no conditional import, and no re-export
  alias after the EP-M0 move. Every caller moves to the beta form in the same
  commit as the pin.
- Prose and comments use en-GB-oxendict spelling (`-ize`, `-yse`, `-our`).

If meeting the objective would violate a constraint, stop, record the conflict
in `Decision log`, and escalate.

## Tolerances (exception triggers)

- Scope: stop and escalate if the work needs more than 24 files or more than
  900 net lines changed. `uv.lock`, the script lockfile, and this plan do not
  count.
- API surprise: stop if the beta breaks any cuprum usage beyond the flat
  `scoped(allowlist=...)` and `run_sync(capture=...)` forms. Examples would be
  `Program`, `ProjectSettings`, `ProgramCatalogue(projects=...)`, `sh.make`,
  `SafeCmd.argv`, or `UnknownProgramError`. The Stage A probe found no such
  break.
- Test surprise: after the pin, stop if any test fails that is not one of the
  13 recorded in `Surprises & discoveries` or a new test planned here, or if a
  failure has any cause other than the flat keyword forms.
- Resolution: stop if uv needs any of the following to select the beta on
  either path:
  - a `--prerelease` flag;
  - a `[tool.uv]` `prerelease` setting;
  - an `exclude-newer` change.

  The Stage A probe resolved `cuprum==0.2.0b1` on both paths without any of
  them.
- Network: stop if the standalone scenario or the lock-freshness test cannot
  reach the package index in CI, and the only remedy is to skip the test.
- Interface: stop if any constraint above would need a signature change.
- Iterations: stop if a gate still fails after three fix attempts.
- Ambiguity: stop and present options if the beta is yanked, or superseded by
  a later pre-release, before merge.

## Risks

- **The standalone scenario and lock-freshness test need the package index or
  a warm uv cache.**
  - Severity: medium. Likelihood: low.
  - Measured cost is small. `uv run --script` took 0.85 s with a cold cache and
    0.25 s with a warm one, and a warm run succeeded with the network blocked.
  - Mitigation:
    - The script lockfile removes resolution drift.
    - The tests set a 60-second module timeout
      (`pytestmark = pytest.mark.timeout(60)`, following
      `tests/integration/test_lockfile_discovery.py:23`) and a 45-second
      subprocess timeout.
    - On failure they report uv's stderr, so a network problem is not an opaque
      pytest-timeout traceback.
    - Do not skip on failure: a skip would hide a broken standalone path.
- **`cuprum==0.2.0b1` is yanked, or replaced by `0.2.0b2` or `0.2.0`, before
  this lands.**
  - Severity: medium. Likelihood: low.
  - Mitigation:
    - Exact pins and lock hashes keep selection deterministic, and installers
      still honour an exact `==` pin to a yanked version.
    - A move to a later version is a deliberate, manual edit (D9). The selection
      contract test forces every site to move together.
- **A lading build from this branch onwards requires a cuprum pre-release.**
  - Severity: low. Likelihood: low.
  - A scratch-wheel probe confirmed that pip, `uv pip`, `uv run --with`, and
    `uv tool install` all accept a transitive `cuprum==0.2.0b1` with no flags.
  - A user environment that also requires `cuprum<0.2` fails to resolve.
  - Mitigation: D8. No final lading 0.x release is cut until cuprum 0.2.0
    final ships and the pin moves to it, so no final lading wheel ever records
    the beta requirement. The users' guide installation note covers source and
    development installs.
- **The native wheel (`manylinux_2_28`) needs glibc 2.28 or newer.**
  - Severity: low. Likelihood: low.
  - On older glibc or on musl, installers fall back to the pure-Python wheel.
    CI covers only Python 3.13 on ubuntu x86-64, so it always tests the native
    wheel.
  - Mitigation:
    - Validate the pure-Python wheel separately (EP-M3).
    - Lading never builds cuprum pipelines, so the Rust stream backend is never
      on its execution path. Capture always uses the Python pathway.
- **The beta ships no `py.typed` marker.**
  - Severity: low. Likelihood: low.
  - Mitigation: cuprum 0.1.0 had no marker either, and `make typecheck` already
    passes. Run `make typecheck` in EP-M2, and escalate on any new diagnostic
    rather than adding `# type: ignore`.
- **Dependabot proposes a cuprum bump that edits only `pyproject.toml` and
  `uv.lock`.**
  - Severity: low. Likelihood: high while the pin is a pre-release.
  - Dependabot runs the `uv` ecosystem daily with a seven-day cooldown, and
    `dependabot-automerge.yml` enables auto-merge.
  - Mitigation: the selection contract test fails on any partial bump, so
    auto-merge cannot land it. The developers' guide tells maintainers to close
    such pull requests and bump by hand (D9).
- **The release job's uv version floats.** `setup-uv` in `release.yml` sets no
  `version:`.
  - Severity: low. Likelihood: low.
  - Mitigation: uv reads older lockfile schemas, and the lockfile schema changes
    only in minor releases. Pinning uv in the release job is out of scope here;
    record it as a follow-up.

## Progress

- [x] (2026-09-25T10:30Z) Imported the cuprum 0.2.0-beta1 users' guide and
  migration guide as `docs/cuprum-users-guide.md` and
  `docs/cuprum-v0-2-0-migration-guide.md`, with absolute upstream links, and
  indexed both in `docs/contents.md` (commit `ace4778`).
- [x] (2026-09-25T11:30Z) Stage A reconnaissance and probes complete (see
  `Surprises & discoveries` and `Artefacts and notes`).
- [x] (2026-09-25T11:45Z) Drafted this ExecPlan (commit `d613a80`).
- [x] (2026-09-25T12:10Z) Community-of-experts design review completed. There
  were three panels covering six lenses; their verdicts were Revise, Proceed
  after revision, and Approve with changes.
- [x] (2026-09-25T12:40Z) Revised the plan to address every blocking finding
  (see `Decision log`, "Design review dispositions").
- [x] (2026-09-25T13:30Z) Maintainer decisions received. D2: adopt
  `scoped(catalogue=...)`. D4: script lockfile approved. D8: no final lading
  0.x release until cuprum 0.2.0 final ships. The maintainer also asked for an
  issue mandating the cuprum release process (leynos/cuprum#488); it is raised
  as #286. The plan is updated to match.
- [x] (2026-09-25T14:00Z) Go-ahead to implement received.
- [x] EP-M0: uploader's cuprum boundary extracted to `scripts/release_gh.py`;
  `check-fmt` and `typecheck` green, and the extracted surface ruff-clean
  (`make lint` red only on the then-untracked in-flight EP-M1 helper, since
  fixed; `make test` not yet re-run on a quiesced tree).
- [x] (2026-09-25) EP-M1: hardened stub helper, characterization tests, and red
  tests committed. The focused red command reports exactly the three expected
  `XFAIL` entries and no `XPASS` (48 passed, 3 xfailed).
- [x] (2026-09-25) EP-M1 gate pass. First run was red on two real defects, both
  now fixed: a bare `assert` (df12 `C9102`) in `test_cuprum_selection.py`, and a
  `GhStub.calls` doctest whose `+SKIP` covered only the binding line, leaving
  the next line to run with `stub` unbound. The doctest now runs for real
  instead of being skipped. Investigating the first defect surfaced a
  pre-existing repo-wide blind spot: `recursive = true` never descends into
  directories lacking `__init__.py`, so `tests/unit/` escapes the df12 tier
  entirely (see `Surprises & discoveries`).
- [ ] EP-M2: beta selected and locked on both paths; callers migrated; markers
  removed; all gates green; seeded mutations observed.
- [ ] EP-M3: distribution evidence recorded; documentation, ADR, and roadmap
  updated; all gates green.

## Surprises & discoveries

- **The beta's published artefacts.**
  - Observation: `0.2.0b1` ships the following, requires Python 3.12 or newer,
    and has no runtime dependencies:
    - five `cp312-abi3` native wheels: macOS x86-64 and arm64, manylinux 2.28
      x86-64 and aarch64, and Windows amd64;
    - one `py3-none-any` pure-Python wheel;
    - an sdist.
  - Evidence: `https://pypi.org/pypi/cuprum/0.2.0b1/json`, queried 2026-09-25.
  - Impact: both distribution types exist, so assessment gate 1 applies to
    both.
- **uv selects the beta without extra configuration.**
  - Observation: uv 0.11.19 resolves both `cuprum==0.2.0b1` and
    `cuprum>=0.2.0b1,<0.3` on both paths without any pre-release flag. A plain
    `uv lock` changed only cuprum (`Updated cuprum v0.1.0 -> v0.2.0b1`, still 62
    packages).
  - Evidence: scratch copies under `/tmp`; transcripts in
    `Artefacts and notes`.
  - Impact: no `[tool.uv]` pre-release configuration is needed.
- **Inline metadata really does bypass the project, and a script lockfile
  works without a workflow change.**
  - Observation: uv's documentation says that with inline script metadata "the
    project's dependencies will be ignored", even inside a project.
    `uv lock --script` writes an adjacent `<script>.lock`, and `uv run --script`
    then uses it automatically (the probe printed "Found existing lockfile for
    script"). `uv lock --script <script> --check` exits 0 when that lock is
    fresh.
  - Evidence: <https://docs.astral.sh/uv/guides/scripts/>, retrieved with
    Firecrawl; local probes.
  - Impact: the standalone path is independent of `uv.lock`. It can be locked
    without changing the workflow (D4).
- **The standalone path resolves a different cyclopts major version.**
  - Observation: resolved afresh, `cyclopts>=3` becomes cyclopts 5.0.0, while
    `uv.lock` holds 3.24.0. The release job resolves at tag time with no lock
    and no hash checks, and a CI run with a cold cache fetches whatever is
    newest.
  - Evidence: `uv tree --script` probes, reproduced by two reviewers.
  - Impact: the draft's claim that an exact cuprum pin makes the two paths
    "select the same artefact" held only for cuprum. The revised plan locks the
    standalone path (D4).
- **The suite's breakage under the beta is known and small.**
  - Observation: running the existing suite against the beta through an
    overlay (`uv run --with cuprum==0.2.0b1 pytest ...`) fails exactly 13
    tests. Every failure is
    `TypeError: scoped() got an unexpected keyword argument 'allowlist'`. They
    fall as follows, and the other 49 tests in those files pass:
    - three in `tests/unit/utils/test_commands.py`;
    - three BDD scenarios from
      `tests/bdd/steps/test_commands_catalogue_steps.py`;
    - three in `tests/unit/test_upload_release_wheels.py`;
    - four in `tests/e2e/test_upload_release_wheels_cli.py`.
  - Evidence: transcript in `Artefacts and notes`.
  - Impact: the migration surface is known and small. The roadmap bullet names
    only `run_gh`, but the developers' guide assigns the catalogue test call
    sites to this task too.
- **The beta's signatures and capture behaviour.**
  - Observation: the relevant beta signatures are:

    ```python
    scoped(config: ScopeConfig | None = None, *, catalogue: ProgramCatalogue | None = None)
    ScopeConfig(allowlist=None, before_hooks=(), after_hooks=(), observe_hooks=(),
                timeout=None, env_overlay=None)
    RunOutputOptions(capture=True, echo=False, ...)
    SafeCmd.run_sync(self, *, output=None, timeout=None, context=None, stdin=None)
    ```

    Reviewers confirmed that 0.1.0 and the beta capture output the same way:
    - an empty stream comes back as `''`, not `None`;
    - carriage return plus line feed (CRLF) is preserved;
    - invalid UTF-8 becomes U+FFFD (`errors='replace'`, whatever the locale);
    - a child killed by SIGTERM reports `exit_code == -15`;
    - the parent environment, including tokens, is inherited.

    The beta adds `CommandResult` fields `started_at`, `duration`,
    `max_rss_bytes`, `user_cpu_seconds`, `system_cpu_seconds`, and
    `relay_fallbacks`. `run_gh` ignores all of them.
  - Evidence: `inspect.signature` probe in `Artefacts and notes`, plus the
    contracts panel's probes.
  - Impact: the adapter change is two call sites plus an import.
    `RunOutputOptions(capture=True)` preserves 0.1.0 semantics exactly.
- **Two files are already over the 400-line limit, and the migration would add
  about seven lines.**
  - Observation: `scripts/release_wheel_upload.py` has 401 lines and
    `tests/unit/test_upload_release_wheels.py` has 417. Adding the beta's
    names to the one-line `from cuprum import ...` takes it past ruff's
    88-column limit, and `ruff format` then turns it into a parenthesized
    block several lines long.
  - Evidence: `wc -l`; `pyproject.toml` `line-length = 88`.
  - Impact: EP-M0 extracts the cuprum boundary into its own module before the
    migration.
- **CI does not run `make test`, and `make` re-locks silently.**
  - Observation: `ci.yml` runs the suite through the shared coverage action
    (slipcover plus `pytest -n auto`), so doctests do not run in CI. Locally,
    `make test` depends on `build`, which runs `uv sync --group dev` without
    `--locked`, so a stale lock is silently rewritten. No workflow runs
    `uv lock --check`.
  - Evidence: `Makefile` lines 68 and 149; `ci.yml`.
  - Impact: comparing versions across files cannot prove that a lock is fresh.
    The plan adds a lock-freshness test (O1b).
- **mutmut copies only some files into its sandbox.**
  - Observation: mutmut's sandbox receives `tests/`, `pyproject.toml`, and the
    `also_copy` entries (`docs/`, `scripts/`), but not `uv.lock`.
  - Evidence: `pyproject.toml` `[tool.mutmut]`.
  - Impact: without a fix, a test that reads `uv.lock` breaks the nightly
    mutation baseline. The plan adds `uv.lock` to `also_copy`, and the
    freshness test skips when there is no Git checkout.
- **A developer's machine can publish without a token.**
  - Observation: this host has an authenticated `gh` whose stored login needs
    no `GH_TOKEN`. The e2e tests run with the lading repository as their working
    directory, and the existing stub overwrites its record, so "called exactly
    once" could never fail.
  - Evidence: `~/.config/gh/hosts.yml`;
    `tests/e2e/test_upload_release_wheels_cli.py`
    `_GH_STUB`.
  - Impact: removing tokens alone does not prevent publication. The stub helper
    adds the protections listed in `Constraints` and records one JSON line per
    call.
- **cuprum ships no `py.typed` marker in either 0.1.0 or the beta.**
  - Evidence: probe of the installed packages.
  - Impact: no change in how cuprum is type-checked (see `Risks`).
- **`tests/unit/test_upload_release_wheels.py` could not be brought under 400
  lines by the EP-M0 move alone.**
  - Observation: moving the `run_gh` test out took the file from 417 to 417
    net lines -- adding the `release_gh` fixture put back what the move
    removed. Splitting the fixtures into a shared helper
    (`tests/helpers/script_imports.py`) brought it to 410, still over.
  - Evidence: `wc -l` at each step.
  - Impact: the module was split by concern. Discovery tests, which include the
    unprivileged-permission cases and the Hypothesis property, moved to
    `tests/unit/test_upload_release_wheels_discovery.py`. The original is now
    261 lines, the new module about 190, and both are comfortably inside the
    limit. The split is by responsibility rather than by size: one module
    covers "what gets uploaded and what is reported", the other "where the
    wheels are found and how a bad directory is diagnosed".
- **`make lint` uses the `ruff: ignore[...]` spelling, not `# noqa`.**
  - Observation: new EP-M1 files written with `# noqa: S603` failed
    `noqa-comments` (RUF105), which requires the project's own spelling.
    `DOC502` (extraneous exception) also fires on a `Raises` section that
    documents an exception raised by a *delegated* helper or by the standard
    library call the body makes, not by a `raise` in that function.
  - Evidence: `make lint` log during the EP-M0 gate run; the message
    "Use `ruff: ignore` instead".
  - Impact: every suppression in the new files uses `# ruff: ignore[rule] -
    reason`, matching `tests/bdd/steps/test_publish_when_steps.py`. A partial
    `make lint` run also reaches only as far as `ruff check`; failures there
    mean `interrogate`, `pylint`, `df12-pylint`, and `ambrleaks` have not run
    at all, so a single passed stage is not a passed gate.
- **A `pytest.mark.xfail` under a pytest-bdd `@scenario` is discarded.**
  - Observation: the marker was placed between `@scenario(...)` and the test
    function, matching the order the plan's snippet suggests. The test ran
    unmarked, and the red run reported `1 failed` instead of `1 xfailed`.
    pytest-bdd's `@scenario` returns a wrapper built from the function it
    decorates, and the inner function's marks do not travel outward.
  - Evidence:
    `uv run pytest -rxX tests/bdd/steps/test_release_wheel_upload_steps.py`
    before and after moving the decorator.
  - Impact: the marker goes *above* `@scenario`, with a comment saying why, so
    the next person to add one to another scenario does not repeat it. The
    plan's snippet is a fragment and does not show the required order.
- **The stub's `which` guard is defeated by `extra`, not by the ambient `PATH`
  .**
  - Observation: a test that prepended a shadowing directory to `PATH` could
    not make the guard fire: `isolated_environment` prepends the stub's own
    directory after reading the environment, so the stub always wins. The
    reachable hazard is the caller's `extra` mapping, which is applied last.
  - Evidence: a direct call with a shadowing directory in `PATH` returned
    normally; the same directory passed through `extra` raised.
  - Impact: the helper test asserts the `extra` case, which is the one a
    future caller could actually trigger. The guard itself is unchanged and
    still checks the environment as built.
- **The standalone run reports a usable `VIRTUAL_ENV`, so O2 is measurable.**
  - Observation: `uv run --script` gives the child
    `VIRTUAL_ENV=~/.cache/uv/environments-v2/upload-release-wheels-<hash>`,
    with `site-packages` at `lib/python3.14/site-packages` under it, and
    `UV_PROJECT`, `UV_PROJECT_ENVIRONMENT`, and `UV_WORKING_DIRECTORY` unset.
    A probe reading that path found exactly one cuprum distribution, version
    `0.1.0` -- the pre-beta value the scenario must fail on.
  - Evidence: a temporary probe script run as the stub, recording its
    environment; `importlib.metadata.distributions(name="cuprum", path=[site])`
    against the recorded path.
  - Impact: the O2 step resolves the interpreter version part of the path with
    `Path.glob("lib/python*/site-packages")` rather than assuming 3.13. The
    helper's stripping of the `UV_*` variables is confirmed not to break the
    standalone path, because uv sets them itself for the child.
- **The df12 lint tier does not reach `tests/unit/` at all.**
  - Observation: `make lint`'s df12-pylint stage reported a single `C9102`
    (bare `assert`) in `tests/workflow_contracts/test_cuprum_selection.py`,
    while running the same command against `tests/unit` reports hundreds
    across that directory's existing files. The cause is configuration, not
    rule selection: `[tool.pylint.main] recursive = true` descends only into
    packages, and `tests/unit/`, `tests/unit/utils/`, `tests/bdd/`, and
    `tests/support/` carry no `__init__.py`. A directory without one is never
    walked, so every file under it escapes this tier.
  - Evidence: a two-file scratch tree. With `pkg/sub/__init__.py` absent, a
    bare `assert` in `pkg/sub/mod.py` is unreported; adding the file makes it
    appear. In this repository `tests/unit` reports 781 `C9102` findings when
    named directly and none when reached through `tests`.
  - Impact: pre-existing and repo-wide, not introduced here. It means the
    dif12 tier is silent on the largest test directory, so its `C9102`,
    `R9108`/`R9109` (snapshot-assertion) and `R9111` (dataclass slots) rules
    are unenforced there. This task fixes its own files to the standard the
    tier intends -- `tests/unit/test_gh_stub_helper.py` and
    `tests/unit/test_release_gh_properties.py` are clean when named directly --
    but does not add `__init__.py` files or re-lint the directory wholesale,
    which would be a large unrelated change dwarfing 5.1.4. Recorded as a
    follow-up rather than actioned.
  - Impact on this task's red tests: the `xfail` markers themselves are
    unaffected; only message-carrying asserts were added.
- **`docs/scripting-standards.md`'s cuprum examples name an API that never
  existed in either version.**
  - Observation: the guide's examples use `from cuprum import Catalogue, sh`,
    `Catalogue.from_programs(...)`, `Hook(...)`, and `sh.scoped(CATALOGUE)`.
    Probing both releases: `Catalogue` and `Hook` are absent from `cuprum`
    0.1.0 *and* from `0.2.0b1`; `sh.scoped` does not exist in 0.1.0 at all.
    The real names are `ProgramCatalogue`, `ExecHook`, and the module-level
    `scoped(catalogue=...)`.
  - Evidence: `dir(cuprum)` on both versions, run in scratch environments.
    0.1.0 exposes `ProgramCatalogue`, `observe`, `scoped`, and `sh`;
    `sh.scoped` raises `AttributeError` there. The beta adds
    `RunOutputOptions`, `ScopeConfig`, and `Catalogue`-free `scoped`.
  - Impact: EP-M3 step 6 is a larger correction than "correct the cuprum
    examples" implies. The examples are not merely on the old `0.1.0` form --
    they are wrong for every published cuprum, so a reader following them
    cannot succeed on any version. They are documentation-only and gate-
    invisible (mdformat does not execute them), so they have gone unnoticed.
    The correction is therefore to the beta's real surface, and should say so
    where an example would otherwise look like a version choice.
- **`scoped(catalogue=...)` preserves the rejection semantics O5 asserts.**
  - Observation: under `0.2.0b1`, `sh.make` of an unregistered programme
    inside `scoped(catalogue=LADING_CATALOGUE)` raises `UnknownProgramError`
    with "is not in the catalogue allowlist", and the allowlist derives from
    the catalogue's programmes (`cargo`, `git`, `sccache`).
  - Evidence: a probe against the beta building the same `ProgramCatalogue`
    shape as `lading/utils/commands.py`.
  - Impact: confirms D2 from the maintainer. It also shows a limit worth
    recording: a programme from a *different* catalogue, constructed inside
    our scope, did **not** raise in this probe. Nothing in 5.1.4 relies on
    that case, and no existing test asserts it, but §7.3's "nested scopes
    cannot widen their parent's allowlist" should not be read as covering it.

## Decision log

- **D1: pin exactly, `cuprum==0.2.0b1`, in both `pyproject.toml` and the
  script metadata.**
  - Rejected alternatives:
    - a range such as `>=0.2.0b1,<0.3`;
    - a split, with a range in `pyproject.toml` and exactness only in
      `uv.lock`.
  - Rationale:
    - The roadmap asks for an "explicit beta selection", and assessment §3.4
      proposes "a trial pin to `cuprum==0.2.0b1`".
    - Pre-release APIs may change between `b1`, later betas, and `0.2.0`. A
      published lading wheel should not admit an untested beta.
    - The split becomes attractive once 0.2.0 final ships. ADR-006 records it
      as the policy to revisit then.
  - Date/Author: 2026-09-25, planning agent; kept after review.
- **D2 (maintainer decision): `run_gh` uses
  `scoped(catalogue=RELEASE_CATALOGUE)` and
  `run_sync(output=RunOutputOptions(capture=True))`.** The catalogue tests use
  `scoped(catalogue=LADING_CATALOGUE)`.
  - Rationale:
    - The migration guide recommends `scoped(catalogue=...)` when the
      allowlist should equal the catalogue's programmes, which is exactly this
      case. `scoped` derives the allowlist from the catalogue.
    - Roadmap 5.1.4 names `ScopeConfig`, but it predates this affordance. The
      maintainer directed adopting the shorter form. This is an accepted
      deviation from `RM-5.1.4-API`'s wording, not from its intent (the
      removed flat forms are replaced). EP-M3 updates the roadmap wording,
      design §7.3, the developers' guide, and the `lading/utils/commands.py`
      docstring to match.
    - `ScopeConfig` remains the documented form when a scope also needs hooks,
      a timeout, or an environment overlay. Nothing in 5.1.4 does.
    - Stating `capture=True` explicitly keeps the guarantee that `gh`'s
      diagnostic reaches the caller.
  - Evidence: a probe against the beta with the stub `gh` showed the same
    behaviour as the `ScopeConfig` form. `gh` ran with both streams captured
    and exit status 3. `sh.make` of an unregistered programme raised
    `UnknownProgramError`. A programme from another catalogue, run inside the
    scope, raised `ForbiddenProgramError` ("denied by context allowlist").
  - Date/Author: 2026-09-25, maintainer (leynos); replaces the planning
    agent's `ScopeConfig` choice.
- **D3: migrate the catalogue tests (`tests/unit/utils/test_commands.py`,
  `tests/bdd/steps/test_commands_catalogue_steps.py`) in the same commit as the
  pin.**
  - Rationale: they fail under the beta. The developers' guide says they "must
    be corrected as part of task 5.1.4". No shim is justified.
  - Date/Author: 2026-09-25, planning agent.
- **D4 (reversed after review): commit a script lockfile,
  `scripts/upload_release_wheels.py.lock`, generated with `uv lock --script`.**
  - Rationale:
    - The standalone path is otherwise unlocked, and it resolves cyclopts 5.0.0
      while `uv.lock` holds 3.24.0.
    - The release job holds a token with `contents: write`, yet it would
      install whatever PyPI serves at tag time. The lock pins every package by
      hash.
    - `uv run --script` picks the lock up automatically, so the workflow
      command and its contract test stay unchanged.
    - Staleness is guarded by `uv lock --script ... --check` in the suite (O1b).
    - The lock deliberately keeps the standalone path's own resolution
      (cyclopts 5.0.0, which the release job already uses) rather than forcing
      the repository's 3.24.0. Each path is tested with its own lock, and only
      cuprum must match across paths.
  - Cost: one more artefact, refreshed with one command, which the developers'
    guide documents. Dependabot will not refresh it, but D9 handles cuprum, and
    an unrefreshed lock stays valid and pinned.
  - Date/Author: 2026-09-25, planning agent, after the three panels raised the
    cyclopts drift.
- **D5: prove "no GitHub release is published by validation" by
  construction.** The shared stub helper enforces the protections in
  `Constraints`. The stub records one JSON line per call, with its argv, token
  presence, a sentinel variable, `GH_CONFIG_DIR`, and `VIRTUAL_ENV`. The
  scenarios assert the complete call list.
  - Rationale: the stub is the primary guarantee. Removing tokens, isolating
    the configuration directory, and using an invalid host are defence in
    depth, and they hold even if a refactor moves the stub off the front of
    `PATH`.
  - Date/Author: 2026-09-25; hardened after review.
- **D6: record the policy in design §7 and a new ADR,
  `docs/adr/006-align-cuprum-selection-across-dependency-paths.md`.** The
  policy covers:
  - exact pins while on a pre-release;
  - a lock on each path;
  - cross-path agreement enforced by a contract test;
  - manual bumps;
  - the release gate in D8.

  The ADR takes its title from the lasting alignment policy, not from the
  temporary pin. It amends ADR-005's standalone-script contract, which the ADR
  must say.
  - The repository keeps ADRs under `docs/adr/NNN-*.md`, but the style guide
    says `docs/adr-NNN-*.md`. Follow the repository, and correct the style guide
    in the same commit.
  - Date/Author: 2026-09-25; retitled after review.
- **D7: before migrating, extract the uploader's cuprum boundary into
  `scripts/release_gh.py` (EP-M0).** The new module holds `GH`,
  `RELEASE_CATALOGUE`, `CommandOutcome`, and `run_gh`. `release_wheel_upload`
  imports them and keeps `UploadRunner` and the production default binding.
  - Rationale:
    - The migration would push an over-limit file further over.
    - Isolating the only process-starting code makes it the single driven
      adapter behind the `UploadRunner` port, which is exactly where 5.1.4's
      change belongs.
    - `release_gh` imports only cuprum and the standard library, so there is no
      import cycle.
    - Tests import from the new module directly; there is no alias.
  - Date/Author: 2026-09-25, after review.
- **D8 (maintainer decision): no final lading 0.x release until cuprum 0.2.0
  final ships.**
  - Rationale: both repositories are under the same ownership, so the lading
    release can wait for the cuprum release. The pin then moves from
    `0.2.0b1` to `0.2.0` through the D9 procedure before any final lading tag.
    No final lading wheel ever records a pre-release requirement.
  - Consequences:
    - ADR-006 and the developers' guide release section state the gate.
    - This task adds no automated enforcement, such as a release-workflow
      check that refuses a final tag while a pre-release pin exists. Nobody
      asked for it; if wanted, it belongs with #286, which reworks the release
      workflow.
    - #286 (adopt the cuprum release process from leynos/cuprum#488) should
      land before that first final release.
  - Date/Author: 2026-09-25, maintainer (leynos); replaces the planning
    agent's "do not block releases" proposal.
- **D9: cuprum bumps are manual. Leave Dependabot configured as it is.**
  - Rationale:
    - A Dependabot pull request that bumps cuprum edits only `pyproject.toml`
      and `uv.lock`. The selection contract fails it, so auto-merge cannot land
      it, and the red pull request is itself the notice that a new release
      exists.
    - The developers' guide gives the manual procedure: edit the two
      requirement sites, run `uv lock` and `uv lock --script ...`, rerun the
      distribution smoke, and update the version named in documentation.
  - Date/Author: 2026-09-25, after review.

### Design review dispositions

A community-of-experts panel (Logisphere) reviewed the draft through six lenses
in three panels on 2026-09-25. Their verdicts were: structure and alternatives,
"Revise"; contracts and scaling, "Proceed after revision"; failure modes and
viability, "Approve with changes". Every blocking finding is addressed:

- **Red-commit mechanics.** A module-level strict `xfail` would XPASS on the
  scenarios that already pass under 0.1.0. Markers also lacked `raises=`, and
  the property test's import guard invented a failure.
  - Fixed: explicit `@scenario` bindings; strict
    `xfail(raises=AssertionError)` only on assertions that are genuinely red;
    the property test and regression scenarios land green as characterization
    tests; an acceptance `rg` check confirms that no marker survives.
- **File size.** The "do not grow" constraint could not be met as written.
  - Fixed: EP-M0 extraction (D7).
- **Mutation restore.** `git checkout --` before the EP-M2 commit would wipe
  the migration.
  - Fixed: commit first; apply and reverse mutations from patch files; finish
    with `git diff --exit-code`.
- **No-publication proof.** Removing tokens alone does not stop a stored
  `gh` login.
  - Fixed: D5's protections; one JSON line per stub call; a tag that cannot
    exist.
- **Timeouts.** The 180-second timeout did not exist; the global timeout is 30
  seconds.
  - Fixed: module `pytestmark` timeout of 60 seconds, subprocess timeout of 45
    seconds, uv's stderr in assertion messages, and Hypothesis `deadline=None`.
- **Lock freshness.** `uv.lock` checks prove nothing under `make`, and the
  mutmut sandbox lacks `uv.lock`.
  - Fixed: O1b freshness test; `also_copy` gains `uv.lock`; the freshness test
    skips when there is no Git checkout.
- **Cyclopts drift on the standalone path.**
  - Fixed: D4 reversed.
- **Hard-coded version in the contract test.**
  - Fixed: O1 compares the sites with each other and checks their shape. The
    version string appears only in the requirement sites, the locks, and the
    documentation.
- **Proxy version check.** `uv tree --script` performs a fresh resolution; it
  does not inspect the environment that ran.
  - Fixed: the stub records `VIRTUAL_ENV`, and the scenario reads the cuprum
    version from that environment's `site-packages`.
- **Untested environment inheritance.**
  - Fixed: a sentinel variable must reach the stub (O3).
- **Duplication with the existing e2e tests.**
  - Fixed: the BDD feature covers only the standalone mode. The repository mode
    stays with the existing e2e tests, which go red under the pin and green
    after the migration, and with O1's installed-version check.
- **Scope bookkeeping.**
  - Fixed: roadmap 5.3.2's inventory gains the new stub helper. The
    `scripting-standards.md` cuprum examples are corrected. The CI description
    is corrected.
- **Considered and not adopted.**
  - Replacing the Hypothesis property with three or four examples was not
    adopted. The input domain now includes invalid UTF-8, CRLF, and signal
    statuses, and the property's cost is about 0.25 s.
  - A cyclopts major-version cap was not adopted; D4 supersedes it.
  - Pinning uv in `release.yml` was deferred (see `Risks`).

## Outcomes & retrospective

Not started. Fill this in at each milestone and at completion. Before setting
the status to `COMPLETE`, reconcile every discovery with the artefacts in
`Conformance basis`: update the assessment's evidence boundary and design §7,
and mark roadmap item 5.1.4 done.

## Context and orientation

The repository root contains the `lading` package, `scripts/`, `tests/`, and
`docs/`. Run all commands from the repository root. `make` targets wrap `uv`.

The files this task touches:

- **`pyproject.toml`.**
  - Line 19 declares `"cuprum>=0.1.0"` under `[project] dependencies`, and
    `requires-python = ">=3.13"`.
  - The only `[tool.uv]` setting is `package = true`, so uv's default
    pre-release handling applies.
  - `[tool.mutmut] also_copy = ["docs/", "scripts/"]`.
  - `[tool.pytest.ini_options] timeout = 30`.
- **`uv.lock`** pins `cuprum` 0.1.0, with one `py3-none-any` wheel and an sdist.
  Only `lading` depends on it; see lading's `requires-dist`.
- **`scripts/upload_release_wheels.py`** is the uploader's command-line edge and
  composition root. Its first lines are:

  ```python
  #!/usr/bin/env -S uv run python
  # /// script
  # requires-python = ">=3.13"
  # dependencies = ["cuprum>=0.1.0", "cyclopts>=3"]
  # ///
  ```

  It imports its sibling `release_wheel_upload`. Python finds the sibling
  because a script's own directory is first on `sys.path`, and that holds for
  both paths.
- **`scripts/release_wheel_upload.py`** holds the uploader logic.
  - It builds `RELEASE_CATALOGUE`, a `ProgramCatalogue` whose only programme is
    `GH = Program("gh")`.
  - It defines `CommandOutcome` and the `UploadRunner` protocol (the port).
  - `run_gh` (around line 226) is the driven adapter and the only place the
    script starts a process:

    ```python
    with scoped(allowlist=RELEASE_CATALOGUE.allowlist):
        command = sh.make(GH, catalogue=RELEASE_CATALOGUE)(*arguments)
        result = command.run_sync(capture=True)
    ```

  - `upload_wheels(tag, wheels, *, run=run_gh)` and `Dependencies` bind the
    production defaults.
- **`lading/utils/commands.py`** defines `LADING_CATALOGUE` (cargo, git,
  sccache). It is not wired into execution yet. Its docstring already shows the
  beta form.
- **The catalogue tests.** `tests/unit/utils/test_commands.py` (lines 88-120)
  and `tests/bdd/steps/test_commands_catalogue_steps.py` (lines 93-96 and
  233-237) call `scoped(allowlist=LADING_CATALOGUE.allowlist)`. The feature
  file is `tests/bdd/features/commands_catalogue.feature`.
- **`tests/unit/test_upload_release_wheels.py`** unit-tests the uploader. It
  fakes `gh` with cmd-mox (`cmd_mox.mock("gh").with_args(...)`). The `cmd_mox`
  fixture comes from `cmd_mox.pytest_plugin`, registered in `tests/conftest.py`.
- **`tests/e2e/test_upload_release_wheels_cli.py`** runs
  `[sys.executable, scripts/upload_release_wheels.py, ...]` as a subprocess,
  with a recording `gh` stub first on `PATH`. It exercises only the repository
  path.
- **`tests/workflow_contracts/`** holds configuration-contract tests. For
  example, `test_release_workflow.py` pins the upload command, and
  `test_mutation_testing.py` shows how to skip inside the mutmut sandbox.
- **`tests/helpers/`** holds shared test helpers such as `cwd.py` and
  `workspace_builders.py`.
- **`.github/workflows/release.yml`** creates a draft release, runs the
  uploader, then publishes with `gh release edit --draft=false`.
- **`.github/workflows/ci.yml`** runs `make lint` and `make typecheck`. It runs
  the tests through the shared coverage action (slipcover plus
  `pytest -n auto`) on Python 3.13 on ubuntu x86-64. It does not run
  `make test`, so doctests are not run in CI.
- **`.github/dependabot.yml`** runs the `uv` ecosystem daily, and
  `dependabot-automerge.yml` enables auto-merge.

Terms used in this plan:

- **Dependency path:** one route by which an execution environment selects a
  cuprum version; the repository path or the standalone path, as defined above.
- **Stub `gh`:** an executable file named `gh` that a test writes into a
  temporary `bin/` directory placed first on `PATH`. It appends one JSON line
  per call to a record file and exits with a chosen status.
- **Native and pure-Python distributions:** the `cp312-abi3` wheels contain an
  optional Rust extension; the `py3-none-any` wheel does not.
  `cuprum.is_rust_available()` reports which one is installed.
- **Characterization test:** a test written to pin current behaviour before a
  change, which must stay green across it.
- **Red-Green-Refactor:** write a failing test first (red), make it pass with
  the smallest change (green), then tidy up without changing behaviour
  (refactor).

Documentation and skills to consult:

- **The roadmap and design.** `docs/roadmap.md` §5, items 5.1.4 and 5.3.2.
  `docs/lading-design.md` §7, especially §7.2 item 6 and §7.3.
  `docs/cuprum-v0-2-0-beta1-adoption-assessment.md` §3.1, §3.4, and §7 gate 1.
- **The cuprum guides.** In `docs/cuprum-users-guide.md`: "Apply a policy",
  "Control output", "Choosing a stream backend", and "Checking the native
  extension". In `docs/cuprum-v0-2-0-migration-guide.md`: "Catalogue-backed
  scoped contexts".
- **Testing and scripts.** `docs/cmd-mox-usage-guide.md` for the cmd-mox
  fixture. `docs/scripting-standards.md` for the PEP 723, cyclopts, and cuprum
  conventions.
- **The uploader's records.** `docs/adr/005-release-wheel-publication.md`. In
  `docs/developers-guide.md`: "Release workflow" and the `LADING_CATALOGUE`
  note near line 1606.
- **Style.** `docs/documentation-style-guide.md` for the ADR and prose rules.
- **Skills.**
  - `execplans` (this plan).
  - `python-router`, routing to `python-testing` (pytest-bdd scenarios,
    parametrization) and `hypothesis` (the property test).
  - `hexagonal-architecture`, to keep the change inside the adapter.
  - `dependency-update` for the pin change.
  - `codegraph-mcp`, for the callers of `run_gh` and `scoped`.
  - `firecrawl-mcp`, for any further uv or PyPI lookups.
  - `en-gb-oxendict`, `commit-message`, and `pr-creation`.

## Conformance basis

There are no Terms of Reference or technical-design identifiers for this work.
The upstream artefacts are these, as of commit `d613a80` on this branch:

- `RM-5.1.4-SEL`: roadmap 5.1.4, first bullet. It requires an explicit beta
  selection in `pyproject.toml` and `uv.lock`, with aligned inline script
  metadata.
- `RM-5.1.4-API`: roadmap 5.1.4, second bullet. `run_gh` replaces the removed
  flat forms. The bullet names `ScopeConfig` and `RunOutputOptions`; the
  maintainer replaced `ScopeConfig` with `scoped(catalogue=...)` (D2, an
  accepted deviation). EP-M3 updates the roadmap text.
- `RM-5.1.4-OK`: roadmap 5.1.4, success bullet. The installed beta works through
  repository and standalone execution with a stub `gh`, and validation
  publishes nothing.
- `ASM-3.1` and `ASM-3.4`: assessment §3.1 (legacy calls break) and §3.4
  (explicit selection, and installation in a clean environment).
- `ASM-G1`: assessment §7 gate 1, including separate verification of the
  pure-Python and native distributions.
- `DG-CAT`: the developers' guide note that assigns the catalogue test call
  sites to 5.1.4.
- `DES-7.2.6` and `DES-7.3`: design §7.2 item 6 (update the uploader) and §7.3
  (the beta forms).
- `ADR-005`: the uploader stays a standalone PEP 723 script whose catalogue
  allowlists `gh` alone.
- `AGENTS-400`: the 400-line file limit in `AGENTS.md`.

Trace links:

```plaintext
AGENTS-400, ADR-005 -> EP-M0 -> tests/unit/test_release_gh.py (moved run_gh tests, unchanged assertions)
RM-5.1.4-SEL, ASM-3.4 -> EP-M2 -> tests/workflow_contracts/test_cuprum_selection.py (O1a, O1b)
RM-5.1.4-API, ASM-3.1, DES-7.2.6 -> EP-M2 -> tests/unit/test_release_gh.py, tests/e2e/test_upload_release_wheels_cli.py
RM-5.1.4-API -> EP-M1/EP-M2 -> tests/unit/test_release_gh_properties.py (O4 capture fidelity)
DG-CAT, DES-7.3 -> EP-M2 -> tests/unit/utils/test_commands.py, commands_catalogue.feature (O5)
RM-5.1.4-OK, ASM-G1 -> EP-M1/EP-M2 -> tests/bdd/features/release_wheel_upload.feature (O2, O3)
ASM-G1 (distributions) -> EP-M3 -> recorded pure-Python and native smoke transcripts
ADR-005 -> all milestones -> tests/workflow_contracts/test_release_workflow.py (unchanged, still green)
```

## Verification plan

This change introduces no new business logic. It introduces one configuration
invariant, selection alignment, and it depends on one adapter contract: capture
fidelity through the beta.

### Obligation O1a: selection alignment

**Statement.** The sites below all name one exact cuprum version, and the
repository test interpreter has that version installed:

- `pyproject.toml`'s cuprum requirement;
- the PEP 723 block's cuprum requirement;
- `uv.lock`'s single `cuprum` package entry and lading's `requires-dist`
  specifier;
- the script lockfile's `cuprum` package entry and its script `requires-dist`
  specifier.

**Method.** A contract test over a finite set of sites, so enumeration is
exhaustive. The expected value comes from `pyproject.toml`; the test contains
no version literal.

**Shape checks.** Each requirement is a single `==` specifier with no extras
and no markers. Parse with the pattern `^cuprum==(?P<version>[^\s,;\[\]]+)$`
after removing whitespace, and fail with a message that names the site. Do not
declare `packaging`: it is only a transitive dependency, and a new development
dependency trips a tolerance.

**Artefact.** `tests/workflow_contracts/test_cuprum_selection.py`.

- It reads the TOML files with `tomllib`.
- It extracts the PEP 723 block with the regular expression from the PEP 723
  reference implementation.
- It checks the installed version with `importlib.metadata.version("cuprum")`.

**Evidence.**

- In EP-M1 the alignment test is `xfail(strict=True, raises=AssertionError)`.
  It fails because `pyproject.toml` says `>=0.1.0`, which is not an exact pin,
  and because the script lockfile is missing, which the test asserts. In EP-M2
  it passes.

**Non-vacuity.** Helper tests in the same file land green in EP-M1. They feed
the checker in-memory documents, and each must be reported with the offending
site named:

- a metadata block that pins `cuprum==0.1.0` while `pyproject.toml` pins
  another version;
- a range specifier;
- a requirement with a marker;
- a script with no metadata block;
- a lock with two `cuprum` entries.

### Obligation O1b: lock freshness

**Statement.** `uv lock --check` and
`uv lock --script scripts/upload_release_wheels.py --check` both exit 0.

**Method.** A contract test that runs both commands with a 45-second timeout
and reports uv's stderr on failure. The module is marked
`pytest.mark.timeout(60)`.

**Sandbox handling.** The test skips when the repository has no `.git` entry.
Inside mutmut's sandbox, follow the precedent in `test_mutation_testing.py`.

**Artefact.** The same module.

**Evidence.**

- In EP-M1 the project check passes. The script check is strict-xfail with
  `raises=AssertionError`, because no script lock exists yet.
- In EP-M2 both pass.

**Non-vacuity.** In EP-M2, add an unrelated dependency to the PEP 723 block
without re-locking. The script check must fail. Reverse the change from a patch
file.

### Obligation O2: the standalone path runs the beta end to end

**Statement.** `uv run --script scripts/upload_release_wheels.py` does all of
the following:

- discovers the wheel;
- calls the stub `gh` exactly once with
  `release upload v0.0.0-stub <wheel> --clobber`;
- exits 0;
- runs in an environment whose cuprum version equals the pin in
  `pyproject.toml`.

**Method.** A pytest-bdd scenario, run through the shared stub helper.

- The stub records `VIRTUAL_ENV`.
- The step locates `lib/python*/site-packages` under it and reads the version
  with `next(importlib.metadata.distributions(name="cuprum", path=[site]))`.
- It compares that version with the pin parsed by O1a's helper.

**Artefact.** `tests/bdd/features/release_wheel_upload.feature` and
`tests/bdd/steps/test_release_wheel_upload_steps.py`.

**Evidence.**

- In EP-M1 this scenario is bound explicitly with
  `@scenario(...)` and marked `xfail(strict=True, raises=AssertionError)`. It
  fails on the pin assertion (`>=0.1.0` is not an exact pin), and the recorded
  environment has cuprum 0.1.0.
- In EP-M2 the marker is removed and the scenario passes.

**Non-vacuity.**

- The record must hold exactly one JSON line. Recording appends, so a second
  call would be counted, and a run that never reaches `gh` fails.
- The version is read from the environment that ran, not from a separate
  resolution.
- In EP-M2, before migrating `run_gh`, run the scenario once after the pin
  alone. It must fail with the `TypeError` traceback in the uploader's stderr.
  That shows a dependency-only change would have been caught.

### Obligation O3: inheritance without credentials

**Statement.** For each standalone run, and for each repository-mode run in the
e2e tests:

- the stub sees the sentinel variable `LADING_STUB_SENTINEL`, which shows the
  environment is inherited as production needs for `GITHUB_TOKEN`;
- it sees neither `GH_TOKEN` nor `GITHUB_TOKEN`;
- it sees `GH_CONFIG_DIR` pointing at an empty directory;
- it sees `GH_HOST=stub.invalid`;
- no recorded call is `release create` or `release edit`.

**Method.**

- The helper performs a guard before spawning:
  `shutil.which("gh", path=env["PATH"])` must be the stub.
- A green scenario asserts the recorded observations.
- The helper's own unit test sets a dummy `GH_TOKEN` in the parent and asserts
  the child record lacks it.

**Artefact.** `tests/helpers/gh_stub.py`, its test
`tests/unit/test_gh_stub_helper.py`, and the scenario "gh inherits the
environment but never credentials".

**Evidence.** Green from EP-M1 onwards. This is a regression property, not a
red test.

**Non-vacuity.** The helper test proves the token is removed, not merely
absent. The sentinel assertion would fail if cuprum stopped inheriting the
environment. The `which` guard fails if another `PATH` entry shadows the stub.

### Obligation O4: capture fidelity of the adapter

**Statement.** For any payload, `run_gh` returns a `CommandOutcome` whose
`stdout` and `stderr` equal each stream's payload decoded with
`bytes.decode("utf-8", "replace")`, and whose `exit_code` equals the status.
The payloads are arbitrary bytes on each stream, including invalid UTF-8, CRLF,
and NUL. The status is any value from 0 to 255, or death by SIGTERM (-15).

**Method.** A Hypothesis property test that drives the real `run_gh` against a
real stub process.

- The stub reads its payloads and status from a file in a directory created
  with `tmp_path_factory`, because Hypothesis rejects function-scoped fixtures.
- `PATH` is patched inside the test body with `pytest.MonkeyPatch.context()`.
- Settings: `max_examples=25`, `deadline=None`.
- Explicit `@example`s cover empty streams, status 0, status 255, invalid UTF-8,
  CRLF, non-ASCII text, and SIGTERM.

**Rationale.** This checks repository-owned mapping against the real beta
interface rather than cuprum's internals. The input domain matters because the
beta decodes with `errors="replace"`.

**Domain.** `st.binary(max_size=200)` for each stream and `st.integers(0, 255)`
for the status, plus the SIGTERM example.

**Artefact.** `tests/unit/test_release_gh_properties.py`.

**Evidence.**

- The test lands green in EP-M1 as a characterization test against 0.1.0 and
  must stay green through EP-M2.
- Record Hypothesis statistics (`--hypothesis-show-statistics`), which show
  that non-empty streams and non-zero statuses were generated.

**Non-vacuity.** After the EP-M2 commit, apply each seeded mutation from a
patch file, observe the failure, reverse it, and confirm
`git diff --exit-code`. Each mutation must fail the property for the stated
reason:

- `capture=False`, which empties both streams;
- `stdout` and `stderr` swapped in the `CommandOutcome` construction;
- `exit_code=0` hard-coded.

### Obligation O5: catalogue behaviour is unchanged under the beta

**Statement.** The existing catalogue unit tests and the
`commands_catalogue.feature` scenarios pass using
`scoped(catalogue=LADING_CATALOGUE)`. That includes rejecting an unregistered
programme with `UnknownProgramError`.

**Method.** The existing named tests and scenarios, which are finite contract
examples.

**Evidence.** They fail under the beta overlay today (Stage A) and pass in
EP-M2.

### Axioms

These are trusted and are not verified by this plan:

- **A1:** uv resolves an explicit `==` pre-release pin without extra
  configuration. `uv run --script` honours PEP 723 metadata, ignores the
  project, and uses an adjacent script lockfile. This comes from the uv
  documentation and was confirmed locally with uv 0.11.19.
- **A2:** cuprum 0.2.0b1 behaves as its users' guide describes. Capture is
  available through `RunOutputOptions`. `scoped(catalogue=...)` derives its
  allowlist from the catalogue's programmes. A catalogued programme is found on
  `PATH`, and the environment is inherited. (Directly probed on 2026-09-25
  against the published beta; see `Surprises & discoveries`.)
- **A3:** PyPI serves the published artefacts with the hashes recorded in the
  locks.
- **A4:** with `GH_CONFIG_DIR` empty, no token, and `GH_HOST=stub.invalid`, a
  real `gh` cannot reach a real account. This is defence in depth only; the
  stub is the primary guarantee.

### Methods not used

Rust, Kani, and Verus do not apply. The repository has no Rust extension, and
the change has no arithmetic, memory, or protocol logic that a proof would
strengthen.

CrossHair is not used. The only repository-owned logic, the result mapping, is
exercised against the real interface by O4. A symbolic run over a mocked
`CommandResult` would test the mock, not the contract.

No syrupy snapshot is added. The uploader's output format does not change, and
the existing e2e tests already assert exact outcome lines.

## Plan of work

**Stage A (complete).** Reconnaissance, probes, and design review, recorded
above. No code changes.

### Stage B0 (EP-M0): extract the cuprum boundary (refactor only)

1. Create `scripts/release_gh.py`. Move these from
   `scripts/release_wheel_upload.py`, unchanged:
   - `GH`, `_RELEASE_PROJECT`, and `RELEASE_CATALOGUE`;
   - the `CommandOutcome` dataclass;
   - `run_gh`.

   Give the module a docstring stating that it is the only module that starts a
   process, and that it is the driven adapter behind `UploadRunner`.
2. In `scripts/release_wheel_upload.py`, import `CommandOutcome`,
   `RELEASE_CATALOGUE` (only if still referenced), and `run_gh` from
   `release_gh`. Keep `UploadRunner` and the defaults. Remove the `cuprum`
   import.
3. Update the tests to import from the new module, with no alias:
   - Move `test_run_gh_returns_the_diagnostic_it_captured` from
     `tests/unit/test_upload_release_wheels.py` into a new
     `tests/unit/test_release_gh.py`.
   - Replace the remaining `upload_module.CommandOutcome` references with the
     `release_gh` import.
4. Check that both paths still import the module. Run the e2e tests, which use
   the repository interpreter, and
   `uv run --script scripts/upload_release_wheels.py --help`.
5. Run the four gates, then commit
   "Extract the release uploader's gh adapter".

### Stage B (EP-M1): stub helper, characterization tests, and red tests

1. Create `tests/helpers/gh_stub.py`, following the interface in
   `Interfaces and dependencies`, and its unit test,
   `tests/unit/test_gh_stub_helper.py`. Move
   `tests/e2e/test_upload_release_wheels_cli.py` onto the helper. The file gets
   shorter, and the repository path gains the same no-publication protections.
2. Create `tests/unit/test_release_gh_properties.py` (O4). It is green and
   unmarked.
3. Create `tests/workflow_contracts/test_cuprum_selection.py`. It holds O1a with
   green checker self-tests, and O1b with the project check green and the
   script check strict-xfail.
4. Create `tests/bdd/features/release_wheel_upload.feature` and
   `tests/bdd/steps/test_release_wheel_upload_steps.py`.
   - Bind each scenario with its own `@scenario(...)` decorator; do not use
     `scenarios()`, so that each generated test can carry its own markers.
   - Mark only "The standalone uploader attaches a wheel with the pinned cuprum"
     with a strict expected failure:

     ```python
     @pytest.mark.xfail(
         strict=True,
         raises=AssertionError,
         reason="5.1.4: cuprum beta not yet selected",
     )
     ```

   - Set `pytestmark = pytest.mark.timeout(60)`.
5. Run the focused red command. Every marked test must be reported `x`
   (xfailed), and every other new test must pass. Run the four gates, then
   commit.

### Stage C (EP-M2): select, lock, migrate

These steps form one commit.

1. `pyproject.toml`: change `"cuprum>=0.1.0"` to `"cuprum==0.2.0b1"`.
2. `scripts/upload_release_wheels.py` line 4: change it to
   `# dependencies = ["cuprum==0.2.0b1", "cyclopts>=3"]`.
3. Run `uv lock` and confirm that `git diff uv.lock` touches only the `cuprum`
   package block and lading's `requires-dist` specifier.
4. Run `uv lock --script scripts/upload_release_wheels.py` to create
   `scripts/upload_release_wheels.py.lock`.
5. Update configuration:
   - Add the script lockfile to `typos.local.toml`'s exclusions, beside the
     generated `uv.lock` exclusion. Its hashes must not reach the spelling gate.
     Do not edit `typos.toml` by hand.
   - Add `"uv.lock"` to `[tool.mutmut] also_copy`.
6. Run `uv sync`. Then run the focused tests and observe the pin-only red:
   - the 13 known `TypeError` failures;
   - O2 failing with the `TypeError` in the uploader's stderr.

   Record both in `Artefacts and notes`.
7. Migrate `scripts/release_gh.py`: import `RunOutputOptions`, and change
   `run_gh` as shown in `Interfaces and dependencies`.
8. Migrate `tests/unit/utils/test_commands.py` and
   `tests/bdd/steps/test_commands_catalogue_steps.py` to
   `scoped(catalogue=LADING_CATALOGUE)`. Update the `lading/utils/commands.py`
   module docstring, which shows `scoped(ScopeConfig(...))`, to the same form.
   This is a docstring-only change to a production module; its doctests must
   still pass.
9. Remove every `xfail` marker added in EP-M1. Then
   `rg "cuprum beta not yet selected" tests` must print nothing.
10. Run the focused green command and the four gates, then commit.
11. After the commit, run the seeded mutations for O1b, O4, and O2. For O2,
    reapply the old flat call forms from a patch. Apply each with `git apply`,
    run the focused test, and reverse it with `git apply -R`. Finish with
    `git diff --exit-code`. Record the failures.

### Stage D (EP-M3): evidence and documentation

1. Run the distribution smoke checks in `Concrete steps` for the native wheel
   and the pure-Python wheel. Paste the transcripts into `Artefacts and notes`.
2. Write `docs/adr/006-align-cuprum-selection-across-dependency-paths.md`,
   recording D1, D2, D4, D8, and D9, and link it from `docs/contents.md` and
   design §7. In `docs/documentation-style-guide.md`, correct the ADR path rule
   to `docs/adr/NNN-short-description.md`.
3. `docs/lading-design.md` §7:
   - in §7.2 item 6, record that the uploader now uses the beta forms through
     `scripts/release_gh.py`;
   - in §7.3, replace the `scoped(ScopeConfig(allowlist=...))` example with
     `scoped(catalogue=...)`, say when `ScopeConfig` is still needed, and
     state the selection policy with a link to ADR-006;
   - add "Implementation notes (Step 5.1.4)".
4. `docs/developers-guide.md`:
   - Replace the paragraph saying the catalogue call sites "must be corrected
     as part of task 5.1.4" with the current state. The `LADING_CATALOGUE`
     paragraph's `scoped(ScopeConfig(allowlist=…))` becomes
     `scoped(catalogue=…)`.
   - In the release section, state D8's gate: no final lading 0.x release
     until cuprum 0.2.0 final ships and the pin has moved to it. Link #286.
   - Update the "Release workflow" description of the uploader's layout
     (composition root, logic module, `release_gh` adapter) and of the capture
     call (`RunOutputOptions(capture=True)`).
   - Add "Changing the cuprum version", with the manual bump procedure from D9
     and the stub-helper rule from `Constraints`.
5. `docs/users-guide.md` "Installation": say that source and development
   installs of lading currently depend on the cuprum 0.2.0 beta. Pip installs
   it automatically because the pin names the pre-release, and environments
   that pin another cuprum will conflict.
6. `docs/scripting-standards.md`: correct the cuprum examples
   (`sh.scoped(CATALOGUE)`, `Catalogue`) to the beta forms used by
   `scripts/release_gh.py`.
7. `docs/cuprum-v0-2-0-beta1-adoption-assessment.md` §1.1: append a dated
   follow-up paragraph saying that the published `0.2.0b1` artefact has been
   selected and validated on both dependency paths (gate 1), with a pointer to
   this plan. Leave the original text intact.
8. `docs/roadmap.md`:
   - Mark 5.1.4 done (`- [x]`) with a one-line evidence note. Reword its
     second bullet to name `scoped(catalogue=...)` and `RunOutputOptions`
     (D2).
   - Add `tests/helpers/gh_stub.py` to 5.3.2's list of capture-oriented
     subprocess call sites.
9. Run `make fmt`, then `make markdownlint`, `make nixie`, and the four code
   gates. Commit.

Every stage ends with its validation. Do not start the next stage while any
gate is red.

## Milestones and plateaus

### EP-M0: the adapter is extracted

- **Outcome:** `scripts/release_gh.py` holds the whole cuprum boundary.
  Behaviour is unchanged, and the repository still locks cuprum 0.1.0. Both
  over-limit files shrink.
- **Requirements:** `AGENTS-400`; it prepares `RM-5.1.4-API`.
- **Acceptance:**
  - The four gates are green.
  - The e2e tests pass.
  - `uv run --script scripts/upload_release_wheels.py --help` exits 0.
  - `wc -l` shows both files at 400 lines or fewer.
- **Conformance check:**
  - No behaviour change.
  - No alias for the moved names.
  - The workflow command is unchanged.
- **Recovery:** revert the commit.
- **Remaining gaps:** everything else.
- **Compatibility decision:** none. The moved names are private to the script,
  and every importer is updated.

### EP-M1: the red specification is committed

- **Outcome:**
  - The helper and the characterization tests (O3, O4, and O1a's self-tests)
    are green.
  - O1a alignment, the O1b script check, and O2 are strict-xfail.
  - The repository still selects 0.1.0, and every gate is green.
- **Requirements:** specifies `RM-5.1.4-SEL`, `RM-5.1.4-OK`, and `ASM-G1`
  (paths).
- **Acceptance:**
  - The red command reports each marked test as `x`; none is `XPASS`.
  - The gates are green.
- **Conformance check:** no production file changed, and no new dependency.
- **Recovery:** revert the commit.
- **Remaining gaps:** selection, migration, and documentation.
- **Compatibility decision:** none.

### EP-M2: the beta is selected and the callers are migrated

- **Outcome:**
  - Both paths select and lock `cuprum==0.2.0b1`.
  - `run_gh` and the catalogue tests use the beta forms.
  - Every new test passes unmarked.
- **Requirements:** discharges `RM-5.1.4-SEL`, `RM-5.1.4-API`, `DG-CAT`,
  `DES-7.2.6`, `RM-5.1.4-OK`, and O1-O5.
- **Acceptance:**
  - The green command passes.
  - The `rg` marker check prints nothing.
  - The seeded mutations fail as predicted, and the tree is clean afterwards.
  - `uv tree --script ... | grep cuprum` prints `cuprum v0.2.0b1`.
  - The four gates are green.
- **Conformance check:**
  - Unchanged: `run_gh`'s signature, `CommandOutcome`, `UploadRunner`, the
    allowlist, and the workflow command.
  - No file under `lading/runtime/` or `lading/testing/` changed.
  - No compatibility shim.
  - `uv.lock` changed only for cuprum.
- **Recovery:** revert EP-M3 first, if it has landed, then this commit. `make`
  re-syncs `.venv`, and the repository returns to the EP-M1 plateau with its
  markers.
- **Remaining gaps:** distribution evidence and documentation.
- **Compatibility decision:** none. The flat forms appear only at private call
  sites and in test code, and they are updated in the same commit as the pin.

### EP-M3: evidence and documentation

- **Outcome:** the native and pure-Python smoke transcripts are recorded. The
  design, ADR-006, the style guide, the developers' guide, the users' guide,
  the scripting standards, the assessment, and the roadmap all reflect the new
  state.
- **Requirements:** discharges `ASM-G1` (distributions) and closes 5.1.4.
- **Acceptance:** the Markdown and code gates are green, and roadmap 5.1.4 is
  checked.
- **Conformance check:** the documentation states the policy exactly as
  implemented, and claims nothing beyond gate 1.
- **Recovery:** this is a documentation-only commit and can be reverted freely.
- **Remaining gaps:**
  - roadmap 5.1.5 onwards;
  - pinning uv in `release.yml`;
  - moving the pin to cuprum 0.2.0 final before the first final lading
    release (D8);
  - adopting the cuprum release process (#286).
- **Compatibility decision:** none.

## Concrete steps

Run everything from the repository root. Capture gate output with `tee`, using
the template `/tmp/$ACTION-lading-$(git branch --show-current).out`. Run gates
one at a time, never in parallel. Prefer delegating gate runs to the
`scrutineer` agent, which runs them in sequence and reports the log paths.

### Refactor (EP-M0)

```bash
uv run --script scripts/upload_release_wheels.py --help >/dev/null && echo ok
wc -l scripts/release_wheel_upload.py tests/unit/test_upload_release_wheels.py
```

### Red (EP-M1)

```bash
uv run pytest -q -rxX tests/workflow_contracts/test_cuprum_selection.py \
  tests/bdd/steps/test_release_wheel_upload_steps.py \
  tests/unit/test_release_gh_properties.py \
  tests/unit/test_gh_stub_helper.py \
  tests/e2e/test_upload_release_wheels_cli.py \
  | tee /tmp/red-lading-$(git branch --show-current).out
```

Expected: three tests are reported `XFAIL` (the O1a alignment test, the O1b
script-lock check, and the O2 scenario). Every other test passes, and none is
`XPASS`.

### Green (EP-M2)

```bash
uv lock && uv lock --script scripts/upload_release_wheels.py
git diff --stat uv.lock          # a small diff, limited to cuprum
uv sync
uv run python -c "import importlib.metadata as m; print(m.version('cuprum'))"
# 0.2.0b1
uv tree --script scripts/upload_release_wheels.py --depth 1 | grep cuprum
# cuprum v0.2.0b1
uv run pytest -q -rxX tests/workflow_contracts/test_cuprum_selection.py \
  tests/bdd/steps/test_release_wheel_upload_steps.py \
  tests/unit/test_release_gh_properties.py tests/unit/test_release_gh.py \
  tests/unit/test_gh_stub_helper.py tests/unit/utils/test_commands.py \
  tests/bdd/steps/test_commands_catalogue_steps.py \
  tests/unit/test_upload_release_wheels.py \
  tests/e2e/test_upload_release_wheels_cli.py \
  --hypothesis-show-statistics \
  | tee /tmp/green-lading-$(git branch --show-current).out
rg "cuprum beta not yet selected" tests && echo "markers remain" || echo "no markers"
```

Expected: every test passes, with no `xfail` or `xpass` entries, and the last
command prints `no markers`.

### Seeded mutations (after the EP-M2 commit)

```bash
git apply /tmp/mutation-capture-false.patch
uv run pytest -q tests/unit/test_release_gh_properties.py   # expect failure
git apply -R /tmp/mutation-capture-false.patch
git diff --exit-code && echo clean
```

Repeat for each mutation named in the `Verification plan`. The patch files are
scratch files and are never committed.

### Gates, in sequence, after each milestone

```bash
make check-fmt | tee /tmp/check-fmt-lading-$(git branch --show-current).out
make typecheck | tee /tmp/typecheck-lading-$(git branch --show-current).out
make lint      | tee /tmp/lint-lading-$(git branch --show-current).out
make test      | tee /tmp/test-lading-$(git branch --show-current).out
make markdownlint | tee /tmp/markdownlint-lading-$(git branch --show-current).out
make nixie     | tee /tmp/nixie-lading-$(git branch --show-current).out
```

### Distribution smoke (EP-M3)

The stub directory can be any scratch directory under `/tmp`; it holds only the
stub. Each command runs `run_gh` against a stub `gh` that prints to both
streams and exits 3:

```bash
STUB=$(mktemp -d); printf '#!/bin/sh\necho "stub $*"; echo diag >&2; exit 3\n' > "$STUB/gh"
chmod +x "$STUB/gh"; mkdir "$STUB/config"
SMOKE='import sys, cuprum; sys.path.insert(0, "scripts"); import release_gh as r
print(cuprum.is_rust_available(), r.run_gh(["release", "view"]))'
ISOLATE="env -u GH_TOKEN -u GITHUB_TOKEN -u CUPRUM_STREAM_BACKEND GH_CONFIG_DIR=$STUB/config GH_HOST=stub.invalid PATH=$STUB:$PATH"

# Native wheel (the default selection on manylinux x86-64):
$ISOLATE uv run --no-project --python 3.13 --with cuprum==0.2.0b1 python -c "$SMOKE"
# True CommandOutcome(exit_code=3, stdout='stub release view\n', stderr='diag\n')

# Pure-Python wheel, selected by URL (copy the py3-none-any URL from uv.lock):
$ISOLATE uv run --no-project --python 3.13 \
  --with "cuprum @ <py3-none-any wheel URL from uv.lock>" python -c "$SMOKE"
# False CommandOutcome(exit_code=3, stdout='stub release view\n', stderr='diag\n')
```

## Validation and acceptance

Acceptance is behavioural:

- `make test` passes.
  - The new scenario "The standalone uploader attaches a wheel with the pinned
    cuprum" passes. So do the existing e2e tests, which cover repository mode.
  - The O1a, O1b, and O2 tests fail before EP-M2 (recorded as strict `XFAIL` in
    EP-M1) and pass after it.
- `uv tree --script scripts/upload_release_wheels.py --depth 1` lists
  `cuprum v0.2.0b1`, and
  `uv run python -c "import importlib.metadata as m; print(m.version('cuprum'))"`
  prints `0.2.0b1`.
- `uv lock --check` and
  `uv lock --script scripts/upload_release_wheels.py --check` both exit 0.
- The distribution smoke prints `True ...` for the native wheel and
  `False ...` for the pure-Python wheel. Each shows `exit_code=3` and both
  streams captured.
- No test or smoke step reaches a real `gh` or a real account. The stub records
  show every call; the `which` guard, the token and configuration isolation,
  and the invalid host all hold.

The BDD specification that drives EP-M1 and EP-M2 lives in
`tests/bdd/features/release_wheel_upload.feature`:

```gherkin
Feature: Standalone release wheel upload through the cuprum beta
  The release workflow runs the uploader with `uv run --script`, which builds
  its environment from the script's inline metadata and lockfile rather than
  from the repository lock. That environment must hold the pinned cuprum, and
  gh must only ever be the recording stub.

  Background:
    Given a dist directory containing "lading-0.0.0-py3-none-any.whl"

  Scenario: The standalone uploader attaches a wheel with the pinned cuprum
    Given a recording gh stub that exits 0
    When the uploader runs standalone for tag "v0.0.0-stub"
    Then the uploader exits 0
    And gh was called exactly once with "release upload v0.0.0-stub" and the wheel and "--clobber"
    And the environment that ran holds the cuprum version pinned in pyproject.toml

  Scenario: A rejected upload reports gh's diagnostic
    Given a recording gh stub that writes "HTTP 422: asset exists" to stderr and exits 1
    When the uploader runs standalone for tag "v0.0.0-stub"
    Then the uploader exits 1
    And the uploader's error line contains "HTTP 422: asset exists"

  Scenario: gh inherits the environment but never credentials
    Given a recording gh stub that exits 0
    And the parent environment holds a GH_TOKEN and the sentinel "LADING_STUB_SENTINEL"
    When the uploader runs standalone for tag "v0.0.0-stub"
    Then gh saw the sentinel "LADING_STUB_SENTINEL"
    And gh saw no GH_TOKEN or GITHUB_TOKEN and an empty GH_CONFIG_DIR
    And no recorded gh call is "release create" or "release edit"
```

Quality criteria:

- **Tests:** `make test` is green. The targeted green command is green, with no
  xfail entries and no surviving marker.
- **Verification:** O1-O5 are discharged as described. The O1b, O2, and O4
  seeded mutations were observed failing, then reversed, leaving a clean tree.
- **Lint, type checking, and formatting:** `make lint`, `make typecheck`, and
  `make check-fmt` are green. After the documentation changes, so are
  `make markdownlint` and `make nixie`.
- **Security:** no credentials, stored login, or real host reaches any child
  process during validation. The catalogue still allowlists only `gh`. The
  release job installs only hash-locked packages.

## Idempotence and recovery

Every step can be repeated. For a fixed input, `uv lock`, `uv lock --script`,
and `uv sync` are idempotent. Stub directories live under pytest's temporary
directories or a `mktemp -d` scratch directory.

Seeded mutations happen only after a commit, and only through patch files that
are applied and then reversed. Confirm with `git diff --exit-code`; never use
`git checkout --` on a file with uncommitted work.

If the standalone scenario fails with a network error, rerun it once. If it
fails again, stop under the network tolerance.

To abandon the work, revert the milestone commits in reverse order: EP-M3, then
EP-M2, then EP-M1, then EP-M0. Each milestone is a coherent plateau.

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

Scratch lock probe, using a copy of `pyproject.toml` and `uv.lock` with
`"cuprum==0.2.0b1"`:

```plaintext
$ uv lock
Resolved 62 packages in 557ms
Updated cuprum v0.1.0 -> v0.2.0b1
```

Scratch standalone probe, using a copy of both scripts with the inline pin
changed:

```plaintext
$ uv tree --script upload_release_wheels.py --depth 1 | tail -1
cuprum v0.2.0b1
$ uv lock --script upload_release_wheels.py && uv lock --script upload_release_wheels.py --check
Resolved 9 packages in 0.47ms          (exit 0)
$ uv run --script -v upload_release_wheels.py --help
DEBUG Found existing lockfile for script
```

The current `run_gh` under the beta, and the beta forms, run against a stub
`gh` that prints `gh stub $*` to stdout and `warn` to stderr, then exits 3:

```plaintext
TypeError scoped() got an unexpected keyword argument 'allowlist'
scoped(config: ScopeConfig | None = None, *, catalogue: ProgramCatalogue | None = None)
SafeCmd.run_sync(self, *, output=None, timeout=None, context=None, stdin=None)
new forms -> 3 'gh stub release view\n' 'warn\n' CommandResult
```

The existing suite run against the beta overlay:

```bash
uv run --with cuprum==0.2.0b1 pytest -q tests/unit/utils/test_commands.py \
  tests/bdd/steps/test_commands_catalogue_steps.py \
  tests/unit/test_upload_release_wheels.py \
  tests/e2e/test_upload_release_wheels_cli.py
```

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

The dependency after EP-M2, identical on both paths:

```toml
# pyproject.toml, [project] dependencies
"cuprum==0.2.0b1",
```

```python
# scripts/upload_release_wheels.py, PEP 723 block
# dependencies = ["cuprum==0.2.0b1", "cyclopts>=3"]
```

These are locked by `uv.lock` and by `scripts/upload_release_wheels.py.lock`
respectively.

The adapter module after EP-M2, `scripts/release_gh.py`. The signature and the
`CommandOutcome` fields are unchanged from today:

```python
from cuprum import (
    Program,
    ProgramCatalogue,
    ProjectSettings,
    RunOutputOptions,
    scoped,
    sh,
)

GH = Program("gh")
RELEASE_CATALOGUE = ProgramCatalogue(projects=(...,))  # gh alone, as today


@dc.dataclass(frozen=True, slots=True)
class CommandOutcome:
    exit_code: int
    stdout: str = ""
    stderr: str = ""


def run_gh(arguments: cabc.Sequence[str]) -> CommandOutcome:
    with scoped(catalogue=RELEASE_CATALOGUE):
        command = sh.make(GH, catalogue=RELEASE_CATALOGUE)(*arguments)
        result = command.run_sync(output=RunOutputOptions(capture=True))
    return CommandOutcome(
        exit_code=result.exit_code,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )
```

`scripts/release_wheel_upload.py` keeps `UploadRunner`, `upload_wheels`, and
`Dependencies`, and it imports `CommandOutcome` and `run_gh` from `release_gh`.

The shared stub helper, `tests/helpers/gh_stub.py`:

```python
@dc.dataclass(frozen=True, slots=True)
class GhCall:
    argv: tuple[str, ...]
    saw_gh_token: bool
    saw_github_token: bool
    sentinel: str | None
    gh_config_dir: str | None
    virtual_env: str | None


@dc.dataclass(frozen=True, slots=True)
class GhStub:
    bin_directory: Path
    record: Path          # JSON lines, appended once per call

    def calls(self) -> tuple[GhCall, ...]: ...


def install_gh_stub(directory: Path, *, exit_code: int = 0, stderr: str = "") -> GhStub: ...


def isolated_environment(stub: GhStub, *, sentinel: str | None = None) -> dict[str, str]:
    """Return a child environment in which ``gh`` can only be the stub.

    Prepends the stub directory to PATH and checks it with ``shutil.which``,
    removes GH_TOKEN, GITHUB_TOKEN, VIRTUAL_ENV, and UV_* variables other than
    UV_CACHE_DIR, and sets GH_CONFIG_DIR (an empty directory), GH_HOST, and
    GH_PROMPT_DISABLED.
    """


def run_uploader(
    mode: typ.Literal["repository", "standalone"],
    directory: Path,
    environment: cabc.Mapping[str, str],
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    """Run the uploader as the workflow does, with a 45-second timeout."""
```

The new test modules:

- `tests/unit/test_release_gh.py`: the moved `run_gh` cmd-mox test.
- `tests/unit/test_release_gh_properties.py`: capture fidelity (O4).
- `tests/unit/test_gh_stub_helper.py`: the helper's isolation guarantees (O3).
- `tests/workflow_contracts/test_cuprum_selection.py`: selection alignment and
  lock freshness (O1a, O1b).
- `tests/bdd/features/release_wheel_upload.feature` and
  `tests/bdd/steps/test_release_wheel_upload_steps.py`: standalone behaviour
  (O2, O3).

The configuration changes:

- `typos.local.toml` excludes `scripts/upload_release_wheels.py.lock`.
- `[tool.mutmut] also_copy` gains `"uv.lock"`.

No new runtime or development dependency is added. `hypothesis`, `pytest-bdd`,
`pytest-timeout`, `cmd-mox`, and `pyyaml` are already development dependencies.

## Revision note

- 2026-09-25, initial draft. It drew on Wyvern reconnaissance, PyPI and uv
  documentation research through Firecrawl, and local probes.
- 2026-09-25, revision 1, after the community-of-experts review.
  - **What changed:**
    - D4 is reversed: the plan now adds a script lockfile.
    - New milestone EP-M0 extracts `scripts/release_gh.py`.
    - The red strategy now uses explicit `@scenario` bindings and strict
      `xfail` with `raises=`, and it lands characterization tests green.
    - A shared, hardened stub helper covers both e2e and BDD.
    - O1 is split into alignment (no version literal) and lock freshness.
    - The BDD feature is limited to standalone mode, and its version check reads
      the environment that ran.
    - The seeded mutations are applied only after the commit, from patch files.
    - Timeouts are real.
    - The mutmut and typos configuration are updated.
    - D8 (the release stance, pending confirmation) and D9 (manual bumps) are
      added.
    - The CI description is corrected.
  - **Why:** the review found that the red commit would fail its own gates, that
    a file-size constraint could not be met, that a restore step would destroy
    work, that the no-publication proof could be defeated by a stored `gh`
    login, and that the standalone path resolves a different cyclopts major
    version.
  - **Effect on remaining work:** one extra refactor milestone and about eight
    more files. The scope tolerance is raised to match.
- 2026-09-25, revision 2, after the maintainer's decisions.
  - **What changed:**
    - D2 now uses `scoped(catalogue=...)` for `run_gh` and the catalogue
      tests. The roadmap's `ScopeConfig` wording is an accepted deviation,
      corrected in EP-M3.
    - D4 (the script lockfile) is approved.
    - D8 is reversed: no final lading 0.x release until cuprum 0.2.0 final
      ships.
    - Issue #286, raised at the maintainer's request, mandates adopting the
      cuprum release process from leynos/cuprum#488 once it merges. It is
      referenced as remaining work.
    - EP-M2 and EP-M3 gain the matching docstring, design, developers'
      guide, users' guide, and roadmap wording changes.
  - **Why:** the maintainer answered the three decisions the PR raised.
  - **Effect on remaining work:** there are no new milestones. `run_gh` needs
    one fewer import, and EP-M3 has a few more wording edits. Implementation
    waits for an explicit go-ahead.
