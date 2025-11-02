# Lading

Welcome aboard the Lading! This repository packages a cheerful command-line
assistant that keeps Rust workspaces shipshape by coordinating version bumps
and publication plans. Whether you're preparing a release or just keeping
manifests tidy, `lading` helps the fleet stay in sync.

## Highlights

- 🚀 **Workspace aware** – orchestrates manifest updates across every crate in
  your Rust workspace.
- 🧭 **Configuration first** – reads `lading.toml` so each project can declare
  its own publishing rules and documentation globs.
- 🧪 **Safety checks included** – dry runs, cleanliness validation, and cargo
  health checks keep surprises to a minimum.

## Quick start

```bash
uv run lading --help
```

Prefer calling the module directly while developing?

```bash
uv run python -m lading.cli --help
```

Point the tool at your workspace with `--workspace-root /path/to/project` and
use subcommands such as `bump` to synchronise versions or `publish` to stage a
release plan.

## Learn more

Ready for a full tour? Sail over to the
[usage guide](https://github.com/leynos/lading/blob/main/docs/usage-guide.md)
for detailed walkthroughs, configuration examples, and command reference.

Fair winds and following seas! ⚓
