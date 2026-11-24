"""Pipeline coverage for fixture-only non-member manifest changes."""

import collections.abc as cabc
import pathlib
import typing as typ
from unittest import mock

import pytest

from lading import config as config_module
from lading.commands import bump, bump_manifest_rewrite_application
from lading.config import ManifestRewriteConfig
from tests.helpers.recording_lockfile_repository import RecordingLockfileRepository
from tests.helpers.workspace_builders import _make_workspace

if typ.TYPE_CHECKING:
    from lading.commands.bump_manifest_rewrite_model import ManifestRewritePlan

pytestmark = pytest.mark.usefixtures("reset_process_metrics")


def test_fixture_only_change_triggers_lockfile_regeneration(
    tmp_path: pathlib.Path,
) -> None:
    """A rewritten fixture alone opens the lockfile regeneration gate."""
    workspace = _make_workspace(tmp_path)
    root_manifest = tmp_path / "Cargo.toml"
    member_manifests = tuple(
        tmp_path / f"crates/{name}/Cargo.toml" for name in ("alpha", "beta")
    )
    unchanged_manifest_bytes: dict[pathlib.Path, bytes] = {}
    for manifest_path in (root_manifest, *member_manifests):
        original = manifest_path.read_text(encoding="utf-8")
        updated = original.replace('version = "0.1.0"', 'version = "1.2.3"')
        assert updated != original, f"expected a version field in {manifest_path}"
        manifest_path.write_text(updated, encoding="utf-8")
        unchanged_manifest_bytes[manifest_path] = manifest_path.read_bytes()

    fixture = tmp_path / "tests/fixtures/standalone/Cargo.toml"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('[dependencies]\nalpha = "0.1.0"\n', encoding="utf-8")
    observed_fixture_text: list[str] = []
    repository = RecordingLockfileRepository(
        before_regenerate=lambda _root, _manifests: observed_fixture_text.append(
            fixture.read_text(encoding="utf-8")
        )
    )
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(
            lockfile_manifests=("tests/fixtures/standalone/Cargo.toml",),
            manifest_rewrites=(
                ManifestRewriteConfig(paths=("tests/fixtures/standalone/Cargo.toml",)),
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

    assert observed_fixture_text == ['[dependencies]\nalpha = "1.2.3"\n'], (
        "the lockfile stage must observe the fixture-only rewrite"
    )
    assert repository.regenerated == [
        (tmp_path.resolve(), ("tests/fixtures/standalone/Cargo.toml",))
    ], "a fixture-only manifest change must trigger configured lockfile regeneration"
    assert {
        path: path.read_bytes() for path in unchanged_manifest_bytes
    } == unchanged_manifest_bytes, (
        "the root and member manifests already at target stay unchanged"
    )
    assert message.count("- tests/fixtures/standalone/Cargo.toml") == 1, (
        "the fixture-only change should appear once in the result"
    )
    for member_manifest in member_manifests:
        assert member_manifest.relative_to(tmp_path).as_posix() not in message, (
            f"unchanged member manifest should not be reported: {member_manifest}"
        )


def test_report_keeps_planned_path_if_parent_becomes_symlink(
    tmp_path: pathlib.Path,
) -> None:
    """Reporting does not re-resolve a manifest after its write completes."""
    workspace = _make_workspace(tmp_path)
    configured_directory = tmp_path / "tests/fixtures/configured"
    configured_directory.mkdir(parents=True)
    manifest = configured_directory / "Cargo.toml"
    manifest.write_text('[dependencies]\nalpha = "0.1.0"\n', encoding="utf-8")
    redirected_directory = tmp_path / "tests/fixtures/redirected"
    redirected_directory.mkdir()
    archived_directory = tmp_path / "tests/fixtures/configured-archived"
    configured_path = manifest.relative_to(tmp_path).as_posix()
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(
            manifest_rewrites=(ManifestRewriteConfig(paths=(configured_path,)),)
        )
    )
    apply_manifest_rewrites = bump_manifest_rewrite_application.apply_manifest_rewrites

    def rewrite_then_redirect(
        plans: cabc.Sequence[ManifestRewritePlan],
        *,
        workspace_root: pathlib.Path,
        dry_run: bool,
    ) -> tuple[pathlib.Path, ...]:
        """Write the plan, then redirect its parent before reporting."""
        rewritten_paths = apply_manifest_rewrites(
            plans,
            workspace_root=workspace_root,
            dry_run=dry_run,
        )
        configured_directory.rename(archived_directory)
        configured_directory.symlink_to(redirected_directory, target_is_directory=True)
        return rewritten_paths

    with mock.patch.object(
        bump_manifest_rewrite_application,
        "apply_manifest_rewrites",
        side_effect=rewrite_then_redirect,
    ):
        message = bump.run(
            tmp_path,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=configuration,
                workspace=workspace,
            ),
        )

    assert f"- {configured_path}" in message, (
        "the report must identify the path selected and rewritten during planning"
    )
    assert "- tests/fixtures/redirected/Cargo.toml" not in message, (
        "a later symlink target must not change the reported manifest path"
    )
