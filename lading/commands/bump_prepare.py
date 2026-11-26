"""Prepare every bump write before any workspace file is mutated.

Each stage here computes complete file contents and records them on a
:class:`~lading.commands.bump_plan.PlanBuilder`. Nothing is written; the
pipeline commits the finished plan only after every target has passed
confinement and every content transform has succeeded.
"""

import collections.abc as cabc
import dataclasses as dc
import logging
import typing as typ
from pathlib import PurePosixPath

from tomlkit.exceptions import TOMLKitError

from lading.commands import bump_docs, bump_readme
from lading.commands.bump_manifests import (
    _WORKSPACE_SELECTORS,
    ManifestRenderOptions,
    _dependency_sections_for_crate,
    _determine_package_selectors,
    _should_skip_crate_update,
    _workspace_dependency_sections,
    render_manifest,
)
from lading.commands.bump_plan import PlanBuilder, WriteCategory
from lading.utils.confinement import (
    BumpPreflightError,
    BumpTargetExaminationError,
    ConfinedPath,
    ConfineOptions,
    confine_relative_path,
)
from lading.workspace.graph_paths import graph_relative_manifest

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
    from lading.commands.bump_manifests import _BumpContext
    from lading.workspace import WorkspaceCrate

_log = logging.getLogger(__name__)

_ROOT_MANIFEST = "Cargo.toml"
_ROOT_MANIFEST_PARTS: tuple[str, ...] = (_ROOT_MANIFEST,)


@dc.dataclass(slots=True)
class _ManifestRequest:
    """The selectors and dependency sections accumulated for one manifest."""

    path: ConfinedPath
    selectors: list[tuple[str, ...]] = dc.field(default_factory=list)
    dependency_sections: dict[str, set[str]] = dc.field(default_factory=dict)
    include_workspace_sections: bool = False

    def add_selectors(self, selectors: cabc.Iterable[tuple[str, ...]]) -> None:
        """Append selectors that are not already present."""
        for selector in selectors:
            if selector not in self.selectors:
                self.selectors.append(selector)

    def add_sections(
        self,
        sections: cabc.Mapping[str, cabc.Collection[str]],
    ) -> None:
        """Merge dependency names into the request's section map."""
        for section, names in sections.items():
            self.dependency_sections.setdefault(section, set()).update(names)


def prepare_manifests(
    context: _BumpContext,
    target_version: str,
    builder: PlanBuilder,
) -> None:
    """Prepare every manifest write for the bump.

    Parameters
    ----------
    context : _BumpContext
        The initialised bump context.
    target_version : str
        Version to apply.
    builder : PlanBuilder
        Proposal sink.
    """
    requests = _manifest_requests(context)
    for request in requests:
        content = _render_request(request, target_version)
        if content is not None:
            builder.propose(request.path, content, WriteCategory.MANIFEST)
    _log.debug("Prepared %d manifest write(s)", len(requests))


def _manifest_requests(context: _BumpContext) -> list[_ManifestRequest]:
    """Return one request per distinct manifest path, the workspace root first."""
    workspace_manifest = confine_relative_path(
        context.root_path, _ROOT_MANIFEST, key="workspace manifest"
    )
    root_request = _ManifestRequest(path=workspace_manifest)
    root_request.add_selectors(_WORKSPACE_SELECTORS)
    root_request.add_sections(
        _workspace_dependency_sections(context.updated_crate_names)
    )
    root_request.include_workspace_sections = True
    requests = [root_request]
    requests_by_parts = {workspace_manifest.parts: root_request}
    skipped = 0
    for crate in context.workspace.crates:
        selectors = _determine_package_selectors(crate.name, context.excluded)
        sections = _dependency_sections_for_crate(crate, context.updated_crate_names)
        if _should_skip_crate_update(selectors, sections):
            skipped += 1
            continue
        request, is_new = _request_for_crate(context, crate, requests_by_parts)
        if is_new:
            requests.append(request)
        request.add_selectors(selectors)
        request.add_sections(sections)
    _log.debug("Manifest requests: %d, skipped crates: %d", len(requests), skipped)
    return requests


def _request_for_crate(
    context: _BumpContext,
    crate: WorkspaceCrate,
    requests_by_parts: dict[tuple[str, ...], _ManifestRequest],
) -> tuple[_ManifestRequest, bool]:
    """Return the request for ``crate``'s manifest and whether it is new."""
    key = f"workspace member {crate.name!r} manifest"
    relative = graph_relative_manifest(
        context.root_path,
        context.workspace.workspace_root,
        crate.manifest_path,
        key=key,
    )
    confined = confine_relative_path(context.root_path, relative.as_posix(), key=key)
    existing = requests_by_parts.get(confined.parts)
    if existing is not None:
        return existing, False
    request = _ManifestRequest(path=confined)
    requests_by_parts[confined.parts] = request
    return request, True


def _render_request(request: _ManifestRequest, target_version: str) -> str | None:
    """Render one manifest request, mapping read and parse failures."""
    key = (
        "workspace manifest"
        if request.path.parts == _ROOT_MANIFEST_PARTS
        else f"manifest {request.path.display}"
    )
    try:
        return render_manifest(
            request.path.path,
            request.selectors,
            target_version,
            ManifestRenderOptions(
                dependency_sections=request.dependency_sections,
                include_workspace_sections=request.include_workspace_sections,
            ),
        )
    except (OSError, UnicodeDecodeError) as exc:
        message = f"{key}: cannot read {request.path.display}: {exc}"
        raise BumpTargetExaminationError(message) from exc
    except TOMLKitError as exc:
        message = f"{key}: not valid TOML: {request.path.display}: {exc}"
        raise BumpPreflightError(message) from exc


def prepare_documentation(
    context: _BumpContext,
    target_version: str,
    builder: PlanBuilder,
) -> None:
    """Prepare configured documentation rewrites.

    Parameters
    ----------
    context : _BumpContext
        The initialised bump context.
    target_version : str
        Version to apply inside TOML fences.
    builder : PlanBuilder
        Proposal sink.
    """

    def read_selected(path: ConfinedPath) -> str:
        """Return the selected file's current or proposed content."""
        content = builder.read(path)
        if content is None:
            message = f"selected documentation file is missing: {path.display}"
            raise BumpTargetExaminationError(message)
        return content

    request = bump_docs.DocumentationUpdateRequest(
        target_version=target_version,
        updated_crates=context.updated_crate_names,
        read_source=read_selected,
    )
    updates = bump_docs.prepare_documentation_updates(
        context.root_path,
        context.configuration.bump.documentation,
        request,
    )
    for path, content in updates:
        builder.propose(path, content, WriteCategory.DOCUMENTATION)
    _log.debug("Prepared %d documentation write(s)", len(updates))


def prepare_readmes(context: _BumpContext, builder: PlanBuilder) -> None:
    """Prepare README adoption for crates that opt in.

    Parameters
    ----------
    context : _BumpContext
        The initialised bump context.
    builder : PlanBuilder
        Proposal sink.

    Raises
    ------
    ReadmeTranspositionError
        If the workspace README is missing or a crate lies outside the
        workspace root.
    """  # ruff: ignore[docstring-extraneous-exception]  # propagated from bump_readme
    opted_in = [
        crate for crate in context.workspace.crates if crate.readme_is_workspace
    ]
    if not opted_in:
        return
    source_text = bump_readme.read_workspace_readme(
        context.root_path, read_source=builder.read
    )
    for crate in opted_in:
        _prepare_readme(context, crate, source_text, builder)
    _log.debug("Prepared %d README write(s)", len(opted_in))


def _prepare_readme(
    context: _BumpContext,
    crate: WorkspaceCrate,
    source_text: str,
    builder: PlanBuilder,
) -> None:
    """Prepare one crate's adopted README."""
    key = f"workspace member {crate.name!r} README"
    relative_crate = graph_relative_manifest(
        context.root_path,
        context.workspace.workspace_root,
        crate.root_path,
        key=key,
    )
    content = bump_readme.prepare_readme_content(
        context.root_path,
        crate,
        source_text,
        crate_relative_path=relative_crate,
    )
    pattern = PurePosixPath(relative_crate.as_posix(), "README.md").as_posix()
    destination = confine_relative_path(
        context.root_path,
        pattern,
        key=key,
        options=ConfineOptions(may_create=True),
    )
    builder.propose(destination, content, WriteCategory.README)
