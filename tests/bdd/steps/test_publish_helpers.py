"""Helper utilities shared across publish BDD step modules."""

from __future__ import annotations

import typing as typ
from pathlib import Path

from lading.testing import toml_utils

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from tomlkit.toml_document import TOMLDocument

    from .test_publish_infrastructure import _PreflightInvocationRecorder


def _publish_plan_lines(cli_run: dict[str, typ.Any]) -> list[str]:
    """Return trimmed publish plan output lines for ``cli_run``."""
    return [line.strip() for line in cli_run["stdout"].splitlines() if line.strip()]


def _extract_staging_root_from_plan(lines: list[str]) -> Path:
    """Return the staging root path parsed from publish plan ``lines``."""
    staging_line = next(
        (line for line in lines if line.startswith("Staged workspace at:")), None
    )
    assert staging_line is not None, "Staging location not found in publish plan output"
    return Path(staging_line.split(": ", 1)[1])


def _load_staged_manifest(cli_run: dict[str, typ.Any]) -> TOMLDocument:
    """Return the staged workspace manifest for ``cli_run``."""
    lines = _publish_plan_lines(cli_run)
    staging_root = _extract_staging_root_from_plan(lines)
    manifest_path = staging_root / "Cargo.toml"
    return toml_utils.load_manifest(manifest_path)


def _get_patch_entries(document: typ.Mapping[str, typ.Any]) -> dict[str, typ.Any]:
    """Return the ``[patch.crates-io]`` mapping if it exists."""
    patch_table = document.get("patch")
    if not isinstance(patch_table, typ.Mapping):
        return {}
    crates_io = patch_table.get("crates-io")
    return {} if not isinstance(crates_io, typ.Mapping) else dict(crates_io)


def _split_names(crate_names: str) -> list[str]:
    """Split and trim comma-separated crate names."""
    return [name.strip() for name in crate_names.split(",") if name.strip()]


def _get_test_invocations(
    recorder: _PreflightInvocationRecorder,
) -> list[tuple[str, ...]]:
    """Return recorded cargo test invocations or raise if missing."""
    if invocations := recorder.by_label("cargo::test"):
        return [args for args, _ in invocations]
    message = "cargo test pre-flight command was not invoked"
    raise AssertionError(message)


def _get_package_invocations(
    recorder: _PreflightInvocationRecorder,
) -> list[tuple[tuple[str, ...], dict[str, str]]]:
    """Return recorded cargo package invocations or raise if missing."""
    if invocations := recorder.by_label("cargo::package"):
        return invocations
    message = "cargo package was not invoked for publishable crates"
    raise AssertionError(message)


def _get_publish_invocations(
    recorder: _PreflightInvocationRecorder,
) -> list[tuple[tuple[str, ...], dict[str, str]]]:
    """Return recorded cargo publish invocations or raise if missing."""
    if invocations := recorder.by_label("cargo::publish"):
        return invocations
    message = "cargo publish was not invoked for publishable crates"
    raise AssertionError(message)


def _extract_crate_names_from_invocations(
    invocations: list[tuple[tuple[str, ...], dict[str, str]]],
) -> list[str]:
    """Extract crate directory names from invocation environments."""
    crate_names: list[str] = []
    for _args, env in invocations:
        cwd = env.get("PWD", "")
        crate_names.append(Path(cwd).name if cwd else "")
    return crate_names


def _get_test_invocation_envs(
    recorder: _PreflightInvocationRecorder,
) -> list[dict[str, str]]:
    """Return recorded cargo test environments or raise if missing."""
    if invocations := recorder.by_label("cargo::test"):
        return [env for _, env in invocations]
    message = "cargo test pre-flight command was not invoked"
    raise AssertionError(message)


def _has_contiguous_args(args: tuple[str, ...], first: str, second: str) -> bool:
    """Return True when ``first`` is immediately followed by ``second`` in ``args``."""
    for index in range(len(args) - 1):
        if args[index] == first and args[index + 1] == second:
            return True
    return False


def _has_ordered_args_non_contiguous(
    args: tuple[str, ...], first: str, second: str
) -> bool:
    """Return True when ``first`` appears before ``second`` in ``args``."""
    try:
        start_index = args.index(first)
    except ValueError:
        return False
    return second in args[start_index + 1 :]


def _has_ordered_args(
    invocations: list[tuple[str, ...]],
    first: str,
    second: str,
    *,
    contiguous: bool = True,
) -> bool:
    """Detect ``first`` followed by ``second`` in ``invocations``."""
    checker = _has_contiguous_args if contiguous else _has_ordered_args_non_contiguous
    return any(checker(args, first, second) for args in invocations)


def _assert_invocations_flag_presence(
    invocations: list[tuple[tuple[str, ...], dict[str, str]]],
    flag: str,
    command_name: str,
    *,
    should_contain: bool,
) -> None:
    """Assert that invocations contain or lack ``flag`` based on ``should_contain``."""
    for args, _env in invocations:
        flag_present = flag in args
        if flag_present != should_contain:
            expectation = "Expected" if should_contain else "Did not expect"
            message = f"{expectation} {flag!r} in {command_name} invocation"
            raise AssertionError(message)


def _assert_invocations_have_flag(
    invocations: list[tuple[tuple[str, ...], dict[str, str]]],
    flag: str,
    command_name: str,
) -> None:
    """Assert that every invocation contains ``flag``."""
    _assert_invocations_flag_presence(
        invocations, flag, command_name, should_contain=True
    )


def _assert_invocations_lack_flag(
    invocations: list[tuple[tuple[str, ...], dict[str, str]]],
    flag: str,
    command_name: str,
) -> None:
    """Assert that no invocation contains ``flag``."""
    _assert_invocations_flag_presence(
        invocations, flag, command_name, should_contain=False
    )


def _assert_crate_order_matches(
    observed: list[str],
    expected: list[str],
    context: str,
) -> None:
    """Assert observed crate names match expected order."""
    if observed != expected:
        message = (
            f"Unexpected crate order for {context}: "
            f"observed={observed!r}, expected={expected!r}"
        )
        raise AssertionError(message)
