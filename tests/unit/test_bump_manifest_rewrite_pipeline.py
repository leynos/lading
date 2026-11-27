"""Pipeline integration for configured non-member manifest rewrites."""

import dataclasses as dc
import typing as typ

import pytest

from lading import config as config_module
from lading.commands import bump, bump_toml
from lading.commands.bump_manifest_rewrites import ManifestRewriteError
from lading.config import ManifestRewriteConfig, StringValueRewriteConfig
from tests.helpers.manifest_rewrite_metrics import (
    manifest_rewrite_counter_value,
    manifest_rewrite_duration_stats,
)
from tests.helpers.recording_lockfile_repository import RecordingLockfileRepository
from tests.helpers.workspace_builders import _make_workspace

if typ.TYPE_CHECKING:
    import pathlib

    from lading.workspace.models import WorkspaceGraph

pytestmark = pytest.mark.usefixtures("reset_process_metrics")


@dc.dataclass(frozen=True, slots=True)
class _StandaloneFixtureRewriteCase:
    """Workspace inputs and observations for a fixture rewrite stage."""

    root: pathlib.Path
    workspace: WorkspaceGraph
    fixture: pathlib.Path
    configuration: config_module.LadingConfig
    repository: RecordingLockfileRepository
    observed_fixture_text: list[str]


def _make_standalone_fixture_rewrite_case(
    root: pathlib.Path,
) -> _StandaloneFixtureRewriteCase:
    """Configure a fixture and observe it immediately before lockfile work."""
    workspace = _make_workspace(root)
    fixture = root / "tests/fixtures/standalone/Cargo.toml"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        '[dependencies]\nalpha = "^0.1.0"\n\n'
        '[patch.crates-io]\nalpha = { path = "../alpha-0.1.0" }\n',
        encoding="utf-8",
    )
    observed_fixture_text: list[str] = []
    repository = RecordingLockfileRepository(
        before_regenerate=lambda _root, _manifests: observed_fixture_text.append(
            fixture.read_text(encoding="utf-8")
        )
    )
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(
            lockfile_manifests=("nested/Cargo.toml",),
            manifest_rewrites=(
                ManifestRewriteConfig(
                    paths=("tests/fixtures/standalone/Cargo.toml",),
                    string_values=(
                        StringValueRewriteConfig(
                            table=("patch", "crates-io"), field="path"
                        ),
                    ),
                ),
            ),
        )
    )
    return _StandaloneFixtureRewriteCase(
        root=root,
        workspace=workspace,
        fixture=fixture,
        configuration=configuration,
        repository=repository,
        observed_fixture_text=observed_fixture_text,
    )


def _assert_fixture_rewritten_before_lockfile(
    case: _StandaloneFixtureRewriteCase,
    message: str,
) -> None:
    """Check fixture contents, lockfile order, result text, and metrics."""
    assert case.observed_fixture_text, (
        "lockfile regeneration must observe the rewritten fixture manifest"
    )
    observed_text = case.observed_fixture_text[0]
    assert 'alpha = "^1.2.3"' in observed_text, (
        "dependency rewrites must be visible before lockfile regeneration"
    )
    assert 'path = "../alpha-1.2.3"' in observed_text, (
        "selected path strings must be rewritten before lockfile regeneration"
    )
    assert case.repository.regenerated == [
        (case.root.resolve(), ("nested/Cargo.toml",))
    ], "the live pipeline must regenerate the configured lockfile target"
    assert not case.repository.resolved, (
        "a live bump must not project lockfile paths without regenerating"
    )
    assert message.count("- tests/fixtures/standalone/Cargo.toml") == 1, (
        "the changed fixture must appear exactly once in the result"
    )
    assert (
        manifest_rewrite_counter_value(
            phase="planning", outcome="planned", dry_run="false", error_category="none"
        )
        == 1
    ), "the rewrite plan should be counted once"
    assert (
        manifest_rewrite_counter_value(
            phase="application",
            outcome="applied",
            dry_run="false",
            error_category="none",
        )
        == 1
    ), "the rewrite application should be counted once"
    assert (
        manifest_rewrite_duration_stats(
            outcome="applied", dry_run="false", error_category="none"
        ).count
        == 1
    ), "the rewrite duration should be recorded once"


def test_rewrites_fixture_before_lockfile_regeneration(
    tmp_path: pathlib.Path,
) -> None:
    """Fixture changes are written before the lockfile port runs."""
    case = _make_standalone_fixture_rewrite_case(tmp_path)
    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            configuration=case.configuration,
            workspace=case.workspace,
            lockfile_repository=case.repository,
        ),
    )
    _assert_fixture_rewritten_before_lockfile(case, message)


def test_rewrite_records_planning_and_application_durations(
    tmp_path: pathlib.Path,
) -> None:
    """Each configured rewrite phase records one duration sample."""
    workspace = _make_workspace(tmp_path)
    fixture = tmp_path / "tests/fixtures/standalone/Cargo.toml"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('[dependencies]\nalpha = "0.1.0"\n', encoding="utf-8")
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(
            manifest_rewrites=(
                ManifestRewriteConfig(paths=("tests/fixtures/standalone/Cargo.toml",)),
            )
        )
    )
    bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            rebuild_lockfiles=False,
            configuration=configuration,
            workspace=workspace,
        ),
    )

    planning = manifest_rewrite_duration_stats(
        outcome="planned",
        dry_run="false",
        error_category="none",
    )
    application = manifest_rewrite_duration_stats(
        outcome="applied",
        dry_run="false",
        error_category="none",
    )
    assert planning.count == 1, "planning should record one duration"
    assert application.count == 1, "application should record one duration"
    assert planning.total_seconds >= 0.0, "planning duration must be non-negative"
    assert application.total_seconds >= 0.0, "application duration must be non-negative"


def test_dry_run_reports_fixture_and_projects_lockfiles_without_writes(
    tmp_path: pathlib.Path,
) -> None:
    """Dry runs report fixture plans and project lockfiles without Cargo writes."""
    workspace = _make_workspace(tmp_path)
    fixture = tmp_path / "tests/fixtures/standalone/Cargo.toml"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('[dependencies]\nalpha = "0.1.0"\n', encoding="utf-8")
    original_bytes = fixture.read_bytes()
    repository = RecordingLockfileRepository()
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(
            lockfile_manifests=("nested/Cargo.toml",),
            manifest_rewrites=(
                ManifestRewriteConfig(paths=("tests/fixtures/standalone/Cargo.toml",)),
            ),
        )
    )

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            dry_run=True,
            configuration=configuration,
            workspace=workspace,
            lockfile_repository=repository,
        ),
    )

    assert fixture.read_bytes() == original_bytes, (
        "dry-run must leave the configured fixture bytes unchanged"
    )
    assert "- tests/fixtures/standalone/Cargo.toml" in message, (
        "dry-run must report the configured fixture manifest"
    )
    assert repository.planned == [(tmp_path.resolve(), ("nested/Cargo.toml",))], (
        "dry-run must project the configured lockfile target"
    )
    assert not repository.regenerated, "dry-run must not regenerate lockfiles"
    assert (
        manifest_rewrite_counter_value(
            phase="application",
            outcome="skipped",
            dry_run="true",
            error_category="none",
        )
        == 1
    ), "the skipped rewrite application should be counted once"


def test_manifest_rewrite_overlap_with_member_is_reported_once(
    tmp_path: pathlib.Path,
) -> None:
    """A configured member path remains owned and reported by the member stage."""
    workspace = _make_workspace(tmp_path)
    member_manifest = tmp_path / "crates/alpha/Cargo.toml"
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(
            manifest_rewrites=(
                ManifestRewriteConfig(paths=("crates/alpha/Cargo.toml",)),
            )
        )
    )

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            rebuild_lockfiles=False,
            configuration=configuration,
            workspace=workspace,
        ),
    )

    assert message.count("- crates/alpha/Cargo.toml") == 1, (
        "a configured member must be reported once by its owning stage"
    )
    assert 'version = "1.2.3"' in member_manifest.read_text(encoding="utf-8"), (
        "the regular member stage must update the overlapping member manifest"
    )


def test_missing_rewrite_manifest_fails_before_any_workspace_write(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planning errors leave root, member, and valid fixture bytes unchanged."""
    workspace = _make_workspace(tmp_path)
    fixture = tmp_path / "tests/fixtures/standalone/Cargo.toml"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('[dependencies]\nalpha = "0.1.0"\n', encoding="utf-8")
    root_manifest = tmp_path / "Cargo.toml"
    original_bytes = {
        root_manifest: root_manifest.read_bytes(),
        fixture: fixture.read_bytes(),
        **{
            crate.manifest_path: crate.manifest_path.read_bytes()
            for crate in workspace.crates
        },
    }
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(
            manifest_rewrites=(
                ManifestRewriteConfig(
                    paths=(
                        "tests/fixtures/standalone/Cargo.toml",
                        "tests/missing/Cargo.toml",
                    )
                ),
            )
        )
    )
    write_attempts: list[pathlib.Path] = []
    original_write = bump_toml.write_atomic_text

    def record_write(path: pathlib.Path, content: str) -> None:
        """Record and forward an atomic write attempt."""
        write_attempts.append(path)
        original_write(path, content)

    monkeypatch.setattr(bump_toml, "write_atomic_text", record_write)

    with pytest.raises(ManifestRewriteError, match=r"regular Cargo\.toml file"):
        bump.run(
            tmp_path,
            "1.2.3",
            options=bump.BumpOptions(
                configuration=configuration,
                workspace=workspace,
            ),
        )

    assert not write_attempts, "planning errors must precede all manifest writes"
    assert {path: path.read_bytes() for path in original_bytes} == original_bytes, (
        "a planning error must occur before any workspace manifest is written"
    )
    assert (
        manifest_rewrite_counter_value(
            phase="planning",
            outcome="failed",
            dry_run="false",
            error_category="planning",
        )
        == 1
    ), "the failed rewrite plan should be counted once"
    assert (
        manifest_rewrite_duration_stats(
            outcome="failed", dry_run="false", error_category="planning"
        ).count
        == 1
    ), "the failed rewrite duration should be recorded once"
