"""Validate Cargo manifest paths and project their lockfile targets.

This module keeps workspace-boundary and ``Cargo.toml`` validation shared
between dry-run projection and live regeneration. Dry runs use
:func:`resolve_lockfile_paths` to report the root and configured lockfiles
without invoking Cargo.

Examples
--------
Project the lockfiles that a configured nested manifest would update:

```python
resolve_lockfile_paths(workspace_root, ("fixtures/example/Cargo.toml",))
```
"""

from __future__ import annotations

import collections.abc as cabc
from pathlib import Path

from lading.exceptions import LadingError


class LockfileRegenerationError(LadingError):
    """Report that Cargo lockfile regeneration cannot be prepared or completed.

    Notes
    -----
    This exception derives from :class:`lading.exceptions.LadingError`. It is
    raised when configured lockfile-regeneration manifests cannot be validated
    or when regeneration cannot proceed.

    """


def resolve_lockfile_paths(
    workspace_root: Path,
    lockfile_manifests: cabc.Sequence[str],
) -> tuple[Path, ...]:
    """Return lockfile paths implied by configured manifest paths.

    Parameters
    ----------
    workspace_root : Path
        Absolute path to the Cargo workspace root.
    lockfile_manifests : Sequence[str]
        Configured manifest paths relative to *workspace_root*.

    Returns
    -------
    tuple[Path, ...]
        Execution-ordered, de-duplicated paths to the implied ``Cargo.lock``
        files, with the workspace-root lockfile first.

    Raises
    ------
    LockfileRegenerationError
        If a configured manifest escapes the workspace or is not named
        ``Cargo.toml``, as enforced by :func:`resolve_manifest_paths`.

    Examples
    --------
    The workspace-root lockfile is returned before configured lockfiles:

    ```python
    root = Path("/workspace")
    paths = resolve_lockfile_paths(root, ("fixtures/minimal/Cargo.toml",))
    [path.as_posix() for path in paths]
    # ["/workspace/Cargo.lock", "/workspace/fixtures/minimal/Cargo.lock"]
    ```

    """  # ruff: ignore[docstring-extraneous-exception]  # delegated validator raises this documented error
    manifests = resolve_manifest_paths(workspace_root, lockfile_manifests)
    return tuple(manifest.parent / "Cargo.lock" for manifest in manifests)


def resolve_manifest_paths(
    workspace_root: Path,
    lockfile_manifests: cabc.Sequence[str],
) -> tuple[Path, ...]:
    """Return validated manifest paths in lockfile execution order.

    Parameters
    ----------
    workspace_root : Path
        Absolute path to the Cargo workspace root.
    lockfile_manifests : Sequence[str]
        Configured manifest paths relative to *workspace_root*.

    Returns
    -------
    tuple[Path, ...]
        Execution-ordered, de-duplicated manifest paths, with the workspace-root
        ``Cargo.toml`` first.

    Raises
    ------
    LockfileRegenerationError
        If a configured manifest escapes the workspace or is not named
        ``Cargo.toml``.

    Examples
    --------
    Equivalent spellings normalize to one manifest after the workspace root:

    ```python
    root = Path("/workspace")
    manifests = resolve_manifest_paths(
        root,
        ("fixtures/../fixtures/minimal/Cargo.toml", "fixtures/minimal/Cargo.toml"),
    )
    [manifest.as_posix() for manifest in manifests]
    # ["/workspace/Cargo.toml", "/workspace/fixtures/minimal/Cargo.toml"]
    ```

    """  # ruff: ignore[docstring-extraneous-exception]  # shared candidate validator raises this error
    root_manifest = (workspace_root / "Cargo.toml").resolve()
    seen_manifests: set[Path] = {root_manifest}
    manifests = [root_manifest]
    for manifest in lockfile_manifests:
        candidate = resolve_manifest_candidate(
            workspace_root, manifest, error_type=LockfileRegenerationError
        )
        if candidate in seen_manifests:
            continue
        seen_manifests.add(candidate)
        manifests.append(candidate)
    return tuple(manifests)


def resolve_manifest_candidate(
    workspace_root: Path,
    manifest: str | Path,
    *,
    error_type: type[LadingError] = LockfileRegenerationError,
    label: str = "Lockfile manifest",
) -> Path:
    r"""Resolve one Cargo manifest after enforcing workspace and filename rules.

    Parameters
    ----------
    workspace_root : Path
        Workspace directory that owns the candidate manifest.
    manifest : str | Path
        Workspace-relative path or resolved glob candidate to validate.
    error_type : type[LadingError], default LockfileRegenerationError
        Domain error class used for unsafe paths and invalid filenames.
    label : str, default "Lockfile manifest"
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
    >>> from pathlib import Path
    >>> resolve_manifest_candidate(Path("/workspace"), "fixtures/demo/Cargo.toml")
    PosixPath('/workspace/fixtures/demo/Cargo.toml')
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
