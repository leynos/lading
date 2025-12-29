"""Version bumping command implementation."""

from __future__ import annotations

import dataclasses as dc
import os
import re
import tempfile
import types
import typing as typ
from contextlib import suppress
from pathlib import Path

from markdown_it import MarkdownIt
from tomlkit import parse as parse_toml
from tomlkit import string
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import InlineTable, Item, Table

from lading import config as config_module
from lading.utils import normalise_workspace_root

if typ.TYPE_CHECKING:
    from markdown_it.token import Token
    from tomlkit.toml_document import TOMLDocument

    from lading.config import LadingConfig
    from lading.workspace import WorkspaceCrate, WorkspaceGraph
else:  # pragma: no cover - provide runtime placeholders for type checking imports
    LadingConfig = WorkspaceCrate = WorkspaceGraph = TOMLDocument = Token = typ.Any

type _TableLike = Table | OutOfOrderTableProxy
_TABLE_LIKE_TYPES: typ.Final[tuple[type[Table], type[OutOfOrderTableProxy]]] = (
    Table,
    OutOfOrderTableProxy,
)

_WORKSPACE_SELECTORS: typ.Final[tuple[tuple[str, ...], ...]] = (
    ("package",),
    ("workspace", "package"),
)

_DEPENDENCY_SECTION_BY_KIND: typ.Final[dict[str | None, str]] = {
    None: "dependencies",
    "normal": "dependencies",
    "dev": "dev-dependencies",
    "build": "build-dependencies",
}

_NON_DIGIT_PREFIX: typ.Final[re.Pattern[str]] = re.compile(r"^([^\d]*)")


@dc.dataclass(frozen=True, slots=True)
class BumpOptions:
    """Configuration options for bump operations."""

    dry_run: bool = False
    configuration: LadingConfig | None = None
    workspace: WorkspaceGraph | None = None
    dependency_sections: typ.Mapping[str, typ.Collection[str]] = dc.field(
        default_factory=lambda: types.MappingProxyType({})
    )
    include_workspace_sections: bool = False


@dc.dataclass(frozen=True, slots=True)
class BumpChanges:
    """Collection of files altered by a bump run."""

    manifests: typ.Sequence[Path] = ()
    documents: typ.Sequence[Path] = ()


@dc.dataclass(frozen=True, slots=True)
class _BumpContext:
    """Initialisation context for bump operations."""

    root_path: Path
    configuration: LadingConfig
    workspace: WorkspaceGraph
    base_options: BumpOptions
    workspace_manifest: Path
    excluded: frozenset[str]
    updated_crate_names: frozenset[str]


def _build_changes_description(changes: BumpChanges) -> str:
    """Build a human-readable description of changed files."""
    parts: list[str] = []
    if changes.manifests:
        parts.append(f"{len(changes.manifests)} manifest(s)")
    if changes.documents:
        parts.append(f"{len(changes.documents)} documentation file(s)")
    return parts[0] if len(parts) == 1 else " and ".join(parts)


def _format_no_changes_message(target_version: str, dry_run: bool) -> str:  # noqa: FBT001
    """Format message when no changes are required."""
    if dry_run:
        return (
            "Dry run; no manifest changes required; "
            f"all versions already {target_version}."
        )
    return f"No manifest changes required; all versions already {target_version}."


def _format_header(description: str, target_version: str, dry_run: bool) -> str:  # noqa: FBT001
    """Format the summary header line."""
    if dry_run:
        return f"Dry run; would update version to {target_version} in {description}:"
    return f"Updated version to {target_version} in {description}:"


def run(
    workspace_root: Path | str,
    target_version: str,
    *,
    options: BumpOptions | None = None,
) -> str:
    """Update workspace and crate manifest versions to ``target_version``."""
    context = _initialize_bump_context(workspace_root, options)
    changed_manifests: set[Path] = set()
    _process_workspace_manifest(context, target_version, changed_manifests)
    _process_crate_manifests(context, target_version, changed_manifests)
    changed_documents = _process_documentation_files(context, target_version)
    changes = _prepare_sorted_changes(context, changed_manifests, changed_documents)
    return _format_result_message(
        changes,
        target_version,
        dry_run=context.base_options.dry_run,
        workspace_root=context.root_path,
    )


def _initialize_bump_context(
    workspace_root: Path | str,
    options: BumpOptions | None,
) -> _BumpContext:
    """Return initialised bump context for ``workspace_root``."""
    resolved_options = BumpOptions() if options is None else options
    root_path = normalise_workspace_root(workspace_root)
    configuration = resolved_options.configuration
    if configuration is None:
        configuration = config_module.current_configuration()

    workspace = resolved_options.workspace
    if workspace is None:
        from lading.workspace import load_workspace

        workspace = load_workspace(root_path)

    base_options = BumpOptions(
        dry_run=resolved_options.dry_run,
        configuration=configuration,
        workspace=workspace,
    )
    excluded = frozenset(configuration.bump.exclude)
    updated_crate_names = frozenset(
        crate.name for crate in workspace.crates if crate.name not in excluded
    )
    workspace_manifest = root_path / "Cargo.toml"
    return _BumpContext(
        root_path=root_path,
        configuration=configuration,
        workspace=workspace,
        base_options=base_options,
        workspace_manifest=workspace_manifest,
        excluded=excluded,
        updated_crate_names=updated_crate_names,
    )


def _process_workspace_manifest(
    context: _BumpContext,
    target_version: str,
    changed_manifests: set[Path],
) -> None:
    """Update the workspace manifest when necessary."""
    dependency_sections = _workspace_dependency_sections(context.updated_crate_names)
    workspace_options = dc.replace(
        context.base_options,
        dependency_sections=_freeze_dependency_sections(dependency_sections),
        include_workspace_sections=True,
    )
    if _update_manifest(
        context.workspace_manifest,
        _WORKSPACE_SELECTORS,
        target_version,
        workspace_options,
    ):
        changed_manifests.add(context.workspace_manifest)


def _process_crate_manifests(
    context: _BumpContext,
    target_version: str,
    changed_manifests: set[Path],
) -> None:
    """Update member crate manifests for the workspace."""
    for crate in context.workspace.crates:
        if _update_crate_manifest(crate, target_version, context.base_options):
            changed_manifests.add(crate.manifest_path)


def _process_documentation_files(
    context: _BumpContext,
    target_version: str,
) -> set[Path]:
    """Update configured documentation targets for the workspace."""
    documentation_paths = _resolve_documentation_targets(
        context.root_path, context.configuration.bump.documentation
    )
    return _update_documentation_files(
        documentation_paths,
        target_version,
        context.updated_crate_names,
        dry_run=context.base_options.dry_run,
    )


def _prepare_sorted_changes(
    context: _BumpContext,
    changed_manifests: set[Path],
    changed_documents: set[Path],
) -> BumpChanges:
    """Return ordered :class:`BumpChanges` suitable for result rendering."""
    ordered_manifests = tuple(
        sorted(
            changed_manifests,
            key=lambda path: (path != context.workspace_manifest, str(path)),
        )
    )
    ordered_documents: tuple[Path, ...] = tuple(
        sorted(changed_documents, key=lambda path: str(path))
    )
    return BumpChanges(manifests=ordered_manifests, documents=ordered_documents)


def _update_crate_manifest(
    crate: WorkspaceCrate,
    target_version: str,
    options: BumpOptions,
) -> bool:
    """Apply updates for ``crate`` while respecting exclusion rules."""
    configuration, workspace = _validate_bump_options(options)

    excluded = set(configuration.bump.exclude)
    updated_crate_names = {
        member.name for member in workspace.crates if member.name not in excluded
    }

    selectors = _determine_package_selectors(crate.name, excluded)
    dependency_sections = _dependency_sections_for_crate(crate, updated_crate_names)

    if _should_skip_crate_update(selectors, dependency_sections):
        return False

    crate_options = dc.replace(
        options,
        dependency_sections=_freeze_dependency_sections(dependency_sections),
    )
    return _update_manifest(
        crate.manifest_path,
        selectors,
        target_version,
        crate_options,
    )


def _format_result_message(
    changes: BumpChanges,
    target_version: str,
    *,
    dry_run: bool,
    workspace_root: Path,
) -> str:
    """Summarise the bump outcome for CLI presentation."""
    if not changes.manifests and not changes.documents:
        return _format_no_changes_message(target_version, dry_run)

    description = _build_changes_description(changes)
    header = _format_header(description, target_version, dry_run)
    formatted_paths = [
        f"- {_format_manifest_path(manifest_path, workspace_root)}"
        for manifest_path in changes.manifests
    ]
    formatted_paths.extend(
        f"- {_format_manifest_path(document_path, workspace_root)} (documentation)"
        for document_path in changes.documents
    )
    return "\n".join([header, *formatted_paths])


def _format_manifest_path(manifest_path: Path, workspace_root: Path) -> str:
    """Return ``manifest_path`` relative to ``workspace_root`` when possible."""
    try:
        relative = manifest_path.relative_to(workspace_root)
    except ValueError:
        return str(manifest_path)
    return str(relative)


def _validate_bump_options(options: BumpOptions) -> tuple[LadingConfig, WorkspaceGraph]:
    """Validate and extract required configuration and workspace from options.

    Raises:
        ValueError: If configuration or workspace is None.

    Returns:
        Tuple of (configuration, workspace).

    """
    if options.configuration is None or options.workspace is None:
        message = "BumpOptions must supply configuration and workspace."
        raise ValueError(message)
    return options.configuration, options.workspace


def _determine_package_selectors(
    crate_name: str,
    excluded: typ.Collection[str],
) -> tuple[tuple[str, ...], ...]:
    """Return package selectors for the crate, respecting exclusion rules.

    Args:
        crate_name: Name of the crate to check.
        excluded: Collection of excluded crate names.

    Returns:
        Package selectors tuple, or empty tuple if crate is excluded.

    """
    return () if crate_name in excluded else (("package",),)


def _should_skip_crate_update(
    selectors: tuple[tuple[str, ...], ...],
    dependency_sections: typ.Mapping[str, typ.Collection[str]],
) -> bool:
    """Check if a crate update should be skipped due to no work required.

    Returns:
        True if both selectors and dependency_sections are empty.

    """
    return not selectors and not dependency_sections


def _freeze_dependency_sections(
    sections: typ.Mapping[str, typ.Collection[str]],
) -> typ.Mapping[str, typ.Collection[str]]:
    """Return an immutable mapping for dependency sections."""
    if not sections:
        return types.MappingProxyType({})
    frozen_sections = {key: tuple(sorted(names)) for key, names in sections.items()}
    return types.MappingProxyType(frozen_sections)


def _update_manifest(
    manifest_path: Path,
    selectors: tuple[tuple[str, ...], ...],
    target_version: str,
    options: BumpOptions,
) -> bool:
    """Apply ``target_version`` to each table described by ``selectors``.
 
    Args:
        manifest_path: Path to the Cargo.toml manifest file.
        selectors: Tuple of key tuples identifying version tables to update.
        target_version: The target version to apply.
        options: Bump options controlling dry-run, dependency sections, and
            whether to include workspace-level dependency sections.

    Returns:
        True if any changes were made.

    """
    document = _parse_manifest(manifest_path)
    changed = False
    for selector in selectors:
        table = _select_table(document, selector)
        changed |= _assign_version(table, target_version)
    if options.dependency_sections:
        changed |= _update_dependency_sections(
            document,
            options.dependency_sections,
            target_version,
            include_workspace_sections=options.include_workspace_sections,
        )
    if changed and not options.dry_run:
        _write_atomic_text(manifest_path, document.as_string())
    return changed


def _workspace_dependency_sections(
    updated_crates: typ.Collection[str],
) -> dict[str, set[str]]:
    """Return dependency names to update for the workspace manifest."""
    crate_names = {name for name in updated_crates if name}
    if not crate_names:
        return {}
    return {
        "dependencies": set(crate_names),
        "dev-dependencies": set(crate_names),
        "build-dependencies": set(crate_names),
    }


def _dependency_sections_for_crate(
    crate: WorkspaceCrate,
    updated_crates: typ.Collection[str],
) -> dict[str, set[str]]:
    """Return dependency names grouped by section for ``crate``."""
    if not crate.dependencies:
        return {}
    targets = {name for name in updated_crates if name}
    if not targets:
        return {}
    sections: dict[str, set[str]] = {}
    for dependency in crate.dependencies:
        if dependency.name not in targets:
            continue
        section = _DEPENDENCY_SECTION_BY_KIND.get(dependency.kind, "dependencies")
        # ``manifest_name`` preserves the dependency key used in the manifest.
        # When a crate is aliased (e.g. ``alpha-core = { package = "alpha" }``)
        # the workspace dependency name remains ``alpha`` while the manifest
        # entry becomes ``alpha-core``. Recording the manifest key ensures the
        # corresponding table entry can be located and updated.
        sections.setdefault(section, set()).add(dependency.manifest_name)
    return sections


def _update_dependency_sections(
    document: TOMLDocument,
    dependency_sections: typ.Mapping[str, typ.Collection[str]],
    target_version: str,
    *,
    include_workspace_sections: bool = False,
) -> bool:
    """Apply ``target_version`` to dependency entries for the provided sections.

    When ``include_workspace_sections`` is True, workspace-level sections
    (e.g. ``[workspace.dependencies]``) are also updated.
    """
    changed = False
    for section, names in dependency_sections.items():
        if not names:
            continue
        changed |= _update_section(document, (section,), names, target_version)
        if include_workspace_sections:
            changed |= _update_section(
                document, ("workspace", section), names, target_version
            )
    return changed


def _update_section(
    document: TOMLDocument,
    path: tuple[str, ...],
    names: typ.Collection[str],
    target_version: str,
) -> bool:
    """Update dependency versions in the table at ``path``.

    Args:
        document: The parsed TOML manifest document.
        path: Tuple of keys identifying the table path (e.g., ``("workspace",
            "dependencies")``).
        names: Collection of dependency names to update.
        target_version: The target version to apply.

    Returns:
        True if any version entries were changed.

    """
    table = _select_table(document, path)
    if table is None:
        return False
    return _update_dependency_table(table, names, target_version)


def _update_dependency_table(
    table: _TableLike,
    dependency_names: typ.Collection[str],
    target_version: str,
) -> bool:
    """Update dependency requirements within ``table`` for ``dependency_names``."""
    changed = False
    for name in dependency_names:
        if name not in table:
            continue
        entry = table[name]  # type: ignore[index]  # OutOfOrderTableProxy supports indexing
        if _update_dependency_entry(table, name, entry, target_version):
            changed = True
    return changed


def _update_dependency_entry(
    container: _TableLike,
    key: str,
    entry: object,
    target_version: str,
) -> bool:
    """Update a dependency entry with ``target_version`` if it records a version."""
    if isinstance(entry, InlineTable | Table):
        return _assign_dependency_version_field(entry, target_version)
    replacement = _prepare_version_replacement(entry, target_version)
    if replacement is None:
        return False
    container[key] = replacement  # type: ignore[index]  # OutOfOrderTableProxy supports item assignment
    return True


def _assign_dependency_version_field(
    container: InlineTable | Table,
    target_version: str,
) -> bool:
    """Update the ``version`` key of ``container`` if present."""
    current = container.get("version")
    replacement = _prepare_version_replacement(current, target_version)
    if replacement is None:
        return False
    container["version"] = replacement
    return True


def _prepare_version_replacement(
    value: object,
    target_version: str,
) -> Item | None:
    """Return an updated requirement value when ``value`` stores a string."""
    current = _value_as_string(value)
    if current is None:
        return None
    replacement_text = _compose_requirement(current, target_version)
    if replacement_text == current:
        return None
    replacement = string(replacement_text)
    if isinstance(value, Item):
        with suppress(AttributeError):  # Preserve inline comments and whitespace trivia
            replacement._trivia = value._trivia  # type: ignore[attr-defined]
    return replacement


def _value_as_string(value: object) -> str | None:
    """Return ``value`` as a string if possible."""
    raw_value = value.value if isinstance(value, Item) else value
    if isinstance(raw_value, str):
        return raw_value
    return None


def _compose_requirement(existing: str, target_version: str) -> str:
    """Prefix ``target_version`` with any non-numeric operator from ``existing``."""
    match = _NON_DIGIT_PREFIX.match(existing)
    if not match:
        return target_version
    prefix = match.group(1)
    if not prefix or prefix == existing:
        return target_version
    return f"{prefix}{target_version}"


def _resolve_documentation_targets(
    workspace_root: Path, documentation: config_module.DocumentationConfig
) -> tuple[Path, ...]:
    """Return documentation files that should be scanned for version updates."""
    patterns = documentation.globs
    if not patterns:
        return ()

    resolved: set[Path] = set()
    for pattern in patterns:
        for candidate in workspace_root.glob(pattern):
            if candidate.is_file():
                resolved.add(candidate)
    return tuple(resolved)


def _update_documentation_files(
    documentation_paths: typ.Iterable[Path],
    target_version: str,
    updated_crates: typ.Collection[str],
    *,
    dry_run: bool,
) -> set[Path]:
    """Rewrite documentation TOML fences that mention workspace crates."""
    changed: set[Path] = set()
    dependency_targets = {name for name in updated_crates if name}
    for doc_path in documentation_paths:
        original_text = doc_path.read_text(encoding="utf-8")
        updated_text, snippet_changed = _rewrite_markdown_toml_fences(
            original_text, dependency_targets, target_version
        )
        if not snippet_changed:
            continue
        changed.add(doc_path)
        if not dry_run:
            _write_atomic_text(doc_path, updated_text)
    return changed


def _rewrite_markdown_toml_fences(
    markdown_text: str,
    dependency_targets: typ.Collection[str],
    target_version: str,
) -> tuple[str, bool]:
    """Rewrite TOML fences for ``dependency_targets`` within Markdown text."""
    changed = False

    def _apply(snippet: str) -> str:
        nonlocal changed
        replacement, snippet_changed = _update_toml_snippet_versions(
            snippet, dependency_targets, target_version
        )
        if snippet_changed:
            changed = True
        return replacement

    updated = _replace_markdown_fences(markdown_text, "toml", _apply)
    return updated, changed


def _replace_markdown_fences(
    markdown_text: str,
    language: str,
    transform: typ.Callable[[str], str],
) -> str:
    """Replace fenced code blocks of ``language`` with ``transform``."""
    parser = MarkdownIt("commonmark")
    tokens = parser.parse(markdown_text)
    lines = markdown_text.splitlines(keepends=True)
    output: list[str] = []
    last_index = 0
    for token in tokens:
        if not _token_matches_language(token, language) or token.map is None:
            continue
        start, end = token.map
        output.append("".join(lines[last_index:start]))
        output.append(_render_fence(token, lines, language, transform))
        last_index = end
    output.append("".join(lines[last_index:]))
    return "".join(output)


def _token_matches_language(token: Token, language: str) -> bool:
    """Return ``True`` when ``token`` is a fence with ``language``."""
    if token.type != "fence":
        return False
    info = (token.info or "").split()
    info_lang = info[0].lower() if info else ""
    return info_lang == language.lower()


def _render_fence(
    token: Token,
    lines: list[str],
    language: str,
    transform: typ.Callable[[str], str],
) -> str:
    """Return a rewritten fence for ``token`` using ``transform``."""
    if token.map is None:
        message = "Fence token missing map data"
        raise ValueError(message)
    start, _ = token.map
    fence_marker = token.markup or "```"
    indent = _extract_fence_indent(lines[start], fence_marker)
    info = token.info or language
    original_body = token.content
    new_body = transform(original_body)
    suffix_match = re.search(r"(\r?\n+)$", original_body)
    suffix = suffix_match.group(1) if suffix_match else ""
    body_text = new_body.rstrip("\r\n") + suffix
    indented_body = "".join(
        f"{indent}{line}" for line in body_text.splitlines(keepends=True)
    )
    return f"{indent}{fence_marker}{info}\n{indented_body}{indent}{fence_marker}\n"


def _extract_fence_indent(line: str, fence_marker: str) -> str:
    """Return indentation preceding ``fence_marker`` in ``line``."""
    position = line.find(fence_marker)
    return "" if position < 0 else line[:position]


def _update_toml_snippet_dependencies(
    document: TOMLDocument,
    dependency_targets: typ.Collection[str],
    target_version: str,
) -> bool:
    """Update dependency sections in a TOML snippet document."""
    if not dependency_targets:
        return False

    changed = False
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        table = _select_table(document, (section,))
        if table is None:
            continue
        if _update_dependency_table(table, dependency_targets, target_version):
            changed = True
    return changed


def _update_toml_snippet_versions(
    snippet: str,
    dependency_targets: typ.Collection[str],
    target_version: str,
) -> tuple[str, bool]:
    """Return a TOML snippet with dependency versions rewritten."""
    try:
        document = parse_toml(snippet)
    except TOMLKitError:
        return snippet, False

    changed = False
    if _assign_version(_select_table(document, ("package",)), target_version):
        changed = True
    if _assign_version(
        _select_table(document, ("workspace", "package")), target_version
    ):
        changed = True

    if _update_toml_snippet_dependencies(document, dependency_targets, target_version):
        changed = True

    if not changed:
        return snippet, False

    suffix_match = re.search(r"((?:\r?\n)*)$", snippet)
    newline_suffix = suffix_match.group(1) if suffix_match else ""
    rendered = document.as_string().rstrip("\r\n")
    return (f"{rendered}{newline_suffix}" if newline_suffix else rendered, True)


def _parse_manifest(manifest_path: Path) -> TOMLDocument:
    """Load ``manifest_path`` into a :class:`tomlkit` document."""
    content = manifest_path.read_text(encoding="utf-8")
    return parse_toml(content)


def _select_table(
    document: TOMLDocument | _TableLike,
    keys: tuple[str, ...],
) -> _TableLike | None:
    """Return the nested table located by ``keys`` if it exists."""
    if not keys:
        return document if isinstance(document, _TABLE_LIKE_TYPES) else None
    current: object = document
    for key in keys:
        getter = getattr(current, "get", None)
        if getter is None:
            return None
        next_value = getter(key)
        if not isinstance(next_value, _TABLE_LIKE_TYPES):
            return None
        current = next_value
    return current if isinstance(current, _TABLE_LIKE_TYPES) else None


def _assign_version(table: _TableLike | None, target_version: str) -> bool:
    """Update ``table['version']`` when ``table`` is present."""
    if table is None:
        return False
    current = table.get("version")
    if _value_matches(current, target_version):
        return False
    if isinstance(current, Item):
        replacement = string(target_version)
        with suppress(AttributeError):  # Preserve existing formatting and comments
            replacement._trivia = current._trivia  # type: ignore[attr-defined]
        table["version"] = replacement
    else:
        table["version"] = target_version
    return True


def _value_matches(value: object, expected: str) -> bool:
    """Return ``True`` when ``value`` already equals ``expected``."""
    if isinstance(value, Item):
        return value.value == expected
    return value == expected


def _write_atomic_text(file_path: Path, content: str) -> None:
    """Persist ``content`` to ``file_path`` atomically using UTF-8 encoding."""
    dirpath = file_path.parent
    existing_mode: int | None = None
    with suppress(FileNotFoundError):
        existing_mode = file_path.stat().st_mode
    fd, tmp_path = tempfile.mkstemp(
        dir=dirpath,
        prefix=f"{file_path.name}.",
        text=True,
    )
    try:
        if existing_mode is not None:
            with suppress(AttributeError):
                os.fchmod(fd, existing_mode)  # not available on Windows
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        Path(tmp_path).replace(file_path)
    finally:
        with suppress(FileNotFoundError):
            Path(tmp_path).unlink()
