"""Pipeline integration for configured non-member manifest rewrites."""

from __future__ import annotations

import pathlib

import pytest

from lading import config as config_module
from lading.commands import bump
from lading.commands.bump_manifest_rewrites import ManifestRewriteError
from lading.config import ManifestRewriteConfig, StringValueRewriteConfig
from tests.helpers.workspace_builders import _make_workspace
from tests.unit.test_bump_lockfile_repository import _RecordingLockfileRepository


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
    repository = _RecordingLockfileRepository(
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

    assert 'alpha = "^1.2.3"' in observed_fixture_text[0]
    assert 'path = "../alpha-1.2.3"' in observed_fixture_text[0]
    assert repository.regenerated == [(tmp_path.resolve(), ("nested/Cargo.toml",))]
    assert repository.resolved == []
    assert message.count("- tests/fixtures/standalone/Cargo.toml") == 1


def test_dry_run_reports_fixture_and_projects_lockfiles_without_writes(
    tmp_path: pathlib.Path,
) -> None:
    """Dry runs report fixture plans and project lockfiles without Cargo writes."""
    workspace = _make_workspace(tmp_path)
    fixture = tmp_path / "tests/fixtures/standalone/Cargo.toml"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('[dependencies]\nalpha = "0.1.0"\n', encoding="utf-8")
    original_bytes = fixture.read_bytes()
    repository = _RecordingLockfileRepository()
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

    assert fixture.read_bytes() == original_bytes
    assert "- tests/fixtures/standalone/Cargo.toml" in message
    assert repository.resolved == [(tmp_path.resolve(), ("nested/Cargo.toml",))]
    assert repository.regenerated == []


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

    assert message.count("- crates/alpha/Cargo.toml") == 1
    assert 'version = "1.2.3"' in member_manifest.read_text(encoding="utf-8")


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

    assert {path: path.read_bytes() for path in original_bytes} == original_bytes
