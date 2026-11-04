"""Share version and planning helpers across non-member rewrite tests only."""

import collections.abc as cabc
import typing as typ
from pathlib import Path

from lading.commands import bump_manifest_rewrites

if typ.TYPE_CHECKING:
    from lading.config import ManifestRewriteConfig


def manifest_rewrite_versions(
    names: frozenset[str] = frozenset({"alpha"}),
    pre_bump_versions: cabc.Mapping[str, str] | None = None,
    target_version: str = "2.0.0",
) -> bump_manifest_rewrites.ManifestRewriteVersions:
    """Build a compact version context for rewrite tests."""
    return bump_manifest_rewrites.ManifestRewriteVersions(
        updated_crate_names=names,
        pre_bump_versions=(
            pre_bump_versions
            if pre_bump_versions is not None
            else dict.fromkeys(names, "1.0.0")
        ),
        target_version=target_version,
    )


def plan_fixture_rewrites(
    root: Path,
    groups: tuple[ManifestRewriteConfig, ...],
    versions: bump_manifest_rewrites.ManifestRewriteVersions | None = None,
    members: tuple[Path, ...] = (),
) -> tuple[bump_manifest_rewrites.ManifestRewritePlan, ...]:
    """Plan fixture changes using the compact default rewrite context."""
    version_context = versions if versions is not None else manifest_rewrite_versions()
    return bump_manifest_rewrites.plan_manifest_rewrites(
        root,
        groups,
        members,
        version_context,
    )
