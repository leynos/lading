r"""Render documentation rewrite diffs for ``lading bump --diff``.

``lading bump --dry-run`` names the files a bump would rewrite, and for
documentation that is not enough: the rewrite edits free-form TOML snippets a
human wrote for readers. This module turns the exact original and proposed text
recorded for each changed file into a unified diff a reviewer can read in a
terminal.

The output is deliberately human-readable rather than a patch. It uses
``a/`` and ``b/`` prefixes because that is what reviewers expect, but it does
not promise ``git apply`` compatibility: there are no index lines, modes, or
path quoting rules.

Examples
--------
>>> from pathlib import Path
>>> from lading.commands.bump_preview import DocumentationPreview
>>> from lading.commands.bump_diff import format_documentation_diff
>>> root = Path("/ws")
>>> preview = DocumentationPreview(
...     path=root / "README.md",
...     original='[dependencies]\nalpha = "0.1.0"\n',
...     proposed='[dependencies]\nalpha = "1.2.3"\n',
... )
>>> print(format_documentation_diff([preview], root))
--- a/README.md
+++ b/README.md
@@ -1,2 +1,2 @@
 [dependencies]
-alpha = "0.1.0"
+alpha = "1.2.3"
"""

import collections.abc as cabc
import difflib
import typing as typ

from lading.commands.bump_output import _format_manifest_path

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.commands.bump_preview import DocumentationPreview

_NO_NEWLINE_MARKER = r"\ No newline at end of file"
_LINE_TERMINATORS = "\r\n"
_CRLF_NOTICE = (
    "Note: this rewrite writes the file with LF line endings; the original used CRLF."
)
_CR_NOTICE = (
    "Note: this rewrite writes the file with LF line endings; the original used CR."
)


def format_documentation_diff(
    previews: cabc.Sequence[DocumentationPreview],
    workspace_root: Path,
) -> str:
    """Return a unified diff for each changed documentation file.

    Parameters
    ----------
    previews
        Original and proposed text for every changed documentation file.
    workspace_root
        Workspace root used to render workspace-relative diff headers.

    Returns
    -------
    str
        One diff block per file, sorted by path and separated by a blank
        line. ``No documentation changes.`` when no preview differs.

    Examples
    --------
    >>> from pathlib import Path
    >>> format_documentation_diff([], Path("/ws"))
    'No documentation changes.'
    """
    rendered = [
        _format_file_diff(preview, workspace_root)
        for preview in sorted_documentation_previews(previews)
        if preview.original != preview.proposed
    ]
    if not rendered:
        return "No documentation changes."
    return "\n\n".join(rendered)


def sorted_documentation_previews(
    previews: cabc.Sequence[DocumentationPreview],
) -> tuple[DocumentationPreview, ...]:
    """Return ``previews`` ordered by their string path.

    Parameters
    ----------
    previews
        Previews to order.

    Returns
    -------
    tuple[DocumentationPreview, ...]
        The same previews, sorted by ``str(path)`` for deterministic output.
    """
    return tuple(sorted(previews, key=lambda preview: str(preview.path)))


def _format_file_diff(preview: DocumentationPreview, workspace_root: Path) -> str:
    """Return the diff block for one documentation file."""
    relative_path = _format_manifest_path(preview.path, workspace_root)
    return _render_lines(
        _unified_diff_lines(preview, relative_path),
        notice=_line_ending_notice(preview),
    )


def _unified_diff_lines(preview: DocumentationPreview, relative_path: str) -> list[str]:
    """Return the raw unified-diff lines for ``preview``."""
    return list(
        difflib.unified_diff(
            _split_file_lines(preview.original),
            _split_file_lines(preview.proposed),
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
            lineterm="\n",
        )
    )


def _split_file_lines(text: str) -> list[str]:
    """Split ``text`` at CR and LF boundaries, preserving each terminator.

    ``str.splitlines`` also treats U+2028, form feed, and several other
    characters as line boundaries. Those characters are content in a Markdown
    file, so splitting on them would show a shared line as a spurious change
    and could attach a missing-newline marker to a partial line. Only CR, LF,
    and CRLF end a line here.

    Returns
    -------
    list[str]
        The file lines, each keeping its CR, LF, or CRLF terminator when
        present.
    """
    lines: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        match text[index]:
            case "\r":
                end = index + 2 if text.startswith("\r\n", index) else index + 1
            case "\n":
                end = index + 1
            case _:
                index += 1
                continue
        lines.append(text[start:end])
        start = end
        index = end
    if start < len(text):
        lines.append(text[start:])
    return lines


def _line_ending_notice(preview: DocumentationPreview) -> str | None:
    """Return the notice for a rewrite that removes CR endings, if any."""
    # A notice is warranted only when the original uses CR and the proposal
    # does not; otherwise the reader would mistake the ending change for a
    # content change.
    if "\r" not in preview.original or "\r" in preview.proposed:
        return None
    return _CRLF_NOTICE if "\r\n" in preview.original else _CR_NOTICE


def _render_lines(lines: cabc.Sequence[str], *, notice: str | None) -> str:
    r"""Render raw diff lines as text, disclosing unterminated lines.

    ``difflib`` writes headers with ``lineterm`` but leaves body lines carrying
    their source terminator, and the last line of a file without a trailing
    newline has none. A plain ``"\\n"`` join over those lines would invent a
    newline the file does not have, so an unterminated line is followed by the
    marker diff tools use to disclose exactly that. A line-ending rewrite adds
    its notice directly after the ``+++`` header, before the first hunk.

    Returns
    -------
    str
        The rendered block, with header and body lines joined by LF.
    """
    rendered: list[str] = []
    for index, line in enumerate(lines):
        rendered.append(line.rstrip(_LINE_TERMINATORS))
        if not line.endswith(tuple(_LINE_TERMINATORS)):
            rendered.append(_NO_NEWLINE_MARKER)
        if notice is not None and index == 1:
            rendered.append(notice)
    return "\n".join(rendered)
