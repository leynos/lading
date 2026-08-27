"""Unit tests exercising publish staging utilities."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

import pytest

from lading.commands import publish
from tests.unit.conftest import (
    PublishFixtures,
    _CrateSpec,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


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


def test_prepare_workspace_does_not_stage_workspace_readme(
    publish_fixtures: PublishFixtures,
) -> None:
    """Publish staging leaves workspace README adoption to bump."""
    workspace_root = publish_fixtures.tmp_path / "workspace"
    workspace_root.mkdir()
    readme = workspace_root / "README.md"
    readme.write_text("Workspace README", encoding="utf-8")
    crate = publish_fixtures.make_crate(
        workspace_root, "alpha", _CrateSpec(readme_workspace=True)
    )
    workspace = publish_fixtures.make_workspace(workspace_root, crate)
    configuration = publish_fixtures.make_config()
    plan = publish.plan_publication(workspace, configuration)
    preparation = publish.prepare_workspace(
        plan, options=publish_fixtures.publish_options
    )

    staging_root = preparation.staging_root
    assert staging_root.exists()
    staged_readme = (
        staging_root / crate.root_path.relative_to(workspace_root) / "README.md"
    )
    assert not staged_readme.exists()
    assert preparation.copied_readmes == ()


def test_prepare_workspace_registers_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    publish_fixtures: PublishFixtures,
) -> None:
    """Cleanup-enabled staging registers an atexit handler."""
    workspace_root = publish_fixtures.tmp_path / "workspace"
    workspace_root.mkdir()
    crate = publish_fixtures.make_crate(workspace_root, "alpha")
    workspace = publish_fixtures.make_workspace(workspace_root, crate)
    plan = publish.plan_publication(workspace, publish_fixtures.make_config())

    build_directory = publish_fixtures.publish_options.build_directory
    registered: list[cabc.Callable[[], None]] = []

    def capture(callback: cabc.Callable[[], None]) -> None:
        registered.append(callback)

    monkeypatch.setattr(publish.atexit, "register", capture)

    options = publish.PublishOptions(build_directory=build_directory, cleanup=True)
    preparation = publish.prepare_workspace(plan, options=options)

    assert len(registered) == 1
    cleanup = registered[0]
    assert callable(cleanup)
    assert preparation.staging_root.parent == build_directory
    assert build_directory.exists()

    cleanup()
    assert not build_directory.exists()


@pytest.mark.parametrize(
    "crate_spec",
    [
        pytest.param(
            _CrateSpec(readme_workspace=True),
            id="opted_in_missing_workspace_readme",
        ),
        pytest.param(_CrateSpec(), id="no_readme_opt_in"),
    ],
)
def test_prepare_workspace_returns_empty_copied_readmes(
    publish_fixtures: PublishFixtures,
    crate_spec: _CrateSpec,
) -> None:
    """Staging reports no copied READMEs regardless of readme opt-in status."""
    workspace_root = publish_fixtures.tmp_path / "workspace"
    workspace_root.mkdir()
    crate = publish_fixtures.make_crate(workspace_root, "alpha", crate_spec)
    workspace = publish_fixtures.make_workspace(workspace_root, crate)
    configuration = publish_fixtures.make_config()
    plan = publish.plan_publication(workspace, configuration)

    preparation = publish.prepare_workspace(
        plan, options=publish_fixtures.publish_options
    )

    assert preparation.staging_root.exists()
    assert preparation.copied_readmes == ()


def test_prepare_workspace_keeps_copied_readmes_empty_for_opted_in_crates(
    publish_fixtures: PublishFixtures,
) -> None:
    """Readme opt-in does not produce publish-time copied paths."""
    workspace_root = publish_fixtures.tmp_path / "workspace"
    workspace_root.mkdir()
    readme = workspace_root / "README.md"
    readme.write_text("Workspace", encoding="utf-8")
    crate_alpha = publish_fixtures.make_crate(
        workspace_root, "alpha", _CrateSpec(readme_workspace=True)
    )
    crate_beta = publish_fixtures.make_crate(
        workspace_root, "beta", _CrateSpec(readme_workspace=True)
    )
    workspace = publish_fixtures.make_workspace(workspace_root, crate_alpha, crate_beta)
    plan = publish.plan_publication(workspace, publish_fixtures.make_config())

    preparation = publish.prepare_workspace(
        plan, options=publish_fixtures.publish_options
    )

    assert preparation.copied_readmes == ()


def test_prepare_workspace_does_not_register_cleanup_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    publish_fixtures: PublishFixtures,
) -> None:
    """Cleanup hook is not registered when the option remains disabled."""
    workspace_root = publish_fixtures.tmp_path / "workspace"
    workspace_root.mkdir()
    crate = publish_fixtures.make_crate(workspace_root, "alpha")
    workspace = publish_fixtures.make_workspace(workspace_root, crate)
    plan = publish.plan_publication(workspace, publish_fixtures.make_config())

    registered: list[cabc.Callable[[], None]] = []

    def capture(callback: cabc.Callable[[], None]) -> None:
        registered.append(callback)

    monkeypatch.setattr(publish.atexit, "register", capture)

    publish.prepare_workspace(plan, options=publish_fixtures.publish_options)

    assert registered == []
