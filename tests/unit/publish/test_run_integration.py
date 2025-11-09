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
    make_preflight_config,
    make_workspace,
)
from .preflight_test_utils import (
    _extract_cargo_test_call,
    _setup_preflight_test,
)

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
        command: typ.Sequence[str],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
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
        publish._compose_preflight_arguments(
            target_dir,
            include_all_targets=include_all_targets,
        )
    )
    options = publish._CargoPreflightOptions(
        extra_args=tuple(base_arguments),
        test_excludes=configured_excludes,
        unit_tests_only=configuration.preflight.unit_tests_only,
    )
    rebuilt_arguments = publish._build_test_arguments(
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
    assert args[-4:] == ("--exclude", "alpha", "--exclude", "gamma")


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
        command: typ.Sequence[str],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[0] == "git":
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
        command: typ.Sequence[str],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
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
        command: typ.Sequence[str],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
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
