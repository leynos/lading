"""Pre-flight checks for publish command workflows.

``publish_preflight`` contains the local validation work that runs before the
publish pipeline packages or uploads crates. It builds the cargo environment,
checks whether the working tree is clean, runs configured auxiliary build
commands, and executes cargo check/test commands with the publish pre-flight
configuration.

The publish command calls :func:`_run_preflight_checks` before planning and
dispatching publication. The function returns ``None`` when every check passes
and raises :class:`lading.commands.publish_errors.PublishPreflightError` when a
required check fails.

Examples
--------
>>> from pathlib import Path
>>> from lading.commands.publish_preflight import _run_preflight_checks
>>> _run_preflight_checks(Path("."), allow_dirty=False, configuration=config)
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import logging
import os
import tempfile
import typing as typ
from pathlib import Path

from lading.commands.lockfile import (
    discover_tracked_lockfiles,
    validate_lockfile_freshness,
)
from lading.commands.publish_diagnostics import _append_compiletest_diagnostics
from lading.commands.publish_errors import PublishPreflightError
from lading.commands.publish_execution import _invoke

if typ.TYPE_CHECKING:
    from lading.config import CompiletestExtern, LadingConfig
    from lading.runtime import CommandRunner


LOGGER = logging.getLogger(__name__)


@dc.dataclass(frozen=True, slots=True)
class _CargoPreflightOptions:
    """Options controlling how cargo pre-flight commands are invoked."""

    extra_args: cabc.Sequence[str]
    test_excludes: cabc.Sequence[str] = ()
    unit_tests_only: bool = False
    env: cabc.Mapping[str, str] | None = None
    diagnostics_tail_lines: int | None = None


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
    runner: CommandRunner,
    env: cabc.Mapping[str, str] | None,
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
            LOGGER.error(message)
            raise PublishPreflightError(message)


def _resolve_extern_path(workspace_root: Path, raw_path: str) -> Path:
    """Return ``raw_path`` resolved relative to ``workspace_root`` when needed."""
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    return candidate.expanduser().resolve(strict=False)


def _apply_compiletest_externs(
    env: cabc.Mapping[str, str],
    externs: tuple[CompiletestExtern, ...],
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


def _run_preflight_checks(
    workspace_root: Path,
    *,
    allow_dirty: bool,
    configuration: LadingConfig,
    runner: CommandRunner | None = None,
) -> None:
    """Execute publish pre-flight checks for ``workspace_root``."""
    command_runner = runner or _invoke
    preflight_config = configuration.preflight
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
    _validate_lockfile_freshness(
        workspace_root,
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

def _validate_lockfile_freshness(
    workspace_root: Path,
    *,
    runner: _CommandRunner,
    env: cabc.Mapping[str, str] | None = None,
) -> None:
    """Fail early when tracked Cargo.lock files are stale."""
    base_env = env

    def runner_with_env(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        effective_env = base_env if env is None else env
        return runner(command, cwd=cwd, env=effective_env)

    stale_lockfiles: list[Path] = []
    for lockfile_path in discover_tracked_lockfiles(workspace_root, runner_with_env):
        manifest_path = lockfile_path.parent / "Cargo.toml"
        if not validate_lockfile_freshness(manifest_path, runner_with_env):
            stale_lockfiles.append(lockfile_path)

    if not stale_lockfiles:
        return

    lines = [
        "Tracked Cargo.lock files are stale after manifest version changes.",
        (
            "This commonly happens after running `lading bump`; re-run it to "
            "refresh tracked lockfiles, or repair each stale lockfile manually:"
        ),
    ]
    for lockfile_path in stale_lockfiles:
        manifest_path = lockfile_path.parent / "Cargo.toml"
        lines.append(f"- {lockfile_path}")
        lines.append(f"  cargo generate-lockfile --manifest-path {manifest_path}")

    message = "\n".join(lines)
    LOGGER.error(message)
    raise PublishPreflightError(message)
def _preflight_argument_sets(
    target_dir: Path, *, unit_tests_only: bool
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return argument tuples for cargo check and cargo test pre-flight calls."""
    check_arguments = _compose_preflight_arguments(target_dir, include_all_targets=True)
    test_arguments = _compose_preflight_arguments(
        target_dir, include_all_targets=not unit_tests_only
    )
    return check_arguments, test_arguments


def _normalise_test_excludes(entries: cabc.Sequence[str]) -> tuple[str, ...]:
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
    runner: CommandRunner,
    env: cabc.Mapping[str, str] | None = None,
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
        LOGGER.error(message)
        raise PublishPreflightError(message)
    if stdout.strip():
        message = (
            "Workspace has uncommitted changes; commit or stash them "
            "before publishing or re-run without --forbid-dirty."
        )
        LOGGER.error(message)
        raise PublishPreflightError(message)


def _run_cargo_preflight(
    workspace_root: Path,
    subcommand: typ.Literal["check", "test"],
    *,
    runner: CommandRunner,
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
        LOGGER.error(message)
        raise PublishPreflightError(message)


def _build_cargo_error_message(
    subcommand: str, exit_code: int, stdout: str, stderr: str
) -> str:
    """Return a consistent failure message for cargo pre-flight commands."""
    message = f"Pre-flight cargo {subcommand} failed with exit code {exit_code}"
    if detail := (stderr or stdout).strip():
        message = f"{message}: {detail}"
    return message


__all__ = [
    "_CargoPreflightOptions",
    "_build_test_arguments",
    "_compose_preflight_arguments",
    "_run_cargo_preflight",
    "_run_preflight_checks",
    "_verify_clean_working_tree",
]
