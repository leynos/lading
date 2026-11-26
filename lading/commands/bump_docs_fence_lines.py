"""Preserve Markdown fence line endings while rendering changed fences."""

import re

_MAX_FENCE_INDENT = 3


def preserve_fence_line_endings(
    lines: list[str],
    token_map: list[int],
    fence_marker: str,
    body_text: str,
) -> tuple[str, str, str]:
    """Keep source line endings and separate an EOF-terminated opener.

    Parameters
    ----------
    lines
        Source Markdown lines with their physical line endings retained.
    token_map
        Opening and exclusive end indices from the Markdown parser's token.
    fence_marker
        Backtick or tilde marker used by the source fence.
    body_text
        Rendered fence body before it is normalized to the source endings.

    Returns
    -------
    tuple[str, str, str]
        Opening ending, normalized body, and closing ending. An unterminated
        source fence receives a generated closing line separated from its body.
    """
    start, end = token_map
    opening_ending = _line_ending(lines[start])
    closing_line = lines[end - 1]
    is_closed = end - 1 > start and _is_closing_fence(closing_line, fence_marker)
    body_end = end - 1 if is_closed else end
    body_ending = _first_line_ending(lines[start + 1 : body_end], opening_ending)
    body_text = _replace_line_endings(body_text, body_ending)
    closing_ending = _line_ending(closing_line)
    if is_closed:
        return opening_ending, body_text, closing_ending
    return _close_unterminated_fence(
        opening_ending,
        body_text,
        closing_ending,
        body_ending,
    )


def _first_line_ending(lines: list[str], fallback: str) -> str:
    """Return the first physical line ending in a sequence or its fallback."""
    for line in lines:
        if ending := _line_ending(line):
            return ending
    return fallback


def _close_unterminated_fence(
    opening_ending: str,
    body_text: str,
    closing_ending: str,
    body_ending: str,
) -> tuple[str, str, str]:
    """Append a generated terminator while retaining source line endings."""
    generated_ending = closing_ending or body_ending or opening_ending or "\n"
    opening_ending = opening_ending or generated_ending
    if body_text and not body_text.endswith(("\n", "\r")):
        body_text += generated_ending
    return opening_ending, body_text, generated_ending


def _line_ending(line: str) -> str:
    """Return the physical line ending at the end of ``line``."""
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith(("\r", "\n")):
        return line[-1]
    return ""


def _replace_line_endings(text: str, line_ending: str) -> str:
    """Normalize generated fence text to its original physical line ending."""
    return re.sub(r"\r\n|\r|\n", line_ending, text)


def _is_closing_fence(line: str, fence_marker: str) -> bool:
    """Check whether the final mapped line closes this Markdown fence."""
    candidate = line.lstrip(" ")
    indentation = len(line) - len(candidate)
    if indentation > _MAX_FENCE_INDENT:
        return False
    marker_length = len(candidate) - len(candidate.lstrip(fence_marker[0]))
    suffix = candidate[marker_length:]
    if suffix.endswith("\r\n"):
        suffix = suffix[:-2]
    elif suffix.endswith(("\r", "\n")):
        suffix = suffix[:-1]
    return marker_length >= len(fence_marker) and not suffix.strip(" \t")
