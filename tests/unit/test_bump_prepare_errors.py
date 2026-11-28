"""Unit tests for manifest and documentation preparation failures."""

from pathlib import Path

import pytest

from lading.commands import bump, bump_docs, bump_prepare
from lading.commands.bump_manifests import _BumpContext
from lading.commands.bump_plan_builder import PlanBuilder
from lading.utils.confinement import (
    BumpPreflightError,
    BumpTargetExaminationError,
    ConfinedPath,
    ConfineOptions,
    confine_relative_path,
)
from lading.workspace import WorkspaceGraph
from tests.helpers.workspace_builders import _make_config


def _confined(root: Path, relative: str) -> ConfinedPath:
    """Return a confined may-create path for ``relative`` beneath ``root``."""
    return confine_relative_path(
        root,
        relative,
        key="test",
        options=ConfineOptions(may_create=True),
    )


def _context(root: Path) -> _BumpContext:
    """Return a bump context whose documentation globs select ``docs/*.md``."""
    configuration = _make_config(documentation_globs=("docs/*.md",))
    workspace = WorkspaceGraph(workspace_root=root, crates=())
    return _BumpContext(
        root_path=root,
        configuration=configuration,
        workspace=workspace,
        base_options=bump.BumpOptions(
            configuration=configuration,
            workspace=workspace,
        ),
        workspace_manifest=root / "Cargo.toml",
        excluded=frozenset(),
        updated_crate_names=frozenset(),
    )


def test_render_request_reports_a_missing_manifest(tmp_path: Path) -> None:
    """A request for an absent manifest is an examination error."""
    root = tmp_path.resolve()
    request = bump_prepare._ManifestRequest(path=_confined(root, "Cargo.toml"))

    with pytest.raises(BumpTargetExaminationError) as excinfo:
        bump_prepare._render_request(request, "1.2.3")

    assert "cannot read" in str(excinfo.value), (
        "the diagnostic must name the unreadable manifest"
    )


def test_render_request_reports_invalid_toml(tmp_path: Path) -> None:
    """A manifest that is not valid TOML is a preflight error."""
    root = tmp_path.resolve()
    (root / "Cargo.toml").write_text("not toml [\n", encoding="utf-8")
    request = bump_prepare._ManifestRequest(path=_confined(root, "Cargo.toml"))

    with pytest.raises(BumpPreflightError) as excinfo:
        bump_prepare._render_request(request, "1.2.3")

    assert "not valid TOML" in str(excinfo.value), (
        "the diagnostic must name the malformed manifest"
    )


def test_prepare_documentation_reports_a_missing_selected_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A selected documentation file that vanished is an examination error."""
    root = tmp_path.resolve()
    (root / "docs").mkdir()
    missing = _confined(root, "docs/missing.md")

    def fake_updates(
        workspace_root: Path,
        documentation: object,
        request: bump_docs.DocumentationUpdateRequest,
    ) -> tuple[tuple[ConfinedPath, str, str], ...]:
        """Read the missing selection as a real expansion would."""
        del workspace_root, documentation
        request.read_source(missing)
        return ()

    monkeypatch.setattr(
        bump_docs, "_prepare_documentation_updates_with_sources", fake_updates
    )
    builder = PlanBuilder(root=root)

    with pytest.raises(BumpTargetExaminationError) as excinfo:
        bump_prepare.prepare_documentation(_context(root), "1.2.3", builder)

    assert "selected documentation file is missing" in str(excinfo.value), (
        "the diagnostic must say the selected file is absent"
    )
