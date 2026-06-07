"""Workspace README adoption helpers for version bumping.

This module transposes the workspace ``README.md`` into crates that opt into
``package.readme.workspace`` and rewrites relative Markdown links so they keep
resolving from the crate directory.

Examples
--------
>>> from pathlib import Path
>>> compute_link_prefix(Path("crates/example"))
'../../'
>>> rewrite_relative_links("[Guide](docs/guide.md)", "../../")
('[Guide](../../docs/guide.md)', True)

"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

from lading.commands import bump_toml
from lading.commands.publish_manifest import PublishPreparationError

if typ.TYPE_CHECKING:
    from lading.workspace import WorkspaceCrate
else:  # pragma: no cover - provide runtime placeholders for type checking imports
    WorkspaceCrate = typ.Any

_MARKDOWN_LINK_TARGET: typ.Final[re.Pattern[str]] = re.compile(
    r"(!?\[[^\]]*]\()([^\s)]*)([^)]*\))"
)
_ABSOLUTE_LINK_PREFIXES: typ.Final[tuple[str, ...]] = (
    "http://",
    "https://",
    "//",
    "/",
    "#",
)


def compute_link_prefix(crate_relative_path: Path) -> str:
    """Return the relative path prefix from a crate to the workspace root.

    Parameters
    ----------
    crate_relative_path
        The crate root path relative to the workspace root.

    Returns
    -------
    str
        A sequence of ``../`` segments matching the crate path depth.

    Examples
    --------
    >>> compute_link_prefix(Path("crates/foo"))
    '../../'
    >>> compute_link_prefix(Path("crates/nested/deep"))
    '../../../'

    """
    return "../" * len(crate_relative_path.parts)


def rewrite_relative_links(markdown_text: str, prefix: str) -> tuple[str, bool]:
    """Rewrite relative Markdown links by prepending ``prefix``.

    Parameters
    ----------
    markdown_text
        Markdown source containing inline links or images.
    prefix
        Relative path prefix to apply to non-absolute link targets.

    Returns
    -------
    tuple[str, bool]
        A tuple of rewritten Markdown and a flag indicating whether at least
        one link target changed.

    Examples
    --------
    >>> rewrite_relative_links("![Logo](assets/logo.png)", "../../")
    ('![Logo](../../assets/logo.png)', True)
    >>> rewrite_relative_links("[Site](https://example.com)", "../../")
    ('[Site](https://example.com)', False)

    """
    changed = False

    def _replace(match: re.Match[str]) -> str:
        nonlocal changed
        opener, target, suffix = match.groups()
        if not _should_rewrite_link_target(target):
            return match.group(0)
        changed = True
        return f"{opener}{prefix}{target}{suffix}"

    rewritten = _MARKDOWN_LINK_TARGET.sub(_replace, markdown_text)
    return rewritten, changed


def transpose_readme_to_crate(
    workspace_root: Path, crate: WorkspaceCrate, *, dry_run: bool
) -> Path | None:
    """Transpose the workspace README into ``crate`` when content changes.

    Parameters
    ----------
    workspace_root
        Root directory containing the source ``README.md``.
    crate
        Workspace crate that should receive the adopted README.
    dry_run
        When True, report changes without writing the target file.

    Returns
    -------
    Path | None
        The target README path when it was, or would be, created or modified;
        otherwise ``None``.

    Raises
    ------
    PublishPreparationError
        Raised when the workspace README is required but missing, or when the
        crate root is outside the workspace.

    """
    source_readme = workspace_root / "README.md"
    if not source_readme.exists():
        message = (
            "Workspace README.md is required by crates that set readme.workspace = true"
        )
        raise PublishPreparationError(message)

    try:
        crate_relative_path = crate.root_path.relative_to(workspace_root)
    except ValueError as exc:
        message = (
            f"Crate {crate.name!r} is outside the workspace root; "
            "cannot transpose README"
        )
        raise PublishPreparationError(message) from exc

    source_text = source_readme.read_text(encoding="utf-8")
    rewritten_text = rewrite_relative_links(
        source_text, compute_link_prefix(crate_relative_path)
    )[0]

    target_readme = crate.root_path / "README.md"
    if (
        target_readme.exists()
        and target_readme.read_text(encoding="utf-8") == rewritten_text
    ):
        return None
    if not dry_run:
        bump_toml.write_atomic_text(target_readme, rewritten_text)
    return target_readme


def _should_rewrite_link_target(target: str) -> bool:
    """Return True when ``target`` is a non-empty relative Markdown URL."""
    return bool(target) and not target.startswith(_ABSOLUTE_LINK_PREFIXES)


__all__ = [
    "compute_link_prefix",
    "rewrite_relative_links",
    "transpose_readme_to_crate",
]
