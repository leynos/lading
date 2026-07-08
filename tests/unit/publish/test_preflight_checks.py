"""Unit tests targeting publish pre-flight helper utilities."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from pathlib import Path

import pytest

from lading.commands import publish, publish_preflight

from .conftest import ORIGINAL_PREFLIGHT, make_config, make_preflight_config

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

    from lading.config import LadingConfig
    from lading.runtime import CommandRunner


def test_publish_preflight_aliases_are_wired_correctly() -> None:
    """Backwards-compatible preflight names keep resolving into publish_preflight.

    ``_preflight_argument_sets`` is a bare re-export, so identity must hold.
    ``_run_preflight_checks`` is intentionally a thin wrapper (issue #96) that
    preserves the optional-``configuration`` contract, so it must *not* be the
    canonical object; its delegation is pinned by
    ``test_preflight_wrapper_loads_configuration_when_omitted``.
    """
    assert (
        publish._preflight_argument_sets is publish_preflight._preflight_argument_sets
    )
    assert publish._run_preflight_checks is not publish_preflight._run_preflight_checks


def test_preflight_wrapper_loads_configuration_when_omitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Omitting ``configuration`` resolves it before delegating to canonical."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    configuration = make_config()
    monkeypatch.setattr(
        publish.config_module, "current_configuration", lambda: configuration
    )
    recorded: dict[str, typ.Any] = {}

    def recording_preflight(
        workspace_root: Path,
        *,
        allow_dirty: bool,
        configuration: LadingConfig,
        runner: CommandRunner | None = None,
    ) -> None:
        recorded["workspace_root"] = workspace_root
        recorded["allow_dirty"] = allow_dirty
        recorded["configuration"] = configuration

    monkeypatch.setattr(publish_preflight, "_run_preflight_checks", recording_preflight)

    root = tmp_path / "workspace"
    root.mkdir()

    publish._run_preflight_checks(root, allow_dirty=True)

    assert recorded["configuration"] is configuration
    assert recorded["workspace_root"] == root
    assert recorded["allow_dirty"] is True


def test_preflight_checks_remove_all_targets_for_unit_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unit-test-only mode omits --all-targets from cargo test pre-flight."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    monkeypatch.setattr(
        publish_preflight, "_verify_clean_working_tree", lambda *_args, **_kwargs: None
    )
    recorded: dict[str, dict[str, typ.Any]] = {}

    def recording_preflight(
        workspace_root: Path,
        subcommand: str,
        *,
        runner: CommandRunner,
        options: publish_preflight._CargoPreflightOptions,
    ) -> None:
        recorded[subcommand] = options

    monkeypatch.setattr(publish_preflight, "_run_cargo_preflight", recording_preflight)

    root = tmp_path / "workspace"
    root.mkdir()
    configuration = make_config(preflight=make_preflight_config(unit_tests_only=True))

    publish._run_preflight_checks(root, allow_dirty=False, configuration=configuration)

    assert set(recorded) == {"check", "test"}
    check_args = recorded["check"].extra_args
    assert "--all-targets" in check_args

    test_options = recorded["test"]
    test_args = test_options.extra_args
    assert "--all-targets" not in test_args
    assert "--workspace" in test_args
    assert any(arg.startswith("--target-dir=") for arg in test_args)
    assert test_options.unit_tests_only is True


def test_preflight_checks_support_special_target_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Target directories with spaces/symbols propagate without quoting issues."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    monkeypatch.setattr(
        publish_preflight, "_verify_clean_working_tree", lambda *_args, **_kwargs: None
    )
    recorded: dict[str, tuple[str, ...]] = {}

    def recording_preflight(
        workspace_root: Path,
        subcommand: str,
        *,
        runner: CommandRunner,
        options: publish_preflight._CargoPreflightOptions,
    ) -> None:
        recorded[subcommand] = tuple(options.extra_args)

    monkeypatch.setattr(publish_preflight, "_run_cargo_preflight", recording_preflight)

    special_dir = tmp_path / "target dir with spaces & symbols!@#"

    class DummyTempDir:
        def __enter__(self) -> str:
            special_dir.mkdir(parents=True, exist_ok=True)
            return str(special_dir)

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(
        publish_preflight.tempfile,
        "TemporaryDirectory",
        lambda prefix=None: DummyTempDir(),
    )

    root = tmp_path / "workspace"
    root.mkdir()
    configuration = make_config()

    publish._run_preflight_checks(root, allow_dirty=False, configuration=configuration)

    assert set(recorded) == {"check", "test"}
    for args in recorded.values():
        assert any(
            arg.startswith("--target-dir=") and str(special_dir) in arg for arg in args
        )


def test_preflight_runs_aux_build_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Auxiliary build commands execute before cargo pre-flight calls."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    root = tmp_path / "workspace"
    root.mkdir()
    commands: list[tuple[tuple[str, ...], Path | None]] = []

    def recording_runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append((tuple(command), cwd))
        return 0, "", ""

    monkeypatch.setattr(
        publish_preflight, "_verify_clean_working_tree", lambda *_args, **_kwargs: None
    )
    configuration = make_config(
        preflight=make_preflight_config(aux_build=(("cargo", "test", "-p", "lint"),))
    )

    publish._run_preflight_checks(
        root,
        allow_dirty=True,
        configuration=configuration,
        runner=recording_runner,
    )

    assert commands, "expected at least one command invocation"
    first_command, first_cwd = commands[0]
    assert first_command == ("cargo", "test", "-p", "lint")
    assert first_cwd == root


def test_aux_build_failure_surfaces_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Failures in aux build commands abort pre-flight with context."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    root = tmp_path / "workspace"
    root.mkdir()

    failing_command = ("cargo", "build", "--package", "lint")

    def runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if tuple(command) == failing_command:
            return 1, "", "aux failure"
        return 0, "", ""

    monkeypatch.setattr(
        publish_preflight, "_verify_clean_working_tree", lambda *_args, **_kwargs: None
    )
    configuration = make_config(
        preflight=make_preflight_config(
            aux_build=(("cargo", "build", "--package", "lint"),)
        )
    )

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._run_preflight_checks(
            root,
            allow_dirty=True,
            configuration=configuration,
            runner=runner,
        )

    assert "cargo build --package lint" in str(excinfo.value)


def test_preflight_env_overrides_forwarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Environment overrides propagate to cargo pre-flight invocations."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    root = tmp_path / "workspace"
    root.mkdir()
    captured_env: dict[str, str] = {}

    def env_recording_runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:2] == ("cargo", "test"):
            captured_env.update(env or {})
        return 0, "", ""

    monkeypatch.setattr(
        publish_preflight, "_verify_clean_working_tree", lambda *_args, **_kwargs: None
    )
    configuration = make_config(
        preflight=make_preflight_config(env_overrides=(("DYLINT_LOCALE", "cy"),))
    )

    publish._run_preflight_checks(
        root,
        allow_dirty=True,
        configuration=configuration,
        runner=env_recording_runner,
    )

    assert captured_env["DYLINT_LOCALE"] == "cy"


def test_preflight_append_compiletest_externs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Compiletest externs extend RUSTFLAGS for cargo test."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    root = tmp_path / "workspace"
    root.mkdir()
    artifact = root / "target" / "lint" / "liblint_macro.so"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.touch()
    rustflags: list[str] = []

    def recording_runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        # Decompose complex conditional into readable business rules
        is_cargo_test = command[:2] == ("cargo", "test")
        has_environment = env is not None
        has_rustflags = has_environment and "RUSTFLAGS" in env

        should_record_rustflags = is_cargo_test and has_rustflags

        if should_record_rustflags:
            rustflags.append(env["RUSTFLAGS"])
        return 0, "", ""

    monkeypatch.setattr(
        publish_preflight, "_verify_clean_working_tree", lambda *_args, **_kwargs: None
    )
    configuration = make_config(
        preflight=make_preflight_config(
            compiletest_externs=(("lint_macro", artifact.relative_to(root).as_posix()),)
        )
    )

    publish._run_preflight_checks(
        root,
        allow_dirty=True,
        configuration=configuration,
        runner=recording_runner,
    )

    assert rustflags, "Expected cargo test env to include RUSTFLAGS"
    last_flags = rustflags[-1]
    assert "--extern lint_macro" in last_flags
    assert str(artifact) in last_flags


def test_verify_clean_working_tree_detects_dirty_state(
    snapshot: SnapshotAssertion, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dirty workspaces cause preflight to abort unless allow-dirty is set."""
    root = tmp_path.resolve()

    def dirty_runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        assert cwd == root
        return 0, " M file\n", ""

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish_preflight._verify_clean_working_tree(
            root, allow_dirty=False, runner=dirty_runner
        )

    assert "uncommitted changes" in str(excinfo.value)
    # Lock the operator-facing dirty-tree message (issue #96 failure-message
    # snapshot coverage) so its wording cannot drift silently.
    assert str(excinfo.value) == snapshot()

    # Allow dirty should bypass the runner entirely.
    publish_preflight._verify_clean_working_tree(
        root, allow_dirty=True, runner=dirty_runner
    )


def test_verify_clean_working_tree_reports_missing_repo(
    snapshot: SnapshotAssertion,
    tmp_path: Path,
) -> None:
    """A missing git repository surfaces a descriptive error."""

    def missing_runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        assert command == ("git", "status", "--porcelain")
        assert cwd == tmp_path
        return 128, "", "fatal: Not a git repository"

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish_preflight._verify_clean_working_tree(
            tmp_path, allow_dirty=False, runner=missing_runner
        )

    message = str(excinfo.value)
    assert "git repository" in message
    assert "fatal" in message
    # Lock the operator-facing missing-repository message (issue #96
    # failure-message snapshot coverage) so its wording cannot drift silently.
    assert message == snapshot()
