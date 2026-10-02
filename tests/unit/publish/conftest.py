"""Shared fixtures and helper factories for publish unit tests."""

import dataclasses as dc
import logging
import typing as typ
from pathlib import Path

import pytest

from lading import config as config_module
from lading.commands import (
    publish,
    publish_pipeline,
    publish_plan,
    publish_preflight,
    publish_staging,
)
from lading.commands.publish_pipeline import _invoke as _real_invoke
from lading.commands.publish_preflight import (
    _run_preflight_checks as _real_preflight,
)
from lading.workspace import WorkspaceCrate, WorkspaceDependency, WorkspaceGraph

if typ.TYPE_CHECKING:
    import collections.abc as cabc

__all__ = [
    "CARGO_PACKAGE",
    "CARGO_PUBLISH",
    "CARGO_PUBLISH_DRY_RUN",
    "INDEX_MISSING_STDERR_BETA",
    "INDEX_MISSING_STDERR_EXTERNAL",
    "INDEX_MISSING_STDERR_UNPARSEABLE",
    "CallTrackingRunner",
    "PhaseContext",
    "_real_invoke",
    "_real_preflight",
    "_warning_records",
    "invoke_phase",
    "make_config",
    "make_crate",
    "make_dependency",
    "make_dependency_chain",
    "make_failing_runner",
    "make_n_crate_chain",
    "make_preflight_config",
    "make_workspace",
    "plan_with_crates",
    "prepare_staging_root",
    "publish_plan_and_prep",
]

# Cargo command tuples shared by the publish ordering tests. Centralised here so
# expectations track changes to the underlying invocations in one place.
CARGO_PACKAGE = ("cargo", "package", "--allow-dirty")
CARGO_PUBLISH = ("cargo", "publish", "--allow-dirty")
CARGO_PUBLISH_DRY_RUN = ("cargo", "publish", "--allow-dirty", "--dry-run")

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


class _PreflightOverrides(typ.TypedDict, total=False):
    """Keyword overrides accepted by :func:`make_preflight_config`.

    ``total=False`` because every field falls back to the ``PreflightConfig``
    default. Enumerating the keys, rather than typing the parameter as a bare
    mapping, is what keeps a mistyped keyword a type error instead of a
    silently ignored entry.
    """

    skip: bool
    test_exclude: tuple[str, ...]
    unit_tests_only: bool
    aux_build: tuple[tuple[str, ...], ...]
    compiletest_externs: tuple[tuple[str, str], ...]
    env_overrides: tuple[tuple[str, str], ...]
    stderr_tail_lines: int


def make_preflight_config(
    **overrides: typ.Unpack[_PreflightOverrides],
) -> config_module.PreflightConfig:
    """Build a :class:`PreflightConfig` with convenient defaults.

    Parameters
    ----------
    **overrides : Unpack[_PreflightOverrides]
        ``PreflightConfig`` fields to override. ``compiletest_externs`` is
        the one field that is not passed through verbatim: callers supply
        ``(crate, path)`` string pairs, which are converted to
        ``CompiletestExtern`` objects here.

    Returns
    -------
    config_module.PreflightConfig
        A configuration with the ``PreflightConfig`` defaults merged with the
        supplied overrides.

    """
    externs = tuple(
        config_module.CompiletestExtern(crate=name, path=path)
        for name, path in overrides.get("compiletest_externs", ())
    )
    return config_module.PreflightConfig(
        skip=overrides.get("skip", False),
        test_exclude=overrides.get("test_exclude", ()),
        unit_tests_only=overrides.get("unit_tests_only", False),
        aux_build=overrides.get("aux_build", ()),
        compiletest_externs=externs,
        env_overrides=overrides.get("env_overrides", ()),
        stderr_tail_lines=overrides.get("stderr_tail_lines", 40),
    )


def make_config(
    *,
    preflight: config_module.PreflightConfig | None = None,
    exclude: tuple[str, ...] = (),
    order: tuple[str, ...] = (),
) -> config_module.LadingConfig:
    """Return a configuration tailored for publish command tests."""
    publish_table = config_module.PublishConfig(
        strip_patches="all", exclude=exclude, order=order
    )
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


def make_n_crate_chain(root: Path, count: int) -> tuple[WorkspaceCrate, ...]:
    """Return ``count`` crates wired as a linear dependency chain.

    Parameters
    ----------
    root : Path
        Root directory beneath which the crate directories are created.
    count : int
        Number of crates to create. Must be at least ``1``.

    Returns
    -------
    tuple[WorkspaceCrate, ...]
        Crates wired as a linear dependency chain. The first crate has no
        dependencies, and each subsequent crate depends on the one before it.

    Raises
    ------
    ValueError
        If ``count`` is less than one.

    Examples
    --------
    >>> import tempfile
    >>> from pathlib import Path
    >>> with tempfile.TemporaryDirectory() as directory:
    ...     chain = make_n_crate_chain(Path(directory), 3)
    ...     [crate.name for crate in chain]
    ...     [len(crate.dependencies) for crate in chain]
    ['crate_0', 'crate_1', 'crate_2']
    [0, 1, 1]

    """
    if count < 1:
        message = "count must be >= 1"
        raise ValueError(message)
    crates: list[WorkspaceCrate] = []
    for index in range(count):
        name = f"crate_{index}"
        dependencies = () if index == 0 else (make_dependency(f"crate_{index - 1}"),)
        crates.append(make_crate(root, name, dependencies=dependencies))
    return tuple(crates)


def plan_with_crates(
    tmp_path: Path,
    crates: tuple[WorkspaceCrate, ...],
    *,
    exclude: tuple[str, ...] = (),
    order: tuple[str, ...] = (),
) -> publish_plan.PublishPlan:
    """Plan publication for ``crates`` using ``tmp_path`` as the workspace root."""
    root = tmp_path.resolve()
    workspace = make_workspace(root, *crates)
    configuration = make_config(exclude=exclude, order=order)
    return publish.plan_publication(workspace, configuration)


def prepare_staging_root(plan: publish_plan.PublishPlan, base_dir: Path) -> Path:
    """Create a staged workspace tree matching ``plan`` under ``base_dir``."""
    staging_root = base_dir / "staging" / plan.workspace_root.name
    for crate in plan.publishable:
        relative_root = crate.root_path.relative_to(plan.workspace_root)
        (staging_root / relative_root).mkdir(parents=True, exist_ok=True)
    return staging_root


def _warning_records(
    caplog: pytest.LogCaptureFixture,
) -> tuple[tuple[object, object], ...]:
    """Return captured warning format strings and arguments."""
    return tuple(
        (record.msg, record.args)
        for record in caplog.records
        if record.levelno == logging.WARNING
    )


@pytest.fixture
def publish_plan_and_prep(
    tmp_path: Path,
) -> tuple[publish_plan.PublishPlan, publish_staging.PublishPreparation, Path]:
    """Provide a publish plan, preparation object, and staging root."""
    workspace_root = tmp_path / "workspace"
    crates = make_dependency_chain(workspace_root)
    plan = publish.plan_publication(
        make_workspace(workspace_root, *crates), make_config()
    )
    staging_root = prepare_staging_root(plan, tmp_path)
    preparation = publish_staging.PublishPreparation(
        staging_root=staging_root,
    )
    return plan, preparation, staging_root


@pytest.fixture(autouse=True)
def disable_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub publish pre-flight checks for tests unless explicitly restored."""
    monkeypatch.setattr(
        publish_preflight, "_run_preflight_checks", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        publish_pipeline,
        "_invoke",
        lambda *_args, **_kwargs: (0, "", ""),
    )


@pytest.fixture
def use_real_invoke(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore the original _invoke helper for tests that exercise it."""
    monkeypatch.setattr(publish_pipeline, "_invoke", _real_invoke)


class CallTrackingRunner:
    """Track command invocations while returning successful results."""

    def __init__(self) -> None:
        """Initialise the runner with an empty call log."""
        self._calls: list[tuple[tuple[str, ...], Path | None]] = []

    @property
    def calls(self) -> list[tuple[tuple[str, ...], Path | None]]:
        """Stable snapshot of recorded invocations."""
        return list(self._calls)

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        """Record the invocation and return a successful result."""
        del env, echo_stdout
        self._calls.append((tuple(command), cwd))
        return 0, "", ""


@dc.dataclass(frozen=True, slots=True)
class PhaseContext:
    """Execution context shared across both cargo phase dispatches."""

    plan: publish_plan.PublishPlan
    preparation: publish_staging.PublishPreparation
    runner: cabc.Callable[..., tuple[int, str, str]]
    options: publish_pipeline._PublishExecutionOptions


def invoke_phase(phase_name: str, ctx: PhaseContext) -> None:
    """Dispatch to the appropriate cargo sub-command under test."""
    state = publish_pipeline._PublicationPipelineState(
        ctx.plan, ctx.preparation, ctx.options
    )
    match phase_name:
        case "package":
            publish_pipeline._package_publishable_crates(state, runner=ctx.runner)
        case "publish":
            publish_pipeline._publish_crates(state, runner=ctx.runner)
        case _:
            message = (
                f"Unknown phase_name {phase_name!r}; expected 'package' or 'publish'."
            )
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
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        """Execute the command and return a failing result."""
        del command, cwd, env, echo_stdout
        return 1, stdout, stderr

    return _runner
