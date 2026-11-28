# ADR-013: Show documentation rewrite diffs during bump dry runs

## Status

Accepted.

## Date

2026-11-27.

## Context and problem statement

`lading bump --dry-run` lists the manifests, documentation files, and lockfiles
a bump would change. For manifests the list is usually enough, but for
documentation it is not: the rewrite edits free-form TOML snippets that a human
wrote for readers, and only the resulting text shows whether the edit stayed
inside the intended snippet.

During the ortho-config 0.10.0 release with lading 0.3.0, the dry run listed
`docs/users-guide.md` as expected. Only the `git diff` after the real run
showed that lading had also inserted a `[package]` table version into an
unrelated snippet ([leynos/lading#368][issue-368]). The dry-run summary gave an
operator no way to notice that before writing.

Issue [#373][issue-373] proposed printing a unified diff per changed file, or
at least per rewritten fence, with the file name and line numbers. It also
raised a machine-readable form for CI to assert that a bump touches only the
expected snippets.

ADR-012 confines every bump write target and prepares the complete plan before
mutation. That plan already holds each target's complete proposed content, so
the preview needed by this decision is available without a second rewrite or a
second read of the workspace.

## Decision drivers

- A reviewer must be able to see the exact documentation text a bump would
  write before approving or running it.
- The default `--dry-run` output is consumed by scripts and pinned by
  snapshots; it must not change when the new behaviour is not requested.
- The diff must describe the exact write, including line-ending normalization
  and a missing final newline, or it becomes another incomplete preview.
- The interface should stay small until a consumer defines what a
  machine-readable schema must contain.

## Options considered

### Option A: Diff by default under `--dry-run`

Append the documentation diff to every dry run. This makes the information
impossible to miss, but changes the output contract for every existing caller,
including CI scripts that parse the first lines and syrupy snapshots that pin
the summary. It also makes routine dry runs noisy for workspaces with many
documentation files.

### Option B: Opt-in `--diff` that requires `--dry-run`

Add a keyword-only flag, reject it on a live run, and append the diff only when
requested. The default output stays byte-for-byte identical. A reviewer or CI
job opts in explicitly.

### Option C: Machine-readable `--format json`

Emit a structured list of changed fences. This is the strongest long-term
option for CI, but it needs a schema for fence identity, line ranges, and old
and new text, plus a decision about whether the JSON shares stdout with the
human summary. No consumer has specified those requirements yet.

| Topic                 | Option A     | Option B     | Option C       |
| --------------------- | ------------ | ------------ | -------------- |
| Default output stable | No           | Yes          | Yes            |
| Human review value    | Always       | On request   | Indirect       |
| CI contract           | Fragile text | Fragile text | Needs a schema |
| Implementation scope  | Small        | Small        | Large          |

_Table 1: Trade-offs between the diff delivery options._

## Decision outcome / proposed direction

Adopt Option B. The public surface is:

- `lading bump --dry-run --diff` appends a blank line, `Documentation diff:`,
  and one unified diff block per changed documentation file.
- `--diff` without `--dry-run` raises `BumpOptionsError` from
  `lading.commands.bump._initialize_bump_context` before any preparation stage
  runs. The CLI maps it to stderr with exit status 1, leaving stdout empty.
- `BumpOptions.diff` defaults to `False`; `BumpChanges.document_previews`
  carries the preparation-ordered previews to the renderer, which sorts them;
  `_format_result_message` is unchanged.
- `DocumentationPreview` in `lading/commands/bump_preview.py` retains the raw
  text read from disk and the exact proposed write. `bump_prepare` records one
  for every documentation update produced by
  `bump_docs._prepare_documentation_updates_with_sources`. That internal worker
  returns the exact source text the rewrite consumed, so a transient read
  failure cannot leave a write without its preview, and it proposes the write
  against that same snapshot. A preview exists only while the plan holds a
  write for the file, keyed by the same filesystem identity as the write; a
  later stage that restores the original content removes both. The exported
  `bump_docs.prepare_documentation_updates` keeps its established two-value
  rows. `BumpPlan`/`BumpChanges` carry the previews in preparation order. A
  preview exists only for a file whose rewrite proposes different text.
- `lading.commands.bump_diff.format_documentation_diff` sorts previews by
  `str(path)`, splits each text at CR and LF while keeping each terminator,
  renders `--- a/<relative path>` and `+++ b/<relative path>` headers with
  `difflib.unified_diff(..., lineterm="\n")`, strips the terminators for
  display, and emits `\ No newline at end of file` after each unterminated line.
- When the original text contains CR and the proposal does not, the block
  carries a notice between the `+++` header and the first hunk that the write
  normalizes line endings to LF.
- With no changed documentation, the appended text is
  `No documentation changes.` so the reader can distinguish "nothing changed"
  from a rendering failure.

The output is human-readable, not patch-compatible. It deliberately omits index
lines, file modes, and path quoting, and does not promise `git apply`
compatibility.

Documentation is the only diffed category. Manifests have structured edits,
README transposition is a whole-file copy, and lockfiles are machine-generated.
Each remains listed in the summary as before.

## Goals and non-goals

- Goals:
  - Show the exact documentation text a dry run would write.
  - Keep the default dry-run summary byte-for-byte unchanged.
  - Disclose line-ending normalization and a missing final newline.
  - Cover documentation only, with deterministic ordering.
- Non-goals:
  - Fixing the #368 behaviour in this decision. That rewrite policy was
    narrowed separately in #424; this diff continues to report whatever the
    policy changes.
  - A `--format json` output or any other machine-readable schema.
  - Diffs for manifests, transposed README files, or lockfiles.
  - A patch that `git apply` accepts.

## Known risks and limitations

- #368 was fixed by #424 after this decision, so the unrelated `[package]`
  insertion this diff used to expose no longer occurs. The disclosure tests
  were retired with the old policy.
- The diff's original text is what the documentation stage read. A path that a
  later stage also rewrites, such as a crate README adopted through
  transposition, keeps a preview whose added lines follow the final planned
  write, so the block still describes what a live run would write.
- Diagnostics such as `cargo metadata` and lockfile discovery messages remain
  on stderr. A terminal that merges streams may show them before the summary.
- The preview records only files whose rewrite reports a change. A file whose
  content would be rewritten to identical text is not shown, which matches the
  plan's proposal rule.

## Consequences

- Operators can review the exact documentation write before applying it.
- CI can assert on the human-readable diff text without a new schema, at the
  cost of matching text rather than structured fields.
- `lading/commands/bump_output.py` keeps `DocumentationPreview` as a runtime
  import so CrossHair resolves the `BumpChanges` annotations; a scoped
  `typing-only-first-party-import` exception records the reason.
  `_build_changes_description` takes a `_ChangeCounts` record rather than
  `BumpChanges`, so CrossHair explores non-empty categories without proxying
  nested `Path` values.
- Adding JSON output later is an additive decision that must define the stream
  and schema; it does not require changing the preview record.

## References

- [Issue #373: show what `--dry-run` would change in documentation][issue-373]
- [Issue #368: unrelated snippet edited during the ortho-config bump][issue-368]
- [ADR-011: Preserve the CLI result and diagnostic-stream boundary under
  Cyclopts 4](adr-0011-cyclopts-4-cli-result-and-diagnostic-boundary.md)
- [ADR-012: Confine all bump write targets](adr-0012-confine-bump-write-targets.md)

[issue-368]: https://github.com/leynos/lading/issues/368
[issue-373]: https://github.com/leynos/lading/issues/373
