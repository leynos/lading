"""Plan and apply opted-in rewrites in non-member Cargo manifests."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import glob
import re
import typing as typ
from pathlib import Path

from tomlkit.container import OutOfOrderTableProxy
from tomlkit.exceptions import ParseError
from tomlkit.items import InlineTable, Item, Table

from lading.commands import bump_lockfile_paths, bump_toml
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
            _rewrite_dependency_requirements(
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
    root_manifest = (workspace_root / "Cargo.toml").resolve()
    skipped_manifests = {root_manifest}
    skipped_manifests.update(
        (workspace_root / path).resolve() for path in member_manifest_paths
    )
    selections: dict[Path, _RewriteSelection] = {}
    for group in rewrite_groups:
        for configured_path in group.paths:
            candidates = _expand_path(workspace_root, configured_path)
            for candidate in candidates:
                manifest_path = bump_lockfile_paths.resolve_manifest_candidate(
                    workspace_root,
                    candidate,
                    error_type=ManifestRewriteError,
                    label="Manifest rewrite",
                )
                if not manifest_path.is_file():
                    message = (
                        "Manifest rewrite path must point to a regular file: "
                        f"{configured_path}"
                    )
                    raise ManifestRewriteError(message)
                if manifest_path in skipped_manifests:
                    continue
                selection = selections.setdefault(manifest_path, _RewriteSelection())
                selection.dependencies |= group.dependencies
                selection.string_values.extend(group.string_values)
    return selections


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


def _rewrite_dependency_requirements(
    document: TOMLDocument,
    updated_crate_names: cabc.Collection[str],
    target_version: str,
) -> bool:
    """Rewrite matching dependency requirements in Cargo dependency tables."""
    changed = False
    for section in bump_toml.DEPENDENCY_SECTIONS:
        changed |= _rewrite_dependency_section(
            document, (section,), updated_crate_names, target_version
        )
        changed |= _rewrite_dependency_section(
            document,
            ("workspace", section),
            updated_crate_names,
            target_version,
        )
    target_table = bump_toml.select_table(document, ("target",))
    if target_table is not None:
        for selector in tuple(target_table):
            for section in bump_toml.DEPENDENCY_SECTIONS:
                changed |= _rewrite_dependency_section(
                    document,
                    ("target", selector, section),
                    updated_crate_names,
                    target_version,
                )
    return changed


def _rewrite_dependency_section(
    document: TOMLDocument,
    path: tuple[str, ...],
    updated_crate_names: cabc.Collection[str],
    target_version: str,
) -> bool:
    """Match dependency keys and package aliases, then reuse section updates."""
    table = bump_toml.select_table(document, path)
    if table is None:
        return False
    matching_names = {
        name
        for name, entry in tuple(table.items())
        if _is_matching_dependency(name, entry, updated_crate_names)
    }
    if not matching_names:
        return False
    return bump_toml.update_section(document, path, matching_names, target_version)


def _is_matching_dependency(
    name: str,
    entry: object,
    updated_crate_names: cabc.Collection[str],
) -> bool:
    """Return whether a dependency key or package alias is safe to update."""
    if isinstance(entry, _TABLE_TYPES):
        workspace_flag = entry.get("workspace")
        if isinstance(workspace_flag, Item):
            workspace_flag = workspace_flag.value
        if workspace_flag is True:
            return False
        package_name = bump_toml.value_as_string(entry.get("package"))
        return name in updated_crate_names or package_name in updated_crate_names
    return name in updated_crate_names and bump_toml.value_as_string(entry) is not None


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
    changed = False
    if rule.field is None:
        for key, value in tuple(table.items()):
            changed |= _replace_string_item(table, key, value, replacement)
        return changed
    for _, entry in tuple(table.items()):
        if not isinstance(entry, _TABLE_TYPES):
            continue
        value = entry.get(rule.field)
        changed |= _replace_string_item(entry, rule.field, value, replacement)
    return changed


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
    pattern = re.compile(f"{prefix}(?:{alternatives}){suffix}")
    return lambda value: pattern.sub(lambda match: replacements[match.group()], value)


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
    replacement_item = bump_toml._prepare_string_value_replacement(
        value, replacement(current)
    )
    if replacement_item is None:
        return False
    table[key] = replacement_item
    return True
