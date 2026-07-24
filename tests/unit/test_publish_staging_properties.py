"""Property tests for publish staging path safety."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from lading.commands import publish_staging

_SAFE_PATH_COMPONENT = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=1,
    max_size=8,
)


@given(parts=st.lists(_SAFE_PATH_COMPONENT, min_size=1, max_size=3))
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_normalise_build_directory_rejects_workspace_descendants(
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
        publish_staging._normalise_build_directory(workspace_root, descendant)


@given(name=_SAFE_PATH_COMPONENT)
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_normalise_build_directory_accepts_workspace_siblings(
    tmp_path: Path, name: str
) -> None:
    """Every generated sibling build directory remains outside the workspace."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    sibling = tmp_path / "build" / name

    build_directory = publish_staging._normalise_build_directory(
        workspace_root, sibling
    )

    assert build_directory == sibling.resolve()
    assert not build_directory.is_relative_to(workspace_root)
