"""Normalize Cyclopts diagnostics captured from a CLI stream.

Cyclopts renders errors through Rich, so a captured stream carries ANSI colour
sequences and a box-drawn panel around the message. The panel's width depends
on the terminal, and the colour depends on ``FORCE_COLOR``, so asserting on the
raw text would make a test depend on the environment rather than on the
diagnostic. :func:`normalize_diagnostic` reduces the panel to its words while
leaving the message itself intact.

Only diagnostics are normalized. Command summaries are lading's own plain text
and are asserted verbatim, so a regression that wrapped or coloured them would
still fail.
"""

import re

# CSI sequences: ESC '[' parameters intermediates final-byte. Rich emits these
# for colour and for cursor movement; neither carries meaning for an assertion.
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# The box-drawing block Rich uses for panels, plus the horizontal rules it
# repeats to the terminal width.
_BOX_DRAWING = re.compile(r"[─-╿]")
_WHITESPACE = re.compile(r"\s+")


def normalize_diagnostic(text: str) -> str:
    r"""Reduce a Rich-rendered diagnostic to its message text.

    Parameters
    ----------
    text : str
        Captured stdout or stderr from a Cyclopts invocation.

    Returns
    -------
    str
        The text with ANSI escapes and box-drawing characters removed and
        whitespace runs collapsed to single spaces.

    Examples
    --------
    >>> normalize_diagnostic("\x1b[31m│ Unknown command \x1b[0m│")
    'Unknown command'
    """
    without_ansi = _ANSI_ESCAPE.sub("", text)
    without_box = _BOX_DRAWING.sub(" ", without_ansi)
    return _WHITESPACE.sub(" ", without_box).strip()
