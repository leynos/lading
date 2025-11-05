"""Integration-style tests for :func:`lading.commands.publish.run`."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from lading import config as config_module
from lading.commands import publish
from lading.workspace import WorkspaceGraph, WorkspaceModelError

from .conftest import (
    ORIGINAL_PREFLIGHT,
    make_config,
    make_crate,
    make_workspace,
)
from .preflight_test_utils import (
    _extract_cargo_test_call,
    _setup_preflight_test,
)


def test_run_normalises_workspace_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The run helper resolves the workspace root before planning."""
    workspace = Path("workspace")
    monkeypatch.chdir(tmp_path)
    resolved = tmp_path / "workspace"
    plan_workspace = make_workspace(resolved)
    configuration = make_config()

    def fake_load(root: Path) -> WorkspaceGraph:
        assert root == resolved
        return plan_workspace

    monkeypatch.setattr("lading.workspace.load_workspace", fake_load)
    monkeypatch.setattr(
        publish,
        "prepare_workspace",
        lambda *_args, **_kwargs: publish.PublishPreparation(
            staging_root=resolved, copied_readmes=()
        ),
    )
    output = publish.run(workspace, configuration)

    assert output.splitlines()[0] == f"Publish plan for {resolved}"


def test_run_uses_active_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``run`` falls back to :func:`current_configuration` when needed."""
    configuration = make_config(exclude=("skip-me",))
    monkeypatch.setattr(config_module, "current_configuration", lambda: configuration)
    root = tmp_path.resolve()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    monkeypatch.setattr("lading.workspace.load_workspace", lambda _: workspace)

    output = publish.run(tmp_path)

    assert "skip-me" in output


def test_run_loads_configuration_when_inactive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``run`` loads configuration from disk if no active configuration exists."""
    root = tmp_path.resolve()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    monkeypatch.setattr("lading.workspace.load_workspace", lambda _: workspace)
    loaded_configuration = make_config()
    load_calls: list[Path] = []

    def raise_not_loaded() -> config_module.LadingConfig:
        message = "Configuration unavailable"
        raise config_module.ConfigurationNotLoadedError(message)

    def capture_load(path: Path) -> config_module.LadingConfig:
        load_calls.append(path)
        return loaded_configuration

    monkeypatch.setattr(config_module, "current_configuration", raise_not_loaded)
    monkeypatch.setattr(config_module, "load_configuration", capture_load)

    output = publish.run(root)

    assert "Crates to publish" in output
    assert load_calls == [root]


def test_run_formats_plan_summary(tmp_path: Path) -> None:
    """``run`` returns a structured summary of the publish plan."""
    root = tmp_path.resolve()
    publishable = make_crate(root, "alpha")
    manifest_skipped = make_crate(root, "beta", publish_flag=False)
    config_skipped = make_crate(root, "gamma")
    workspace = make_workspace(root, publishable, manifest_skipped, config_skipped)
    configuration = make_config(exclude=("gamma", "missing"))

    message = publish.run(root, configuration, workspace)

    lines = message.splitlines()
    assert lines[0] == f"Publish plan for {root}"
    assert "Strip patch strategy: all" in lines[1]
    assert "- alpha @ 0.1.0" in lines
    assert "Skipped (publish = false):" in lines
    assert "- beta" in lines
    assert "Skipped via publish.exclude:" in lines
    assert "- gamma" in lines
    assert "Configured exclusions not found in workspace:" in lines
    assert "- missing" in lines


def test_run_reports_no_publishable_crates(tmp_path: Path) -> None:
    """``run`` highlights when no crates are eligible for publication."""
    root = tmp_path.resolve()
    manifest_skipped = make_crate(root, "alpha", publish_flag=False)
    config_skipped_first = make_crate(root, "beta")
    config_skipped_second = make_crate(root, "gamma")
    workspace = make_workspace(
        root, manifest_skipped, config_skipped_first, config_skipped_second
    )
    configuration = make_config(exclude=("beta", "gamma"))

    message = publish.run(root, configuration, workspace)

    lines = message.splitlines()
    assert "Crates to publish: none" in lines
    assert "Skipped (publish = false):" in lines
    assert "- alpha" in lines
    assert "Skipped via publish.exclude:" in lines
    assert "- beta" in lines
    assert "- gamma" in lines


def test_run_surfaces_missing_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``run`` converts missing workspace roots into workspace model errors."""
    configuration = make_config()

    def raise_missing(_: Path) -> WorkspaceGraph:
        message = "workspace missing"
        raise FileNotFoundError(message)

    monkeypatch.setattr("lading.workspace.load_workspace", raise_missing)

    with pytest.raises(WorkspaceModelError) as excinfo:
        publish.run(tmp_path, configuration)

    message = str(excinfo.value)
    assert "Workspace root not found" in message
    assert str(tmp_path.resolve()) in message


def test_run_surfaces_configuration_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``run`` propagates configuration errors encountered while loading."""

    def raise_not_loaded() -> config_module.LadingConfig:
        message = "Configuration inactive"
        raise config_module.ConfigurationNotLoadedError(message)

    def raise_config_error(_: Path) -> config_module.LadingConfig:
        message = "invalid configuration"
        raise config_module.ConfigurationError(message)

    monkeypatch.setattr(config_module, "current_configuration", raise_not_loaded)
    monkeypatch.setattr(config_module, "load_configuration", raise_config_error)

    with pytest.raises(config_module.ConfigurationError) as excinfo:
        publish.run(tmp_path)

    assert str(excinfo.value) == "invalid configuration"


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
        command: typ.Sequence[str], *, cwd: Path | None = None
    ) -> tuple[int, str, str]:
        calls.append((tuple(command), cwd))
        return 0, "", ""

    monkeypatch.setattr(publish, "_invoke", fake_invoke)

    publish.run(root, configuration, workspace)

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
    [
        pytest.param(("alpha", "beta"), ("alpha", "beta"), id="ordered"),
        pytest.param(("beta", "alpha"), ("alpha", "beta"), id="sorted"),
        pytest.param((" alpha ", "beta", "alpha"), ("alpha", "beta"), id="trimmed"),
        pytest.param(("", " ", "\t"), (), id="blank_entries"),
        pytest.param(
            ("gamma", "beta", "alpha"), ("alpha", "beta", "gamma"), id="unsorted"
        ),
        pytest.param(("beta", "beta", "beta"), ("beta",), id="deduplicated"),
    ],
)
def test_run_includes_preflight_test_excludes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    configured_excludes: tuple[str, ...],
    expected_excludes: tuple[str, ...],
) -> None:
    """Configured test exclusions propagate to cargo test invocations."""
    configuration = make_config(preflight_test_exclude=configured_excludes)
    root, _workspace, calls = _setup_preflight_test(
        monkeypatch, tmp_path, configuration
    )
    args, cwd = _extract_cargo_test_call(calls)
    assert cwd == root
    assert args[2] == "--workspace"
    assert args[3] == "--all-targets"
    trailing = args[4:]
    assert any(part.startswith("--target-dir=") for part in trailing)
    exclude_values = [
        trailing[index + 1]
        for index, value in enumerate(trailing[:-1])
        if value == "--exclude"
    ]
    if expected_excludes:
        assert tuple(exclude_values) == expected_excludes
    else:
        assert "--exclude" not in trailing


def test_run_omits_preflight_excludes_when_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When no crates are excluded the test command omits --exclude flags."""
    configuration = make_config()
    root, _workspace, calls = _setup_preflight_test(
        monkeypatch, tmp_path, configuration
    )
    args, cwd = _extract_cargo_test_call(calls)
    assert cwd == root
    assert args[2] == "--workspace"
    assert args[3] == "--all-targets"
    assert any(part.startswith("--target-dir=") for part in args[4:])
    assert "--exclude" not in args


def test_run_honours_preflight_unit_tests_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unit-test-only preflight mode narrows cargo test targets."""
    configuration = make_config(preflight_unit_tests_only=True)
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


def _verify_cargo_commands_executed(
    calls: list[tuple[tuple[str, ...], Path | None]],
    expected_cwd: Path,
) -> None:
    """Verify cargo check and test were invoked with correct arguments."""
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
        assert cwd == expected_cwd
        assert command[2] == "--workspace"
        assert command[3] == "--all-targets"
        assert any(arg.startswith("--target-dir=") for arg in command[4:])


def test_dirty_workspace_rejected_without_allow_dirty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Publish rejects dirty workspaces when --allow-dirty is not set."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    configuration = make_config()

    def dirty_invoke(
        command: typ.Sequence[str], *, cwd: Path | None = None
    ) -> tuple[int, str, str]:
        if command[0] == "git":
            return 0, " M Cargo.toml\n", ""
        return 0, "", ""

    monkeypatch.setattr(publish, "_invoke", dirty_invoke)

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish.run(root, configuration, workspace)

    assert "uncommitted changes" in str(excinfo.value)


def test_allow_dirty_flag_bypasses_git_status_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--allow-dirty`` skips git status check but runs cargo commands."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    configuration = make_config()
    calls: list[tuple[tuple[str, ...], Path | None]] = []

    def allow_dirty_invoke(
        command: typ.Sequence[str], *, cwd: Path | None = None
    ) -> tuple[int, str, str]:
        if command[0] == "git":
            message = "git status should be skipped when allow-dirty is set"
            raise AssertionError(message)
        calls.append((tuple(command), cwd))
        return 0, "", ""

    monkeypatch.setattr(publish, "_invoke", allow_dirty_invoke)

    message = publish.run(
        root,
        configuration,
        workspace,
        options=publish.PublishOptions(allow_dirty=True),
    )

    assert message.startswith(f"Publish plan for {root}")
    _verify_cargo_commands_executed(calls, root)


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
        command: typ.Sequence[str], *, cwd: Path | None = None
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
