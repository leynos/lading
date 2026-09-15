# ADR-005: Draft-then-publish releases with a standalone wheel uploader

## Status

Accepted.

## Date

2026-09-15

## Context and problem statement

`lading` publishes a pure Python wheel as an asset on the GitHub release for
each `v*.*.*` tag. The release job created the release, downloaded the build
artefact, and attached the wheel with a shell pipeline:

```bash
find dist/wheels-* -type f -name "*.whl" -print0 | \
  xargs -0 -r gh release upload "$TAG"
```

Two properties of that arrangement combined into a silent failure. A pipeline
reports the exit status of its last command, so `set -eu` never saw `find` fail
when `dist/wheels-*` did not exist, and `xargs -r` then ran nothing and exited
zero. Separately, `softprops/action-gh-release` publishes immediately by
default, so the release was already visible before the upload ran at all.

Both `v0.3.0` and `v0.3.1` published with no wheel attached and a green job.
Each was completed by hand afterwards (issue #266). A release that ships
nothing has to be indistinguishable from no release at all, and the job that
produced it has to fail.

## Decision

Publication is the last thing the release job does, and the work that could
fail runs in Python rather than in a shell.

- The release is created with `draft: true`. The download and upload steps run
  next. A final step clears the flag with
  `gh release edit "$GITHUB_REF_NAME" --draft=false`. A failure anywhere in
  between leaves a draft, which is not a release anyone can install from.
- The download names the artefact (`name: wheels-pure`), so the wheel's
  location does not depend on the action's default layout.
- Wheel discovery, the empty case, and the upload move into
  `scripts/upload_release_wheels.py` and its sibling `release_wheel_upload`
  module. The script exits non-zero when it finds no wheel. Per
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
- `tests/workflow_contracts/test_release_workflow.py` holds the properties this
  decision rests on: the named artefact, the script invocation, the absence of
  a shell search, the draft flag, and the publish step's position after the
  upload. Changing the order or dropping the draft fails those contracts.
