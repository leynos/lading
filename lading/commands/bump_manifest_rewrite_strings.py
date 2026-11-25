r"""Compile selected TOML string rewrites for configured manifests."""

import bisect
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

    from lading.commands.bump_toml_types import _TableLike
    from lading.config import StringValueRewriteConfig

_CRATE_NAME_CHARACTER: typ.Final[str] = r"A-Za-z0-9_-"
_VERSION_PREFIX_CHARACTER: typ.Final[str] = r"A-Za-z0-9."
_VERSION_SUFFIX_CHARACTER: typ.Final[str] = r"A-Za-z0-9.+-"
_SEMVER_COMPONENT_SUFFIXES: typ.Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9.])\d+\.\d+\.\d+"
    r"(?P<prerelease>-(?:[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?)?"
    r"(?P<build>\+(?:[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?)?"
)
# A configured match may include the build-metadata separator itself.
_SEMVER_MATCH_TEXT: typ.Final[re.Pattern[str]] = re.compile(r"[+-]?[0-9A-Za-z.+-]+")
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
    rules: cabc.Sequence[StringValueRewriteConfig],
    versions: ManifestRewriteVersions,
    manifest_path: Path,
) -> bool:
    """Apply rules against one snapshot of the selected source string values."""
    if len(rules) == 1:
        return _rewrite_one_string_rule(document, rules[0], versions, manifest_path)

    targets: dict[tuple[int, str], tuple[_TableLike, str, object, set[str]]] = {}
    for rule in rules:
        table = bump_toml.select_table(document, rule.table)
        if table is None:
            continue
        _raise_if_pre_bump_versions_are_missing(manifest_path, versions)
        replacement = _compile_string_rewrite(rule, versions, manifest_path)
        if replacement is None:
            continue
        _collect_replacements_for_rule(table, rule, replacement, targets)
    return _apply_snapshot_replacements(targets, manifest_path)


def _rewrite_one_string_rule(
    document: TOMLDocument,
    rule: StringValueRewriteConfig,
    versions: ManifestRewriteVersions,
    manifest_path: Path,
) -> bool:
    """Use the shared table updater when only one selector can run."""
    table = bump_toml.select_table(document, rule.table)
    if table is None:
        return False
    _raise_if_pre_bump_versions_are_missing(manifest_path, versions)
    replacement = _compile_string_rewrite(rule, versions, manifest_path)
    if replacement is None:
        return False
    return bump_toml_strings.update_string_values(table, replacement, field=rule.field)


def _collect_replacements_for_rule(
    table: _TableLike,
    rule: StringValueRewriteConfig,
    replacement: cabc.Callable[[str], str],
    targets: dict[tuple[int, str], tuple[_TableLike, str, object, set[str]]],
) -> None:
    """Collect one rule's output without mutating its source values."""
    for container, key, value in bump_toml_strings._selected_string_value_targets(
        table, rule.field
    ):
        current = bump_toml.value_as_string(value)
        if current is None:
            continue
        replacement_text = replacement(current)
        if replacement_text == current:
            continue
        target_key = (id(container), key)
        target = targets.get(target_key)
        if target is None:
            targets[target_key] = (container, key, value, {replacement_text})
        else:
            target[3].add(replacement_text)


def _apply_snapshot_replacements(
    targets: cabc.Mapping[tuple[int, str], tuple[_TableLike, str, object, set[str]]],
    manifest_path: Path,
) -> bool:
    """Commit agreed outputs and reject overlapping rules with different text."""
    changed = False
    for container, key, value, replacements in targets.values():
        if len(replacements) != 1:
            message = (
                "Configured string rewrite selectors produce conflicting "
                f"replacements for one TOML value in {manifest_path}"
            )
            raise ManifestRewriteError(message, failed_path=manifest_path)
        replacement_text = next(iter(replacements))
        if bump_toml_strings._replace_selected_string_value(
            container, key, value, replacement_text
        ):
            changed = True
    return changed


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
    return lambda value: _rewrite_string_value(value, pattern, matching_replacements)


def _rewrite_string_value(
    value: str,
    pattern: re.Pattern[str],
    replacements: cabc.Mapping[str, str],
) -> str:
    """Rewrite once using SemVer ranges calculated for this source value."""
    protected_ranges = _semver_component_ranges(value)
    range_starts = tuple(start for start, _end in protected_ranges)
    return pattern.sub(
        lambda match: _replacement_for_match(
            match, replacements, protected_ranges, range_starts
        ),
        value,
    )


def _replacement_for_match(
    match: re.Match[str],
    replacements: cabc.Mapping[str, str],
    protected_ranges: tuple[tuple[int, int], ...],
    range_starts: tuple[int, ...],
) -> str:
    """Replace a match unless it is nested in a SemVer prerelease or build."""
    matched_text = match.group()
    if _SEMVER_MATCH_TEXT.fullmatch(matched_text) is not None and (
        _is_semver_component_match(match.start(), protected_ranges, range_starts)
    ):
        return matched_text
    return replacements[matched_text]


def _semver_component_ranges(value: str) -> tuple[tuple[int, int], ...]:
    """Return prerelease and build-metadata ranges in a source value."""
    ranges: list[tuple[int, int]] = []
    for match in _SEMVER_COMPONENT_SUFFIXES.finditer(value):
        for component in ("prerelease", "build"):
            start, end = match.span(component)
            if start >= 0:
                ranges.append((start, end))
    return tuple(ranges)


def _is_semver_component_match(
    match_start: int,
    protected_ranges: tuple[tuple[int, int], ...],
    range_starts: tuple[int, ...],
) -> bool:
    """Check a match start against the precomputed SemVer component ranges."""
    range_index = bisect.bisect_right(range_starts, match_start) - 1
    return range_index >= 0 and match_start < protected_ranges[range_index][1]


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
