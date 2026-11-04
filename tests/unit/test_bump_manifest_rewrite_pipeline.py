"""Pipeline integration for configured non-member manifest rewrites."""

import collections.abc as cabc
import pathlib
from types import SimpleNamespace

import pytest

from lading import config as config_module
from lading.commands import (
    bump,
    bump_manifest_rewrite_application,
    bump_manifest_rewrite_metrics,
    bump_pipeline,
)
from lading.commands.bump_manifest_rewrites import ManifestRewriteError
from lading.config import ManifestRewriteConfig, StringValueRewriteConfig
from lading.utils import metrics
from tests.helpers.recording_lockfile_repository import RecordingLockfileRepository
from tests.helpers.workspace_builders import _make_workspace


@pytest.fixture(autouse=True)
def _metrics_registry() -> cabc.Iterator[None]:
    """Reset process-local metrics around each test."""
    metrics.reset()
    yield
    metrics.reset()


def _assert_single_rewrite_counter(**labels: str) -> None:
    """Assert one rewrite counter sample has the supplied bounded labels."""
    value = metrics.counter_value(
        bump_manifest_rewrite_metrics.MANIFEST_REWRITE_RUN_METRIC,
        **labels,
    )
    assert value == 1, f"expected one rewrite counter sample for {labels}"


def _assert_single_rewrite_duration(**labels: str) -> None:
    """Assert one rewrite duration sample has the supplied bounded labels."""
    sample = metrics.duration_stats(
        bump_manifest_rewrite_metrics.MANIFEST_REWRITE_DURATION_METRIC,
        **labels,
    )
    assert sample.count == 1, f"expected one rewrite duration sample for {labels}"


def _assert_partial_write_failure(
    error: ManifestRewriteError,
    failed_path: pathlib.Path,
) -> None:
    """Assert the failing path and write progress from the exception."""
    assert error.failed_path == failed_path, (
        "the exception should identify the manifest whose write failed"
    )
    assert error.completed_writes == 1, (
        "the exception should expose the number of completed fixture writes"
    )
    assert error.planned_writes == 2, (
        "the exception should expose the number of planned fixture writes"
    )


def test_rewrites_fixture_before_lockfile_regeneration(
    tmp_path: pathlib.Path,
) -> None:
    """Fixture changes are written before the lockfile port runs."""
    workspace = _make_workspace(tmp_path)
    fixture = tmp_path / "tests/fixtures/standalone/Cargo.toml"
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

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            configuration=configuration,
            workspace=workspace,
            lockfile_repository=repository,
        ),
    )

    assert 'alpha = "^1.2.3"' in observed_fixture_text[0], (
        "dependency rewrites must be visible before lockfile regeneration"
    )
    assert 'path = "../alpha-1.2.3"' in observed_fixture_text[0], (
        "selected path strings must be rewritten before lockfile regeneration"
    )
    assert repository.regenerated == [(tmp_path.resolve(), ("nested/Cargo.toml",))], (
        "the live pipeline must regenerate the configured lockfile target"
    )
    assert not repository.resolved, (
        "a live bump must not project lockfile paths without regenerating"
    )
    assert message.count("- tests/fixtures/standalone/Cargo.toml") == 1, (
        "the changed fixture must appear exactly once in the result"
    )
    _assert_single_rewrite_counter(
        phase="planning", outcome="planned", dry_run="false", error_category="none"
    )
    _assert_single_rewrite_counter(
        phase="application", outcome="applied", dry_run="false", error_category="none"
    )
    _assert_single_rewrite_duration(
        outcome="applied", dry_run="false", error_category="none"
    )


def test_rewrite_duration_excludes_intervening_manifest_stages(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rewrite timing excludes workspace and member manifest processing."""
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
    clock_readings = iter((10.0, 12.0, 100.0, 103.0))
    monkeypatch.setattr(
        bump_pipeline,
        "time",
        SimpleNamespace(perf_counter=lambda: next(clock_readings)),
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

    sample = metrics.duration_stats(
        bump_manifest_rewrite_metrics.MANIFEST_REWRITE_DURATION_METRIC,
        outcome="applied",
        dry_run="false",
        error_category="none",
    )
    assert sample.count == 1, "the configured rewrite should record one duration"
    assert sample.total_seconds == pytest.approx(5.0), (
        "duration should sum two seconds of planning and three seconds of "
        "application, excluding the intervening member stages"
    )


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
    assert repository.resolved == [(tmp_path.resolve(), ("nested/Cargo.toml",))], (
        "dry-run must project the configured lockfile target"
    )
    assert not repository.regenerated, "dry-run must not regenerate lockfiles"
    _assert_single_rewrite_counter(
        phase="application", outcome="skipped", dry_run="true", error_category="none"
    )


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
) -> None:
    """Planning errors leave root, member, and valid fixture bytes unchanged."""
    workspace = _make_workspace(tmp_path)
    fixture = tmp_path / "tests/fixtures/standalone/Cargo.toml"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('[dependencies]\nalpha = "0.1.0"\n', encoding="utf-8")
    root_manifest = tmp_path / "Cargo.toml"
    member_manifest = tmp_path / "crates/alpha/Cargo.toml"
    original_bytes = {
        path: path.read_bytes() for path in (root_manifest, member_manifest, fixture)
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

    with pytest.raises(ManifestRewriteError, match="regular file"):
        bump.run(
            tmp_path,
            "1.2.3",
            options=bump.BumpOptions(
                configuration=configuration,
                workspace=workspace,
            ),
        )

    assert {path: path.read_bytes() for path in original_bytes} == original_bytes, (
        "a planning error must occur before any workspace manifest is written"
    )
    _assert_single_rewrite_counter(
        phase="planning", outcome="failed", dry_run="false", error_category="planning"
    )
    _assert_single_rewrite_duration(
        outcome="failed", dry_run="false", error_category="planning"
    )


def test_partial_write_failure_names_path_and_completed_count(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write failure reports its path and counts completed fixture writes."""
    workspace = _make_workspace(tmp_path)
    fixtures = tuple(
        tmp_path / f"tests/fixtures/{name}/Cargo.toml" for name in ("first", "second")
    )
    for fixture in fixtures:
        fixture.parent.mkdir(parents=True)
        fixture.write_text('[dependencies]\nalpha = "0.1.0"\n', encoding="utf-8")
    second_original = fixtures[1].read_bytes()
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(
            manifest_rewrites=(
                ManifestRewriteConfig(
                    paths=tuple(
                        path.relative_to(tmp_path).as_posix() for path in fixtures
                    )
                ),
            )
        )
    )
    write_atomic_text = bump_manifest_rewrite_application.bump_toml.write_atomic_text
    message = "simulated write failure"

    def fail_second_write(path: pathlib.Path, text: str) -> None:
        if path == fixtures[1].resolve():
            raise OSError(message)
        write_atomic_text(path, text)

    monkeypatch.setattr(
        bump_manifest_rewrite_application.bump_toml,
        "write_atomic_text",
        fail_second_write,
    )
    with pytest.raises(ManifestRewriteError, match="1 of 2") as failure:
        bump.run(
            tmp_path,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=configuration,
                workspace=workspace,
            ),
        )

    assert fixtures[0].read_text(encoding="utf-8").endswith('alpha = "1.2.3"\n'), (
        "the first manifest should reflect its completed atomic write"
    )
    assert fixtures[1].read_bytes() == second_original, (
        "the failed manifest should retain its original bytes"
    )
    _assert_partial_write_failure(failure.value, fixtures[1].resolve())
    _assert_single_rewrite_counter(
        phase="application", outcome="failed", dry_run="false", error_category="write"
    )
    _assert_single_rewrite_duration(
        outcome="failed", dry_run="false", error_category="write"
    )
