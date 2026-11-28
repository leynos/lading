"""A stand-in ``cargo`` for the ``publish --expect-digests`` end-to-end tests.

It is run as a script from a ``cargo`` file placed on ``PATH``. It records its
argv and working directory to the file named by ``STUB_CARGO_LOG``, answers
``cargo metadata`` from the workspace manifests it finds above its working
directory, and, for ``package`` and ``publish``, writes a ``.crate`` under the
workspace's ``target/package`` where Cargo would. The archive's bytes are
``archive:<name>`` unless ``STUB_ARCHIVE_<NAME>`` overrides them.
"""

import json
import os
import sys
import tomllib
from pathlib import Path


def _workspace_root(start: Path) -> Path:
    """Return the nearest directory above ``start`` holding a workspace manifest."""
    for directory in (start, *start.parents):
        manifest = directory / "Cargo.toml"
        if manifest.exists() and "workspace" in tomllib.loads(
            manifest.read_text(encoding="utf-8")
        ):
            return directory
    message = f"no workspace above {start}"
    raise SystemExit(message)


def _packages(root: Path, members: list[str]) -> list[dict[str, object]]:
    """Describe each workspace member the way ``cargo metadata`` does."""
    packages: list[dict[str, object]] = []
    for member in members:
        manifest = root / member / "Cargo.toml"
        package = tomllib.loads(manifest.read_text(encoding="utf-8"))["package"]
        dependencies = [{"name": "alpha", "kind": None}] if member == "beta" else []
        packages.append({
            "name": package["name"],
            "version": package["version"],
            "id": f"{package['name']}-id",
            "publish": None,
            "manifest_path": str(manifest),
            "dependencies": dependencies,
        })
    return packages


def _write_archive(root: Path) -> None:
    """Write the ``.crate`` for the crate in the current directory."""
    package = tomllib.loads(Path("Cargo.toml").read_text(encoding="utf-8"))["package"]
    name = package["name"]
    payload = os.environ.get(f"STUB_ARCHIVE_{name.upper()}", f"archive:{name}")
    target = root / "target" / "package"
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{name}-{package['version']}.crate").write_bytes(payload.encode())


def main(args: list[str]) -> None:
    """Record ``args`` and behave as the Cargo subcommand they name."""
    entry = {"argv": args, "cwd": str(Path.cwd())}
    with Path(os.environ["STUB_CARGO_LOG"]).open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry) + "\n")
    root = _workspace_root(Path.cwd())
    match args[0]:
        case "metadata":
            members = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))[
                "workspace"
            ]["members"]
            packages = _packages(root, members)
            print(
                json.dumps({
                    "workspace_root": str(root),
                    "target_directory": str(root / "target"),
                    "packages": packages,
                    "workspace_members": [package["id"] for package in packages],
                })
            )
        case "package" | "publish":
            _write_archive(root)
        case _:
            pass


if __name__ == "__main__":
    main(sys.argv[1:])
