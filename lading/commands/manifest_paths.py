"""Validate resolved Cargo manifest candidates for workspace commands."""

import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    from lading.exceptions import LadingError


def resolve_manifest_candidate(
    workspace_root: Path,
    manifest: str | Path,
    *,
    error_type: type[LadingError],
    label: str,
) -> Path:
    r"""Resolve one Cargo manifest after enforcing workspace and filename rules.

    Parameters
    ----------
    workspace_root : Path
        Workspace directory that owns the candidate manifest.
    manifest : str | Path
        Workspace-relative path or resolved glob candidate to validate.
    error_type : type[LadingError]
        Domain error class used for unsafe paths and invalid filenames.
    label : str
        Subject used to distinguish validation errors for each caller.

    Returns
    -------
    Path
        Resolved path to the candidate manifest.

    Raises
    ------
    LadingError
        The requested ``error_type`` when the path escapes the workspace or
        does not end in ``Cargo.toml``.

    Examples
    --------
    >>> from lading.exceptions import LadingError
    >>> from pathlib import Path
    >>> options = {"error_type": LadingError, "label": "Manifest"}
    >>> root = Path("/workspace")
    >>> candidate = resolve_manifest_candidate(
    ...     root, "fixtures/demo/Cargo.toml", **options
    ... )
    >>> candidate.relative_to(root.resolve()).as_posix()
    'fixtures/demo/Cargo.toml'
    """  # ruff: ignore[docstring-extraneous-exception]  # error_type selects the raised subclass
    candidate = (workspace_root / manifest).resolve()
    try:
        candidate.relative_to(workspace_root.resolve())
    except ValueError as exc:
        message = f"{label} path must stay within the workspace: {manifest}"
        raise error_type(message) from exc
    if candidate.name != "Cargo.toml":
        message = f"{label} path must point to a Cargo.toml file: {manifest}"
        raise error_type(message)
    return candidate
