# ADR-011: Preserve the CLI result and diagnostic-stream boundary under Cyclopts 4

## Status

Accepted.

## Date

2026-11-04

## Context and problem statement

Dependabot pull request [leynos/lading#316][dependabot-316] moved `cyclopts`
from 3.24.0 to 4.25.3 in `uv.lock` alone, which the existing `cyclopts>=2.9`
requirement admitted. The lint-test job on that head reported 19 failures, so
the upgrade was a behavioural boundary rather than a routine lock refresh.

Two upstream defaults changed in Cyclopts 4 and both are observable at
`lading`'s own application boundary.

- **Result handling.** `App.__call__` defaults to
  `result_action="print_non_int_sys_exit"`. It renders a command's return value
  through Rich and calls `sys.exit`. In Cyclopts 3 the app returned the value
  to its caller. Direct in-process calls such as `cli.app(["bump", ...])`
  therefore began raising `SystemExit(0)` on success, and every reader of the
  returned summary string broke.
- **Diagnostic streams.** Parse and validation errors are written to the
  app's error console, which defaults to stderr. Cyclopts 3 wrote them to
  stdout. Help remains on the normal console, which defaults to stdout.

`lading` already had an owner for both concerns: `_dispatch_and_print` in
`lading/cli.py` caught `SystemExit`, printed a string result once with `print`,
and mapped it to an exit code. That function and `main()` were correct; the app
was no longer cooperating with them.

The pathological failure was not only the exit status. Rich re-rendered the
returned summary, wrapping long lines and adding ANSI colour, so
machine-consumed output changed shape. A caller that parsed the first line of
the publish plan saw `Publish plan for` with the workspace path moved to a
later line, and a test that compared summary text saw escape sequences. This is
a regression in a public output contract, not merely a moved assertion.

## Decision Drivers

- The published wheel's CLI contract must not change silently under a
  dependency upgrade.
- Machine-consumed output must stay plain, unwrapped, and printed exactly
  once, whichever process entry point a caller uses.
- Diagnostics belong on stderr so a pipeline that reads stdout sees only
  output; help and summaries belong on stdout.
- Behaviour verified by the suite must be the behaviour that ships, including
  the standalone release uploader, which resolves its own environment.
- A dependency floor should name the release the repository actually tests.

## Decision

### Return-value boundary

`lading.cli.app` is constructed with `result_action="return_value"`.
`lading/cli.py:106` states the ownership in a comment so the choice is not
mistaken for a default.

```python
app = App(
    help="Manage Rust workspaces with the lading toolkit.",
    result_action="return_value",
)
```

The division of responsibility is:

| Layer                 | Owns                                                         |
| --------------------- | ------------------------------------------------------------ |
| `cyclopts.App`        | Parsing, validation, help rendering, and diagnostics         |
| `_dispatch_and_print` | Printing a non-`None` result once, and mapping to a status   |
| `main()`              | Bootstrap, configuration, workspace errors, and `SystemExit` |

`_dispatch_and_print` and `main()` keep their responsibility under this
decision. The mapping they apply is part of the contract. A `bool` result is
checked before `int` because Python makes `bool` a subclass of `int`; the
mapping matches Cyclopts's own, so `True` succeeds and `False` fails:

| App outcome               | Exit status | Streams                              |
| ------------------------- | ----------- | ------------------------------------ |
| Returns `None`            | 0           | none                                 |
| Returns a non-zero `int`  | that `int`  | none                                 |
| Returns `0`               | 0           | none                                 |
| Returns `True`            | 0           | none                                 |
| Returns `False`           | 1           | none                                 |
| Returns a `str`           | 0           | the string once on stdout, unwrapped |
| Raises `SystemExit(None)` | 0           | none                                 |
| Raises `SystemExit(int)`  | that `int`  | whatever Cyclopts already wrote      |
| Raises `SystemExit(str)`  | 1           | the string once on stderr            |

A successful command therefore prints exactly once, in `lading`'s own
formatting, with no Rich panel and no ANSI escape sequences. The
`_PublishExecutionOptions` table and the publish-plan text documented in the
[users' guide](../users-guide.md) remain accurate.

### Diagnostic stream policy

Adopting the upstream convention rather than restoring the old one was
deliberate. stderr for diagnostics is the convention a caller expects, and
Cyclopts 4's separate consoles express it directly.

- Diagnostics, including `Unknown command`, `Invalid version argument`, and
  missing-required-argument errors, are asserted on **stderr only**.
- Help and usage for a missing subcommand stay on **stdout**.
- Test assertions never merge the two streams. They assert the diagnostic on
  stderr and assert that it does **not** appear on stdout, so a regression that
  duplicated it would fail.
- Text is asserted only for messages `lading` promises. Cyclopts's own
  diagnostics (`Unknown command`, `requires an argument`) are matched on a
  distinctive fragment rather than the full sentence. Rich's box drawing and
  colour are removed by `tests/helpers/cli_diagnostics.normalize_diagnostic`,
  so an assertion does not depend on the terminal width or `FORCE_COLOR`.

| Message                          | Owner    | Promised text                               |
| -------------------------------- | -------- | ------------------------------------------- |
| Unknown subcommand               | Cyclopts | contains `Unknown command` and the token    |
| Invalid version                  | lading   | `Invalid version argument '<value>'`        |
| Missing `--tag`                  | Cyclopts | contains `requires an argument` and `--tag` |
| Missing subcommand usage         | Cyclopts | contains `Usage`, on stdout                 |
| Workspace/configuration failures | lading   | unchanged; written by `main()` to stderr    |

### Dependency constraints

Both dependency paths state the floor this decision was verified against.

- `pyproject.toml` declares `cyclopts>=4.25.3,<5`. The upper bound keeps the
  repository on the major whose behaviour the suite pins; `4.25.3` is the last
  4.x release, so `uv.lock` resolves exactly it.
- `scripts/upload_release_wheels.py` declares `cyclopts>=4.25.3` in its PEP
  723 block. Its committed lock, `uv lock --script`, continues to resolve
  independently and currently selects **5.0.0**. That is the same independence
  [ADR-006](adr-0006-align-cuprum-selection-across-dependency-paths.md) records
  for the standalone path: only cuprum is required to agree across paths, and
  both paths are exercised by the tag-required test, so the repository covers
  4.25.3 and 5.0.0 rather than assuming one implies the other.

Dependabot pull request #316 is superseded by this change and should be closed
once this lands.

Two further PEP 723 scripts import Cyclopts and are deliberately not moved.
`scripts/duplication_gate.py` pins `cyclopts==4.25.2` as part of its own pinned
toolchain, and `scripts/format_markdown.py` retains its own floor. Both are
tool scripts that never import `lading`; neither participates in the CLI
contract above, and changing either would widen this decision without testing
anything it asserts.

### Contract exceptions

Three exits bypass the table above and are part of the contract as stated.

- **Bootstrap `SystemExit`.** `_select_runner` and `_validate_workspace_value`
  raise `SystemExit(str)` before dispatch. `main()` does not catch it, so the
  interpreter prints the message to stderr and exits 1.
- **Missing subcommand.** `main()` prints usage and returns **2**, the
  conventional status for a missing subcommand, rather than the 0 Cyclopts
  would return for an empty invocation.
- **Cancellation.** `KeyboardInterrupt` returns **130** after printing
  `Operation cancelled by user.` to stderr.

## Consequences

- The CLI's return, stream, and exit-status contracts are explicit and pinned
  by executable tests rather than by a library default.
- `tests/unit/test_cyclopts_contract.py` describes the upstream behaviour
  with a throwaway app, so a future Cyclopts upgrade fails one small,
  self-describing test instead of 36 diffuse ones.
  `tests/unit/test_cli_dispatch.py` pins the dispatcher's ten outcome cases, and
  `tests/e2e/test_cli_entrypoint_contract.py` pins the same streams through
  `python -m lading.cli` and the installed `lading` script, including a
  successful summary that must reach stdout unwrapped and once.
- A future `cyclopts` 5.x adoption in the repository is a deliberate decision
  that must move the upper bound and reconcile the contract test, not a
  resolution the solver performs.
- The standalone uploader still resolves a different major. That is visible in
  its committed lock and is covered by its own tests; it is not silently
  assumed equivalent.
- This ADR records a policy, not a new abstraction. No port or adapter was
  introduced: `lading/cli.py` remains the driving adapter, and the boundary it
  already owned is now configured to match.

## References

- [Cyclopts 3.24.0 and 4.25.3: controlled comparison][comparison]
- [Cyclopts 4.25.3 calling documentation][cyclopts-calling]
- [Cyclopts 4.25.3 packaging guidance][cyclopts-packaging]
- [ADR-005: Draft-then-publish releases with a standalone wheel
  uploader](adr-0005-release-wheel-publication.md)
- [ADR-006: Align cuprum selection across dependency paths](adr-0006-align-cuprum-selection-across-dependency-paths.md)

[comparison]: ../cyclopts-4-version-comparison.md
[cyclopts-calling]: https://github.com/BrianPugh/cyclopts/blob/v4.25.3/docs/source/app_calling.rst
[cyclopts-packaging]: https://github.com/BrianPugh/cyclopts/blob/v4.25.3/docs/source/packaging.rst
[dependabot-316]: https://github.com/leynos/lading/pull/316
