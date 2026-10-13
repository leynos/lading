# ADR-009: Publish Python distributions to PyPI first

## Status

Accepted. Amends ADR-005.

## Date

2026-10-13

## Context and problem statement

Lading's tagged release workflow built a wheel and attached it only to a GitHub
Release. Adopting cuprum's merged release process requires a buildable source
distribution, PyPI publication through Trusted Publishing, and safe retries
when a tag is run more than once. GitHub Release assets remain part of lading's
existing installation path and must follow the same filename-based retry rule.

## Decision

- Every build runs plain `uv build`. The sdist is built first and each wheel
  is built from that sdist. Pull-request CI fails unless it produces exactly
  one sdist and at least one wheel; release collection repeats the sdist count
  check and gathers every wheel.
- Publish to PyPI before the GitHub Release. The `publish-pypi` job uses the
  GitHub Actions environment `pypi` and `id-token: write`; PyPI Trusted
  Publishing is configured for this repository,
  `.github/workflows/release.yml`, and that environment. No PyPI token is
  stored in GitHub secrets.
- Before upload, a standard-library script queries the PEP 691 project index
  and removes each local file whose exact filename already exists there. A 404
  means the project has no files yet. Network errors, HTTP 429, and 5xx
  responses are retried; every other HTTP error fails the job. The remaining
  files are published with
  `uv publish --trusted-publishing always --check-url <index>/simple/`. If the
  staging step removes every file, the publish step succeeds without calling
  `uv`.
- Keep the GitHub Release as a second publication target after PyPI succeeds.
  Create or reuse it as a draft, query its attached asset names through the
  existing cuprum `gh` allowlist, skip matching filenames with a notice, and
  upload only missing names without replacing assets. Clear the draft only
  after attachment succeeds. Run the PEP 723 uploader with
  `uv run --locked --script` so its hash-locked cuprum environment is enforced
  in the credential-bearing release job.
- Serialize releases by tag without cancelling an active run. Re-running the
  same tag skips files already present at each target and completes when no
  upload remains.

## Alternatives considered

Replacing GitHub Release assets with PyPI would remove a publication target,
but would break the established direct-download path. Keeping both targets
preserves that path and lets users install from PyPI as the primary route.

Publishing to PyPI with the PyPA GitHub Action was also considered. The
workflow instead uses `uv publish` with Trusted Publishing and `--check-url`,
which matches the cuprum process and leaves the skip policy in a small,
standard-library staging script.

## Consequences and limitations

- Filename presence determines whether a file is skipped. A rebuilt
  distribution can have different bytes under the same filename; the first
  version accepted by a target remains there. Since the workflow does not
  reconcile bytes across targets, PyPI and a later GitHub upload can contain
  different bytes for the same name if a rerun rebuilds the distribution.
- `--check-url` asks `uv` to check the simple index during publication, which
  helps with a race after staging. It does not make builds reproducible or
  compare bytes with the files already published.
- This `uv publish` flow uploads no PEP 740 attestations. Distribution
  attestations and validation that the tag version matches package metadata are
  possible follow-ups.
- The workflow remains a release procedure rather than a version gate. No
  final lading 0.x release is made until cuprum 0.2.0 final ships.

## References

- [ADR-005: Draft-then-publish releases with a standalone wheel uploader](005-release-wheel-publication.md)
- [cuprum#488: Publish the sdist and tolerate re-run releases](https://github.com/leynos/cuprum/pull/488)
- [lading#286: Adopt the cuprum release process](https://github.com/leynos/lading/issues/286)
