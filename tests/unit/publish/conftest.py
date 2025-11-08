"""Shared fixtures and helper factories for publish unit tests."""

from __future__ import annotations

import typing as typ

import pytest

from lading import config as config_module
from lading.commands import publish
from lading.workspace import (
    WorkspaceCrate,
    WorkspaceDependency,
    WorkspaceGraph,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "ORIGINAL_PREFLIGHT",
    "make_config",
    "make_crate",
    "make_dependency",
    "make_dependency_chain",
    "make_preflight_config",
    "make_workspace",
    "plan_with_crates",
]


def make_preflight_config(**overrides: object) -> config_module.PreflightConfig:
    """Build a :class:`PreflightConfig` with convenient defaults.

    Args:
        **overrides: Keyword arguments passed to PreflightConfig constructor.
            Special handling: compiletest_externs as tuple of (name, path) pairs
            will be converted to CompiletestExtern objects.

    Returns:
        A PreflightConfig with defaults merged with the provided overrides.

    """
    compiletest_externs_raw = overrides.pop("compiletest_externs", ())
    externs = tuple(
        config_module.CompiletestExtern(crate=name, path=path)
        for name, path in compiletest_externs_raw
    )

    defaults: dict[str, object] = {
        "test_exclude": (),
        "unit_tests_only": False,
        "aux_build": (),
        "compiletest_externs": externs,
        "env_overrides": (),
        "stderr_tail_lines": 40,
    }
    defaults.update(overrides)
    return config_module.PreflightConfig(**defaults)


def make_config(
    *,
    preflight: config_module.PreflightConfig | None = None,
    **overrides: object,
) -> config_module.LadingConfig:
    """Return a configuration tailored for publish command tests."""
    publish_table = config_module.PublishConfig(strip_patches="all", **overrides)
    preflight_config = preflight if preflight is not None else make_preflight_config()
    return config_module.LadingConfig(
        publish=publish_table,
        preflight=preflight_config,
    )


def make_crate(
    root: Path,
    name: str,
    *,
    publish_flag: bool = True,
    dependencies: tuple[WorkspaceDependency, ...] | None = None,
) -> WorkspaceCrate:
    """Construct a :class:`WorkspaceCrate` rooted under ``root``."""
    crate_root = root / name
    manifest = crate_root / "Cargo.toml"
    return WorkspaceCrate(
        id=f"{name}-id",
        name=name,
        version="0.1.0",
        manifest_path=manifest,
        root_path=crate_root,
        publish=publish_flag,
        readme_is_workspace=False,
        dependencies=() if dependencies is None else dependencies,
    )


def make_dependency(name: str) -> WorkspaceDependency:
    """Return a workspace dependency pointing at the crate named ``name``."""
    return WorkspaceDependency(
        package_id=f"{name}-id",
        name=name,
        manifest_name=name,
        kind=None,
    )


def make_workspace(root: Path, *crates: WorkspaceCrate) -> WorkspaceGraph:
    """Construct a :class:`WorkspaceGraph` for ``crates`` rooted at ``root``."""
    if not crates:
        crates = (make_crate(root, "alpha"),)
    return WorkspaceGraph(workspace_root=root, crates=tuple(crates))


def make_dependency_chain(
    root: Path,
) -> tuple[WorkspaceCrate, WorkspaceCrate, WorkspaceCrate]:
    """Return crates that form a simple alpha→beta→gamma dependency chain."""
    alpha = make_crate(root, "alpha")
    beta = make_crate(root, "beta", dependencies=(make_dependency("alpha"),))
    gamma = make_crate(root, "gamma", dependencies=(make_dependency("beta"),))
    return alpha, beta, gamma


def plan_with_crates(
    tmp_path: Path,
    crates: tuple[WorkspaceCrate, ...],
    **config_overrides: object,
) -> publish.PublishPlan:
    """Plan publication for ``crates`` using ``tmp_path`` as the workspace root."""
    root = tmp_path.resolve()
    workspace = make_workspace(root, *crates)
    configuration = make_config(**config_overrides)
    return publish.plan_publication(workspace, configuration)


ORIGINAL_PREFLIGHT = publish._run_preflight_checks


@pytest.fixture(autouse=True)
def disable_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub publish pre-flight checks for tests unless explicitly restored."""
    monkeypatch.setattr(
        publish, "_run_preflight_checks", lambda *_args, **_kwargs: None
    )
