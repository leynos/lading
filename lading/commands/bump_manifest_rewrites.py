"""Plan and apply opted-in rewrites in non-member Cargo manifests.

The bump pipeline resolves configured manifests and prepares their changes
before any write, then applies the plans after member manifests and before
lockfile regeneration. This keeps configuration failures fail-fast and dry
runs write-free.

Usage
-----
Call :func:`plan_manifest_rewrites` to prepare changes, then pass the returned
plans to :func:`apply_manifest_rewrites` when the pipeline reaches its rewrite
stage::

    plans = plan_manifest_rewrites(root, groups, members, versions)
    changed_paths = apply_manifest_rewrites(plans, dry_run=dry_run)
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import glob
import re
import typing as typ
from pathlib import Path

from tomlkit.container import OutOfOrderTableProxy
from tomlkit.exceptions import ParseError
from tomlkit.items import InlineTable, Table

from lading.commands import bump_lockfile_paths, bump_toml
from lading.commands.bump_manifest_rewrite_dependencies import (
    rewrite_dependency_requirements,
)
from lading.exceptions import LadingError

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument

    from lading.config import ManifestRewriteConfig, StringValueRewriteConfig

_TABLE_TYPES: typ.Final[
    tuple[type[InlineTable], type[Table], type[OutOfOrderTableProxy]]
] = (
    InlineTable,
    Table,
    OutOfOrderTableProxy,
)
_CRATE_NAME_CHARACTER: typ.Final[str] = r"A-Za-z0-9_-"
_VERSION_SUFFIX_CHARACTER: typ.Final[str] = r"A-Za-z0-9.+-"


class ManifestRewriteError(LadingError):
    """Report that configured manifest rewrites cannot be prepared."""


@dc.dataclass(frozen=True, slots=True)
class ManifestRewritePlan:
    """A non-member manifest's original and planned TOML content.

    Attributes
    ----------
    path : Path
        Resolved path to the selected Cargo manifest.
    original_text : str
        TOML content read before any rewrite was applied.
    new_text : str
        Planned TOML content, preserving tomlkit comments and formatting.
    """

    path: Path
    original_text: str
    new_text: str


@dc.dataclass(frozen=True, slots=True)
class ManifestRewriteVersions:
    """Version context shared by dependency and string rewrite passes.

    Attributes
    ----------
    updated_crate_names : Collection[str]
        Workspace crates selected for this bump.
    pre_bump_versions : Mapping[str, str]
        Current versions used to find old text in configured string values.
    target_version : str
        Version assigned to matching dependency and string references.
    """

    updated_crate_names: cabc.Collection[str]
    pre_bump_versions: cabc.Mapping[str, str]
    target_version: str


@dc.dataclass(slots=True)
class _RewriteSelection:
    """Merged rewrite rules selected for one resolved manifest path."""

    dependencies: bool = False
    string_values: list[StringValueRewriteConfig] = dc.field(default_factory=list)


@dc.dataclass(slots=True)
class _RewriteSelectionContext:
    """Track paths and merged rules while resolving configured groups."""

    workspace_root: Path
    skipped_manifests: set[Path]
    selections: dict[Path, _RewriteSelection] = dc.field(default_factory=dict)


def plan_manifest_rewrites(
    workspace_root: Path,
    rewrite_groups: cabc.Sequence[ManifestRewriteConfig],
    member_manifest_paths: cabc.Collection[Path],
    versions: ManifestRewriteVersions,
) -> tuple[ManifestRewritePlan, ...]:
    """Return planned content changes for configured non-member manifests.

    Parameters
    ----------
    workspace_root : Path
        Root directory against which configured manifest paths are resolved.
    rewrite_groups : Sequence[ManifestRewriteConfig]
        Validated, opt-in manifest allowlist and rewrite rules.
    member_manifest_paths : Collection[Path]
        Workspace member manifests, which remain owned by the normal bump
        manifest stage.
    versions : ManifestRewriteVersions
        Updated crate names, their current versions, and the target version.

    Returns
    -------
    tuple[ManifestRewritePlan, ...]
        Changed manifests in configuration and path order. Each plan retains
        the original text so callers can inspect the exact planned change.

    Raises
    ------
    ManifestRewriteError
        If a selected path is unsafe, missing, not a regular Cargo manifest,
        or cannot be read and parsed as TOML.
    """
    if not rewrite_groups:
        return ()
    root = workspace_root.resolve()
    selections = _resolve_rewrite_selections(
        root, rewrite_groups, member_manifest_paths
    )
    plans: list[ManifestRewritePlan] = []
    for manifest_path, selection in selections.items():
        try:
            document = bump_toml.parse_manifest(manifest_path)
        except (OSError, UnicodeError, ParseError) as exc:
            message = (
                "Could not read or parse manifest rewrite target "
                f"{manifest_path}: {exc}"
            )
            raise ManifestRewriteError(message) from exc
        original_text = document.as_string()
        if selection.dependencies:
            rewrite_dependency_requirements(
                document, versions.updated_crate_names, versions.target_version
            )
        for rule in selection.string_values:
            _rewrite_string_values(document, rule, versions)
        new_text = document.as_string()
        if new_text != original_text:
            plans.append(ManifestRewritePlan(manifest_path, original_text, new_text))
    return tuple(plans)


def apply_manifest_rewrites(
    plans: cabc.Sequence[ManifestRewritePlan],
    *,
    dry_run: bool,
) -> tuple[Path, ...]:
    """Write planned manifests atomically unless this is a dry run.

    Parameters
    ----------
    plans : Sequence[ManifestRewritePlan]
        Changed non-member manifest plans returned by
        :func:`plan_manifest_rewrites`.
    dry_run : bool
        When true, report planned paths without writing any files.

    Returns
    -------
    tuple[Path, ...]
        Paths represented by the plans, including during dry runs.
    """
    if plans and not dry_run:
        for plan in plans:
            bump_toml.write_atomic_text(plan.path, plan.new_text)
    return tuple(plan.path for plan in plans)


def _resolve_rewrite_selections(
    workspace_root: Path,
    rewrite_groups: cabc.Sequence[ManifestRewriteConfig],
    member_manifest_paths: cabc.Collection[Path],
) -> dict[Path, _RewriteSelection]:
    """Resolve allowlisted paths and merge rules for each non-member manifest."""
    skipped = {
        (workspace_root / "Cargo.toml").resolve(),
        *((workspace_root / path).resolve() for path in member_manifest_paths),
    }
    context = _RewriteSelectionContext(workspace_root, skipped)
    for group in rewrite_groups:
        _select_rewrite_group(context, group)
    return context.selections


def _select_rewrite_group(
    context: _RewriteSelectionContext, group: ManifestRewriteConfig
) -> None:
    """Resolve every path selected by one configuration group."""
    for configured_path in group.paths:
        _select_configured_path(context, group, configured_path)


def _select_configured_path(
    context: _RewriteSelectionContext,
    group: ManifestRewriteConfig,
    configured_path: str,
) -> None:
    """Resolve and merge every candidate matched by one configured path."""
    candidates = _expand_path(context.workspace_root, configured_path)
    for candidate in candidates:
        _merge_rewrite_candidate(context, group, configured_path, candidate)


def _merge_rewrite_candidate(
    context: _RewriteSelectionContext,
    group: ManifestRewriteConfig,
    configured_path: str,
    candidate: Path,
) -> None:
    """Validate one candidate and merge its dependency and string rules."""
    manifest_path = bump_lockfile_paths.resolve_manifest_candidate(
        context.workspace_root,
        candidate,
        error_type=ManifestRewriteError,
        label="Manifest rewrite",
    )
    if manifest_path in context.skipped_manifests:
        return
    if not manifest_path.is_file():
        message = (
            f"Manifest rewrite path must point to a regular file: {configured_path}"
        )
        raise ManifestRewriteError(message)
    selection = context.selections.setdefault(manifest_path, _RewriteSelection())
    selection.dependencies |= group.dependencies
    selection.string_values.extend(group.string_values)


def _expand_path(workspace_root: Path, configured_path: str) -> tuple[Path, ...]:
    """Expand a glob or return its single literal candidate."""
    if not glob.has_magic(configured_path):
        return (workspace_root / configured_path,)
    try:
        matches = tuple(sorted(workspace_root.glob(configured_path)))
    except (OSError, ValueError) as exc:
        message = f"Could not expand manifest rewrite glob {configured_path!r}: {exc}"
        raise ManifestRewriteError(message) from exc
    if not matches:
        message = f"Manifest rewrite glob matched no files: {configured_path}"
        raise ManifestRewriteError(message)
    return matches


def _rewrite_string_values(
    document: TOMLDocument,
    rule: StringValueRewriteConfig,
    versions: ManifestRewriteVersions,
) -> bool:
    """Apply a single bounded replacement rule to its selected TOML table."""
    replacement = _compile_string_rewrite(rule, versions)
    if replacement is None:
        return False
    table = bump_toml.select_table(document, rule.table)
    if table is None:
        return False
    if rule.field is None:
        return _rewrite_direct_string_values(table, replacement)
    return _rewrite_selected_fields(table, rule.field, replacement)


def _rewrite_direct_string_values(
    table: Table | InlineTable | OutOfOrderTableProxy,
    replacement: cabc.Callable[[str], str],
) -> bool:
    """Rewrite direct string values in a selected table."""
    changed = False
    for key, value in tuple(table.items()):
        changed |= _replace_string_item(table, key, value, replacement)
    return changed


def _rewrite_selected_fields(
    table: Table | InlineTable | OutOfOrderTableProxy,
    field: str,
    replacement: cabc.Callable[[str], str],
) -> bool:
    """Rewrite one field in each inline or standard sub-table."""
    changed = False
    for _, entry in tuple(table.items()):
        if not isinstance(entry, _TABLE_TYPES):
            continue
        value = entry.get(field)
        changed |= _replace_string_item(entry, field, value, replacement)
    return changed


def _compile_string_rewrite(
    rule: StringValueRewriteConfig,
    versions: ManifestRewriteVersions,
) -> cabc.Callable[[str], str] | None:
    """Build a one-pass replacement callable for the configured template."""
    replacements = _string_replacements(rule, versions)
    if not replacements:
        return None
    pattern = _string_replacement_pattern(rule, replacements)
    return lambda value: pattern.sub(lambda match: replacements[match.group()], value)


def _string_replacements(
    rule: StringValueRewriteConfig,
    versions: ManifestRewriteVersions,
) -> dict[str, str]:
    """Render distinct old and new strings for updated workspace crates."""
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
    return replacements


def _string_replacement_pattern(
    rule: StringValueRewriteConfig,
    replacements: cabc.Mapping[str, str],
) -> re.Pattern[str]:
    """Compile longest-first alternatives with template-specific boundaries."""
    alternatives = "|".join(
        re.escape(old_text)
        for old_text in sorted(replacements, key=lambda item: (-len(item), item))
    )
    prefix = (
        rf"(?<![{_CRATE_NAME_CHARACTER}])"
        if rule.template.startswith("{crate}")
        else ""
    )
    suffix = (
        rf"(?![{_VERSION_SUFFIX_CHARACTER}])"
        if rule.template.endswith("{version}")
        else ""
    )
    return re.compile(f"{prefix}(?:{alternatives}){suffix}")


def _replace_string_item(
    table: Table | InlineTable | OutOfOrderTableProxy,
    key: str,
    value: object,
    replacement: cabc.Callable[[str], str],
) -> bool:
    """Replace a selected string item while retaining its TOML formatting."""
    current = bump_toml.value_as_string(value)
    if current is None:
        return False
    replacement_item = bump_toml.prepare_string_value_replacement(
        value, replacement(current)
    )
    if replacement_item is None:
        return False
    table[key] = replacement_item
    return True
