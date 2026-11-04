# ADR-005: Draft-then-publish releases with a standalone wheel uploader

## Status

Accepted. Amended by [ADR-009](009-pypi-first-distribution-publication.md).

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

The original download omitted `name`. In both v4 and v8, a download that
selects a single artefact extracts its files directly into `path`. When several
artefacts match and `merge-multiple` is false, the action creates a
subdirectory for each artefact under `path`. These rules are visible in the
v4.1.8 source[^1] and documentation[^2], and v8.0.2 source[^3]. Therefore,
whenever only `wheels-pure` matched, its files were placed directly in `dist`
and `find dist/wheels-*` found no match. The v4-to-v8 version change alone does
not explain the empty glob; a named download makes the intended artefact and
destination explicit.

The pipeline then hid that empty match. Without `pipefail`, it reported the
exit status of the last command: `find` failed when `dist/wheels-*` did not
exist, while `xargs -r` ran nothing and exited zero. Consequently, `set -eu`
did not fail the step. Naming the download and setting its path makes the
artefact identity and location explicit. The collector requires at least one
wheel and exactly one sdist before either publisher proceeds. The Python
uploader also fails when its directory is missing or contains no distributions.
Phase 1 contract tests bind the download name and path to the collector and
uploader directories.

Issue #256 reports that the `v0.3.0` release had no wheel and that the wheel
was attached by hand; it also notes that `v0.2.0` retained its wheel. The
workflow comments report that `v0.3.0` and `v0.3.1` were live without wheels.
These are attributed reports, not run-log details independently verified by
this ADR. A release that ships nothing has to be indistinguishable from no
release at all, and the job that produced it has to fail.

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
- The Phase 1 contract tests in
  `tests/workflow_contracts/test_release_workflow.py` bind the producer
  artefact and job dependency to the collector download name and path and to
  the uploader directory. They also check failure-safe ordering, publication
  after upload, and the absence of a shell search. Changing these contracts
  fails the tests.

## Addendum, 2026-10-13: PyPI-first publication

[ADR-009](009-pypi-first-distribution-publication.md) amends this release
process. Tagged releases now publish an sdist and every wheel to PyPI through
Trusted Publishing before attaching any missing filenames to the GitHub
Release. The GitHub-only wheel process above remains the historical decision;
ADR-009 records the current order, retry rules, and trade-offs.

## Addendum, 2026-11-04: Incident account and contract corrections

Adopted 2026-11-04. The earlier Context said that both `v0.3.0` and `v0.3.1`
had no wheel and green jobs, and that each was completed by hand. Those
statements were not independently supported by run logs. The Context above now
attributes the `v0.3.0` manual attachment and the retained `v0.2.0` wheel to
issue #256, and attributes reports about `v0.3.0` and `v0.3.1` to workflow
comments. The corrected download layout is based on the pinned v4.1.8 and
v8.0.2 action sources cited above.

The Decision's `"$GITHUB_REF_NAME"` command and `--clobber` upload describe the
historical workflow. The current workflow passes `"$TAG"`, set from
`github.ref_name`, to its publication step and uses
`scripts/upload_release_wheels.py`. Phase 1 contract tests lock those commands
and reject `--clobber` in workflow run blocks. They also bind artefact
identity, extraction path, collection directory, and publication ordering.

[^1]: <https://github.com/actions/download-artifact/blob/fa0a91b85d4f404e444e00e005971372dc801d16/src/download-artifact.ts>
[^2]: <https://github.com/actions/download-artifact/blob/fa0a91b85d4f404e444e00e005971372dc801d16/README.md>
[^3]: <https://github.com/actions/download-artifact/blob/9000827ccba6bdab643e8b6fd33ac0654aef8333/src/download-artifact.ts>
