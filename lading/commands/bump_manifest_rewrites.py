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
import re
import typing as typ

from tomlkit import parse as parse_toml
from tomlkit.exceptions import TOMLKitError

from lading.commands import bump_toml
from lading.commands.bump_manifest_rewrite_application import (
    apply_manifest_rewrites,
)
from lading.commands.bump_manifest_rewrite_dependencies import (
    rewrite_dependency_requirements,
)
from lading.commands.bump_manifest_rewrite_model import (
    ManifestRewriteError,
    ManifestRewritePlan,
    ManifestRewriteSource,
    ManifestRewriteVersions,
)

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument

    from lading.config import StringValueRewriteConfig

__all__ = [
    "ManifestRewriteError",
    "ManifestRewritePlan",
    "ManifestRewriteSource",
    "ManifestRewriteVersions",
    "apply_manifest_rewrites",
    "plan_manifest_rewrites",
]

_CRATE_NAME_CHARACTER: typ.Final[str] = r"A-Za-z0-9_-"
_VERSION_PREFIX_CHARACTER: typ.Final[str] = r"A-Za-z0-9."
_VERSION_SUFFIX_CHARACTER: typ.Final[str] = r"A-Za-z0-9.+-"
_LEADING_BOUNDARIES: typ.Final[tuple[tuple[str, str], ...]] = (
    ("{crate}", rf"(?<![{_CRATE_NAME_CHARACTER}])"),
    ("{version}", rf"(?<![{_VERSION_PREFIX_CHARACTER}])"),
)
_TRAILING_BOUNDARIES: typ.Final[tuple[tuple[str, str], ...]] = (
    ("{version}", rf"(?![{_VERSION_SUFFIX_CHARACTER}])"),
    ("{crate}", rf"(?![{_CRATE_NAME_CHARACTER}])"),
)


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
        If any source is malformed TOML. No source is written by this function.
    """
    plans: list[ManifestRewritePlan] = []
    for source in sources:
        try:
            document = parse_toml(source.content)
        except TOMLKitError as exc:
            message = f"Could not parse manifest rewrite target {source.path}: {exc}"
            raise ManifestRewriteError(message) from exc
        if source.dependencies:
            rewrite_dependency_requirements(
                document, versions.updated_crate_names, versions.target_version
            )
        for rule in source.string_values:
            _rewrite_string_values(document, rule, versions)
        new_text = document.as_string()
        if new_text != source.content:
            plans.append(ManifestRewritePlan(source.path, source.content, new_text))
    return tuple(plans)


def _rewrite_string_values(
    document: TOMLDocument,
    rule: StringValueRewriteConfig,
    versions: ManifestRewriteVersions,
) -> bool:
    """Apply one bounded replacement rule to its selected TOML table."""
    replacement = _compile_string_rewrite(rule, versions)
    if replacement is None:
        return False
    table = bump_toml.select_table(document, rule.table)
    if table is None:
        return False
    return bump_toml.update_string_values(table, replacement, field=rule.field)


def _compile_string_rewrite(
    rule: StringValueRewriteConfig,
    versions: ManifestRewriteVersions,
) -> cabc.Callable[[str], str] | None:
    """Build a one-pass replacement callable for the configured template."""
    replacements: dict[str, str] = {}
    for crate_name in versions.updated_crate_names:
        old_version = versions.pre_bump_versions.get(crate_name)
        if old_version is None:
            continue
        old_text = rule.template.format(crate=crate_name, version=old_version)
        new_text = rule.template.format(
            crate=crate_name, version=versions.target_version
        )
        if old_text != new_text:
            replacements[old_text] = new_text
    if not replacements:
        return None
    pattern = _string_replacement_pattern(rule.template, replacements)
    return lambda value: pattern.sub(lambda match: replacements[match.group()], value)


def _string_replacement_pattern(
    template: str,
    replacements: cabc.Mapping[str, str],
) -> re.Pattern[str]:
    """Compile longest-first alternatives with template-specific boundaries."""
    alternatives = "|".join(
        re.escape(old_text)
        for old_text in sorted(replacements, key=lambda item: (-len(item), item))
    )
    prefix = _template_boundary(template, str.startswith, _LEADING_BOUNDARIES)
    suffix = _template_boundary(template, str.endswith, _TRAILING_BOUNDARIES)
    return re.compile(f"{prefix}(?:{alternatives}){suffix}")


def _template_boundary(
    template: str,
    matches_edge: cabc.Callable[[str, str], bool],
    boundaries: cabc.Sequence[tuple[str, str]],
) -> str:
    """Return the boundary expression for the matching template edge."""
    for placeholder, boundary_expression in boundaries:
        if matches_edge(template, placeholder):
            return boundary_expression
    return ""
