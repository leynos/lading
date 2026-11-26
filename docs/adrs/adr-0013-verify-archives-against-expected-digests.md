# ADR-013: Verify archives against caller-supplied digests before upload

## Status

Accepted. `lading publish --expect-digests` is the implementation.

## Date

2026-11-26

## Context and problem statement

`lading publish --live` packages each crate in its staged workspace with
`cargo package` and then runs `cargo publish`, which packages the crate again
and uploads the result. Nothing ties the uploaded bytes to archives that the
caller built and checked beforehand. A release workflow that packages in a job
with no registry credential and publishes from a second job that holds one
(issue [#419][issue-419], raised from leynos/wireframe#981) can rebuild
archives in its checkout and compare them, but cannot check the archive lading
uploads until after the upload, when a published version can no longer be
replaced.

Two smaller gaps compounded this. `cargo package` and `cargo publish` ran
without `--locked`, so a stale lockfile was silently updated in the staged tree
and the bytes changed. A version already on crates.io was skipped when
`cargo publish` reported it, so a rerun could not tell a release that completed
from a different upload of the same version.

## Decision drivers

- A published version is permanent, so a difference must be found before the
  upload wherever possible.
- The check must cover the bytes lading itself produces, not a rebuild in the
  caller's checkout.
- Behaviour without the new option must not change.
- Tests must never reach crates.io or run a real `cargo publish`.

## Options considered

| Option                                              | Uploaded bytes are the checked bytes | Reproduces Cargo's publish metadata | Verdict      |
| --------------------------------------------------- | ------------------------------------ | ----------------------------------- | ------------ |
| Compare after `cargo package`, again after upload   | Narrowed, not guaranteed             | No                                  | Chosen       |
| Upload the checked archive through the registry API | By construction                      | Yes                                 | Out of scope |
| Compare only against crates.io after the release    | No                                   | No                                  | Status quo   |

Uploading the checked archive directly would remove the gap and keep Cargo's
verification build away from the credential, but it requires reproducing the
metadata `cargo publish` sends. That is deferred; the post-upload comparison
narrows the remaining gap until then.

## Decision

`lading publish` accepts `--expect-digests PATH`, a `sha256sum`-format file
with one `<64 hex digits>  <name>-<version>.crate` entry per line.

- **Validation before staging.** The file is read and parsed before the
  pre-flight runs, and checked against the plan before staging: every
  publishable crate needs exactly one entry and no entry may name an archive
  outside the plan. The checks live in `lading.commands.publish_digests`.
- **Three comparison points.** `ArchiveVerifier` in
  `lading.commands.publish_archive_check` compares the already-published
  archive before packaging (skipping an identical version without packaging it,
  failing a different one as a conflict, so the published bytes decide whatever
  a local build would produce), the archive after `cargo package` (refusing the
  crate and every later one on a difference), and the archive `cargo publish`
  wrote after a successful live upload (failing loudly, since the upload cannot
  be undone).
- **Locked builds.** When digests are given, both cargo commands receive
  `--locked` and workspace discovery runs `cargo metadata --locked`, because a
  digest only means something against a fixed lockfile.
- **Locating the archive.** Each crate's target directory comes from
  `cargo metadata` run in that crate, not from an assumed `target/` or another
  crate's directory.
- **Published archives.** The comparison downloads
  `https://static.crates.io/crates/<name>/<name>-<version>.crate` through an
  injectable `PublishedDigestFetcher` port, hashing the stream with a size
  bound and bounded retries. The request carries no credential.
- **Dry runs.** The package check applies to every archive packaged. A crate
  whose packaging is downgraded to a warning is recorded as unverified.
- **Reporting.** Each crate ends with one outcome (`verified`,
  `already published and identical`, `conflict`, `mismatch`, `unverified`),
  logged when decided, counted under `publish.archive_check`, and listed in the
  publish summary.
- **Registry.** Only crates.io is supported. The option is refused when
  `CARGO_REGISTRY_DEFAULT` names another registry or a publishable crate's
  `publish` key omits `crates-io`; `WorkspaceCrate.registries` carries that
  key. The effective default registry is found in Cargo's own order
  (`CARGO_REGISTRY_DEFAULT`, then `.cargo/config` or `.cargo/config.toml`, the
  extensionless file winning when both exist, from the workspace root and from
  each publishable crate's root upward, then Cargo home) and refused before
  staging when it is not crates.io. The caller resolves these inputs into a
  `CargoEnvironment`, which `publish.run` requires with digests rather than
  reading the ambient environment. Both cargo commands also receive
  `--registry crates-io` as defence in depth.
- **Dry-run publish.** `cargo publish --dry-run` packages again, so its
  archive is compared too and a difference is a mismatch.
- **Credentials.** With digests, exact values of `CARGO_REGISTRY_TOKEN`,
  `CARGO_REGISTRIES_<NAME>_TOKEN` (from the environment or a pre-flight
  override) and the tokens in Cargo's credentials file are replaced in
  subprocess output where it is read and in the logged command line, so logs
  and errors built from it stay clean. The limit: credentials an external Cargo
  credential provider supplies never pass through lading, so they cannot be
  redacted, and only exact values are replaced.

The verifier rides on `_PublishExecutionOptions` rather than on the pipeline
state, so `--locked` follows from its presence and the helper signatures keep
their argument count.

## Consequences and limitations

- A package built in the caller's checkout and one built in lading's staged
  copy must be byte-identical for the workflow to pass. Lading does not promise
  that; when they differ the run stops at the first crate rather than
  uploading, and `--keep-staging` retains lading's archive for comparison.
- The download adds one request per crate to every run that sets the option,
  including dry runs.
- The post-upload comparison cannot prevent an upload; it only makes a
  difference visible at once.
- Uploading the checked archive directly and supporting other registries remain
  open.

[issue-419]: https://github.com/leynos/lading/issues/419
