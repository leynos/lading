# ADR-005: Draft-then-publish releases with a standalone wheel uploader

## Status

Accepted.

## Date

2026-10-10

## Context and problem statement

`lading` publishes a pure Python wheel as an asset on the GitHub release for
each `v*.*.*` tag. The release job created the release, downloaded the build
artefact, and attached the wheel with a shell pipeline:

```bash
find dist/wheels-* -type f -name "*.whl" -print0 | \
  xargs -0 -r gh release upload "$TAG"
```

The artefact download layout explains why the search stopped finding the wheel.
With `path: dist` and no `name`, v4 placed every downloaded artefact in
`dist/<artifact-name>`, even when one artefact matched. The producer's artefact
was named `wheels-pure`, so the wheel was at `dist/wheels-pure`. A named
single-artefact download writes directly into `path` in both v4 and v8. In v8,
an unnamed download also writes directly into `path` when exactly one artefact
matches; when several match, per-artefact subdirectories are used if
`merge-multiple` is false. The
[v4 source](https://github.com/actions/download-artifact/blob/v4/src/download-artifact.ts)
and
[v8 source](https://github.com/actions/download-artifact/blob/v8/src/download-artifact.ts)
show this layout change. The old `find dist/wheels-*` lookup therefore stopped
matching when the v8 release job downloaded its single `wheels-pure` artefact
without a name filter.

The pipeline then hid the empty match. Without `pipefail`, the pipeline
reported the exit status of its last command: `find` failed when
`dist/wheels-*` did not exist, while `xargs -r` ran nothing and exited zero.
Consequently, `set -eu` did not fail the step. Separately,
`softprops/action-gh-release` publishes immediately by default, so the release
was already visible before the upload ran at all.

Issue #256 reports that the `v0.3.0` release had no wheel and that the wheel
was attached by hand; it also notes that `v0.2.0` retained its wheel. The
workflow comments record that `v0.3.0` and `v0.3.1` were live without wheels.
These are attributed release reports, not run-log details independently
verified by this ADR. A release that ships nothing has to be indistinguishable
from no release at all, and the job that produced it has to fail.

## Decision

Publication is the last thing the release job does, and the work that could
fail runs in Python rather than in a shell.

- The release is created with `draft: true`. The download and upload steps run
  next. A final step clears the flag with
  `gh release edit "$GITHUB_REF_NAME" --draft=false`. A failure anywhere in
  between leaves a draft, which is not a release anyone can install from.
- The download names the artefact (`name: wheels-pure`) and sets `path: dist`,
  so the wheel's location does not depend on the action's default layout.
- Wheel discovery, the empty case, and the upload move into
  `scripts/upload_release_wheels.py` and its sibling `release_wheel_upload`
  module. The script exits non-zero when it finds no wheel, and its failure
  reaches the step without a shell pipeline. Per
  [scripting standards](../scripting-standards.md) it uses a PEP 723 metadata
  block, cyclopts for the interface, cuprum for command execution, and
  `pathlib` for the search.
- `gh release upload` is passed `--clobber`. The draft is reused across runs,
  so a rerun after a failed publication would otherwise meet the asset its own
  previous attempt uploaded.
- Discovery reports read failures rather than absorbing them. `Path.rglob`
  skips directories it cannot open and `Path.exists` answers `False` for a
  permission error, either of which would diagnose a permissions problem as a
  build that produced nothing.
- The uploader's process dependencies -- the `gh` runner, the clock, and the
  two output sinks -- are parameters with production defaults bound in the
  command-line entry point.
- The step reports one bounded outcome, drawn from a closed set, as a
  machine-readable line on stderr and as `outcome` and `wheels` on
  `GITHUB_OUTPUT`.

## Alternatives considered

Keeping the upload in the workflow and adding `set -o pipefail` would fix the
exit status but not the publication order, and would leave multi-command gate
logic in a `run:` block, which the repository's scripting standard rules out.

Importing `lading.utils.metrics` (see
[ADR-004](004-in-process-metrics-backend.md)) for the outcome signal was
rejected. The uploader is a standalone PEP 723 script that does not import the
package, and the workflow step is shorter lived than even a `lading` run, so
the job log and `GITHUB_OUTPUT` are the signals a consumer can actually read.

## Consequences

- A visible release always has its wheel. This is now a user-facing guarantee,
  stated in the [users' guide](../users-guide.md#install-from-a-tagged-release).
- A failed release leaves a draft that a maintainer must delete or rerun the
  tag against. That is deliberate: the draft is the evidence of what failed.
- The uploader is the first script in this repository to run `gh`. Its cuprum
  catalogue allowlists `gh` alone, and that boundary covers the script, not the
  job, which also runs `uv` and its pinned actions.
- The Phase 1 contract tests in
  `tests/workflow_contracts/test_release_workflow.py` tie the producer's
  artefact name and job dependency to the download name and path and the
  uploader directory. They check that draft creation precedes download and
  upload, that critical steps fail closed, and that publication follows upload.
  Changing these contracts fails the tests.
