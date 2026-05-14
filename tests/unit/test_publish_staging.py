"""Unit tests exercising publish staging utilities."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import typing as typ

import pytest

from lading.commands import publish
from tests.unit.conftest import (
    PreparationFixtures,
    PrepareWorkspaceFixtures,
    _CrateSpec,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.workspace import WorkspaceCrate, WorkspaceGraph


@dc.dataclass(slots=True, frozen=True)
class _StageReadmeValidationCase:
    """Represent expectations for README staging validation failures."""

    has_readme: bool
    crate_in_workspace: bool
    expected_error: str


def test_normalise_build_directory_defaults_to_tempdir(tmp_path: Path) -> None:
    """Normalisation creates a temporary directory when none is provided."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    build_directory = publish._normalise_build_directory(workspace_root, None)

    assert build_directory.exists()
    assert build_directory.is_absolute()
    assert not build_directory.is_relative_to(workspace_root)


def test_normalise_build_directory_resolves_relative_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Relative build directories are resolved against the current directory."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    build_directory = publish._normalise_build_directory(workspace_root, "staging")

    expected = (tmp_path / "staging").resolve()
    assert build_directory == expected
    assert build_directory.exists()


def test_normalise_build_directory_rejects_workspace_descendants(
    tmp_path: Path,
) -> None:
    """Normalisation rejects build directories nested under the workspace."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    build_directory = workspace_root / "target"

    with pytest.raises(publish.PublishPreparationError) as excinfo:
        publish._normalise_build_directory(workspace_root, build_directory)

    assert "cannot reside within the workspace root" in str(excinfo.value)


def test_copy_workspace_tree_mirrors_workspace_contents(tmp_path: Path) -> None:
    """Workspace files are cloned into the staging directory."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest = workspace_root / "Cargo.toml"
    manifest.write_text("[workspace]\n", encoding="utf-8")
    nested_dir = workspace_root / "crates" / "alpha"
    nested_dir.mkdir(parents=True)
    nested_file = nested_dir / "README.md"
    nested_file.write_text("# README\n", encoding="utf-8")

    build_directory = tmp_path / "staging"
    build_directory.mkdir()

    staging_root = publish._copy_workspace_tree(
        workspace_root, build_directory, preserve_symlinks=True
    )

    assert staging_root == build_directory / workspace_root.name
    assert (staging_root / "Cargo.toml").read_text(encoding="utf-8") == "[workspace]\n"
    assert (staging_root / "crates" / "alpha" / "README.md").read_text(
        encoding="utf-8"
    ) == "# README\n"


def test_copy_workspace_tree_replaces_existing_clone(tmp_path: Path) -> None:
    """Existing staging directories are replaced with a fresh copy."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "marker.txt").write_text("fresh", encoding="utf-8")

    build_directory = tmp_path / "staging"
    existing_clone = build_directory / workspace_root.name
    existing_clone.mkdir(parents=True)
    stale_file = existing_clone / "stale.txt"
    stale_file.write_text("stale", encoding="utf-8")

    staging_root = publish._copy_workspace_tree(
        workspace_root, build_directory, preserve_symlinks=True
    )

    assert staging_root == existing_clone
    assert not stale_file.exists()
    assert (staging_root / "marker.txt").read_text(encoding="utf-8") == "fresh"


def test_copy_workspace_tree_rejects_nested_clone(tmp_path: Path) -> None:
    """Copying into a directory under the workspace is prohibited."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(publish.PublishPreparationError) as excinfo:
        publish._copy_workspace_tree(
            workspace_root, workspace_root, preserve_symlinks=True
        )

    assert "cannot be nested inside the workspace root" in str(excinfo.value)


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            {"preserve_symlinks": True, "expect_symlink": True},
            id="preserve",
        ),
        pytest.param(
            {"preserve_symlinks": False, "expect_symlink": False},
            id="dereference",
        ),
    ],
)
def test_copy_workspace_tree_symlink_handling(
    tmp_path: Path, scenario: dict[str, bool]
) -> None:
    """Workspace symlinks are preserved or dereferenced based on option."""
    preserve_symlinks = scenario["preserve_symlinks"]
    expect_symlink = scenario["expect_symlink"]
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    target = workspace_root / "data.txt"
    target.write_text("payload", encoding="utf-8")
    link = workspace_root / "alias.txt"
    link.symlink_to(target.name)

    build_directory = tmp_path / "staging"
    build_directory.mkdir()

    staging_root = publish._copy_workspace_tree(
        workspace_root, build_directory, preserve_symlinks=preserve_symlinks
    )

    staged_link = staging_root / "alias.txt"
    assert staged_link.is_file()
    assert staged_link.is_symlink() == expect_symlink
    if expect_symlink:
        assert staged_link.resolve(strict=True) == staging_root / "data.txt"
    assert staged_link.read_text(encoding="utf-8") == "payload"


def test_stage_workspace_readmes_returns_empty_list_when_unused(
    tmp_path: Path,
) -> None:
    """No work is performed when no crates opt into the workspace README."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    staging_root = tmp_path / "staging"
    staging_root.mkdir()

    copied = publish._stage_workspace_readmes(
        crates=(), workspace_root=workspace_root, staging_root=staging_root
    )

    assert copied == ()


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param({"readme_workspace": True, "expected_count": 1}, id="opted_in"),
        pytest.param(
            {"readme_workspace": False, "expected_count": 0}, id="not_opted_in"
        ),
    ],
)
def test_collect_workspace_readme_targets_by_opt_in(
    tmp_path: Path,
    make_crate: cabc.Callable[[Path, str, _CrateSpec | None], WorkspaceCrate],
    make_workspace: cabc.Callable[[Path, WorkspaceCrate], WorkspaceGraph],
    scenario: dict[str, bool | int],
) -> None:
    """Collection includes only crates with readme.workspace = true."""
    readme_workspace = bool(scenario["readme_workspace"])
    expected_count = int(scenario["expected_count"])
    workspace_root = tmp_path / "workspace"
    crate_alpha = make_crate(
        workspace_root, "alpha", _CrateSpec(readme_workspace=readme_workspace)
    )
    crate_beta = make_crate(workspace_root, "beta")
    workspace = make_workspace(workspace_root, crate_alpha, crate_beta)

    result = publish._collect_workspace_readme_targets(workspace)

    assert len(result) == expected_count
    if expected_count > 0:
        assert result == (crate_alpha,)


def test_stage_workspace_readmes_copies_and_sorts_targets(
    tmp_path: Path,
    make_crate: cabc.Callable[[Path, str, _CrateSpec | None], WorkspaceCrate],
) -> None:
    """Workspace README is copied into each opted-in crate in sorted order."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    readme = workspace_root / "README.md"
    readme.write_text("workspace", encoding="utf-8")
    crate_alpha = make_crate(workspace_root, "alpha", _CrateSpec(readme_workspace=True))
    crate_beta = make_crate(workspace_root, "beta", _CrateSpec(readme_workspace=True))
    staging_root = tmp_path / "staging" / "workspace"
    staging_root.mkdir(parents=True)

    copied = publish._stage_workspace_readmes(
        crates=(crate_alpha, crate_beta),
        workspace_root=workspace_root,
        staging_root=staging_root,
    )

    relative = [path.relative_to(staging_root).as_posix() for path in copied]
    assert relative == ["alpha/README.md", "beta/README.md"]
    for path in copied:
        assert path.read_text(encoding="utf-8") == "workspace"


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _StageReadmeValidationCase(
                has_readme=True,
                crate_in_workspace=False,
                expected_error="outside the workspace root",
            ),
            id="rejects_external_crates",
        ),
        pytest.param(
            _StageReadmeValidationCase(
                has_readme=False,
                crate_in_workspace=True,
                expected_error="Workspace README.md is required",
            ),
            id="requires_workspace_readme",
        ),
    ],
)
def test_stage_workspace_readmes_validation_errors(
    tmp_path: Path,
    make_crate: cabc.Callable[[Path, str, _CrateSpec | None], WorkspaceCrate],
    case: _StageReadmeValidationCase,
) -> None:
    """Staging readmes validates workspace README existence and crate location."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    if case.has_readme:
        readme = workspace_root / "README.md"
        readme.write_text("workspace", encoding="utf-8")

    crate_root = workspace_root if case.crate_in_workspace else tmp_path / "external"
    crate = make_crate(crate_root, "alpha", _CrateSpec(readme_workspace=True))

    staging_root = tmp_path / "staging"
    staging_root.mkdir()

    with pytest.raises(publish.PublishPreparationError) as excinfo:
        publish._stage_workspace_readmes(
            crates=(crate,), workspace_root=workspace_root, staging_root=staging_root
        )

    assert case.expected_error in str(excinfo.value)


def test_prepare_workspace_copies_workspace_readme(
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """Staging copies the workspace README into crates that opt in."""
    fx = prepare_workspace_fixtures
    pf = preparation_fixtures
    workspace_root = fx.tmp_path / "workspace"
    workspace_root.mkdir()
    readme = workspace_root / "README.md"
    readme.write_text("Workspace README", encoding="utf-8")
    crate = pf.make_crate(workspace_root, "alpha", _CrateSpec(readme_workspace=True))
    workspace = pf.make_workspace(workspace_root, crate)
    configuration = pf.make_config()
    plan = publish.plan_publication(workspace, configuration)
    preparation = publish.prepare_workspace(plan, workspace, options=fx.publish_options)

    staging_root = preparation.staging_root
    assert staging_root.exists()
    staged_readme = (
        staging_root / crate.root_path.relative_to(workspace_root) / "README.md"
    )
    assert staged_readme.exists()
    assert staged_readme.read_text(encoding="utf-8") == readme.read_text(
        encoding="utf-8"
    )
    assert preparation.copied_readmes == (staged_readme,)


def test_prepare_workspace_requires_workspace_readme(
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """Staging fails fast when crates expect the workspace README."""
    fx = prepare_workspace_fixtures
    pf = preparation_fixtures
    workspace_root = fx.tmp_path / "workspace"
    workspace_root.mkdir()
    crate = pf.make_crate(workspace_root, "alpha", _CrateSpec(readme_workspace=True))
    workspace = pf.make_workspace(workspace_root, crate)
    configuration = pf.make_config()
    plan = publish.plan_publication(workspace, configuration)

    with pytest.raises(publish.PublishPreparationError) as excinfo:
        publish.prepare_workspace(
            plan,
            workspace,
            options=fx.publish_options,
        )

    assert "README.md" in str(excinfo.value)


def test_prepare_workspace_registers_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """Cleanup-enabled staging registers an atexit handler."""
    fx = prepare_workspace_fixtures
    pf = preparation_fixtures
    workspace_root = fx.tmp_path / "workspace"
    workspace_root.mkdir()
    crate = pf.make_crate(workspace_root, "alpha")
    workspace = pf.make_workspace(workspace_root, crate)
    plan = publish.plan_publication(workspace, pf.make_config())

    build_directory = fx.publish_options.build_directory
    registered: list[cabc.Callable[[], None]] = []

    def capture(callback: cabc.Callable[[], None]) -> None:
        registered.append(callback)

    monkeypatch.setattr(publish.atexit, "register", capture)

    options = publish.PublishOptions(build_directory=build_directory, cleanup=True)
    preparation = publish.prepare_workspace(plan, workspace, options=options)

    assert len(registered) == 1
    cleanup = registered[0]
    assert callable(cleanup)
    assert preparation.staging_root.parent == build_directory
    assert build_directory.exists()

    cleanup()
    assert not build_directory.exists()


def test_prepare_workspace_returns_empty_copied_readmes(
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """Staging reports no copied READMEs when no crates opt in."""
    fx = prepare_workspace_fixtures
    pf = preparation_fixtures
    workspace_root = fx.tmp_path / "workspace"
    workspace_root.mkdir()
    crate = pf.make_crate(workspace_root, "alpha")
    workspace = pf.make_workspace(workspace_root, crate)
    configuration = pf.make_config()
    plan = publish.plan_publication(workspace, configuration)

    preparation = publish.prepare_workspace(plan, workspace, options=fx.publish_options)

    assert preparation.copied_readmes == ()


def test_prepare_workspace_copies_multiple_readmes_sorted(
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """Staging returns copied README paths in workspace-relative order."""
    fx = prepare_workspace_fixtures
    pf = preparation_fixtures
    workspace_root = fx.tmp_path / "workspace"
    workspace_root.mkdir()
    readme = workspace_root / "README.md"
    readme.write_text("Workspace", encoding="utf-8")
    crate_alpha = pf.make_crate(
        workspace_root, "alpha", _CrateSpec(readme_workspace=True)
    )
    crate_beta = pf.make_crate(
        workspace_root, "beta", _CrateSpec(readme_workspace=True)
    )
    workspace = pf.make_workspace(workspace_root, crate_alpha, crate_beta)
    plan = publish.plan_publication(workspace, pf.make_config())

    preparation = publish.prepare_workspace(plan, workspace, options=fx.publish_options)

    relative = [
        path.relative_to(preparation.staging_root).as_posix()
        for path in preparation.copied_readmes
    ]
    assert relative == ["alpha/README.md", "beta/README.md"]
    for staged in preparation.copied_readmes:
        assert staged.read_text(encoding="utf-8") == readme.read_text(encoding="utf-8")


def test_prepare_workspace_does_not_register_cleanup_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """Cleanup hook is not registered when the option remains disabled."""
    fx = prepare_workspace_fixtures
    pf = preparation_fixtures
    workspace_root = fx.tmp_path / "workspace"
    workspace_root.mkdir()
    crate = pf.make_crate(workspace_root, "alpha")
    workspace = pf.make_workspace(workspace_root, crate)
    plan = publish.plan_publication(workspace, pf.make_config())

    registered: list[cabc.Callable[[], None]] = []

    def capture(callback: cabc.Callable[[], None]) -> None:
        registered.append(callback)

    monkeypatch.setattr(publish.atexit, "register", capture)

    publish.prepare_workspace(plan, workspace, options=fx.publish_options)

    assert registered == []
