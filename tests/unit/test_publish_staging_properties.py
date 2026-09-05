"""Property tests for publish staging path safety."""

from __future__ import annotations

import collections.abc as cabc
import shutil
import typing as typ
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from lading.commands import publish, publish_staging

if typ.TYPE_CHECKING:
    from tests.unit.conftest import PreparationFixtures, PrepareWorkspaceFixtures

_SAFE_PATH_COMPONENT = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=1,
    max_size=8,
)


class _CleanupCase(typ.NamedTuple):
    """Generated caller-owned content and build-directory ownership mode."""

    is_auto_created_build_directory: bool
    caller_owned_names: tuple[str, ...]


_CLEANUP_CASE = st.builds(
    _CleanupCase,
    is_auto_created_build_directory=st.booleans(),
    caller_owned_names=st.lists(
        _SAFE_PATH_COMPONENT, min_size=2, max_size=4, unique=True
    ).map(tuple),
)


@given(parts=st.lists(_SAFE_PATH_COMPONENT, min_size=1, max_size=3))
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_normalize_build_directory_rejects_workspace_descendants(
    tmp_path: Path, parts: list[str]
) -> None:
    """Every generated workspace descendant is rejected as a build directory."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    descendant = workspace_root.joinpath(*parts)

    with pytest.raises(
        publish_staging.PublishPreparationError,
        match="cannot reside within the workspace root",
    ):
        publish_staging._normalize_build_directory(workspace_root, descendant)


@given(name=_SAFE_PATH_COMPONENT)
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_normalize_build_directory_accepts_workspace_siblings(
    tmp_path: Path, name: str
) -> None:
    """Every generated sibling build directory remains outside the workspace."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    sibling = tmp_path / "build" / name

    build_directory = publish_staging._normalize_build_directory(
        workspace_root, sibling
    )

    assert build_directory == sibling.resolve(), (
        f"Expected build directory {build_directory} to resolve to {sibling.resolve()}"
    )
    assert not build_directory.is_relative_to(workspace_root), (
        f"Expected build directory {build_directory} to be outside workspace root "
        f"{workspace_root.resolve()}"
    )


@given(case=_CLEANUP_CASE)
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_prepare_workspace_cleanup_scopes_generated_caller_content(
    monkeypatch: pytest.MonkeyPatch,
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
    case: _CleanupCase,
) -> None:
    """Cleanup removes only staging artifacts across build-directory ownership."""
    fx = prepare_workspace_fixtures
    pf = preparation_fixtures
    workspace_root = fx.tmp_path / "workspace"
    shutil.rmtree(workspace_root, ignore_errors=True)
    workspace_root.mkdir()
    crate = pf.make_crate(workspace_root, "alpha")
    plan = publish.plan_publication(
        pf.make_workspace(workspace_root, crate), pf.make_config()
    )
    registered: list[cabc.Callable[[], None]] = []
    monkeypatch.setattr(publish_staging.atexit, "register", registered.append)

    build_directory = fx.tmp_path / "build"
    shutil.rmtree(build_directory, ignore_errors=True)
    caller_owned_paths: tuple[Path, ...] = ()
    if case.is_auto_created_build_directory:
        options = publish.PublishOptions(cleanup=True)
    else:
        build_directory.mkdir()
        caller_owned_paths = tuple(
            build_directory / name for name in case.caller_owned_names
        )
        for index, path in enumerate(caller_owned_paths):
            if index % 2:
                path.mkdir()
                (path / "marker.txt").write_text("caller-owned", encoding="utf-8")
            else:
                path.write_text("caller-owned", encoding="utf-8")
        options = publish.PublishOptions(
            build_directory=build_directory,
            cleanup=True,
        )

    preparation = publish_staging.prepare_workspace(plan, options=options)
    generated_build_directory = preparation.staging_root.parent

    assert len(registered) == 1
    registered[0]()

    assert not preparation.staging_root.exists(), (
        f"Cleanup should remove staged root {preparation.staging_root}"
    )
    if case.is_auto_created_build_directory:
        assert not generated_build_directory.exists(), (
            "Cleanup should remove auto-created build directory "
            f"{generated_build_directory}"
        )
    else:
        assert generated_build_directory.exists(), (
            "Cleanup should retain caller-owned build directory "
            f"{generated_build_directory}"
        )
        assert all(path.exists() for path in caller_owned_paths), (
            f"Cleanup should retain caller-owned paths {caller_owned_paths}"
        )
