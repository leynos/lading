"""Resolve manifest paths supplied by an injected workspace graph.

A programmatic caller may build a :class:`~lading.workspace.WorkspaceGraph`
whose root is spelled through a symlink alias. The confinement root is
canonical, so the manifest must be made relative to the spelling the caller
used before its suffix is confined beneath the canonical root.
"""

from pathlib import Path

from lading.utils.confinement import UnsafeBumpTargetError, relative_to_root

__all__ = ["graph_relative_manifest"]


def graph_relative_manifest(
    root_path: Path,
    graph_spelling: Path,
    manifest_path: Path,
    *,
    key: str,
) -> Path:
    """Return ``manifest_path`` relative to the canonical workspace root.

    Parameters
    ----------
    root_path : Path
        Canonical workspace root.
    graph_spelling : Path
        Root spelling recorded on the injected graph, which may be a symlink
        alias of ``root_path``.
    manifest_path : Path
        Manifest path as recorded on the injected crate.
    key : str
        Configuration key or role naming the manifest in diagnostics.

    Returns
    -------
    Path
        The manifest path relative to ``root_path``.

    Raises
    ------
    UnsafeBumpTargetError
        If the manifest escapes both spellings of the root or contains a
        parent-traversing component.
    """
    try:
        relative = manifest_path.relative_to(graph_spelling)
    except ValueError:
        return relative_to_root(root_path, manifest_path, key=key, label="manifest")
    if ".." in relative.parts:
        message = f"{key}: manifest contains a parent traversal: {manifest_path}"
        raise UnsafeBumpTargetError(message)
    return relative
