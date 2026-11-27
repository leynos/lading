"""Unit tests for Cargo configuration include handling in lockfile planning.

A layered Cargo configuration file can include other files, so the lockfile
preflight must follow those includes before it decides whether Cargo could
write a lockfile the validated plan does not name.
"""

import os
from pathlib import Path

import pytest

from lading.commands.bump_lockfile_planning import plan_lockfiles
from lading.commands.bump_lockfiles import CargoLockfileRepository
from lading.utils.confinement import BumpPreflightError
from tests.unit.lockfile_planning_helpers import RecordingRunner, write_workspace

_LOCKFILE_PATH = '"/tmp/outside-409/Cargo.lock"'


def _write_config(root: Path, text: str) -> Path:
    """Write the workspace Cargo configuration and return its path."""
    config = root / ".cargo" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(text, encoding="utf-8")
    return config


def _write_redirect(path: Path) -> Path:
    """Write an included file that sets a lockfile path."""
    path.write_text(
        f"[resolver]\nlockfile-path = {_LOCKFILE_PATH}\n",
        encoding="utf-8",
    )
    return path


def test_plan_rejects_a_lockfile_path_from_an_included_file(tmp_path: Path) -> None:
    """An included file that sets the path is rejected and named."""
    root = tmp_path.resolve()
    write_workspace(root)
    _write_config(root, 'include = ["redirect.toml"]\n')
    redirect = _write_redirect(root / ".cargo" / "redirect.toml")
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={})

    assert str(redirect) in str(excinfo.value), (
        "the diagnostic must name the included file carrying the override"
    )
    assert not runner.calls, "an included override must be rejected before Cargo"


def test_plan_follows_nested_includes(tmp_path: Path) -> None:
    """An include reached through another include is inspected."""
    root = tmp_path.resolve()
    write_workspace(root)
    _write_config(root, 'include = ["first.toml"]\n')
    (root / ".cargo" / "first.toml").write_text(
        'include = ["second.toml"]\n', encoding="utf-8"
    )
    second = _write_redirect(root / ".cargo" / "second.toml")
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={})

    assert str(second) in str(excinfo.value), (
        "the diagnostic must name the nested file carrying the override"
    )


def test_a_deeper_include_overrides_the_cargo_home_setting(tmp_path: Path) -> None:
    """The effective value comes from the deepest layer that declares one."""
    root = tmp_path.resolve()
    write_workspace(root)
    home = tmp_path.resolve() / "cargo-home"
    home.mkdir()
    (home / "config.toml").write_text(
        f"[resolver]\nlockfile-path = {_LOCKFILE_PATH}\n", encoding="utf-8"
    )
    _write_config(root, 'include = ["redirect.toml"]\n')
    redirect = _write_redirect(root / ".cargo" / "redirect.toml")
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={"CARGO_HOME": str(home)})

    assert str(redirect) in str(excinfo.value), (
        "the deeper include must supply the effective setting"
    )


def test_an_optional_missing_include_is_ignored(tmp_path: Path) -> None:
    """An optional include that does not exist is skipped."""
    root = tmp_path.resolve()
    write_workspace(root)
    _write_config(root, 'include = [{ path = "absent.toml", optional = true }]\n')
    runner = RecordingRunner(metadata_root=root)

    plan = plan_lockfiles(root, (), runner=runner, environ={})

    assert plan.lockfiles == (root / "Cargo.lock",), (
        "an absent optional include must not reject the plan"
    )


def test_a_missing_required_include_is_reported(tmp_path: Path) -> None:
    """A required include that does not exist is a preflight error."""
    root = tmp_path.resolve()
    write_workspace(root)
    _write_config(root, 'include = ["absent.toml"]\n')
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={})

    assert "cannot read Cargo configuration" in str(excinfo.value), (
        "the diagnostic must report the unreadable include"
    )


def test_an_include_cycle_is_reported(tmp_path: Path) -> None:
    """Mutually including files are rejected rather than recursed forever."""
    root = tmp_path.resolve()
    write_workspace(root)
    _write_config(root, 'include = ["first.toml"]\n')
    (root / ".cargo" / "first.toml").write_text(
        'include = ["config.toml"]\n', encoding="utf-8"
    )
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={})

    assert "include cycle" in str(excinfo.value), (
        "the diagnostic must report the include cycle"
    )
    assert not runner.calls, "a cycle must be rejected before Cargo"


@pytest.mark.parametrize(
    ("directive", "expected"),
    [
        pytest.param(
            'include = ["redirect.conf"]\n',
            "must end with .toml",
            id="non-toml-path",
        ),
        pytest.param(
            "include = [{ optional = true }]\n",
            "must name a string path",
            id="table-without-path",
        ),
        pytest.param(
            'include = [{ path = "x.toml", optional = "yes" }]\n',
            "optional must be a boolean",
            id="non-boolean-optional",
        ),
        pytest.param(
            'include = "redirect.toml"\n',
            "array of paths or tables",
            id="non-sequence-include",
        ),
    ],
)
def test_malformed_include_directives_are_rejected(
    tmp_path: Path,
    directive: str,
    expected: str,
) -> None:
    """A malformed include directive is a preflight error."""
    root = tmp_path.resolve()
    write_workspace(root)
    _write_config(root, directive)
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={})

    assert expected in str(excinfo.value), (
        "the diagnostic must explain the malformed include directive"
    )


def test_a_literal_tilde_in_cargo_home_is_not_expanded(tmp_path: Path) -> None:
    """CARGO_HOME is joined literally, matching Cargo's interpretation."""
    root = tmp_path.resolve()
    write_workspace(root)
    home = root / "~" / "cargo-home"
    home.mkdir(parents=True)
    config = home / "config.toml"
    config.write_text(
        f"[resolver]\nlockfile-path = {_LOCKFILE_PATH}\n", encoding="utf-8"
    )
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={"CARGO_HOME": "~/cargo-home"})

    assert str(config) in str(excinfo.value), (
        "the literal tilde path beneath the workspace must be inspected"
    )


def test_the_repository_checks_the_inherited_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The adapter validates the environment its runner also inherits."""
    root = tmp_path.resolve()
    write_workspace(root)
    monkeypatch.setenv("CARGO_RESOLVER_LOCKFILE_PATH", "/elsewhere/Cargo.lock")
    repository = CargoLockfileRepository(runner=RecordingRunner(metadata_root=root))

    with pytest.raises(BumpPreflightError) as excinfo:
        repository.plan_lockfiles(root, ())

    assert "CARGO_RESOLVER_LOCKFILE_PATH" in str(excinfo.value), (
        "the inherited environment override must be rejected"
    )


def test_a_multiline_inline_table_is_accepted(tmp_path: Path) -> None:
    """Cargo's TOML 1.1 syntax must not abort the preflight."""
    root = tmp_path.resolve()
    write_workspace(root)
    _write_config(
        root,
        '[env]\nFOO = {\n  value = "bar",\n  force = true,\n}\n',
    )
    runner = RecordingRunner(metadata_root=root)

    plan = plan_lockfiles(root, (), runner=runner, environ={})

    assert plan.lockfiles == (root / "Cargo.lock",), (
        "a configuration file Cargo can read must not be rejected"
    )


def test_an_include_path_is_normalized_before_probing(tmp_path: Path) -> None:
    """``missing/../redirect.toml`` resolves to the file Cargo loads."""
    root = tmp_path.resolve()
    write_workspace(root)
    _write_config(
        root,
        'include = [{ path = "missing/../redirect.toml", optional = true }]\n',
    )
    redirect = _write_redirect(root / ".cargo" / "redirect.toml")
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={})

    assert str(redirect) in str(excinfo.value), (
        "the normalized include target must be inspected"
    )


def test_a_non_regular_include_is_rejected_without_opening_it(
    tmp_path: Path,
) -> None:
    """A FIFO include is rejected rather than blocking on a read."""
    root = tmp_path.resolve()
    write_workspace(root)
    _write_config(root, 'include = ["pipe.toml"]\n')
    pipe = root / ".cargo" / "pipe.toml"
    if not hasattr(os, "mkfifo"):
        pytest.skip("os.mkfifo is unavailable on this platform")
    try:
        os.mkfifo(pipe)
    except OSError as exc:  # pragma: no cover - capability
        pytest.skip(f"cannot create a FIFO here: {exc}")
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={})

    assert "not a regular file" in str(excinfo.value), (
        "the diagnostic must reject the non-regular include"
    )
