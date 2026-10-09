"""Unit tests for the one-shot ``prepare_workspace`` staging entry point.

``prepare_workspace`` stages a copy and returns without managing its lifetime.
The tests here cover the cleanup handler it registers, when it deliberately
does not, and the rule that copying the workspace README must not adopt it as a
crate README.
"""

import collections.abc as cabc
import dataclasses as dc

import pytest

from lading.commands import publish, publish_staging
from tests.unit.conftest import (
    PreparationFixtures,
    PrepareWorkspaceFixtures,
    _CrateSpec,
)


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
    assert build_directory is not None, (
        "the publish_options fixture must supply a build directory"
    )
    build_directory.mkdir(parents=True)
    marker = build_directory / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    registered: list[cabc.Callable[[], None]] = []

    def capture(callback: cabc.Callable[..., None], *arguments: object) -> None:
        registered.append(lambda: callback(*arguments))

    monkeypatch.setattr(publish_staging.atexit, "register", capture)

    options = publish.PublishOptions(build_directory=build_directory, cleanup=True)
    preparation = publish_staging.prepare_workspace(plan, options=options)

    assert len(registered) == 1, (
        "cleanup-enabled staging must register exactly one atexit handler"
    )
    cleanup = registered[0]
    assert callable(cleanup), "the registered atexit hook must be callable"
    assert preparation.staging_root.parent == build_directory, (
        "the staged copy must be placed inside the requested build directory"
    )
    assert build_directory.exists(), (
        "a caller-supplied build directory must still exist after staging"
    )

    cleanup()
    assert build_directory.exists(), (
        "cleanup must keep a caller-supplied build directory in place"
    )
    assert marker.read_text(encoding="utf-8") == "keep", (
        "cleanup must not remove files the caller put in the build directory"
    )
    assert not preparation.staging_root.exists(), (
        "cleanup must remove the staged workspace copy"
    )


def test_prepare_workspace_cleanup_removes_auto_created_build_directory(
    monkeypatch: pytest.MonkeyPatch,
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """Cleanup removes the full temporary build directory that staging creates."""
    fx = prepare_workspace_fixtures
    pf = preparation_fixtures
    workspace_root = fx.tmp_path / "workspace"
    workspace_root.mkdir()
    crate = pf.make_crate(workspace_root, "alpha")
    plan = publish.plan_publication(
        pf.make_workspace(workspace_root, crate), pf.make_config()
    )
    registered: list[cabc.Callable[[], None]] = []

    def capture(callback: cabc.Callable[..., None], *arguments: object) -> None:
        registered.append(lambda: callback(*arguments))

    monkeypatch.setattr(publish_staging.atexit, "register", capture)

    preparation = publish_staging.prepare_workspace(
        plan, options=publish.PublishOptions(cleanup=True)
    )
    build_directory = preparation.staging_root.parent

    assert len(registered) == 1, (
        "cleanup-enabled staging must register exactly one atexit handler"
    )
    registered[0]()

    assert not build_directory.exists(), (
        "cleanup must remove the build directory staging created automatically"
    )


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
def test_prepare_workspace_copies_workspace_readme_without_adopting_it_for_crates(
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
    crate_spec: _CrateSpec,
) -> None:
    """Staging copies the workspace README without creating crate READMEs."""
    fx = prepare_workspace_fixtures
    pf = preparation_fixtures
    workspace_root = fx.tmp_path / "workspace"
    workspace_root.mkdir()
    readme = workspace_root / "README.md"
    readme.write_text("Workspace README", encoding="utf-8")
    crate = pf.make_crate(workspace_root, "alpha", crate_spec)
    workspace = pf.make_workspace(workspace_root, crate)
    configuration = pf.make_config()
    plan = publish.plan_publication(workspace, configuration)

    preparation = publish_staging.prepare_workspace(plan, options=fx.publish_options)

    assert preparation.staging_root.exists(), (
        "staging must leave a workspace copy on disk"
    )
    assert (preparation.staging_root / readme.name).read_text(encoding="utf-8") == (
        "Workspace README"
    ), "the workspace README must be copied into the staging tree verbatim"
    staged_crate_readme = (
        preparation.staging_root
        / crate.root_path.relative_to(workspace_root)
        / "README.md"
    )
    assert not staged_crate_readme.exists(), (
        "staging must not adopt the workspace README as a crate README"
    )


def test_prepare_workspace_does_not_register_cleanup_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """Cleanup hook is not registered when the option is explicitly disabled.

    Cleanup is the default since issue #269, so opting out is what has to be
    stated; a test that relied on the old default would silently stop covering
    anything.
    """
    fx = prepare_workspace_fixtures
    pf = preparation_fixtures
    workspace_root = fx.tmp_path / "workspace"
    workspace_root.mkdir()
    crate = pf.make_crate(workspace_root, "alpha")
    workspace = pf.make_workspace(workspace_root, crate)
    plan = publish.plan_publication(workspace, pf.make_config())

    registered: list[cabc.Callable[[], None]] = []

    def capture(callback: cabc.Callable[..., None], *arguments: object) -> None:
        registered.append(lambda: callback(*arguments))

    monkeypatch.setattr(publish_staging.atexit, "register", capture)

    options = dc.replace(fx.publish_options, cleanup=False)
    publish_staging.prepare_workspace(plan, options=options)

    assert not registered, (
        "staging with cleanup disabled must not register an atexit handler"
    )
