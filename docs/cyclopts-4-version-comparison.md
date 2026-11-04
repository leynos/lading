# Cyclopts 3.24.0 and 4.25.3: controlled comparison

## Purpose

Issue [#340][issue-340] records 19 test failures when Dependabot pull request
[#316][dependabot-316] changed only `uv.lock`, resolving `cyclopts` from 3.24.0
to 4.25.3. Before attributing those failures to Cyclopts, the same tests have
to run on one fixed base under both resolutions, with the interpreter, working
directory, and rendering inputs held constant.

This document records that comparison and the recipe used to repeat it. It is
evidence for `lading` at the base commit named below, produced on the Python
version this repository supports. It is not a certification for another
project's CLI, tests, or interpreter baseline.

## Fixed base

- Repository: `leynos/lading`
- Base commit: `11ad76ff2460ec1dd5b93ea75a9396da7aef68cb`
- Worktree: a clean copy of that commit at `/tmp/lading-340-base`
- Interpreter: CPython 3.14.7 in both arms
- Test runner: pytest 9.1.1 with `--doctest-modules`
- Python 3.13 was not used: the project supports 3.14, and forcing an older
  interpreter would compare Python's behaviour as well as Cyclopts's
- Fixture and probe:
  [evidence/cyclopts-4-version-comparison/](evidence/cyclopts-4-version-comparison/README.md)

## Arms

Table 1 lists the two environments. Both were created with
`uv sync --locked --python 3.14.7` against the base commit, so they share the
same dependency tree. The treatment arm differs only in the Cyclopts
distribution.

Table 1: comparison arms.

| Arm       | Environment       | Cyclopts                                                                                     |
| --------- | ----------------- | -------------------------------------------------------------------------------------------- |
| control   | `/tmp/venv-cp324` | 3.24.0, resolved from the committed `uv.lock`                                                |
| treatment | `/tmp/venv-cp425` | 4.25.3, installed with `uv pip install --python /tmp/venv-cp425/bin/python cyclopts==4.25.3` |

`pyproject.toml` and `uv.lock` were not edited in either arm. The treatment
environment was verified immediately before and after its suite run with the
same interpreter that ran the tests.

Installing one distribution can also move its dependencies, so the treatment
was checked against the control by package set rather than by Cyclopts version
alone. Both environments hold the same 60 distributions and the only line that
differs is the Cyclopts pin:

```shell
NO_COLOR=1 uv pip list --python /tmp/venv-cp324/bin/python --format freeze \
  | sort > /tmp/cp324-freeze.txt
NO_COLOR=1 uv pip list --python /tmp/venv-cp425/bin/python --format freeze \
  | sort > /tmp/cp425-freeze.txt
diff /tmp/cp324-freeze.txt /tmp/cp425-freeze.txt
```

```text
11c11
< cyclopts==3.24.0
---
> cyclopts==4.25.3
```

No other package was added, removed, or changed in version, so a failure that
appears only in the treatment arm is attributable to Cyclopts and not to a
transitive dependency.

## Environment controls

Both arms ran with identical controls:

```shell
env -u PYTHONPATH -u VIRTUAL_ENV -u UV_PROJECT_ENVIRONMENT -u FORCE_COLOR \
  PYTHONHASHSEED=0 COLUMNS=80 LINES=24 TERM=dumb \
  NO_COLOR=1 PYTHONDONTWRITEBYTECODE=1 \
  /tmp/venv-cpNNN/bin/python -m pytest -q -p no:randomly \
  -p no:cacheprovider --tb=no -rf --doctest-modules
```

- `PYTHONHASHSEED=0` and `-p no:randomly` remove hash and collection-order
  variation.
- `COLUMNS`, `LINES`, `TERM`, and `NO_COLOR` pin the inputs Rich reads when it
  renders a value or a diagnostic. `FORCE_COLOR` is explicitly unset rather
  than set to `0`: Rich treats the variable's presence as a request for colour,
  so `FORCE_COLOR=0` would override `NO_COLOR` and colour the output.
- `UV_PROJECT_ENVIRONMENT` is unset because a child `uv` process that adopted
  the environment under test could resynchronize it to `uv.lock` mid-run.
  Invoking the virtual environment's interpreter directly removes that exposure
  and keeps the interpreter that ran the tests unambiguous.
- `-p no:cacheprovider` stops either arm writing shared pytest state.

## Micro-fixture comparison

Issue #340 asks for a tiny CLI fixture covering help, an unknown command, an
invalid value, successful string, `None` and `int` results, and a non-zero
status. The fixture used here adds the two `bool` outcomes and a
`SystemExit(str)`, because those complete the result mapping that
`lading.cli._dispatch_and_print` owns.

Each case is probed four ways: an in-process call with the app's default result
action; an in-process call with `result_action="return_value"`; a subprocess
whose wrapper discards the app's return value, as a naive process caller did
before the fix; and a subprocess whose wrapper applies the dispatcher's mapping.

The fixture, the probe, and both raw records live in
[evidence/cyclopts-4-version-comparison/](evidence/cyclopts-4-version-comparison/README.md);
`results-cyclopts-3.24.0.json` and `results-cyclopts-4.25.3.json` are the data
Table 2 summarizes, and diffing them shows every row behind it.

Table 2: fixture behaviour by outcome shape at `COLUMNS=40`.

| Probe                                    | 3.24.0                                                                 | 4.25.3                                                                       |
| ---------------------------------------- | ---------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| in-process, `summary`, default action    | returns the string; writes nothing                                     | raises `SystemExit(0)`; writes a wrapped summary to stdout                   |
| subprocess, naive caller, `summary`      | exits 0; writes nothing, because the return value is discarded         | exits 0; writes a wrapped summary to stdout                                  |
| subprocess, dispatcher, default action   | exits 0; writes the summary once, unwrapped                            | exits 0; writes a wrapped summary, because Cyclopts prints before it returns |
| subprocess, dispatcher, `return_value`   | rejected with `TypeError: unexpected keyword argument 'result_action'` | exits 0; writes the summary once, unwrapped                                  |
| invalid value                            | exits 1; error panel on stdout                                         | exits 1; error panel on stderr                                               |
| unknown command                          | exits 1; error panel on stdout                                         | exits 1; error panel on stderr                                               |
| help, and missing subcommand             | exits 0; help on stdout                                                | exits 0; help on stdout, with different column widths                        |
| `SystemExit("fatal: workspace missing")` | exits 1; message on stderr                                             | identical                                                                    |
| `True` / `False` result                  | returns the boolean                                                    | exits 0 / exits 1                                                            |
| `int` result `3`                         | returns `3`; a naive process caller exits 0                            | exits 3                                                                      |

At `COLUMNS=40`, the 4.25.3 default action writes the long summary as four
wrapped lines, beginning `Found 1 staging directory under` and breaking inside
the path segment. With `FORCE_COLOR=1` the same line carries ANSI escape
sequences such as `\x1b[1;36m`. Under `result_action="return_value"` followed
by a plain `print`, the output is the single unwrapped line with no escape
sequences, on both the 3.24.0 control and the 4.25.3 treatment.

The `TypeError` row matters for the migration: `result_action` does not exist
in Cyclopts 3.24.0, so the fix cannot be adopted while remaining on the old
release. The upgrade and the explicit result action belong to one change.

## Full-suite results

Table 3: complete suite result per arm.

| Arm       | Result                                         | Cyclopts resolved after the run |
| --------- | ---------------------------------------------- | ------------------------------- |
| control   | 1 failed, 1533 passed, 37 skipped in 353.31 s  | 3.24.0                          |
| treatment | 20 failed, 1514 passed, 37 skipped in 155.66 s | 4.25.3                          |

Wall-clock durations vary with host load and are recorded only for provenance;
the pass and fail counts are the comparison.

The treatment arm's 20 failures contain exactly one that also fails on the
control:
`tests/unit/test_release_gh.py::test_run_gh_returns_the_diagnostic_it_captured`.
It reports an unfulfilled `cmd-mox` expectation for `gh('release', 'view')`
and fails identically under both Cyclopts versions, so it is a harness result
of this invocation and not attributable to the dependency. Excluding it leaves
**19 failures caused by the Cyclopts change**, the count issue #340 recorded
from Continuous Integration (CI) on Python 3.13.15.

Table 4: the 19 attributable failures by mechanism.

| Mechanism                                     | Count | Tests                                                                                                                                                                                                                                                                                                                            |
| --------------------------------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Direct `cli.app(...)` now raises `SystemExit` | 6     | `tests/unit/test_cli_clean.py` (four cases), `tests/unit/test_cli_entrypoints.py::test_cyclopts_invoke_uses_workspace_env`, `tests/unit/test_cli_entrypoints.py::test_publish_via_app_matches_across_config_branches`                                                                                                            |
| Diagnostic moved from stdout to stderr        | 5     | `tests/unit/test_cli_dispatch.py::test_main_handles_invalid_subcommand`, `tests/unit/test_cli_entrypoints.py::test_bump_command_validates_version`, `tests/e2e/test_upload_release_wheels_cli.py::test_the_tag_is_required` (two cases), `tests/bdd/steps/test_common_steps.py::test_bumping_with_an_invalid_version_fails_fast` |
| Returned string re-rendered and wrapped       | 8     | `tests/bdd/steps/test_common_steps.py` publish-plan scenarios                                                                                                                                                                                                                                                                    |

### In-process result handling

The clean-command tests read the returned summary:

```text
tests/unit/test_cli_clean.py:39: in test_the_clean_command_is_registered
E   SystemExit: 0
```

Cyclopts 4's default `print_non_int_sys_exit` renders the value and exits, so
the caller never receives it. This is the failure issue #340 describes as a
direct-call contract regression.

### Diagnostic stream

The same assertions fail because the message is now on stderr:

```text
E   AssertionError: the unknown-command error must be printed
E   assert 'Unknown command' in ''
E    +  where '' = CaptureResult(out='', err='╭─ Error ───── ...').out
```

The diagnostic text itself also changed slightly between releases: 3.24.0
renders `Invalid value for "COUNT"` and 4.25.3 renders
`Invalid value for COUNT`. Only fragments `lading` itself promises should be
asserted literally; Cyclopts's own wording is matched on a distinctive part.

### Return-value rendering

The publish-plan scenario parses the first line of the returned plan:

```text
E   AssertionError: the plan must open by naming the workspace root
E   assert 'Publish plan for' == 'Publish plan...ublish_comma0'
E     - Publish plan for /tmp/pytest-of-leynos/pytest-.../test_running_the_publish_comma0
E     + Publish plan for
```

The workspace path moved to a later line because Rich wrapped the value. This
is the machine-consumed output contract that
[ADR-011](adr/011-cyclopts-4-cli-result-and-diagnostic-boundary.md) preserves
by returning the raw value and printing it once.

## Limitations

- The fixture is a stand-in for the boundary's outcome shapes, not for
  `lading`'s commands. It shows what the library does; the full-suite table
  shows what that does to this repository.
- Help rendering differs cosmetically between the releases, including column
  widths and spacing inside the command list. Help stays on stdout in both, so
  the stream contract is unchanged, but a test that compares the whole help
  text verbatim would need normalizing.
- One test fails identically on both arms under this invocation. It remains a
  known limitation of the harness comparison rather than evidence about
  Cyclopts, and it is reported rather than filtered out.
- A full-suite `uv run --no-sync pytest` invocation re-resolved the project
  environment to `uv.lock` during the run: the interpreter resolved 4.25.3
  before it and 3.24.0 afterwards. Running each test directory separately did
  not reproduce that, so the cause is not established. The recorded arms
  therefore bypass `uv run` and print the resolved version after the suite; a
  changed version marks the run void rather than a result.
- Cyclopts 5.x is not compared here. The standalone uploader resolves its own
  environment and currently selects 5.0.0; it is covered by its own
  tag-required tests and is not assumed equivalent to 4.25.3.

## Reproducing

1. Create a clean worktree at the base commit and, in it, create the two
   environments:

   ```shell
   UV_PROJECT_ENVIRONMENT=/tmp/venv-cp324 uv sync --locked --python 3.14.7
   UV_PROJECT_ENVIRONMENT=/tmp/venv-cp425 uv sync --locked --python 3.14.7
   uv pip install --python /tmp/venv-cp425/bin/python 'cyclopts==4.25.3'
   ```

2. Confirm each environment resolves the interpreter and the version its arm
   names, and that the treatment moved no other distribution:

   ```shell
   /tmp/venv-cpNNN/bin/python -c \
     'import sys, cyclopts; print(sys.version.split()[0], cyclopts.__version__)'
   ```

   ```shell
   NO_COLOR=1 uv pip list --python /tmp/venv-cp324/bin/python --format freeze \
     | sort > /tmp/cp324-freeze.txt
   NO_COLOR=1 uv pip list --python /tmp/venv-cp425/bin/python --format freeze \
     | sort > /tmp/cp425-freeze.txt
   diff /tmp/cp324-freeze.txt /tmp/cp425-freeze.txt
   ```

   The first command must print `3.14.7 3.24.0` and `3.14.7 4.25.3`
   respectively. The diff must contain only the `cyclopts` line; any other
   difference means the arms no longer isolate the dependency.

3. Run the published probe and the suite, one arm at a time. The probe sets its
   own rendering controls (`COLUMNS=40`, `LINES=24`, `TERM=dumb`, `NO_COLOR=1`)
   and removes `FORCE_COLOR` before it constructs an app, so it records the
   same conditions from any shell. From
   `docs/evidence/cyclopts-4-version-comparison/`:

   ```shell
   /tmp/venv-cp324/bin/python probe.py > /tmp/results-3.24.0.json
   /tmp/venv-cp425/bin/python probe.py > /tmp/results-4.25.3.json
   ```

   Compare those records with the committed ones, then run the suite with the
   controls above. Print the resolved version again afterwards; a changed value
   means some process resynchronized the environment and the run is void rather
   than a result.

## References

- Issue: <https://github.com/leynos/lading/issues/340>
- Superseded Dependabot pull request:
  <https://github.com/leynos/lading/pull/316>
- [ADR-011: Preserve the CLI result and diagnostic-stream boundary under
  Cyclopts 4](adr/011-cyclopts-4-cli-result-and-diagnostic-boundary.md)

[issue-340]: https://github.com/leynos/lading/issues/340
[dependabot-316]: https://github.com/leynos/lading/pull/316
