"""Unit tests targeting publish pre-flight helper utilities."""

from __future__ import annotations

import typing as typ

import pytest

from lading.commands import publish
from lading.workspace import metadata as metadata_module

from .conftest import ORIGINAL_PREFLIGHT, make_config, make_preflight_config

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_split_command_rejects_empty_sequence() -> None:
    """Splitting an empty command raises a descriptive error."""
    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._split_command(())

    assert "Command sequence must contain" in str(excinfo.value)


@pytest.mark.parametrize(
    "command",
    [
        ("cargo", "check"),
        ("cargo", "test", "--workspace"),
        ("git", "status", "--porcelain"),
    ],
)
def test_normalise_cmd_mox_command_forwards_non_cargo_commands(
    command: tuple[str, ...],
) -> None:
    """cmd-mox normalisation preserves non-cargo commands and arguments."""
    program, args = command[0], tuple(command[1:])

    rewritten_program, rewritten_args = publish._normalise_cmd_mox_command(
        program, args
    )

    if program == "cargo" and args:
        expected_program = f"cargo::{args[0]}"
        expected_args = list(args[1:])
    else:
        expected_program = program
        expected_args = list(args)

    assert rewritten_program == expected_program
    assert rewritten_args == expected_args


def test_metadata_coerce_text_decodes_bytes() -> None:
    """Binary output is decoded using UTF-8 with replacement semantics."""
    alpha = "\N{GREEK SMALL LETTER ALPHA}"
    encoded = alpha.encode()
    assert metadata_module._coerce_text(encoded) == alpha

    binary = b"foo\xff"
    assert metadata_module._coerce_text(binary) == "foo\ufffd"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "on"])
def test_should_use_cmd_mox_stub_honours_truthy_values(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Environment values recognised as truthy enable cmd-mox stubbing."""
    monkeypatch.setenv(publish.metadata_module.CMD_MOX_STUB_ENV_VAR, value)

    assert publish._should_use_cmd_mox_stub() is True


def test_should_use_cmd_mox_stub_returns_false_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing environment values disable cmd-mox stubbing."""
    monkeypatch.delenv(publish.metadata_module.CMD_MOX_STUB_ENV_VAR, raising=False)

    assert publish._should_use_cmd_mox_stub() is False


def test_run_cargo_preflight_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-zero command results are converted into preflight errors."""

    def failing_runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        assert cwd == tmp_path
        assert command[0] == "cargo"
        return 1, "", "boom"

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._run_cargo_preflight(
            tmp_path,
            "check",
            runner=failing_runner,
            options=publish._CargoPreflightOptions(extra_args=("--workspace",)),
        )

    message = str(excinfo.value)
    assert "cargo check" in message
    assert "boom" in message


def _run_and_record_cargo_preflight(
    workspace_root: Path,
    subcommand: typ.Literal["check", "test"],
    options: publish._CargoPreflightOptions,
) -> tuple[str, ...]:
    """Run cargo preflight with a recording runner and return the command.

    Returns:
        The recorded cargo command as a tuple of strings.

    """
    recorded: list[tuple[str, ...]] = []

    def recording_runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        recorded.append(command)
        return 0, "", ""

    publish._run_cargo_preflight(
        workspace_root,
        subcommand,
        runner=recording_runner,
        options=options,
    )

    assert len(recorded) == 1, f"Expected 1 recorded command, got {len(recorded)}"
    return recorded.pop()


def test_run_cargo_preflight_honours_test_excludes(tmp_path: Path) -> None:
    """Configured test exclusions append ``--exclude`` arguments."""
    command = _run_and_record_cargo_preflight(
        tmp_path,
        "test",
        publish._CargoPreflightOptions(
            extra_args=("--workspace", "--all-targets"),
            test_excludes=(" alpha ", "", "beta"),
        ),
    )
    assert command[:2] == ("cargo", "test")
    assert command[2:4] == ("--workspace", "--all-targets")
    assert command[4:] == ("--exclude", "alpha", "--exclude", "beta")


def test_run_cargo_preflight_excludes_blank_entries(tmp_path: Path) -> None:
    """Blank test exclude entries do not emit ``--exclude`` arguments."""
    command = _run_and_record_cargo_preflight(
        tmp_path,
        "test",
        publish._CargoPreflightOptions(
            extra_args=("--workspace", "--all-targets"),
            test_excludes=["", "   ", "\t", "\n"],
        ),
    )
    assert "--exclude" not in command


def test_run_cargo_preflight_honours_unit_tests_only(tmp_path: Path) -> None:
    """The unit test flag narrows cargo test targets to lib and bins."""
    command = _run_and_record_cargo_preflight(
        tmp_path,
        "test",
        publish._CargoPreflightOptions(
            extra_args=("--workspace", "--all-targets"), unit_tests_only=True
        ),
    )
    assert command[:2] == ("cargo", "test")
    assert command[2:4] == ("--workspace", "--all-targets")
    assert command[4:6] == ("--lib", "--bins")


def test_run_cargo_preflight_defaults_when_unit_tests_only_false(
    tmp_path: Path,
) -> None:
    """When unit-tests-only is disabled, no target narrowing arguments are added."""
    command = _run_and_record_cargo_preflight(
        tmp_path,
        "test",
        publish._CargoPreflightOptions(
            extra_args=("--workspace", "--all-targets"), unit_tests_only=False
        ),
    )
    assert command[:2] == ("cargo", "test")
    assert command[2:4] == ("--workspace", "--all-targets")
    assert "--lib" not in command
    assert "--bins" not in command


def test_preflight_checks_remove_all_targets_for_unit_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unit-test-only mode omits --all-targets from cargo test pre-flight."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    monkeypatch.setattr(
        publish, "_verify_clean_working_tree", lambda *_args, **_kwargs: None
    )
    recorded: dict[str, dict[str, typ.Any]] = {}

    def recording_preflight(
        workspace_root: Path,
        subcommand: str,
        *,
        runner: publish._CommandRunner,
        options: publish._CargoPreflightOptions,
    ) -> None:
        recorded[subcommand] = options

    monkeypatch.setattr(publish, "_run_cargo_preflight", recording_preflight)

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
        publish, "_verify_clean_working_tree", lambda *_args, **_kwargs: None
    )
    recorded: dict[str, tuple[str, ...]] = {}

    def recording_preflight(
        workspace_root: Path,
        subcommand: str,
        *,
        runner: publish._CommandRunner,
        options: publish._CargoPreflightOptions,
    ) -> None:
        recorded[subcommand] = tuple(options.extra_args)

    monkeypatch.setattr(publish, "_run_cargo_preflight", recording_preflight)

    special_dir = tmp_path / "target dir with spaces & symbols!@#"

    class DummyTempDir:
        def __enter__(self) -> str:
            special_dir.mkdir(parents=True, exist_ok=True)
            return str(special_dir)

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(
        publish.tempfile, "TemporaryDirectory", lambda prefix=None: DummyTempDir()
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
    root = tmp_path / "workspace"
    root.mkdir()
    commands: list[tuple[tuple[str, ...], Path | None]] = []

    def recording_runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append((tuple(command), cwd))
        return 0, "", ""

    monkeypatch.setattr(
        publish, "_verify_clean_working_tree", lambda *_args, **_kwargs: None
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
    root = tmp_path / "workspace"
    root.mkdir()

    failing_command = ("cargo", "build", "--package", "lint")

    def runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if tuple(command) == failing_command:
            return 1, "", "aux failure"
        return 0, "", ""

    monkeypatch.setattr(
        publish, "_verify_clean_working_tree", lambda *_args, **_kwargs: None
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
    root = tmp_path / "workspace"
    root.mkdir()
    captured_env: dict[str, str] = {}

    def env_recording_runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:2] == ("cargo", "test"):
            captured_env.update(env or {})
        return 0, "", ""

    monkeypatch.setattr(
        publish, "_verify_clean_working_tree", lambda *_args, **_kwargs: None
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
        env: typ.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        # Decompose complex conditional into readable business rules
        is_cargo_test = command[:2] == ("cargo", "test")
        has_environment = env is not None
        has_rustflags = has_environment and "RUSTFLAGS" in (env or {})

        should_record_rustflags = is_cargo_test and has_rustflags

        if should_record_rustflags:
            rustflags.append(env["RUSTFLAGS"])
        return 0, "", ""

    monkeypatch.setattr(
        publish, "_verify_clean_working_tree", lambda *_args, **_kwargs: None
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


def test_compiletest_diagnostic_details(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Failing cargo test pre-flight lists stderr artifacts with tail output."""
    artifact = tmp_path / "ui.stderr"
    artifact.write_text("line1\nline2\nline3\n", encoding="utf-8")

    def failing_runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        return 1, f"diff at {artifact}", ""

    options = publish._CargoPreflightOptions(
        extra_args=("--workspace",),
        env={},
        diagnostics_tail_lines=2,
    )
    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._run_cargo_preflight(
            tmp_path,
            "test",
            runner=failing_runner,
            options=options,
        )

    message = str(excinfo.value)
    assert str(artifact) in message
    assert "line2" in message
    assert "line3" in message


def test_verify_clean_working_tree_detects_dirty_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dirty workspaces cause preflight to abort unless allow-dirty is set."""
    root = tmp_path.resolve()

    def dirty_runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        assert cwd == root
        return 0, " M file\n", ""

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._verify_clean_working_tree(root, allow_dirty=False, runner=dirty_runner)

    assert "uncommitted changes" in str(excinfo.value)

    # Allow dirty should bypass the runner entirely.
    publish._verify_clean_working_tree(root, allow_dirty=True, runner=dirty_runner)


def test_verify_clean_working_tree_reports_missing_repo(
    tmp_path: Path,
) -> None:
    """A missing git repository surfaces a descriptive error."""

    def missing_runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        assert command == ("git", "status", "--porcelain")
        assert cwd == tmp_path
        return 128, "", "fatal: Not a git repository"

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._verify_clean_working_tree(
            tmp_path, allow_dirty=False, runner=missing_runner
        )

    message = str(excinfo.value)
    assert "git repository" in message
    assert "fatal" in message
