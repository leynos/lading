"""Shared fixtures and helper factories for publish unit tests."""

from __future__ import annotations

import dataclasses as dc
import typing as typ
from pathlib import Path

import pytest

from lading import config as config_module
from lading.commands import publish
from lading.workspace import WorkspaceCrate, WorkspaceDependency, WorkspaceGraph

if typ.TYPE_CHECKING:
    import collections.abc as cabc

__all__ = [
    "INDEX_MISSING_STDERR_BETA",
    "INDEX_MISSING_STDERR_EXTERNAL",
    "INDEX_MISSING_STDERR_UNPARSEABLE",
    "ORIGINAL_INVOKE",
    "ORIGINAL_PREFLIGHT",
    "CallTrackingRunner",
    "PhaseContext",
    "invoke_phase",
    "make_config",
    "make_crate",
    "make_dependency",
    "make_dependency_chain",
    "make_failing_runner",
    "make_preflight_config",
    "make_workspace",
    "plan_with_crates",
    "prepare_staging_root",
    "publish_plan_and_prep",
]

INDEX_MISSING_STDERR_BETA = (
    "error: failed to prepare local package for uploading\n"
    "\n"
    "Caused by:\n"
    '  failed to select a version for the requirement `alpha = "^0.1.0"`\n'
    "  candidate versions found which didn't match: 0.0.1\n"
    "  location searched: crates.io index\n"
    "  required by package `beta v0.1.0`\n"
)

INDEX_MISSING_STDERR_UNPARSEABLE = (
    "error: failed to prepare local package for uploading\n"
    "\n"
    "Caused by:\n"
    "  failed to select a version for the requirement without a quoted name\n"
    "  location searched: crates.io index\n"
)

INDEX_MISSING_STDERR_EXTERNAL = (
    "error: failed to prepare local package for uploading\n"
    "Caused by:\n"
    '  failed to select a version for the requirement `external_crate = "^1"`\n'
    "  location searched: crates.io index\n"
)


def make_preflight_config(**overrides: object) -> config_module.PreflightConfig:
    """Build a :class:`PreflightConfig` with convenient defaults.

    Args:
        **overrides: Keyword arguments passed to PreflightConfig constructor.
            Special handling: compiletest_externs as tuple of (name, path) pairs
            will be converted to CompiletestExtern objects.

    Returns
    -------
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
    root = Path(root)
    crate_root = root / name
    crate_root.mkdir(parents=True, exist_ok=True)
    manifest = crate_root / "Cargo.toml"
    manifest.write_text(
        f'[package]\nname = "{name}"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
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
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
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


def prepare_staging_root(plan: publish.PublishPlan, base_dir: Path) -> Path:
    """Create a staged workspace tree matching ``plan`` under ``base_dir``."""
    staging_root = base_dir / "staging" / plan.workspace_root.name
    for crate in plan.publishable:
        relative_root = crate.root_path.relative_to(plan.workspace_root)
        (staging_root / relative_root).mkdir(parents=True, exist_ok=True)
    return staging_root


@pytest.fixture
def publish_plan_and_prep(
    tmp_path: Path,
) -> tuple[publish.PublishPlan, publish.PublishPreparation, Path]:
    """Provide a publish plan, preparation object, and staging root."""
    workspace_root = tmp_path / "workspace"
    crates = make_dependency_chain(workspace_root)
    plan = publish.plan_publication(
        make_workspace(workspace_root, *crates), make_config()
    )
    staging_root = prepare_staging_root(plan, tmp_path)
    preparation = publish.PublishPreparation(
        staging_root=staging_root,
        copied_readmes=(),
    )
    return plan, preparation, staging_root


ORIGINAL_INVOKE = publish._invoke
ORIGINAL_PREFLIGHT = publish._run_preflight_checks


@pytest.fixture(autouse=True)
def disable_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub publish pre-flight checks for tests unless explicitly restored."""
    monkeypatch.setattr(
        publish, "_run_preflight_checks", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        publish,
        "_invoke",
        lambda *_args, **_kwargs: (0, "", ""),
    )


@pytest.fixture
def use_real_invoke(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore the original _invoke helper for tests that exercise it."""
    monkeypatch.setattr(publish, "_invoke", ORIGINAL_INVOKE)


class CallTrackingRunner:
    """Track command invocations while returning successful results."""

    def __init__(self) -> None:
        """Initialise the runner with an empty call log."""
        self.calls: list[tuple[tuple[str, ...], Path | None]] = []

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Record the invocation and return a successful result."""
        del env
        self.calls.append((tuple(command), cwd))
        return 0, "", ""


@dc.dataclass(frozen=True)
class PhaseContext:
    """Execution context shared across both cargo phase dispatches."""

    plan: publish.PublishPlan
    preparation: publish.PublishPreparation
    runner: cabc.Callable[..., tuple[int, str, str]]
    options: publish._PublishExecutionOptions


def invoke_phase(phase_name: str, ctx: PhaseContext) -> None:
    """Dispatch to the appropriate cargo sub-command under test."""
    if phase_name == "package":
        publish._package_publishable_crates(
            ctx.plan, ctx.preparation, options=ctx.options, runner=ctx.runner
        )
    elif phase_name == "publish":
        publish._publish_crates(
            ctx.plan, ctx.preparation, runner=ctx.runner, options=ctx.options
        )
    else:
        message = f"Unknown phase_name {phase_name!r}; expected 'package' or 'publish'."
        raise ValueError(message)


def make_failing_runner(
    stdout: str = "", stderr: str = ""
) -> cabc.Callable[..., tuple[int, str, str]]:  # pragma: no cover - simple factory
    """Return a runner that always fails with exit code 1."""

    def _runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del command, cwd, env
        return 1, stdout, stderr

    return _runner
