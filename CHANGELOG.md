# Changelog

All notable changes to `lading` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and `lading` follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `lading publish --expect-digests PATH` refuses to upload an archive that
  differs from the SHA-256 digests in a `sha256sum`-format file. The file is
  validated against the publish plan before anything is staged. Each archive is
  checked after `cargo package`, an already-published version is downloaded
  from crates.io and compared (skipped when identical, a conflict when
  different), and the archive `cargo publish` wrote is compared again after a
  live upload. Both cargo commands run with `--locked --registry crates-io`,
  and workspace discovery with `--locked`; per-crate outcomes appear in the log
  and the publish summary, and the `publish.archive_check` metric counts them.
  Only crates.io is supported, and a default registry set in Cargo
  configuration, at the checkout or the staged paths, is refused. The archive
  `cargo publish --dry-run` packages is compared too, and exact registry token
  values (from the environment, pre-flight overrides and Cargo's credentials
  file) are hidden from what lading shows (never from what it parses)
  ([#419](https://github.com/leynos/lading/issues/419); [ADR-013](docs/adrs/adr-0013-verify-archives-against-expected-digests.md)).
- `WorkspaceCrate.registries` carries the registries named by a manifest's
  `publish` key, or `None` when the key does not restrict them.
