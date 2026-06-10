"""Unit tests for workspace README transposition during version bumps."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lading.commands import bump_readme
from lading.commands.bump_readme import ReadmeTranspositionError
from lading.workspace import WorkspaceCrate

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

_PATH_COMPONENT = st.text(
    alphabet=st.characters(blacklist_characters="/\\\x00"),
    min_size=1,
)
_URI_SCHEME = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="+.-",
    ),
    min_size=1,
    max_size=12,
).filter(lambda value: value[0].isalpha())


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


def test_rewrite_relative_links_ignores_code_regions() -> None:
    """Markdown examples inside code regions are preserved verbatim."""
    markdown = (
        "See [Guide](docs/guide.md).\n"
        "`[Inline](docs/inline.md)`\n"
        "```markdown\n"
        "[Fenced](docs/fenced.md)\n"
        "```\n"
        "    [Indented](docs/indented.md)\n"
        "\t[Tabbed](docs/tabbed.md)\n"
    )

    rewritten, changed = bump_readme.rewrite_relative_links(markdown, "../../")

    assert rewritten == (
        "See [Guide](../../docs/guide.md).\n"
        "`[Inline](docs/inline.md)`\n"
        "```markdown\n"
        "[Fenced](docs/fenced.md)\n"
        "```\n"
        "    [Indented](docs/indented.md)\n"
        "\t[Tabbed](docs/tabbed.md)\n"
    )
    assert changed is True


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
    snapshot: SnapshotAssertion,
) -> None:
    """Missing workspace README raises the bump-domain transposition error."""
    crate = _make_crate(tmp_path)

    with pytest.raises(ReadmeTranspositionError) as excinfo:
        bump_readme.transpose_readme_to_crate(tmp_path, crate, dry_run=False)

    assert snapshot == str(excinfo.value)


def test_transpose_readme_to_crate_rejects_external_crate_root(
    tmp_path: Path,
    snapshot: SnapshotAssertion,
) -> None:
    """Crate roots outside the workspace cannot receive transposed READMEs."""
    crate = _make_crate(tmp_path.parent, f"{tmp_path.name}-external")
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")

    with pytest.raises(ReadmeTranspositionError) as excinfo:
        bump_readme.transpose_readme_to_crate(tmp_path, crate, dry_run=False)

    assert snapshot == str(excinfo.value)


@given(parts=st.lists(_PATH_COMPONENT, min_size=1, max_size=8))
@settings(max_examples=100)
def test_compute_link_prefix_depth_matches_parts(parts: list[str]) -> None:
    """Prefix length in '../' units equals the number of path components."""
    path = Path(*parts)
    prefix = bump_readme.compute_link_prefix(path)
    assert prefix == "../" * len(path.parts)
    assert prefix.endswith("/")
    assert not prefix.startswith("/")


@given(text=st.text(max_size=400), prefix=st.just("../../"))
@settings(max_examples=100)
def test_rewrite_relative_links_changed_flag_is_consistent(
    text: str, prefix: str
) -> None:
    """Changed is False if and only if the returned text equals the input."""
    rewritten, changed = bump_readme.rewrite_relative_links(text, prefix)
    assert changed == (rewritten != text)


@given(
    scheme=_URI_SCHEME,
    rest=st.text(
        min_size=1,
        max_size=80,
        alphabet=st.characters(blacklist_characters=" ()\n"),
    ),
    label=st.text(
        min_size=1,
        max_size=20,
        alphabet=st.characters(blacklist_characters="[]\n"),
    ),
)
@settings(max_examples=100)
def test_rewrite_relative_links_preserves_uri_scheme_links(
    scheme: str, rest: str, label: str
) -> None:
    """Links whose target contains a URI scheme are never rewritten."""
    target = f"{scheme}:{rest}"
    markdown = f"[{label}]({target})"
    rewritten, changed = bump_readme.rewrite_relative_links(markdown, "../../")
    assert not changed
    assert rewritten == markdown


_FENCE_HEADER = st.tuples(
    st.sampled_from(["```", "~~~"]),
    st.text(max_size=10, alphabet=st.characters(whitelist_categories=("Ll",))),
).map(
    "".join,
)


# Exclude the fence delimiters so the body cannot open a code block of its own,
# which would close on ``fence_header`` and leave the link outside any fence.
_FENCE_FREE_BODY = st.text(
    max_size=200,
    alphabet=st.characters(blacklist_characters="`~"),
)


@given(
    body=_FENCE_FREE_BODY,
    fence_header=_FENCE_HEADER,
    label=st.text(
        min_size=1,
        max_size=20,
        alphabet=st.characters(blacklist_characters="[]\n"),
    ),
    path=st.text(
        min_size=1,
        max_size=40,
        alphabet=st.characters(blacklist_characters=" ()\n"),
    ),
)
@settings(max_examples=100)
def test_rewrite_relative_links_preserves_fenced_code_blocks(
    body: str, fence_header: str, label: str, path: str
) -> None:
    """Relative links inside fenced code blocks are never rewritten."""
    fenced_link = f"[{label}]({path})"
    fence = fence_header[:3]
    markdown = f"{body}\n{fence_header}\n{fenced_link}\n{fence}\n"
    rewritten, _ = bump_readme.rewrite_relative_links(markdown, "../../")
    assert fenced_link in rewritten
