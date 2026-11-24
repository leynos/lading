r"""Compile selected TOML string rewrites for configured manifests."""

import collections.abc as cabc
import re
import typing as typ
from pathlib import Path

from lading.commands import bump_toml, bump_toml_strings
from lading.commands.bump_manifest_rewrite_model import (
    ManifestRewriteError,
    ManifestRewriteVersions,
)

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument

    from lading.config import StringValueRewriteConfig

_CRATE_NAME_CHARACTER: typ.Final[str] = r"A-Za-z0-9_-"
_VERSION_PREFIX_CHARACTER: typ.Final[str] = r"A-Za-z0-9."
_VERSION_SUFFIX_CHARACTER: typ.Final[str] = r"A-Za-z0-9.+-"
_SEMVER_BUILD_METADATA_PREFIX: typ.Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9.])"
    r"\d+\.\d+\.\d+(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\+$"
)
_SEMVER_PRERELEASE_PREFIX: typ.Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9.])(?:"
    r"\d+\.\d+\.\d+(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?-"
    r"|"
    r"\d+\.\d+\.\d+-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*\."
    r")$"
)
_SEMVER_IDENTIFIER_CHARACTERS: typ.Final[re.Pattern[str]] = re.compile(
    r"[0-9A-Za-z.-]*"
)
# A configured match may include the build-metadata separator itself.
_SEMVER_MATCH_TEXT: typ.Final[re.Pattern[str]] = re.compile(r"[+-]?[0-9A-Za-z.-]+")
_LEADING_BOUNDARIES: typ.Final[tuple[tuple[str, str], ...]] = (
    ("{crate}", rf"(?<![{_CRATE_NAME_CHARACTER}])"),
    ("{version}", rf"(?<![{_VERSION_PREFIX_CHARACTER}])"),
)
_TRAILING_BOUNDARIES: typ.Final[tuple[tuple[str, str], ...]] = (
    ("{version}", rf"(?![{_VERSION_SUFFIX_CHARACTER}])"),
    ("{crate}", rf"(?![{_CRATE_NAME_CHARACTER}])"),
)


def _raise_if_pre_bump_versions_are_missing(
    manifest_path: Path,
    versions: ManifestRewriteVersions,
) -> None:
    """Require source versions before planning a selected rewrite rule."""
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


def _rewrite_string_values(
    document: TOMLDocument,
    rule: StringValueRewriteConfig,
    versions: ManifestRewriteVersions,
    manifest_path: Path,
) -> bool:
    """Apply one bounded replacement rule to its selected TOML table."""
    table = bump_toml.select_table(document, rule.table)
    if table is None:
        return False
    _raise_if_pre_bump_versions_are_missing(manifest_path, versions)
    replacement = _compile_string_rewrite(rule, versions, manifest_path)
    if replacement is None:
        return False
    return bump_toml_strings.update_string_values(table, replacement, field=rule.field)


def _compile_string_rewrite(
    rule: StringValueRewriteConfig,
    versions: ManifestRewriteVersions,
    manifest_path: Path,
) -> cabc.Callable[[str], str] | None:
    """Build a one-pass replacement callable for the configured template."""
    replacements, replacement_pairs = _replacement_pairs(rule, versions, manifest_path)
    if not replacements:
        return None
    target_renderings = _target_renderings(rule, versions)
    _raise_if_target_renderings_conflict(
        target_renderings, replacement_pairs, manifest_path
    )
    matching_replacements = _matching_replacements(replacements, target_renderings)
    pattern = _string_replacement_pattern(rule.template, matching_replacements)
    return lambda value: pattern.sub(
        lambda match: _replacement_for_match(value, match, matching_replacements),
        value,
    )


def _replacement_for_match(
    value: str,
    match: re.Match[str],
    replacements: cabc.Mapping[str, str],
) -> str:
    """Replace a match unless it is nested in a SemVer prerelease or build."""
    matched_text = match.group()
    if _SEMVER_MATCH_TEXT.fullmatch(matched_text) is not None and (
        _is_semver_component_match(value, match.start(), matched_text)
    ):
        return matched_text
    return replacements[matched_text]


def _is_semver_component_match(
    value: str,
    match_start: int,
    matched_text: str,
) -> bool:
    """Identify a match inside a SemVer prerelease or build-metadata segment."""
    return _is_semver_build_metadata_match(value, match_start) or (
        _is_semver_prerelease_match(
            value,
            match_start + int(matched_text.startswith("-")),
        )
    )


def _is_semver_build_metadata_match(
    value: str,
    match_start: int,
) -> bool:
    """Identify a match anywhere inside a SemVer build metadata segment."""
    separator = value.rfind("+", 0, match_start + 1)
    if separator < 0:
        return False
    semver_prefix = value[: separator + 1]
    metadata_prefix = value[separator + 1 : match_start]
    return (
        _SEMVER_BUILD_METADATA_PREFIX.search(semver_prefix) is not None
        and _SEMVER_IDENTIFIER_CHARACTERS.fullmatch(metadata_prefix) is not None
    )


def _is_semver_prerelease_match(value: str, match_start: int) -> bool:
    """Identify a match nested after a SemVer core's prerelease separator."""
    return _SEMVER_PRERELEASE_PREFIX.search(value[:match_start]) is not None


def _replacement_pairs(
    rule: StringValueRewriteConfig,
    versions: ManifestRewriteVersions,
    manifest_path: Path,
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Render old and target text while rejecting ambiguous old renderings."""
    replacements: dict[str, str] = {}
    replacement_pairs: dict[str, tuple[str, str]] = {}
    for crate_name in sorted(versions.updated_crate_names):
        old_version = versions.pre_bump_versions.get(crate_name)
        if old_version is None:
            continue
        old_text, new_text = _render_replacement(
            rule, versions, crate_name, old_version
        )
        previous_pair = replacement_pairs.get(old_text)
        if previous_pair is not None and previous_pair[0] != new_text:
            _raise_string_rewrite_conflict(
                manifest_path,
                previous_pair[1],
                crate_name,
                f"from {old_text!r} to different replacements",
            )
        replacement_pairs.setdefault(old_text, (new_text, crate_name))
        if old_text != new_text:
            replacements[old_text] = new_text
    return replacements, replacement_pairs


def _render_replacement(
    rule: StringValueRewriteConfig,
    versions: ManifestRewriteVersions,
    crate_name: str,
    old_version: str,
) -> tuple[str, str]:
    """Render one crate's old and target text from the configured template."""
    old_text = rule.template.format(crate=crate_name, version=old_version)
    new_text = rule.template.format(crate=crate_name, version=versions.target_version)
    return old_text, new_text


def _target_renderings(
    rule: StringValueRewriteConfig,
    versions: ManifestRewriteVersions,
) -> dict[str, str]:
    """Render all new crate strings used to shield target text from rescans."""
    return {
        rule.template.format(
            crate=crate_name, version=versions.target_version
        ): crate_name
        for crate_name in versions.updated_crate_names
    }


def _raise_if_target_renderings_conflict(
    target_renderings: cabc.Mapping[str, str],
    replacement_pairs: cabc.Mapping[str, tuple[str, str]],
    manifest_path: Path,
) -> None:
    """Reject target text that collides with another crate's old rendering."""
    for target_rendering, target_crate in target_renderings.items():
        previous_pair = replacement_pairs.get(target_rendering)
        if previous_pair is not None and previous_pair[0] != target_rendering:
            _raise_string_rewrite_conflict(
                manifest_path,
                previous_pair[1],
                target_crate,
                f"through conflicting rendering {target_rendering!r}",
            )


def _matching_replacements(
    replacements: cabc.Mapping[str, str],
    target_renderings: cabc.Mapping[str, str],
) -> dict[str, str]:
    """Include identity mappings that protect already-targeted text."""
    matching = dict(replacements)
    matching.update((rendering, rendering) for rendering in target_renderings)
    return matching


def _raise_string_rewrite_conflict(
    manifest_path: Path,
    first_crate: str,
    second_crate: str,
    conflict: str,
) -> typ.NoReturn:
    """Raise a path-specific error for ambiguous template renderings."""
    message = (
        "Configured string rewrite template maps workspace crates "
        f"{first_crate!r} and {second_crate!r} {conflict} in {manifest_path}"
    )
    raise ManifestRewriteError(message, failed_path=manifest_path)


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
