"""Read and validate manifest snapshots used by rewrite planning."""

import collections.abc as cabc
import dataclasses as dc
import typing as typ
from pathlib import Path

from tomlkit import parse as parse_toml
from tomlkit.exceptions import TOMLKitError

from lading.commands.bump_manifest_rewrite_model import ManifestRewriteError

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument

    from lading.commands.bump_manifests import _BumpContext
    from lading.commands.bump_pipeline_manifest_rewrites import (
        _ManifestRewriteStage,
    )
    from lading.workspace.models import WorkspaceCrate


def _read_workspace_manifest_snapshots(
    workspace_manifest: Path,
    member_manifest_paths: cabc.Collection[Path],
) -> tuple[tuple[Path, str], ...]:
    """Read original workspace and member text for failed-stage recovery."""
    paths = dict.fromkeys((workspace_manifest, *member_manifest_paths))
    snapshots: list[tuple[Path, str]] = []
    for manifest_path in paths:
        try:
            content = manifest_path.read_text(encoding="utf-8", newline="")
        except (OSError, UnicodeError) as exc:
            message = (
                "Could not snapshot workspace manifest for rewrite recovery "
                f"{manifest_path}: {exc}"
            )
            raise ManifestRewriteError(message, failed_path=manifest_path) from exc
        snapshots.append((manifest_path, content))
    return tuple(snapshots)


def _pre_bump_versions_from_snapshots(
    context: _BumpContext,
    snapshots: cabc.Sequence[tuple[Path, str]],
) -> dict[str, str]:
    """Check captured versions against discovery and return that metadata."""
    manifest_versions = _manifest_versions_from_snapshots(context, snapshots)
    metadata_versions = {
        crate.name: crate.version for crate in context.workspace.crates
    }
    for crate in context.workspace.crates:
        if manifest_versions[crate.name] != crate.version:
            message = (
                "Manifest package version differs from workspace discovery: "
                f"{crate.manifest_path}"
            )
            raise ManifestRewriteError(message, failed_path=crate.manifest_path)
    return metadata_versions


def _manifest_versions_from_snapshots(
    context: _BumpContext,
    snapshots: cabc.Sequence[tuple[Path, str]],
) -> dict[str, str]:
    """Resolve effective crate versions from captured manifest text."""
    text_by_path = dict(snapshots)
    workspace_document = _parse_manifest_snapshot(
        context.workspace_manifest,
        text_by_path[context.workspace_manifest],
    )
    inherited_version = _workspace_inherited_version(workspace_document)
    versions: dict[str, str] = {}
    for crate in context.workspace.crates:
        document = _parse_manifest_snapshot(
            crate.manifest_path, text_by_path[crate.manifest_path]
        )
        _validate_captured_package_name(crate, document)
        versions[crate.name] = _crate_manifest_version(
            document, inherited_version, context.workspace_manifest
        )
    return versions


def _validate_captured_package_name(
    crate: WorkspaceCrate,
    document: TOMLDocument,
) -> None:
    """Require the captured package name to match workspace discovery."""
    package = document.get("package")
    captured_name = package.get("name") if isinstance(package, cabc.Mapping) else None
    if captured_name == crate.name:
        return
    message = (
        f"Manifest package name differs from workspace discovery: {crate.manifest_path}"
    )
    raise ManifestRewriteError(message, failed_path=crate.manifest_path)


def _workspace_inherited_version(document: TOMLDocument) -> str | None:
    """Return the workspace package version when it is a string."""
    workspace = document.get("workspace")
    package = workspace.get("package") if isinstance(workspace, cabc.Mapping) else None
    version = package.get("version") if isinstance(package, cabc.Mapping) else None
    return str(version) if isinstance(version, str) else None


def _crate_manifest_version(
    document: TOMLDocument,
    inherited_version: str | None,
    workspace_manifest: Path,
) -> str:
    """Resolve a crate's direct, inherited, or discovered package version."""
    package = document.get("package")
    version = package.get("version") if isinstance(package, cabc.Mapping) else None
    match version:
        case str() as direct_version:
            return direct_version
        case {"workspace": True}:
            return _require_workspace_version(inherited_version, workspace_manifest)
        case _:
            # Cargo treats an omitted package version as 0.0.0. Planning
            # compares this with discovery metadata to catch stale snapshots.
            return "0.0.0"


def _require_workspace_version(
    version: str | None,
    workspace_manifest: Path,
) -> str:
    """Require the inherited version needed to plan a fixture rewrite."""
    if version is None:
        message = (
            f"Workspace-inherited package version is unavailable in "
            f"{workspace_manifest}"
        )
        raise ManifestRewriteError(message, failed_path=workspace_manifest)
    return version


def _expected_version_values_from_snapshots(
    snapshots: cabc.Sequence[tuple[Path, str]],
) -> dict[Path, tuple[tuple[tuple[str, ...], str], ...]]:
    """Capture direct package version selectors for optimistic validation."""
    return {
        manifest_path: _expected_package_versions(
            _parse_manifest_snapshot(manifest_path, content)
        )
        for manifest_path, content in snapshots
    }


def _expected_package_versions(
    document: TOMLDocument,
) -> tuple[tuple[tuple[str, ...], str], ...]:
    """Return the direct and workspace package version values in one document."""
    return tuple(
        value
        for selector in (("package",), ("workspace", "package"))
        if (value := _version_at_selector(document, selector)) is not None
    )


def _version_at_selector(
    document: TOMLDocument,
    selector: tuple[str, ...],
) -> tuple[tuple[str, ...], str] | None:
    """Return one string version value at a literal TOML selector."""
    value: object = document
    for segment in selector:
        if not isinstance(value, cabc.Mapping):
            return None
        value = value.get(segment)
    if not isinstance(value, cabc.Mapping):
        return None
    version = value.get("version")
    if not isinstance(version, str):
        return None
    return selector, version


def _current_manifest_snapshots(
    context: _BumpContext,
) -> tuple[tuple[Path, str], ...]:
    """Read the workspace and member manifests as they stand now."""
    return _read_workspace_manifest_snapshots(
        context.workspace_manifest,
        tuple(crate.manifest_path for crate in context.workspace.crates),
    )


def _validate_pre_bump_versions(
    context: _BumpContext,
    stage: _ManifestRewriteStage,
) -> None:
    """Ensure source versions used by fixture plans remain current before writes."""
    current_versions = _manifest_versions_from_snapshots(
        context, _current_manifest_snapshots(context)
    )
    _require_matching_versions(
        context,
        current_versions,
        stage.pre_bump_versions,
        "Workspace crate version changed after manifest rewrite planning",
    )


def _validate_post_bump_versions(
    context: _BumpContext,
    stage: _ManifestRewriteStage,
    target_version: str,
) -> None:
    """Confirm member package versions before applying cached fixture plans."""
    _validate_workspace_manifest_entry(context)
    current_versions = _manifest_versions_from_snapshots(
        context, _current_manifest_snapshots(context)
    )
    _require_matching_versions(
        context,
        current_versions,
        _expected_post_bump_versions(context, stage, target_version),
        "Workspace crate version changed after member manifest updates",
    )


def _require_matching_versions(
    context: _BumpContext,
    current_versions: dict[str, str],
    expected_versions: dict[str, str],
    message: str,
) -> None:
    """Raise naming the first member whose current version is unexpected."""
    if current_versions == expected_versions:
        return
    changed_crate = next(
        name
        for name, expected in expected_versions.items()
        if current_versions.get(name) != expected
    )
    manifest_path = next(
        crate.manifest_path
        for crate in context.workspace.crates
        if crate.name == changed_crate
    )
    description = f"{message}: {manifest_path}"
    raise ManifestRewriteError(description, failed_path=manifest_path)


@dc.dataclass(frozen=True, slots=True)
class _MemberVersionExpectation:
    """Version evidence for one member after the member manifest stage."""

    pre_bump_version: str
    direct_is_updated: bool
    inherited_is_updated: bool


def _expected_post_bump_versions(
    context: _BumpContext,
    stage: _ManifestRewriteStage,
    target_version: str,
) -> dict[str, str]:
    """Return the version each member manifest must hold after member writes."""
    original_text_by_path = dict(stage.workspace_manifest_snapshots)
    return {
        crate.name: _expected_member_version(
            _member_version_expectation(context, stage, crate, original_text_by_path),
            target_version,
        )
        for crate in context.workspace.crates
    }


def _member_version_expectation(
    context: _BumpContext,
    stage: _ManifestRewriteStage,
    crate: WorkspaceCrate,
    original_text_by_path: dict[Path, str],
) -> _MemberVersionExpectation:
    """Derive whether one member's direct or inherited version was updated."""
    document = _parse_manifest_snapshot(
        crate.manifest_path, original_text_by_path[crate.manifest_path]
    )
    package = document.get("package")
    version = _package_version_value(package)
    should_apply = not context.base_options.dry_run
    return _MemberVersionExpectation(
        pre_bump_version=stage.pre_bump_versions[crate.name],
        direct_is_updated=should_apply
        and _direct_version_is_updated(context, crate, package, version),
        inherited_is_updated=should_apply
        and _inherited_version_is_updated(context, stage, version),
    )


def _package_version_value(package: object) -> object:
    """Return a package mapping's version value, or ``None`` without one."""
    return package.get("version") if isinstance(package, cabc.Mapping) else None


def _direct_version_is_updated(
    context: _BumpContext,
    crate: WorkspaceCrate,
    package: object,
    version: object,
) -> bool:
    """Return whether the member stage rewrites this crate's direct version."""
    return (
        isinstance(package, cabc.Mapping)
        and (version is None or isinstance(version, str))
        and (
            crate.manifest_path == context.workspace_manifest
            or crate.name in context.updated_crate_names
        )
    )


def _inherited_version_is_updated(
    context: _BumpContext,
    stage: _ManifestRewriteStage,
    version: object,
) -> bool:
    """Return whether the workspace version inherited by this member changed."""
    if not isinstance(version, cabc.Mapping) or version.get("workspace") is not True:
        return False
    return _workspace_version_is_updated(context, stage)


def _workspace_version_is_updated(
    context: _BumpContext,
    stage: _ManifestRewriteStage,
) -> bool:
    """Return whether the workspace manifest's package version was updated."""
    return any(
        selector == ("workspace", "package")
        for selector, _version in stage.expected_version_values.get(
            context.workspace_manifest, ()
        )
    )


def _expected_member_version(
    expectation: _MemberVersionExpectation,
    target_version: str,
) -> str:
    """Return the target version when the member stage updated this crate."""
    if expectation.direct_is_updated or expectation.inherited_is_updated:
        return target_version
    return expectation.pre_bump_version


def _validate_workspace_manifest_entry(context: _BumpContext) -> None:
    """Ensure the root manifest entry still names the discovered workspace file."""
    root_manifest = context.root_path / "Cargo.toml"
    try:
        current_destination = root_manifest.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        message = f"Could not resolve workspace root manifest {root_manifest}: {exc}"
        raise ManifestRewriteError(message, failed_path=root_manifest) from exc
    if current_destination != context.workspace_manifest:
        message = (
            f"Workspace root manifest destination changed during bump: {root_manifest}"
        )
        raise ManifestRewriteError(message, failed_path=root_manifest)


def _parse_manifest_snapshot(path: Path, content: str) -> TOMLDocument:
    """Parse a captured manifest and retain its path in failures."""
    try:
        return parse_toml(content)
    except TOMLKitError as exc:
        message = f"Could not parse manifest snapshot {path}: {exc}"
        raise ManifestRewriteError(message, failed_path=path) from exc
