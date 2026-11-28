"""Property tests for documentation diff rendering and live writes."""

import collections.abc as cabc
import dataclasses as dc
import re
from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from lading import config as config_module
from lading.commands import bump_diff, bump_docs, bump_plan, bump_plan_builder
from lading.commands.bump_preview import DocumentationPreview

_LINE_CHARS = st.sampled_from(["a", "b", " ", "\n", "\r", "\u2028", "\f"])
_LF_CHARS = st.sampled_from(["a", "b", " ", "\n"])
_TERMINATORS = ("\r\n", "\r", "\n")


@given(text=st.text(alphabet=_LINE_CHARS))
def test_split_file_lines_preserves_text_and_terminators(text: str) -> None:
    """Splitting covers the text and only CR/LF terminate a line."""
    lines = bump_diff._split_file_lines(text)

    assert "".join(lines) == text, "the lines must reassemble the input exactly"
    for index, line in enumerate(lines):
        if line.endswith("\r\n"):
            content = line[:-2]
            terminated = True
        elif line.endswith(("\r", "\n")):
            content = line[:-1]
            terminated = True
        else:
            content = line
            terminated = False
        assert "\r" not in content, "CR may appear only as a terminator"
        assert "\n" not in content, "LF may appear only as a terminator"
        if not terminated:
            assert index == len(lines) - 1, "only the final line may lack a terminator"


def _lf_document() -> st.SearchStrategy[str]:
    """Return an LF-only document of non-blank lines."""
    lines = st.lists(
        st.text(alphabet=st.sampled_from(["a", "b"]), min_size=1, max_size=8),
        min_size=1,
        max_size=8,
    )
    return lines.map(lambda parts: "\n".join(parts) + "\n")


_HUNK = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


@dc.dataclass(frozen=True, slots=True)
class _Hunk:
    """One parsed unified-diff hunk."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    old_lines: list[str]
    new_lines: list[str]
    next_index: int


def _parse_hunk(lines: list[str], index: int) -> _Hunk:
    """Return the hunk starting at ``index`` and its next line index."""
    header = _HUNK.match(lines[index])
    assert header is not None, f"expected a hunk header, got {lines[index]!r}"
    index += 1
    old_lines: list[str] = []
    new_lines: list[str] = []
    while index < len(lines) and not lines[index].startswith("@@"):
        _collect_hunk_line(lines[index], old_lines, new_lines)
        index += 1
    return _Hunk(
        old_start=int(header.group("old_start")),
        old_count=int(header.group("old_count") or 1),
        new_start=int(header.group("new_start")),
        new_count=int(header.group("new_count") or 1),
        old_lines=old_lines,
        new_lines=new_lines,
        next_index=index,
    )


def _collect_hunk_line(line: str, old_lines: list[str], new_lines: list[str]) -> None:
    """Append one hunk body line to the original or proposed side."""
    if line.startswith(" "):
        old_lines.append(line[1:])
        new_lines.append(line[1:])
    elif line.startswith("-"):
        old_lines.append(line[1:])
    elif line.startswith("+"):
        new_lines.append(line[1:])


def _parse_hunks(rendered: str) -> list[_Hunk]:
    """Return every hunk in ``rendered``."""
    lines = rendered.split("\n")[2:]
    hunks: list[_Hunk] = []
    index = 0
    while index < len(lines):
        hunk = _parse_hunk(lines, index)
        hunks.append(hunk)
        index = hunk.next_index
    return hunks


def _apply_hunks(
    lines: list[str],
    edits: cabc.Iterable[tuple[int, int, list[str]]],
) -> list[str]:
    """Apply zero-based edits to ``lines`` and return the reconstructed list."""
    result: list[str] = []
    cursor = 0
    for start, count, replacement in edits:
        result.extend(lines[cursor:start])
        result.extend(replacement)
        cursor = start + count
    result.extend(lines[cursor:])
    return result


def _apply_to_original(original_lines: list[str], hunks: list[_Hunk]) -> list[str]:
    """Apply the diff's proposed side at each hunk's original position."""
    return _apply_hunks(
        original_lines,
        ((hunk.old_start - 1, hunk.old_count, hunk.new_lines) for hunk in hunks),
    )


def _apply_to_proposed(proposed_lines: list[str], hunks: list[_Hunk]) -> list[str]:
    """Apply the diff's original side at each hunk's proposed position."""
    return _apply_hunks(
        proposed_lines,
        ((hunk.new_start - 1, hunk.new_count, hunk.old_lines) for hunk in hunks),
    )


def _assert_hunks_match(hunks: list[_Hunk], original: str, proposed: str) -> None:
    """Check every hunk and reconstruct both complete documents."""
    original_lines = original.split("\n")[:-1]
    proposed_lines = proposed.split("\n")[:-1]
    assert hunks, "a changed pair must render at least one hunk"
    for hunk in hunks:
        assert (
            hunk.old_lines
            == original_lines[hunk.old_start - 1 : hunk.old_start - 1 + hunk.old_count]
        ), "the removed and context lines must match the original hunk"
        assert (
            hunk.new_lines
            == proposed_lines[hunk.new_start - 1 : hunk.new_start - 1 + hunk.new_count]
        ), "the added and context lines must match the proposed hunk"
    assert _apply_to_original(original_lines, hunks) == proposed_lines, (
        "applying every hunk must reconstruct the complete proposal"
    )
    assert _apply_to_proposed(proposed_lines, hunks) == original_lines, (
        "reversing every hunk must reconstruct the complete original"
    )


@given(
    lines=st.lists(
        st.text(alphabet=st.sampled_from(["a", "b"]), min_size=1, max_size=8),
        min_size=12,
        max_size=28,
        unique=True,
    )
)
def test_renderer_reconstructs_two_distant_edits(lines: list[str]) -> None:
    """A diff with two separated edits reconstructs the complete proposal."""
    original = "\n".join(lines) + "\n"
    edited = list(lines)
    edited[0] = f"first {edited[0]}"
    edited[-1] = f"last {edited[-1]}"
    proposed = "\n".join(edited) + "\n"
    preview = DocumentationPreview(
        path=Path("/ws/README.md"), original=original, proposed=proposed
    )

    rendered = bump_diff.format_documentation_diff([preview], Path("/ws"))
    hunks = _parse_hunks(rendered)

    assert len(hunks) >= 2, "edits at both ends must render as separate hunks"
    _assert_hunks_match(hunks, original, proposed)


@given(original=_lf_document(), proposed=_lf_document())
def test_renderer_round_trips_short_documents(original: str, proposed: str) -> None:
    """Every rendered hunk matches the document lines its header names."""
    preview = DocumentationPreview(
        path=Path("/ws/README.md"), original=original, proposed=proposed
    )

    rendered = bump_diff.format_documentation_diff([preview], Path("/ws"))

    if original == proposed:
        assert rendered == "No documentation changes.", (
            "an unchanged pair must render the no-change text"
        )
        return
    _assert_hunks_match(_parse_hunks(rendered), original, proposed)


@given(
    prefix=st.text(alphabet=_LINE_CHARS, max_size=20),
    suffix=st.text(alphabet=_LINE_CHARS, max_size=20),
)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_live_write_matches_proposal_bytes(
    tmp_path: Path, prefix: str, suffix: str
) -> None:
    """A committed plan stores exactly the prepared documentation bytes."""
    fence = '```toml\n[dependencies]\nalpha = "0.1.0"\n```\n'
    readme = tmp_path / "README.md"
    readme.write_bytes((prefix + "\n" + fence + suffix).encode("utf-8"))
    request = bump_docs.DocumentationUpdateRequest(
        target_version="1.2.3",
        updated_crates=frozenset({"alpha"}),
        read_source=lambda path: path.path.read_text(encoding="utf-8"),
    )

    updates = bump_docs._prepare_documentation_updates_with_sources(
        tmp_path,
        config_module.DocumentationConfig(globs=("README.md",)),
        request,
    )
    assert updates, "the targeted dependency fence must produce an update"

    builder = bump_plan_builder.PlanBuilder(root=tmp_path)
    for path, _source, content in updates:
        builder.propose(path, content, bump_plan.WriteCategory.DOCUMENTATION)
    bump_plan.commit_plan(builder.build(target_version="1.2.3", lockfiles=None))

    assert readme.read_bytes() == updates[0][2].encode("utf-8"), (
        "the committed bytes must equal the prepared documentation content"
    )


def test_reconstruction_rejects_an_omitted_hunk() -> None:
    """A diff missing one of its hunks cannot reconstruct the proposal."""
    original = "".join(f"line {index}\n" for index in range(20))
    proposed = original.replace("line 0\n", "changed 0\n").replace(
        "line 19\n", "changed 19\n"
    )
    preview = DocumentationPreview(
        path=Path("/ws/README.md"), original=original, proposed=proposed
    )

    rendered = bump_diff.format_documentation_diff([preview], Path("/ws"))
    hunks = _parse_hunks(rendered)
    assert len(hunks) == 2, "distant edits must render as two separate hunks"

    with pytest.raises(AssertionError, match="reconstruct the complete proposal"):
        _assert_hunks_match(hunks[:-1], original, proposed)
