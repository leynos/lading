"""Integration-style tests for :func:`lading.commands.publish.run`.

Verifies the end-to-end behaviour of ``run()`` from workspace-root
resolution through preflight execution, plan construction, and cargo
command sequencing, using injectable runners and monkeypatched loaders
rather than real cargo or git processes.

Test coverage
-------------
- Workspace root normalisation: ``run`` resolves a relative root before
  planning.
- Configuration loading: ``run`` falls back to the active configuration
  or loads from disk when no active configuration is present.
- Plan summary formatting: the return value contains structured sections
  for publishable crates, skipped crates, and configured exclusions.
- Unpublished workspace dependency override: ``--allow-unpublished-workspace-
  deps`` downgrades index-lookup failures to warnings and logs the
  correct INFO message.
- Dry-run batching: ``live=False`` emits all ``cargo package --allow-dirty``
  calls before any ``cargo publish --allow-dirty --dry-run`` calls;
  verified via full ``(command, cwd)`` pair assertions to confirm each
  operation targets the correct staged crate directory.
- Live interleaving: ``live=True`` emits a ``cargo package`` immediately
  followed by ``cargo publish`` for each crate in plan order; verified via
  full ``(command, cwd)`` pair assertions.
- No publishable crates: the summary calls out "Crates to publish: none".
- Missing workspace: a ``FileNotFoundError`` from the workspace loader is
  converted to a ``WorkspaceModelError``.
- Configuration errors: ``ConfigurationError`` from the loader propagates
  unchanged.
- Preflight invocation location: ``cargo check`` and ``cargo test`` run
  inside the resolved workspace root, not the staging directory.
- Test-exclude normalisation: exclusions are sorted, deduplicated, and
  whitespace-trimmed before being passed to ``cargo test --exclude``.
- Unit-tests-only mode: ``--all-targets`` is omitted and ``--lib --bins``
  are injected.
- Dirty-workspace handling: git status is skipped by default;
  ``allow_dirty=False`` enforces cleanliness and raises on uncommitted
  changes.
- Preflight cargo failures: non-zero ``cargo check`` or ``cargo test``
  raises ``PublishPreflightError`` containing the subcommand name and exit
  code.

Test infrastructure
-------------------
``CallTrackingRunner``
    Records ``(command, cwd)`` pairs without executing real subprocesses,
    enabling post-call assertion of both command and working directory.
``make_workspace`` / ``make_crate`` / ``make_dependency_chain``
    Construct in-memory ``WorkspaceGraph`` instances for use as fixtures.
``make_config`` / ``make_preflight_config``
    Build ``LadingConfig`` instances with controlled field values.
``_setup_preflight_test`` / ``_extract_cargo_test_call``
    Shared helpers from ``preflight_test_utils`` that configure a
    monkeypatched preflight run and extract the recorded cargo test call.
``ORIGINAL_PREFLIGHT``
    The real ``_run_preflight_checks`` function, restored via
    ``monkeypatch.setattr`` in tests that exercise full preflight
    behaviour end-to-end.
"""

from __future__ import annotations

import collections.abc as cabc
import logging
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from lading import config as config_module
from lading.commands import publish, publish_preflight
from lading.workspace import WorkspaceGraph, WorkspaceModelError

from .conftest import (
    INDEX_MISSING_STDERR_BETA,
    ORIGINAL_PREFLIGHT,
    CallTrackingRunner,
    make_config,
    make_crate,
    make_dependency_chain,
    make_n_crate_chain,
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


def test_run_logs_when_unpublished_workspace_dependency_override_is_enabled(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``run`` downgrades in-plan index misses when the override is enabled."""
    caplog.set_level(logging.INFO, logger="lading.commands.publish")
    root = tmp_path / "workspace"
    workspace = make_workspace(root, *make_dependency_chain(root))
    configuration = make_config()

    publish.run(
        root,
        configuration,
        workspace,
        options=publish.PublishOptions(
            build_directory=tmp_path / "build",
            command_runner=_make_beta_package_index_failure_runner(),
            allow_unpublished_workspace_deps=True,
        ),
    )

    assert (
        "Allowing unpublished workspace dependencies during dry-run publish"
        in caplog.messages
    )
    assert any(
        "cargo package for crate beta could not resolve sibling dependency alpha"
        in message
        and "unpublished workspace dependency override is enabled" in message
        for message in caplog.messages
    )


def test_run_honours_explicit_unpublished_workspace_deps_opt_out(
    tmp_path: Path,
) -> None:
    """Explicit ``False`` keeps dry-run index-missing-version failures strict."""
    root = tmp_path / "workspace"
    workspace = make_workspace(root, *make_dependency_chain(root))
    configuration = make_config()

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish.run(
            root,
            configuration,
            workspace,
            options=publish.PublishOptions(
                build_directory=tmp_path / "build",
                command_runner=_make_beta_package_index_failure_runner(),
                allow_unpublished_workspace_deps=False,
            ),
        )

    assert "unpublished workspace dependency override" in str(excinfo.value)


@pytest.mark.parametrize(
    "crate_count",
    [2, 3, 5],
    ids=["two_crates", "three_crates", "five_crates"],
)
def test_run_keeps_dry_run_publication_batched(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, crate_count: int
) -> None:
    """Dry-run publish still packages all crates before publish dry-runs.

    Assert full ``(command, cwd)`` pairs to confirm each crate operation
    runs in the correct staging directory.
    """
    caplog.set_level(logging.INFO, logger="lading.commands.publish")
    root = tmp_path / "workspace"
    workspace = make_workspace(root, *make_n_crate_chain(root, crate_count))
    configuration = make_config()
    runner = CallTrackingRunner()

    publish.run(
        root,
        configuration,
        workspace,
        options=publish.PublishOptions(
            build_directory=tmp_path / "build",
            command_runner=runner,
            live=False,
        ),
    )

    staging_root = (tmp_path / "build") / root.name
    expected_packages = [
        (
            ("cargo", "package", "--allow-dirty"),
            staging_root / crate.root_path.relative_to(root),
        )
        for crate in workspace.crates
    ]
    expected_dry_runs = [
        (
            ("cargo", "publish", "--allow-dirty", "--dry-run"),
            staging_root / crate.root_path.relative_to(root),
        )
        for crate in workspace.crates
    ]
    assert runner.calls == expected_packages + expected_dry_runs
    assert f"Starting publish workflow for workspace {root}" in caplog.messages
    assert any(
        message.startswith("Preparing staged workspace for publication under ")
        for message in caplog.messages
    )
    assert any(
        message.startswith("Staged workspace created at ")
        for message in caplog.messages
    )
    assert "Workspace README staging skipped; handled by lading bump" in caplog.messages
    assert (
        f"Publish workflow completed successfully for workspace {root}"
        in caplog.messages
    )


@pytest.mark.parametrize(
    "crate_count",
    [2, 3, 5],
    ids=["two_crates", "three_crates", "five_crates"],
)
def test_run_keeps_live_publication_interleaved(
    tmp_path: Path, crate_count: int
) -> None:
    """Live publish packages and publishes each crate before advancing."""
    root = tmp_path / "workspace"
    workspace = make_workspace(root, *make_n_crate_chain(root, crate_count))
    configuration = make_config()
    runner = CallTrackingRunner()

    publish.run(
        root,
        configuration,
        workspace,
        options=publish.PublishOptions(
            build_directory=tmp_path / "build",
            command_runner=runner,
            live=True,
        ),
    )

    staging_root = (tmp_path / "build") / root.name
    expected_pairs = [
        (command, staging_root / crate.root_path.relative_to(root))
        for crate in workspace.crates
        for command in (
            ("cargo", "package", "--allow-dirty"),
            ("cargo", "publish", "--allow-dirty"),
        )
    ]
    assert runner.calls == expected_pairs


@given(st.integers(min_value=2, max_value=10))
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_dry_run_batching_invariant_property(tmp_path: Path, crate_count: int) -> None:
    """Dry-run publication packages every crate before publishing any crate."""
    root = tmp_path / f"workspace_{crate_count}"
    workspace = make_workspace(root, *make_n_crate_chain(root, crate_count))
    configuration = make_config()
    runner = CallTrackingRunner()

    publish.run(
        root,
        configuration,
        workspace,
        options=publish.PublishOptions(
            build_directory=tmp_path / f"build_{crate_count}",
            command_runner=runner,
            live=False,
        ),
    )

    expected_packages = [
        (("cargo", "package", "--allow-dirty"), call_cwd)
        for command, call_cwd in runner.calls
        if command == ("cargo", "package", "--allow-dirty")
    ]
    expected_dry_runs = [
        (("cargo", "publish", "--allow-dirty", "--dry-run"), call_cwd)
        for command, call_cwd in runner.calls
        if command == ("cargo", "publish", "--allow-dirty", "--dry-run")
    ]
    assert len(expected_packages) == crate_count
    assert len(expected_dry_runs) == crate_count
    assert runner.calls == expected_packages + expected_dry_runs


@given(st.integers(min_value=2, max_value=10))
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_live_interleaving_invariant_property(tmp_path: Path, crate_count: int) -> None:
    """Live publication packages and publishes each crate before advancing."""
    root = tmp_path / f"workspace_{crate_count}"
    workspace = make_workspace(root, *make_n_crate_chain(root, crate_count))
    configuration = make_config()
    runner = CallTrackingRunner()

    publish.run(
        root,
        configuration,
        workspace,
        options=publish.PublishOptions(
            build_directory=tmp_path / f"build_{crate_count}",
            command_runner=runner,
            live=True,
        ),
    )

    assert len(runner.calls) == crate_count * 2
    for package_call, publish_call in zip(
        runner.calls[::2], runner.calls[1::2], strict=True
    ):
        package_command, package_cwd = package_call
        publish_command, publish_cwd = publish_call
        assert package_command == ("cargo", "package", "--allow-dirty")
        assert publish_command == ("cargo", "publish", "--allow-dirty")
        assert package_cwd == publish_cwd


def _make_beta_package_index_failure_runner() -> cabc.Callable[
    [cabc.Sequence[str]], tuple[int, str, str]
]:
    """Return a runner that simulates beta failing cargo package."""

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del env
        is_package = tuple(command[:2]) == ("cargo", "package")
        is_beta = cwd is not None and cwd.name == "beta"
        if is_package and is_beta:
            return (1, "", INDEX_MISSING_STDERR_BETA)
        return (0, "", "")

    return runner


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
