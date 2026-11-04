# Cyclopts version comparison evidence

This directory holds the executable evidence for
[the Cyclopts 3.24.0 and 4.25.3 comparison](../../cyclopts-4-version-comparison.md).
It instruments the dependency, not `lading`, and is not part of the package,
the test suite, or any lint target. It sits under `docs/`, which the Python
source discovery and Ruff configuration exclude, and `make test` passes
`--ignore=docs/evidence`, so pytest never imports these modules even with
`--doctest-modules` enabled.

## Contents

- `cli_fixture.py` builds a minimal `cyclopts.App` whose commands return the
  outcome shapes `lading.cli` handles: a long summary string, `None`, a
  non-zero integer, both booleans, a `SystemExit` carrying a message, a typed
  argument, plus help, an unknown command, and an empty invocation.
- `probe.py` runs each case four ways, recording the outcome and both streams:
  an in-process call with the default result action, an in-process call with
  `result_action="return_value"`, a naive subprocess wrapper that discards the
  return value, and a subprocess wrapper that applies `lading`'s dispatcher
  mapping.
- `results-cyclopts-3.24.0.json` and `results-cyclopts-4.25.3.json` are the
  records each arm produced. They are the raw data behind the comparison
  tables, and a diff between them is the machine-checkable result.

## Reproducing one arm

Run the probe with the interpreter of the environment under test, from this
directory:

```shell
/tmp/venv-cpNNN/bin/python probe.py > results-cyclopts-<version>.json
```

`probe.py` imports `cli_fixture` from its own directory, starts its
subprocesses with the same interpreter, and sets its own rendering controls
before building an app: `COLUMNS=40`, `LINES=24`, `TERM=dumb`, and
`NO_COLOR=1`. It removes `FORCE_COLOR` from the environment because Rich treats
that variable's presence, including `FORCE_COLOR=0`, as a request for colour.
Each record echoes those controls under `controls`, and names both the
interpreter and the resolved Cyclopts version. Compare the two records with
`diff` or `jq`; ANSI escapes survive `repr` and appear as `\x1b`.
