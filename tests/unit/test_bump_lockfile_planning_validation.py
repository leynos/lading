"""Unit tests for Cargo configuration and metadata rejection during planning.

These cases cover the rejection paths that keep planning aligned with the
lockfiles Cargo would actually write.
"""

import typing as typ
from pathlib import Path

import pytest

from lading.commands.bump_lockfile_planning import LockfilePlan, plan_lockfiles
from lading.commands.bump_lockfile_regeneration import regenerate_planned
from lading.utils.confinement import BumpPreflightError
from tests.unit.lockfile_planning_helpers import (
    RecordingRunner,
    write_package,
    write_workspace,
)


def test_regenerate_planned_runs_each_effective_root(tmp_path: Path) -> None:
    """Regeneration consumes the plan without rediscovering manifests."""
    root = tmp_path.resolve()
    write_workspace(root)
    nested = write_package(root, "fixtures/nested/Cargo.toml", standalone=True)
    runner = RecordingRunner(metadata_root=root)
    nested_plan = LockfilePlan(
        root=root,
        manifests=(root / "Cargo.toml", nested),
        lockfiles=(root / "Cargo.lock", nested.parent / "Cargo.lock"),
    )

    lockfiles = regenerate_planned(nested_plan, runner=runner)

    assert lockfiles == nested_plan.lockfiles, (
        "regeneration must return the validated destinations"
    )
    expected_updates = [
        ("cargo", "update", "--workspace", "--manifest-path", str(root / "Cargo.toml")),
        ("cargo", "update", "--workspace", "--manifest-path", str(nested)),
    ]
    assert runner.cargo_update_calls() == expected_updates, (
        "one update must run per effective workspace root"
    )


def test_plan_rejects_a_cargo_config_lockfile_override(tmp_path: Path) -> None:
    """A resolver.lockfile-path in Cargo configuration is rejected before Git."""
    root = tmp_path.resolve()
    write_workspace(root)
    config = root / ".cargo" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        '[resolver]\nlockfile-path = "/tmp/outside-409/Cargo.lock"\n',
        encoding="utf-8",
    )
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={})

    assert str(config) in str(excinfo.value), (
        "the diagnostic must name the configuration file carrying the override"
    )
    assert not runner.calls, (
        "a Cargo configuration override must be rejected before Git or Cargo"
    )


def test_plan_rejects_a_cargo_home_config_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CARGO_HOME configuration override is found at the home root."""
    root = tmp_path.resolve() / "workspace"
    root.mkdir()
    write_workspace(root)
    cargo_home = tmp_path.resolve() / "cargo-home"
    cargo_home.mkdir()
    config = cargo_home / "config.toml"
    config.write_text(
        'resolver = { lockfile-path = "/tmp/outside-409/Cargo.lock" }\n',
        encoding="utf-8",
    )
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(
            root,
            (),
            runner=runner,
            environ={"CARGO_HOME": str(cargo_home)},
        )

    assert str(config) in str(excinfo.value), (
        "the diagnostic must name the Cargo-home configuration file"
    )
    assert not runner.calls, (
        "a Cargo-home override must be rejected before Git or Cargo"
    )


def test_relative_cargo_home_is_resolved_against_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative CARGO_HOME follows Cargo's workspace-root resolution."""
    root = tmp_path.resolve() / "workspace"
    root.mkdir()
    write_workspace(root)
    cargo_home = root / "cargo-home"
    cargo_home.mkdir()
    config = cargo_home / "config.toml"
    config.write_text(
        'resolver = { lockfile-path = "/tmp/outside-409/Cargo.lock" }\n',
        encoding="utf-8",
    )
    elsewhere = tmp_path.resolve() / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={"CARGO_HOME": "cargo-home"})

    assert str(config) in str(excinfo.value), (
        "the relative Cargo home must resolve against the workspace root"
    )


def test_plan_rejects_unreadable_cargo_configuration(tmp_path: Path) -> None:
    """A malformed Cargo configuration file is a preflight error."""
    root = tmp_path.resolve()
    write_workspace(root)
    config = root / ".cargo" / "config.toml"
    config.parent.mkdir()
    config.write_text("invalid = [", encoding="utf-8")
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={})

    assert str(config) in str(excinfo.value), (
        "the diagnostic must name the unreadable configuration file"
    )


def test_plan_deduplicates_an_explicit_root_manifest(tmp_path: Path) -> None:
    """A configured root manifest is de-duplicated against the implicit one."""
    root = tmp_path.resolve()
    write_workspace(root)
    runner = RecordingRunner(metadata_root=root)

    plan = plan_lockfiles(root, ("Cargo.toml",), runner=runner, environ={})

    assert plan.lockfiles == (root / "Cargo.lock",), (
        "the explicit root manifest must not add a second destination"
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param(
            {"workspace_root": None},
            "did not report a workspace_root",
            id="missing-root",
        ),
        pytest.param(
            {"workspace_root": "relative/path"},
            "relative workspace_root",
            id="relative-root",
        ),
    ],
)
def test_plan_reports_malformed_metadata_roots(
    tmp_path: Path,
    payload: dict[str, typ.Any],
    expected: str,
) -> None:
    """A malformed or relative reported root is a preflight error."""
    root = tmp_path.resolve()
    write_workspace(root)
    write_package(root, "fixtures/nested/Cargo.toml", standalone=True)
    runner = RecordingRunner(metadata_root=root)
    runner.metadata_payload = payload

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, ("fixtures/nested/Cargo.toml",), runner=runner, environ={})

    assert expected in str(excinfo.value), (
        "the diagnostic must explain the malformed workspace root"
    )


def test_plan_reports_a_cargo_query_failure(tmp_path: Path) -> None:
    """A failing effective-root query is a preflight error naming the manifest."""
    root = tmp_path.resolve()
    write_workspace(root)
    nested = write_package(root, "fixtures/nested/Cargo.toml", standalone=True)
    runner = RecordingRunner(metadata_root=root)
    runner.metadata_failure = (1, "", "metadata failed")
    runner.metadata_failure_manifest = nested

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, ("fixtures/nested/Cargo.toml",), runner=runner, environ={})

    message = str(excinfo.value)
    assert "fixtures/nested/Cargo.toml" in message, (
        "the diagnostic must name the manifest"
    )
    assert "metadata failed" in message, "the diagnostic must carry Cargo's detail"


def test_plan_prefers_the_extensionless_cargo_config(tmp_path: Path) -> None:
    """A config.toml that Cargo ignores cannot reject the plan."""
    root = tmp_path.resolve()
    write_workspace(root)
    cargo_dir = root / ".cargo"
    cargo_dir.mkdir()
    (cargo_dir / "config").write_text("[resolver]\n", encoding="utf-8")
    (cargo_dir / "config.toml").write_text(
        '[resolver]\nlockfile-path = "/tmp/outside-409/Cargo.lock"\n',
        encoding="utf-8",
    )
    runner = RecordingRunner(metadata_root=root)

    plan = plan_lockfiles(root, (), runner=runner, environ={})

    assert plan.lockfiles == (root / "Cargo.lock",), (
        "an override in the ignored config.toml must not reject the plan"
    )


def test_plan_reads_the_extensionless_cargo_config_when_alone(tmp_path: Path) -> None:
    """A lone extensionless configuration file is inspected."""
    root = tmp_path.resolve()
    write_workspace(root)
    config = root / ".cargo" / "config"
    config.parent.mkdir()
    config.write_text(
        '[resolver]\nlockfile-path = "/tmp/outside-409/Cargo.lock"\n',
        encoding="utf-8",
    )
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={})

    assert str(config) in str(excinfo.value), (
        "the diagnostic must name the extensionless configuration file"
    )


def test_planning_runs_cargo_from_the_workspace_root(tmp_path: Path) -> None:
    """Planning queries Cargo from the root regeneration also uses."""
    root = tmp_path.resolve()
    write_workspace(root, members=("crates/alpha",))
    member = write_package(root, "crates/alpha/Cargo.toml", standalone=False)
    member_config = root / "crates" / "alpha" / ".cargo" / "config.toml"
    member_config.parent.mkdir()
    member_config.write_text("invalid = [", encoding="utf-8")
    runner = RecordingRunner(metadata_root=root, roots_by_manifest={member: root})

    plan = plan_lockfiles(root, ("crates/alpha/Cargo.toml",), runner=runner, environ={})

    assert plan.lockfiles == (root / "Cargo.lock",), (
        "a member manifest must contribute the outer workspace lockfile"
    )
    metadata_cwds = {
        cwd for command, cwd in runner.calls if command[:2] == ("cargo", "metadata")
    }
    assert metadata_cwds == {root}, (
        "every planning query must run from the workspace root"
    )
