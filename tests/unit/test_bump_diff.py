"""Unit tests for rendering documentation diffs from bump previews."""

import typing as typ
from pathlib import Path

from lading.commands import bump_diff
from lading.commands.bump_preview import DocumentationPreview

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

_ROOT = Path("/ws")


def _preview(name: str, original: str, proposed: str) -> DocumentationPreview:
    """Return a preview rooted at the synthetic workspace path."""
    return DocumentationPreview(path=_ROOT / name, original=original, proposed=proposed)


def test_single_fence_diff_snapshot(snapshot: SnapshotAssertion) -> None:
    """One rewritten fence renders a minimal, readable unified diff."""
    original = '# Sample\n\n```toml\n[dependencies]\nalpha = "0.1.0"\n```\n'
    proposed = original.replace('"0.1.0"', '"1.2.3"')

    rendered = bump_diff.format_documentation_diff(
        [_preview("README.md", original, proposed)], _ROOT
    )

    assert rendered == snapshot, "the single-fence diff drifted from its snapshot"


def test_multiple_fences_diff_snapshot(snapshot: SnapshotAssertion) -> None:
    """Multiple rewritten fences in one file render in document order."""
    original = (
        "# Sample\n\n"
        '```toml\n[dependencies]\nalpha = "0.1.0"\n```\n\n'
        '```toml\n[dev-dependencies]\nalpha = "0.1.0"\n```\n'
    )
    proposed = original.replace('"0.1.0"', '"1.2.3"')

    rendered = bump_diff.format_documentation_diff(
        [_preview("README.md", original, proposed)], _ROOT
    )

    assert rendered == snapshot, "the multi-fence diff drifted from its snapshot"


def test_files_are_sorted_in_diff_snapshot(snapshot: SnapshotAssertion) -> None:
    """File blocks are sorted by path regardless of input order."""
    original = 'alpha = "0.1.0"\n'
    proposed = 'alpha = "1.2.3"\n'

    rendered = bump_diff.format_documentation_diff(
        [
            _preview("z-guide.md", original, proposed),
            _preview("a-guide.md", original, proposed),
        ],
        _ROOT,
    )

    assert rendered == snapshot, "the sorted multi-file diff drifted from its snapshot"
    assert rendered.index("--- a/a-guide.md") < rendered.index("--- a/z-guide.md"), (
        "the diff must order file blocks by path"
    )


def test_crlf_normalization_notice_snapshot(snapshot: SnapshotAssertion) -> None:
    """A CRLF original discloses that the write normalizes endings to LF."""
    rendered = bump_diff.format_documentation_diff(
        [_preview("README.md", 'alpha = "0.1.0"\r\n', 'alpha = "1.2.3"\n')],
        _ROOT,
    )

    assert rendered == snapshot, "the CRLF notice drifted from its snapshot"
    assert "used CRLF" in rendered, "the notice must name the original endings"


def test_missing_final_newline_marker_snapshot(snapshot: SnapshotAssertion) -> None:
    """A fence at end of file without a final newline is disclosed."""
    original = '```toml\n[dependencies]\nalpha = "0.1.0"\n```'
    proposed = original.replace('"0.1.0"', '"1.2.3"')

    rendered = bump_diff.format_documentation_diff(
        [_preview("README.md", original, proposed)], _ROOT
    )

    assert rendered == snapshot, "the end-of-file marker drifted from its snapshot"
    assert "\\ No newline at end of file" in rendered, (
        "an unterminated line must be followed by the standard marker"
    )


def test_no_documentation_changes() -> None:
    """An empty or unchanged preview set renders the explicit no-change text."""
    unchanged = _preview("README.md", 'alpha = "0.1.0"\n', 'alpha = "0.1.0"\n')

    assert bump_diff.format_documentation_diff([], _ROOT) == (
        "No documentation changes."
    ), "an empty preview set must state that no documentation changes"
    assert bump_diff.format_documentation_diff([unchanged], _ROOT) == (
        "No documentation changes."
    ), "a preview without a textual change must render the same message"


def test_only_cr_and_lf_split_file_lines() -> None:
    """U+2028 and form feed stay inside their line instead of splitting it."""
    shared = 'alpha = "0.1.0"\u2028continued\fand more'
    original = f"{shared}\nold\n"
    proposed = f"{shared}\nnew\n"

    rendered = bump_diff.format_documentation_diff(
        [_preview("README.md", original, proposed)], _ROOT
    )

    assert rendered.split("\n") == [
        "--- a/README.md",
        "+++ b/README.md",
        "@@ -1,2 +1,2 @@",
        f" {shared}",
        "-old",
        "+new",
    ], "only CR and LF must end a file line"
    assert "\\ No newline at end of file" not in rendered, (
        "a file ending in LF must not be marked as missing a final newline"
    )


def test_cr_only_normalization_notice_snapshot(snapshot: SnapshotAssertion) -> None:
    """A lone-CR original is described as CR, not as CRLF."""
    rendered = bump_diff.format_documentation_diff(
        [_preview("README.md", 'alpha = "0.1.0"\r', 'alpha = "1.2.3"\n')],
        _ROOT,
    )

    assert rendered == snapshot, "the CR notice drifted from its snapshot"
    assert "original used CR." in rendered, "the notice must name the CR endings"
    assert "CRLF" not in rendered, "a lone-CR original must not be called CRLF"
