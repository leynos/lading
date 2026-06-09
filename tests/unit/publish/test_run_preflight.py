"""Publish preflight execution test coverage."""

from __future__ import annotations

import collections.abc as cabc
from pathlib import Path

import pytest

from lading.commands import publish, publish_preflight

from .conftest import (
    ORIGINAL_PREFLIGHT,
    make_config,
    make_crate,
    make_preflight_config,
    make_workspace,
)
from .preflight_test_utils import _extract_cargo_test_call, _setup_preflight_test

EXCLUDE_SCENARIOS = [
    pytest.param((), (), id="none"),
    pytest.param(("alpha", "beta"), ("alpha", "beta"), id="ordered"),
    pytest.param(("beta", "alpha"), ("alpha", "beta"), id="sorted"),
    pytest.param((" alpha ", "beta", "alpha"), ("alpha", "beta"), id="trimmed"),
    pytest.param(("", " ", "\t"), (), id="blank_entries"),
    pytest.param(
        (" \n", "\rbeta\t", "\talpha", "beta"),
        ("alpha", "beta"),
        id="whitespace_variants",
    ),
    pytest.param(
        ("gamma", "beta", "alpha"),
        ("alpha", "beta", "gamma"),
        id="unsorted",
    ),
    pytest.param(("beta", "beta", "beta"), ("beta",), id="deduplicated"),
    pytest.param(
        ("alpha", "alpha", " alpha ", "\talpha\t"),
        ("alpha",),
        id="duplicate_whitespace",
    ),
    pytest.param(("alpha", "", "beta"), ("alpha", "beta"), id="mixed_blank"),
]


def test_run_executes_preflight_checks_in_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pre-flight commands run inside the resolved workspace root."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    configuration = make_config()

    calls: list[tuple[tuple[str, ...], Path | None]] = []

    def fake_invoke(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        calls.append((tuple(command), cwd))
        return 0, "", ""

    monkeypatch.setattr(publish, "_invoke", fake_invoke)

    publish.run(
        root,
        configuration,
        workspace,
        options=publish.PublishOptions(allow_dirty=False),
    )

    assert (
        ("git", "status", "--porcelain"),
        root,
    ) in calls
    check_call = next(
        command
        for command in calls
        if command[0][0] == "cargo" and command[0][1] == "check"
    )
    test_call = next(
        command
        for command in calls
        if command[0][0] == "cargo" and command[0][1] == "test"
    )

    for command, cwd in (check_call, test_call):
        assert cwd == root
        assert command[2] == "--workspace"
        assert command[3] == "--all-targets"
        assert any(arg.startswith("--target-dir=") for arg in command[4:])


@pytest.mark.parametrize(
    ("configured_excludes", "expected_excludes"),
    EXCLUDE_SCENARIOS,
)
def test_run_includes_preflight_test_excludes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    configured_excludes: tuple[str, ...],
    expected_excludes: tuple[str, ...],
) -> None:
    """Configured exclusions match the builder output and cargo invocation."""
    configuration = make_config(
        preflight=make_preflight_config(test_exclude=configured_excludes)
    )
    root, _workspace, calls = _setup_preflight_test(
        monkeypatch, tmp_path, configuration
    )
    args, cwd = _extract_cargo_test_call(calls)
    assert cwd == root
    arguments = list(args[2:])
    assert arguments[0] == "--workspace"
    include_all_targets = "--all-targets" in arguments
    assert include_all_targets == (not configuration.preflight.unit_tests_only)
    target_argument = next(
        value for value in arguments if value.startswith("--target-dir=")
    )
    target_dir = Path(target_argument.split("=", 1)[1])
    base_arguments = list(
        publish_preflight._compose_preflight_arguments(
            target_dir,
            include_all_targets=include_all_targets,
        )
    )
    options = publish_preflight._CargoPreflightOptions(
        extra_args=tuple(base_arguments),
        test_excludes=configured_excludes,
        unit_tests_only=configuration.preflight.unit_tests_only,
    )
    rebuilt_arguments = publish_preflight._build_test_arguments(
        list(base_arguments),
        options,
    )
    assert rebuilt_arguments == arguments
    exclude_values = tuple(
        arguments[index + 1]
        for index, value in enumerate(arguments[:-1])
        if value == "--exclude"
    )
    assert exclude_values == expected_excludes
    if not expected_excludes:
        assert "--exclude" not in arguments


def test_run_honours_preflight_unit_tests_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unit-test-only preflight mode narrows cargo test targets."""
    configuration = make_config(preflight=make_preflight_config(unit_tests_only=True))
    root, _workspace, calls = _setup_preflight_test(
        monkeypatch, tmp_path, configuration
    )
    args, cwd = _extract_cargo_test_call(calls)
    assert cwd == root
    assert args[2] == "--workspace"
    assert "--all-targets" not in args
    assert any(part.startswith("--target-dir=") for part in args[3:])
    assert "--lib" in args
    assert "--bins" in args
    assert "--exclude" not in args
    lib_index = args.index("--lib")
    bins_index = args.index("--bins")
    assert bins_index == lib_index + 1


def test_run_unit_tests_only_with_excludes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exclusions are still honoured when unit-tests-only mode is enabled."""
    configuration = make_config(
        preflight=make_preflight_config(
            unit_tests_only=True, test_exclude=("gamma", "alpha")
        )
    )
    root, _workspace, calls = _setup_preflight_test(
        monkeypatch,
        tmp_path,
        configuration,
        crate_names=("alpha", "beta", "gamma"),
    )
    args, cwd = _extract_cargo_test_call(calls)
    assert cwd == root
    assert args[2] == "--workspace"
    assert "--all-targets" not in args
    assert "--lib" in args
    assert "--bins" in args
    assert args[-6:] == (
        "--exclude",
        "alpha",
        "--exclude",
        "gamma",
        "--lib",
        "--bins",
    )


def test_dirty_workspace_allowed_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Publish skips git status when cleanliness enforcement is disabled."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    configuration = make_config()

    def skip_git_invoke(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        normalized_cmd = tuple(command)
        if normalized_cmd == ("git", "status", "--porcelain"):
            message = "git status should be skipped by default"
            raise AssertionError(message)
        return 0, "", ""

    monkeypatch.setattr(publish, "_invoke", skip_git_invoke)

    message = publish.run(root, configuration, workspace)

    assert message.startswith(f"Publish plan for {root}")


def test_forbid_dirty_flag_enforces_cleanliness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explicit forbid-dirty option requires a clean git status."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    configuration = make_config()

    def dirty_invoke(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[0] == "git":
            return 0, " M Cargo.toml\n", ""
        return 0, "", ""

    monkeypatch.setattr(publish, "_invoke", dirty_invoke)

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish.run(
            root,
            configuration,
            workspace,
            options=publish.PublishOptions(allow_dirty=False),
        )

    assert "uncommitted changes" in str(excinfo.value)


@pytest.mark.parametrize(
    ("failing_subcommand", "expected_message"),
    [
        ("check", "cargo check"),
        ("test", "cargo test"),
    ],
    ids=["check_failure", "test_failure"],
)
def test_run_raises_when_preflight_cargo_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failing_subcommand: str,
    expected_message: str,
) -> None:
    """Non-zero cargo check/test aborts the publish command."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    configuration = make_config()

    def failing_invoke(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[0] == "git":
            return 0, "", ""
        if len(command) > 1 and command[1] == failing_subcommand:
            return 1, "", expected_message
        return 0, "", ""

    monkeypatch.setattr(publish, "_invoke", failing_invoke)

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish.run(root, configuration, workspace)

    message = str(excinfo.value)
    assert expected_message in message
    assert f"cargo {failing_subcommand}" in message
    assert "exit code 1" in message
