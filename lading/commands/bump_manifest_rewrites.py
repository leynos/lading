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
import string as string_module
import typing as typ
from pathlib import Path

from tomlkit import parse as parse_toml
from tomlkit.exceptions import TOMLKitError

from lading.commands import bump_toml, bump_toml_strings
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
    ManifestRewriteWriteError,
)

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument

    from lading.config import StringValueRewriteConfig

__all__ = [
    "ManifestRewriteError",
    "ManifestRewritePlan",
    "ManifestRewriteSource",
    "ManifestRewriteVersions",
    "ManifestRewriteWriteError",
    "apply_manifest_rewrites",
    "plan_manifest_rewrites",
]

_CRATE_NAME_CHARACTER: typ.Final[str] = r"A-Za-z0-9_-"
_VERSION_PREFIX_CHARACTER: typ.Final[str] = r"A-Za-z0-9."
_VERSION_SUFFIX_CHARACTER: typ.Final[str] = r"A-Za-z0-9.+-"
_SEMVER_TEXT: typ.Final[str] = r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
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
    for rule in source.string_values:
        _raise_if_string_rewrite_is_stale(source.path, document, rule, versions)
        _rewrite_string_values(document, rule, versions, source.path)


def _raise_if_string_rewrite_is_stale(
    manifest_path: Path,
    document: TOMLDocument,
    rule: StringValueRewriteConfig,
    versions: ManifestRewriteVersions,
) -> None:
    """Reject fixture text that cannot be repaired after a partial bump."""
    table = bump_toml.select_table(document, rule.table)
    if table is None:
        return
    selected_values = bump_toml_strings.selected_string_values(table, field=rule.field)
    missing_versions = tuple(
        sorted(
            crate_name
            for crate_name in versions.updated_crate_names
            if crate_name not in versions.pre_bump_versions
        )
    )
    if missing_versions:
        raise ManifestRewriteError.pre_bump_versions_unavailable(
            missing_versions, manifest_path
        )
    stale_references = _stale_string_references(
        selected_values, rule.template, versions
    )
    if stale_references:
        crate_name, stale_version = stale_references[0]
        raise ManifestRewriteError.stale_template_reference(
            crate_name,
            stale_version,
            versions.target_version,
            manifest_path,
        )


def _stale_string_references(
    values: cabc.Sequence[str],
    template: str,
    versions: ManifestRewriteVersions,
) -> tuple[tuple[str | None, str], ...]:
    """Find stale values that a retry cannot attribute to a selected crate."""
    if _template_identifies_crate(template):
        return tuple(
            (crate_name, stale_version)
            for crate_name in sorted(versions.updated_crate_names)
            if versions.pre_bump_versions[crate_name] == versions.target_version
            for stale_version in _versions_for_crate_not_at_target(
                values, template, crate_name, versions.target_version
            )
        )
    pre_bump_versions = {
        versions.pre_bump_versions[crate_name]
        for crate_name in versions.updated_crate_names
    }
    if versions.target_version not in pre_bump_versions:
        return ()
    recoverable_versions = pre_bump_versions - {versions.target_version}
    return tuple(
        (None, stale_version)
        for stale_version in _versions_for_crate_not_at_target(
            values, template, "", versions.target_version
        )
        if stale_version not in recoverable_versions
    )


def _template_identifies_crate(template: str) -> bool:
    """Return whether a rewrite template identifies the crate it names."""
    return any(
        field_name == "crate"
        for _, field_name, _, _ in string_module.Formatter().parse(template)
    )


def _versions_for_crate_not_at_target(
    values: cabc.Sequence[str],
    template: str,
    crate_name: str,
    target_version: str,
) -> tuple[str, ...]:
    """Find stale versions matching one crate's configured template."""
    pattern, version_groups = _stale_template_pattern(template, crate_name)
    target_rendering = template.format(crate=crate_name, version=target_version)
    target_pattern = _string_replacement_pattern(template, {target_rendering: "\x00"})
    # TOML strings cannot contain NUL, so this marker safely separates exact
    # current renderings from stale matches without joining surrounding text.
    # The shared boundaries keep a target prefix inside a stale suffix visible.
    values_to_check = tuple(target_pattern.sub("\x00", value) for value in values)
    return _template_versions_not_target(
        values_to_check, pattern, version_groups, target_version
    )


def _stale_template_pattern(
    template: str,
    crate_name: str,
) -> tuple[re.Pattern[str], tuple[str, ...]]:
    """Compile a template matcher that captures each embedded version field."""
    pieces: list[str] = []
    version_groups: list[str] = []
    for literal, field_name, _, _ in string_module.Formatter().parse(template):
        pieces.append(re.escape(literal))
        match field_name:
            case "crate":
                pieces.append(re.escape(crate_name))
            case "version":
                group_name = f"stale_version_{len(version_groups)}"
                version_groups.append(group_name)
                pieces.append(f"(?P<{group_name}>{_SEMVER_TEXT})")
    prefix = _template_boundary(template, str.startswith, _LEADING_BOUNDARIES)
    suffix = _template_boundary(template, str.endswith, _TRAILING_BOUNDARIES)
    pattern = re.compile(f"{prefix}(?:{''.join(pieces)}){suffix}")
    return pattern, tuple(version_groups)


def _template_versions_not_target(
    values: cabc.Sequence[str],
    pattern: re.Pattern[str],
    version_groups: cabc.Sequence[str],
    target_version: str,
) -> tuple[str, ...]:
    """Find matched template versions that differ from the requested target."""
    return tuple(
        version
        for value in values
        for match in pattern.finditer(value)
        for group_name in version_groups
        if (version := match.group(group_name)) is not None
        if version != target_version
    )


def _rewrite_string_values(
    document: TOMLDocument,
    rule: StringValueRewriteConfig,
    versions: ManifestRewriteVersions,
    manifest_path: Path,
) -> bool:
    """Apply one bounded replacement rule to its selected TOML table."""
    replacement = _compile_string_rewrite(rule, versions, manifest_path)
    if replacement is None:
        return False
    table = bump_toml.select_table(document, rule.table)
    if table is None:
        return False
    return bump_toml_strings.update_string_values(table, replacement, field=rule.field)


def _compile_string_rewrite(
    rule: StringValueRewriteConfig,
    versions: ManifestRewriteVersions,
    manifest_path: Path,
) -> cabc.Callable[[str], str] | None:
    """Build a one-pass replacement callable for the configured template."""
    replacements: dict[str, str] = {}
    replacement_pairs: dict[str, tuple[str, str]] = {}
    for crate_name in sorted(versions.updated_crate_names):
        old_version = versions.pre_bump_versions.get(crate_name)
        if old_version is None:
            continue
        old_text = rule.template.format(crate=crate_name, version=old_version)
        new_text = rule.template.format(
            crate=crate_name, version=versions.target_version
        )
        previous_pair = replacement_pairs.get(old_text)
        if previous_pair is not None and previous_pair[0] != new_text:
            previous_crate = previous_pair[1]
            message = (
                "Configured string rewrite template maps workspace crates "
                f"{previous_crate!r} and {crate_name!r} from {old_text!r} "
                f"to different replacements in {manifest_path}"
            )
            raise ManifestRewriteError(message, failed_path=manifest_path)
        replacement_pairs.setdefault(old_text, (new_text, crate_name))
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
