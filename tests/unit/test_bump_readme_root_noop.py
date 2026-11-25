"""Protect the workspace README when root-crate transposition is a no-op."""

import typing as typ
from pathlib import Path

from lading.commands import bump_readme
from lading.workspace import WorkspaceCrate

if typ.TYPE_CHECKING:
    import pytest


def test_root_crate_noop_does_not_restore_stale_workspace_readme(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A root crate no-op cannot overwrite an edit after source reading."""
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        '[package]\nname = "root"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    crate = WorkspaceCrate(
        name="root",
        version="0.1.0",
        manifest_path=manifest,
        root_path=tmp_path,
        publish=True,
        readme_is_workspace=True,
        dependencies=(),
    )
    source_readme = tmp_path / "README.md"
    source_readme.write_text("# Project\n", encoding="utf-8")
    original_resolve = bump_readme._resolve_crate_readme_destination
    concurrent_text = "# Project\n\n# A change made while bump was running\n"

    def edit_after_read(
        workspace_root: Path,
        selected_crate: WorkspaceCrate,
        source_text: str,
        rewritten_text: str,
    ) -> Path:
        """Insert an external edit after the workspace README was read."""
        destination = original_resolve(
            workspace_root, selected_crate, source_text, rewritten_text
        )
        source_readme.write_text(concurrent_text, encoding="utf-8")
        return destination

    monkeypatch.setattr(
        bump_readme, "_resolve_crate_readme_destination", edit_after_read
    )

    changed_path = bump_readme.transpose_readme_to_crate(tmp_path, crate, dry_run=False)

    assert changed_path is None, "the root README no-op must report no write"
    assert source_readme.read_text(encoding="utf-8") == concurrent_text, (
        "the external workspace README edit must not be replaced with cached text"
    )
