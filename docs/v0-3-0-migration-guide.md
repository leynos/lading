# Migrate to lading 0.3.0

`lading` 0.3.0 broadens lockfile regeneration during `lading bump`. This
release removes the need to list Git-tracked nested lockfiles individually in
configuration.

## Review lockfile coverage

In a Git workspace, `lading bump` discovers Git-tracked `Cargo.lock` files
outside `target/` directories only when they have an adjacent `Cargo.toml`
manifest. It includes each adjacent manifest in regeneration; standalone
tracked lockfiles are ignored. The workspace root manifest remains included
automatically.

`bump.lockfile_manifests` extends that automatic coverage. Keep manifests in
this setting for untracked lockfiles, including ignored fixtures. In a
workspace outside Git, discovery cannot identify tracked lockfiles:
`lading bump` emits a warning and falls back to this configuration. The setting
therefore also covers nested lockfiles in non-Git workspaces.

See the [lockfile configuration reference](users-guide.md#bump) for the
configuration format and the [bump workflow](users-guide.md#2-bump-versions)
for the complete regeneration behaviour.

## Preview the migration

`lading bump <version> --dry-run` discovers and reports the same lockfile set
as a non-dry-run bump. It lists each lockfile with the `(lockfile)` suffix but
does not modify manifests or lockfiles. See the
[dry-run output description](users-guide.md#2-bump-versions) for the full
output format.

## Regenerate before publishing

`lading bump <version> --no-rebuild-lockfiles` is the escape hatch when
automatic regeneration must be skipped. For example, run
`lading bump 1.2.3 --no-rebuild-lockfiles`, replacing `1.2.3` with the version
you want to set. If you use this option or modify manifests directly,
regenerate all affected lockfiles before publishing.

For Git-tracked lockfiles, `lading publish` reports stale files with repair
commands. Its pre-flight does not check configured lockfiles that Git does not
track, so validate and repair those manually before publishing. Regenerate an
untracked lockfile from its adjacent manifest with
`cargo generate-lockfile --manifest-path path/to/Cargo.toml`, replacing the
example path with that manifest's path. See
[publishing with fresh lockfiles](users-guide.md#3-publish-in-dry-run-mode) for
the tracked-lockfile recovery procedure.
