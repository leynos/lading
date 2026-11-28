"""Newline normalization shared by bump preparation and planning.

Documentation reads keep raw CRLF and lone-CR sequences for the preview, while
the rewrite and the restore comparison work on LF text. This module owns that
single conversion so both stages agree.
"""

__all__ = ["normalize_newlines"]


def normalize_newlines(text: str) -> str:
    """Return ``text`` with CRLF and lone CR sequences replaced by LF.

    Parameters
    ----------
    text : str
        Text whose line endings may mix CRLF, CR, and LF.

    Returns
    -------
    str
        The text with every CRLF and lone CR replaced by LF.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")
