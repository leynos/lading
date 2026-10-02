"""Unit tests targeting publish pre-flight helper utilities."""

import collections.abc as cabc
import typing as typ
from pathlib import Path

import pytest

from lading.commands import publish_preflight

from .conftest import _real_preflight, make_config, make_preflight_config

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

    from lading.runtime import CommandRunner


def test_preflight_checks_remove_all_targets_for_unit_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unit-test-only mode omits --all-targets from cargo test pre-flight."""
    monkeypatch.setattr(publish_preflight, "_run_preflight_checks", _real_preflight)
    monkeypatch.setattr(
        publish_preflight, "_verify_clean_working_tree", lambda *_args, **_kwargs: None
    )
    recorded: dict[str, publish_preflight._CargoPreflightOptions] = {}

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

    publish_preflight._run_preflight_checks(
        root,
        publish_preflight.PreflightRequest(
            allow_dirty=False,
            configuration=configuration,
        ),
    )

    assert set(recorded) == {"check", "test"}, (
        "pre-flight must run exactly the check and test cargo subcommands"
    )
    check_args = recorded["check"].extra_args
    assert "--all-targets" in check_args, (
        "cargo check must inspect all targets regardless of unit-tests-only mode"
    )

    test_options = recorded["test"]
    test_args = test_options.extra_args
    assert "--all-targets" not in test_args, (
        "unit-tests-only mode must drop --all-targets from cargo test"
    )
    assert "--workspace" in test_args, "cargo test must still cover the whole workspace"
    assert any(arg.startswith("--target-dir=") for arg in test_args), (
        "cargo test must receive an explicit target directory"
    )
    assert test_options.unit_tests_only is True, (
        "the recorded test options must reflect unit-tests-only mode"
    )


def test_preflight_checks_support_special_target_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Target directories with spaces/symbols propagate without quoting issues."""
    monkeypatch.setattr(publish_preflight, "_run_preflight_checks", _real_preflight)
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

    publish_preflight._run_preflight_checks(
        root,
        publish_preflight.PreflightRequest(
            allow_dirty=False,
            configuration=configuration,
        ),
    )

    assert set(recorded) == {"check", "test"}, (
        "pre-flight must run exactly the check and test cargo subcommands"
    )
    for args in recorded.values():
        assert any(
            arg.startswith("--target-dir=") and str(special_dir) in arg for arg in args
        ), "every cargo invocation must carry the unquoted special target directory"


def test_preflight_runs_aux_build_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Auxiliary build commands execute before cargo pre-flight calls."""
    monkeypatch.setattr(publish_preflight, "_run_preflight_checks", _real_preflight)
    root = tmp_path / "workspace"
    root.mkdir()
    commands: list[tuple[tuple[str, ...], Path | None]] = []

    def recording_runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        commands.append((tuple(command), cwd))
        return 0, "", ""

    monkeypatch.setattr(
        publish_preflight, "_verify_clean_working_tree", lambda *_args, **_kwargs: None
    )
    configuration = make_config(
        preflight=make_preflight_config(aux_build=(("cargo", "test", "-p", "lint"),))
    )

    publish_preflight._run_preflight_checks(
        root,
        publish_preflight.PreflightRequest(
            allow_dirty=True,
            configuration=configuration,
            runner=recording_runner,
        ),
    )

    assert commands, "expected at least one command invocation"
    first_command, first_cwd = commands[0]
    assert first_command == ("cargo", "test", "-p", "lint"), (
        "aux build commands must execute before the cargo pre-flight calls"
    )
    assert first_cwd == root, "aux build commands must run from the workspace root"


def test_aux_build_failure_surfaces_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Failures in aux build commands abort pre-flight with context."""
    monkeypatch.setattr(publish_preflight, "_run_preflight_checks", _real_preflight)
    root = tmp_path / "workspace"
    root.mkdir()

    failing_command = ("cargo", "build", "--package", "lint")

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
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

    with pytest.raises(publish_preflight.PublishPreflightError) as excinfo:
        publish_preflight._run_preflight_checks(
            root,
            publish_preflight.PreflightRequest(
                allow_dirty=True,
                configuration=configuration,
                runner=runner,
            ),
        )

    assert "cargo build --package lint" in str(excinfo.value), (
        "an aux build failure must name the failing command in the error"
    )


def test_preflight_env_overrides_forwarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Environment overrides propagate to cargo pre-flight invocations."""
    monkeypatch.setattr(publish_preflight, "_run_preflight_checks", _real_preflight)
    root = tmp_path / "workspace"
    root.mkdir()
    captured_env: dict[str, str] = {}

    def env_recording_runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
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

    publish_preflight._run_preflight_checks(
        root,
        publish_preflight.PreflightRequest(
            allow_dirty=True,
            configuration=configuration,
            runner=env_recording_runner,
        ),
    )

    assert captured_env["DYLINT_LOCALE"] == "cy", (
        "configured environment overrides must reach the cargo test invocation"
    )


def test_preflight_append_compiletest_externs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Compiletest externs extend RUSTFLAGS for cargo test."""
    monkeypatch.setattr(publish_preflight, "_run_preflight_checks", _real_preflight)
    root = tmp_path / "workspace"
    root.mkdir()
    artefact = root / "target" / "lint" / "liblint_macro.so"
    artefact.parent.mkdir(parents=True, exist_ok=True)
    artefact.touch()
    rustflags: list[str] = []

    def recording_runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        # Decompose complex conditional into readable business rules
        is_cargo_test = command[:2] == ("cargo", "test")
        has_rustflags = env is not None and "RUSTFLAGS" in env

        should_record_rustflags = is_cargo_test and has_rustflags

        if should_record_rustflags:
            assert env is not None, "has_rustflags implies a supplied environment"
            rustflags.append(env["RUSTFLAGS"])
        return 0, "", ""

    monkeypatch.setattr(
        publish_preflight, "_verify_clean_working_tree", lambda *_args, **_kwargs: None
    )
    configuration = make_config(
        preflight=make_preflight_config(
            compiletest_externs=(("lint_macro", artefact.relative_to(root).as_posix()),)
        )
    )

    publish_preflight._run_preflight_checks(
        root,
        publish_preflight.PreflightRequest(
            allow_dirty=True,
            configuration=configuration,
            runner=recording_runner,
        ),
    )

    assert rustflags, "Expected cargo test env to include RUSTFLAGS"
    last_flags = rustflags[-1]
    assert "--extern lint_macro" in last_flags, (
        "compiletest externs must be appended to RUSTFLAGS"
    )
    assert str(artefact) in last_flags, (
        "the resolved extern artefact path must appear in RUSTFLAGS"
    )


def test_verify_clean_working_tree_detects_dirty_state(
    snapshot: SnapshotAssertion, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dirty workspaces cause preflight to abort unless allow-dirty is set."""
    root = tmp_path.resolve()

    def dirty_runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        assert cwd == root, "the dirty-tree probe must inspect the workspace root"
        return 0, " M file\n", ""

    with pytest.raises(publish_preflight.PublishPreflightError) as excinfo:
        publish_preflight._verify_clean_working_tree(
            root, allow_dirty=False, runner=dirty_runner
        )

    assert "uncommitted changes" in str(excinfo.value), (
        "a dirty working tree must be reported as uncommitted changes"
    )
    # Lock the operator-facing dirty-tree message (issue #96 failure-message
    # snapshot coverage) so its wording cannot drift silently.
    assert str(excinfo.value) == snapshot(), (
        "the operator-facing dirty-tree message must not drift"
    )

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
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        assert command == ("git", "status", "--porcelain"), (
            "the repository probe must run git status --porcelain"
        )
        assert cwd == tmp_path, "the repository probe must run from the requested root"
        return 128, "", "fatal: Not a git repository"

    with pytest.raises(publish_preflight.PublishPreflightError) as excinfo:
        publish_preflight._verify_clean_working_tree(
            tmp_path, allow_dirty=False, runner=missing_runner
        )

    message = str(excinfo.value)
    assert "git repository" in message, (
        "a missing repository must be named in the error message"
    )
    assert "fatal" in message, (
        "git's own diagnostic must be preserved in the error message"
    )
    # Lock the operator-facing missing-repository message (issue #96
    # failure-message snapshot coverage) so its wording cannot drift silently.
    assert message == snapshot(), (
        "the operator-facing missing-repository message must not drift"
    )
