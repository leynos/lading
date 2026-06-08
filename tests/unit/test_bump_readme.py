"""Unit tests for workspace README transposition during version bumps."""

from __future__ import annotations

from pathlib import Path

import pytest

from lading.commands import bump_readme
from lading.commands.publish_manifest import PublishPreparationError
from lading.workspace import WorkspaceCrate


def _make_crate(
    workspace_root: Path, relative_root: str = "crates/alpha"
) -> WorkspaceCrate:
    """Create a workspace crate model rooted at ``relative_root``."""
    crate_root = workspace_root / relative_root
    crate_root.mkdir(parents=True, exist_ok=True)
    manifest_path = crate_root / "Cargo.toml"
    manifest_path.write_text(
        '[package]\nname = "alpha"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    return WorkspaceCrate(
        id="alpha-id",
        name="alpha",
        version="0.1.0",
        manifest_path=manifest_path,
        root_path=crate_root,
        publish=True,
        readme_is_workspace=True,
        dependencies=(),
    )


@pytest.mark.parametrize(
    ("relative_path", "expected"),
    [
        ("crates/foo", "../../"),
        ("crates/nested/deep", "../../../"),
        ("foo", "../"),
    ],
)
def test_compute_link_prefix_matches_crate_depth(
    relative_path: str, expected: str
) -> None:
    """The prefix walks from the crate root back to the workspace root."""
    assert bump_readme.compute_link_prefix(Path(relative_path)) == expected


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        (
            "[Guide](docs/v0-6-0-migration-guide.md)",
            "[Guide](../../docs/v0-6-0-migration-guide.md)",
        ),
        ("![Logo](assets/logo.png)", "![Logo](../../assets/logo.png)"),
        (
            "[Guide](docs/guide.md?ref=readme)",
            "[Guide](../../docs/guide.md?ref=readme)",
        ),
        (
            "[Guide](docs/guide.md#install)",
            "[Guide](../../docs/guide.md#install)",
        ),
        (
            "See [Guide](docs/guide.md) and ![Logo](assets/logo.png).",
            "See [Guide](../../docs/guide.md) and ![Logo](../../assets/logo.png).",
        ),
    ],
)
def test_rewrite_relative_links_updates_markdown_targets(
    markdown: str, expected: str
) -> None:
    """Inline links and image targets receive the crate-to-root prefix."""
    rewritten, changed = bump_readme.rewrite_relative_links(markdown, "../../")
    assert rewritten == expected
    assert changed is True


@pytest.mark.parametrize(
    "markdown",
    [
        "[HTTP](http://example.test/docs)",
        "[HTTPS](https://example.test/docs)",
        "[Mail](mailto:team@example.test)",
        "[Phone](tel:+441234567890)",
        "![Inline](data:image/png;base64,AAAA)",
        "[Protocol](//example.test/docs)",
        "[Absolute](/docs/guide.md)",
        "[Fragment](#usage)",
        "[Empty]()",
    ],
)
def test_rewrite_relative_links_preserves_non_relative_targets(markdown: str) -> None:
    """Absolute, fragment-only, and empty targets are left untouched."""
    assert bump_readme.rewrite_relative_links(markdown, "../../") == (
        markdown,
        False,
    )


def test_transpose_readme_to_crate_writes_rewritten_workspace_readme(
    tmp_path: Path,
) -> None:
    """Transposition writes adopted README content into the crate root."""
    crate = _make_crate(tmp_path)
    (tmp_path / "README.md").write_text(
        "# Project\n\nSee [Guide](docs/guide.md).\n",
        encoding="utf-8",
    )

    changed_path = bump_readme.transpose_readme_to_crate(tmp_path, crate, dry_run=False)

    target_readme = tmp_path / "crates" / "alpha" / "README.md"
    assert changed_path == target_readme
    assert target_readme.read_text(encoding="utf-8") == (
        "# Project\n\nSee [Guide](../../docs/guide.md).\n"
    )


def test_transpose_readme_to_crate_reports_dry_run_without_writing(
    tmp_path: Path,
) -> None:
    """Dry runs report the target path and leave the crate unchanged."""
    crate = _make_crate(tmp_path)
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")

    changed_path = bump_readme.transpose_readme_to_crate(tmp_path, crate, dry_run=True)

    assert changed_path == tmp_path / "crates" / "alpha" / "README.md"
    assert not (tmp_path / "crates" / "alpha" / "README.md").exists()


def test_transpose_readme_to_crate_skips_matching_target(tmp_path: Path) -> None:
    """Existing adopted README content is not rewritten unnecessarily."""
    crate = _make_crate(tmp_path)
    content = "# Project\n"
    (tmp_path / "README.md").write_text(content, encoding="utf-8")
    (tmp_path / "crates" / "alpha" / "README.md").write_text(content, encoding="utf-8")

    assert bump_readme.transpose_readme_to_crate(tmp_path, crate, dry_run=False) is None


def test_transpose_readme_to_crate_requires_workspace_readme(
    tmp_path: Path,
) -> None:
    """Missing workspace README is reported through the staging error type."""
    crate = _make_crate(tmp_path)

    with pytest.raises(PublishPreparationError, match=r"Workspace README\.md"):
        bump_readme.transpose_readme_to_crate(tmp_path, crate, dry_run=False)
