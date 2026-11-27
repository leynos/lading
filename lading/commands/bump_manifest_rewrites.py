r"""Plan opted-in rewrites from manifest text and version metadata.

This module applies Cargo dependency and selected string rules to source
records supplied by :mod:`lading.commands.bump_manifest_rewrite_filesystem`.
It performs no path resolution or file access.

Examples
--------
>>> from pathlib import Path
>>> from lading.commands.bump_manifest_rewrite_model import (
...     ManifestRewriteSource, ManifestRewriteVersions,
... )
>>> from lading.commands.bump_manifest_rewrites import plan_manifest_rewrites
>>> source = ManifestRewriteSource(
...     Path("fixtures/demo/Cargo.toml"),
...     '[dependencies]\nalpha = "1.0.0"\n', True, (),
... )
>>> plan = plan_manifest_rewrites(
...     (source,), ManifestRewriteVersions({"alpha"}, {"alpha": "1.0.0"}, "2.0.0")
... )[0]
>>> plan.new_text
'[dependencies]\nalpha = "2.0.0"\n'
"""

import collections.abc as cabc
import typing as typ

from tomlkit import parse as parse_toml
from tomlkit.exceptions import TOMLKitError

from lading.commands.bump_manifest_rewrite_dependencies import (
    rewrite_dependency_requirements,
)
from lading.commands.bump_manifest_rewrite_model import (
    ManifestRewriteError,
    ManifestRewritePlan,
    ManifestRewriteSource,
    ManifestRewriteVersions,
    ManifestRewriteWriteError,
)
from lading.commands.bump_manifest_rewrite_strings import (
    _rewrite_string_values,
)

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument


__all__ = [
    "ManifestRewriteError",
    "ManifestRewritePlan",
    "ManifestRewriteSource",
    "ManifestRewriteVersions",
    "ManifestRewriteWriteError",
    "plan_manifest_rewrites",
]


def plan_manifest_rewrites(
    sources: cabc.Sequence[ManifestRewriteSource],
    versions: ManifestRewriteVersions,
) -> tuple[ManifestRewritePlan, ...]:
    """Return planned content changes for resolved manifest sources.

    Parameters
    ----------
    sources : Sequence[ManifestRewriteSource]
        Original manifest text and the rules selected by the filesystem edge.
    versions : ManifestRewriteVersions
        Updated crate names, their pre-bump versions, and the target version.

    Returns
    -------
    tuple[ManifestRewritePlan, ...]
        Changed manifests in source order. Each plan retains its original text.

    Raises
    ------
    ManifestRewriteError
        If any source is malformed TOML, a selected string rule lacks
        pre-bump version metadata, or configured string templates have
        ambiguous renderings. Unmatched selected strings are preserved because
        they may be intentional historical values. No source is written by this
        function.
    """
    plans: list[ManifestRewritePlan] = []
    for source in sources:
        try:
            document = parse_toml(source.content)
        except TOMLKitError as exc:
            message = f"Could not parse manifest rewrite target {source.path}: {exc}"
            raise ManifestRewriteError(message, failed_path=source.path) from exc
        _rewrite_manifest_document(source, document, versions)
        new_text = document.as_string()
        if new_text != source.content:
            plans.append(ManifestRewritePlan(source.path, source.content, new_text))
    return tuple(plans)


def _rewrite_manifest_document(
    source: ManifestRewriteSource,
    document: TOMLDocument,
    versions: ManifestRewriteVersions,
) -> None:
    """Apply configured dependency and string rules to one parsed manifest."""
    if source.dependencies:
        rewrite_dependency_requirements(
            document, versions.updated_crate_names, versions.target_version
        )
    _rewrite_string_values(document, source.string_values, versions, source.path)
