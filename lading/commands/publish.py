"""Publication planning helpers for :mod:`lading.commands.publish`."""

from __future__ import annotations

import atexit
import dataclasses as dc
import os
import shutil
import tempfile
import typing as typ
from pathlib import Path

from lading import config as config_module
from lading.commands.publish_diagnostics import _append_compiletest_diagnostics
from lading.commands.publish_execution import _CommandRunner, _invoke
from lading.commands.publish_plan import PublishPlan, _format_plan, plan_publication
from lading.utils.path import normalise_workspace_root

if typ.TYPE_CHECKING:
    from lading.config import LadingConfig
    from lading.workspace import WorkspaceCrate, WorkspaceGraph


class PublishPreparationError(RuntimeError):
    """Raised when publish preparation cannot stage required assets."""


@dc.dataclass(frozen=True, slots=True)
class _CargoPreflightOptions:
    """Options controlling how cargo pre-flight commands are invoked."""

    extra_args: typ.Sequence[str]
    test_excludes: typ.Sequence[str] = ()
    unit_tests_only: bool = False
    env: typ.Mapping[str, str] | None = None
    diagnostics_tail_lines: int | None = None


@dc.dataclass(frozen=True, slots=True)
class PublishOptions:
    """Runtime configuration for publish planning, staging, and checks.

    Parameters
    ----------
    allow_dirty:
        When ``True`` the git cleanliness guard is skipped.
    build_directory:
        Optional directory used to stage workspace artifacts. When ``None``,
        a temporary directory is created for each invocation.
    preserve_symlinks:
        Control whether staging preserves symbolic links in the workspace
        clone instead of dereferencing them.
    cleanup:
        When :data:`True`, the staged workspace is removed automatically on
        process exit.
    configuration:
        Optional :class:`~lading.config.LadingConfig` instance to reuse instead
        of loading from disk.
    workspace:
        Optional pre-loaded workspace graph to reuse for planning.
    command_runner:
        Optional callable used to execute shell commands. Primarily intended
        for tests and dependency injection.

    """

    allow_dirty: bool = True
    build_directory: Path | None = None
    preserve_symlinks: bool = True
    cleanup: bool = False
    configuration: LadingConfig | None = None
    workspace: WorkspaceGraph | None = None
    command_runner: _CommandRunner | None = None


@dc.dataclass(frozen=True, slots=True)
class PublishPreparation:
    """Details about the staged workspace copy."""

    staging_root: Path
    copied_readmes: tuple[Path, ...]


class PublishPreflightError(RuntimeError):
    """Raised when required pre-publication checks fail."""


def _build_preflight_environment(
    overrides: tuple[tuple[str, str], ...],
) -> dict[str, str]:
    """Return the base environment for publish pre-flight commands."""
    env = dict(os.environ)
    env.update(overrides)
    return env


def _run_aux_build_commands(
    workspace_root: Path,
    commands: tuple[tuple[str, ...], ...],
    *,
    runner: _CommandRunner,
    env: typ.Mapping[str, str] | None,
) -> None:
    """Execute auxiliary build commands prior to cargo pre-flight runs."""
    for command in commands:
        exit_code, stdout, stderr = runner(command, cwd=workspace_root, env=env)
        if exit_code != 0:
            detail = (stderr or stdout).strip()
            rendered = " ".join(command)
            message = (
                f"Auxiliary build command failed with exit code {exit_code}: {rendered}"
            )
            if detail:
                message = f"{message}; {detail}"
            raise PublishPreflightError(message)


def _resolve_extern_path(workspace_root: Path, raw_path: str) -> Path:
    """Return ``raw_path`` resolved relative to ``workspace_root`` when needed."""
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    return candidate.expanduser().resolve(strict=False)


def _apply_compiletest_externs(
    env: typ.Mapping[str, str],
    externs: tuple[config_module.CompiletestExtern, ...],
    *,
    workspace_root: Path,
) -> dict[str, str]:
    """Return ``env`` with compiletest externs appended to ``RUSTFLAGS``."""
    if not externs:
        return dict(env)
    updated = dict(env)
    flags = " ".join(
        f"--extern {extern.crate}={_resolve_extern_path(workspace_root, extern.path)}"
        for extern in externs
    ).strip()
    if not flags:
        return updated
    previous = updated.get("RUSTFLAGS", "").strip()
    updated["RUSTFLAGS"] = " ".join(filter(None, (previous, flags)))
    return updated


def _normalise_build_directory(
    workspace_root: Path, build_directory: Path | None
) -> Path:
    """Return a directory suitable for staging workspace artifacts."""
    if build_directory is None:
        return Path(tempfile.mkdtemp(prefix="lading-publish-"))

    candidate = Path(build_directory).expanduser()
    candidate = candidate.resolve(strict=False)

    workspace_root = workspace_root.resolve(strict=True)
    if candidate.is_relative_to(workspace_root):
        message = "Publish build directory cannot reside within the workspace root"
        raise PublishPreparationError(message)

    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def _copy_workspace_tree(
    workspace_root: Path, build_directory: Path, *, preserve_symlinks: bool
) -> Path:
    """Copy ``workspace_root`` into ``build_directory`` and return the clone.

    When ``preserve_symlinks`` is :data:`True`, the cloned tree keeps symbolic
    links instead of dereferencing them. This avoids unexpectedly copying large
    directories outside the workspace while still allowing callers to opt into
    dereferencing if required.
    """
    workspace_root = workspace_root.resolve(strict=True)
    staging_root = build_directory / workspace_root.name
    if staging_root.resolve(strict=False).is_relative_to(workspace_root):
        message = "Publish staging directory cannot be nested inside the workspace root"
        raise PublishPreparationError(message)
    if staging_root.exists():
        shutil.rmtree(staging_root)
    shutil.copytree(workspace_root, staging_root, symlinks=preserve_symlinks)
    return staging_root


def _collect_workspace_readme_targets(
    workspace: WorkspaceGraph,
) -> tuple[WorkspaceCrate, ...]:
    """Return crates that opt into using the workspace README."""
    return tuple(crate for crate in workspace.crates if crate.readme_is_workspace)


def _stage_workspace_readmes(
    *,
    crates: tuple[WorkspaceCrate, ...],
    workspace_root: Path,
    staging_root: Path,
) -> tuple[Path, ...]:
    """Copy the workspace README into ``crates`` located at ``staging_root``."""
    if not crates:
        return ()

    workspace_readme = workspace_root / "README.md"
    if not workspace_readme.exists():
        message = (
            "Workspace README.md is required by crates that set readme.workspace = true"
        )
        raise PublishPreparationError(message)

    copied: list[Path] = []
    for crate in crates:
        try:
            relative_crate_root = crate.root_path.relative_to(workspace_root)
        except ValueError as exc:
            message = (
                "Crate "
                f"{crate.name!r} is outside the workspace root; "
                "cannot stage README"
            )
            raise PublishPreparationError(message) from exc
        staged_crate_root = staging_root / relative_crate_root
        staged_crate_root.mkdir(parents=True, exist_ok=True)
        staged_readme = staged_crate_root / "README.md"
        shutil.copyfile(workspace_readme, staged_readme)
        copied.append(staged_readme)

    copied.sort(key=lambda path: path.relative_to(staging_root).as_posix())
    return tuple(copied)


def prepare_workspace(
    plan: PublishPlan,
    workspace: WorkspaceGraph,
    *,
    options: PublishOptions | None = None,
) -> PublishPreparation:
    """Stage a workspace copy and propagate workspace READMEs."""
    active_options = PublishOptions() if options is None else options
    build_directory = _normalise_build_directory(
        plan.workspace_root, active_options.build_directory
    )
    staging_root = _copy_workspace_tree(
        plan.workspace_root,
        build_directory,
        preserve_symlinks=active_options.preserve_symlinks,
    )
    readme_crates = _collect_workspace_readme_targets(workspace)
    copied_readmes = _stage_workspace_readmes(
        crates=readme_crates,
        workspace_root=plan.workspace_root,
        staging_root=staging_root,
    )
    preparation = PublishPreparation(
        staging_root=staging_root, copied_readmes=copied_readmes
    )
    if active_options.cleanup:
        build_root = staging_root.parent

        def _cleanup() -> None:
            shutil.rmtree(build_root, ignore_errors=True)

        atexit.register(_cleanup)
    return preparation


def _format_preparation_summary(preparation: PublishPreparation) -> tuple[str, ...]:
    """Return formatted summary lines for staging results."""
    lines = [f"Staged workspace at: {preparation.staging_root}"]
    if preparation.copied_readmes:
        lines.append("Copied workspace README to:")
        for path in preparation.copied_readmes:
            try:
                relative_path = path.relative_to(preparation.staging_root)
            except ValueError:
                relative_path = path
            lines.append(f"- {relative_path}")
    else:
        lines.append("Copied workspace README to: none required")
    return tuple(lines)


def _ensure_configuration(
    configuration: LadingConfig | None, workspace_root: Path
) -> LadingConfig:
    """Return the active configuration, loading it from disk when required."""
    if configuration is not None:
        return configuration

    try:
        return config_module.current_configuration()
    except config_module.ConfigurationNotLoadedError:
        return config_module.load_configuration(workspace_root)


def _ensure_workspace(
    workspace: WorkspaceGraph | None, workspace_root: Path
) -> WorkspaceGraph:
    """Return the workspace graph rooted at ``workspace_root``."""
    if workspace is not None:
        return workspace

    from lading.workspace import WorkspaceModelError, load_workspace

    try:
        return load_workspace(workspace_root)
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        message = f"Workspace root not found: {workspace_root}"
        raise WorkspaceModelError(message) from exc


def run(
    workspace_root: Path,
    configuration: LadingConfig | None = None,
    workspace: WorkspaceGraph | None = None,
    *,
    options: PublishOptions | None = None,
) -> str:
    """Plan and prepare crate publication for ``workspace_root``."""
    root_path = normalise_workspace_root(workspace_root)
    effective_options = PublishOptions() if options is None else options
    configuration_override = configuration or effective_options.configuration
    workspace_override = workspace or effective_options.workspace
    command_runner = effective_options.command_runner or _invoke
    active_configuration = _ensure_configuration(configuration_override, root_path)
    active_workspace = _ensure_workspace(workspace_override, root_path)

    _run_preflight_checks(
        root_path,
        allow_dirty=effective_options.allow_dirty,
        configuration=active_configuration,
        runner=command_runner,
    )
    plan = plan_publication(
        active_workspace, active_configuration, workspace_root=root_path
    )
    preparation = prepare_workspace(plan, active_workspace, options=options)
    plan_message = _format_plan(
        plan, strip_patches=active_configuration.publish.strip_patches
    )
    summary_lines = _format_preparation_summary(preparation)
    return f"{plan_message}\n\n" + "\n".join(summary_lines)


def _run_preflight_checks(
    workspace_root: Path,
    *,
    allow_dirty: bool,
    configuration: LadingConfig | None = None,
    runner: _CommandRunner | None = None,
) -> None:
    """Execute publish pre-flight checks for ``workspace_root``."""
    command_runner = runner or _invoke
    active_configuration = _ensure_configuration(configuration, workspace_root)
    preflight_config = active_configuration.preflight
    base_env = _build_preflight_environment(preflight_config.env_overrides)
    _verify_clean_working_tree(
        workspace_root,
        allow_dirty=allow_dirty,
        runner=command_runner,
        env=base_env,
    )
    _run_aux_build_commands(
        workspace_root,
        preflight_config.aux_build,
        runner=command_runner,
        env=base_env,
    )

    with tempfile.TemporaryDirectory(prefix="lading-preflight-target-") as target:
        target_path = Path(target)
        unit_tests_only = preflight_config.unit_tests_only
        check_arguments, test_arguments = _preflight_argument_sets(
            target_path, unit_tests_only=unit_tests_only
        )
        _run_cargo_preflight(
            workspace_root,
            "check",
            runner=command_runner,
            options=_CargoPreflightOptions(
                extra_args=check_arguments,
                env=base_env,
            ),
        )
        test_env = _apply_compiletest_externs(
            base_env,
            preflight_config.compiletest_externs,
            workspace_root=workspace_root,
        )
        _run_cargo_preflight(
            workspace_root,
            "test",
            runner=command_runner,
            options=_CargoPreflightOptions(
                extra_args=test_arguments,
                test_excludes=preflight_config.test_exclude,
                unit_tests_only=unit_tests_only,
                env=test_env,
                diagnostics_tail_lines=preflight_config.stderr_tail_lines,
            ),
        )


def _compose_preflight_arguments(
    target_dir: Path, *, include_all_targets: bool
) -> tuple[str, ...]:
    """Build the ordered argument tuple shared by pre-flight cargo commands."""
    arguments = ["--workspace"]
    if include_all_targets:
        arguments.append("--all-targets")
    arguments.append(f"--target-dir={target_dir}")
    return tuple(arguments)


def _preflight_argument_sets(
    target_dir: Path, *, unit_tests_only: bool
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return argument tuples for cargo check and cargo test pre-flight calls."""
    check_arguments = _compose_preflight_arguments(target_dir, include_all_targets=True)
    test_arguments = _compose_preflight_arguments(
        target_dir, include_all_targets=not unit_tests_only
    )
    return check_arguments, test_arguments


def _normalise_test_excludes(entries: typ.Sequence[str]) -> tuple[str, ...]:
    """Return sorted, deduplicated, trimmed crate names for ``--exclude`` flags."""
    return tuple(sorted({crate.strip() for crate in entries if crate.strip()}))


def _build_test_arguments(
    base_arguments: list[str], options: _CargoPreflightOptions
) -> list[str]:
    """Return cargo test arguments derived from ``options``."""
    arguments = list(base_arguments)
    if options.unit_tests_only:
        arguments.extend(("--lib", "--bins"))
    for crate_name in _normalise_test_excludes(options.test_excludes):
        # Sorted unique values keep cargo invocations deterministic for tests/logging.
        arguments.extend(("--exclude", crate_name))
    return arguments


def _verify_clean_working_tree(
    workspace_root: Path,
    *,
    allow_dirty: bool,
    runner: _CommandRunner,
    env: typ.Mapping[str, str] | None = None,
) -> None:
    """Ensure ``workspace_root`` has no uncommitted changes unless allowed."""
    if allow_dirty:
        return

    exit_code, stdout, stderr = runner(
        ("git", "status", "--porcelain"),
        cwd=workspace_root,
        env=env,
    )
    if exit_code != 0:
        detail = (stderr or stdout).strip()
        message = (
            "Failed to verify workspace state; is this a git repository?"
            if "not a git repository" in detail.lower()
            else "Failed to verify workspace state with git status"
        )
        if detail:
            message = f"{message}: {detail}"
        raise PublishPreflightError(message)
    if stdout.strip():
        message = (
            "Workspace has uncommitted changes; commit or stash them "
            "before publishing or re-run without --forbid-dirty."
        )
        raise PublishPreflightError(message)


def _run_cargo_preflight(
    workspace_root: Path,
    subcommand: typ.Literal["check", "test"],
    *,
    runner: _CommandRunner,
    options: _CargoPreflightOptions,
) -> None:
    """Run ``cargo <subcommand>`` inside ``workspace_root``."""
    arguments = list(options.extra_args)
    if subcommand == "test":
        arguments = _build_test_arguments(arguments, options)
    exit_code, stdout, stderr = runner(
        ("cargo", subcommand, *arguments),
        cwd=workspace_root,
        env=options.env,
    )
    if exit_code != 0:
        message = _build_cargo_error_message(subcommand, exit_code, stdout, stderr)
        if options.diagnostics_tail_lines is not None:
            message = _append_compiletest_diagnostics(
                message,
                stdout,
                stderr,
                tail_lines=options.diagnostics_tail_lines,
            )
        raise PublishPreflightError(message)


def _build_cargo_error_message(
    subcommand: str, exit_code: int, stdout: str, stderr: str
) -> str:
    """Return a consistent failure message for cargo pre-flight commands."""
    message = f"Pre-flight cargo {subcommand} failed with exit code {exit_code}"
    if detail := (stderr or stdout).strip():
        message = f"{message}: {detail}"
    return message
