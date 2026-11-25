"""Platform capability checks at the bump command boundary."""

from pathlib import Path

import pytest

from lading import cli
from lading.commands import bump, bump_toml_files
from lading.exceptions import LadingError
from tests.helpers.manifest_rewrite_recovery import _make_partial_write_failure_case
from tests.helpers.recording_lockfile_repository import RecordingLockfileRepository


def test_non_dry_run_rejects_unsupported_bounded_writes_before_workspace_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported systems fail before the bump reads or changes a workspace."""
    missing_workspace = tmp_path / "not-created"
    monkeypatch.setattr(
        bump_toml_files,
        "_supports_bounded_directory_operations",
        lambda: False,
    )

    with pytest.raises(
        LadingError,
        match="lading bump cannot write manifests on this platform",
    ):
        bump.run(missing_workspace, "1.2.3")

    assert not missing_workspace.exists(), (
        "the platform check must precede workspace inspection"
    )


def test_dry_run_remains_available_without_bounded_write_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry runs can plan changes on a platform that cannot safely write them."""
    case = _make_partial_write_failure_case(tmp_path)
    original_workspace = {path: path.read_bytes() for path in case.workspace_manifests}
    original_fixtures = {path: path.read_bytes() for path in case.fixtures}
    repository = RecordingLockfileRepository()
    monkeypatch.setattr(
        bump_toml_files,
        "_supports_bounded_directory_operations",
        lambda: False,
    )

    report = bump.run(
        case.root,
        "1.2.3",
        options=bump.BumpOptions(
            dry_run=True,
            rebuild_lockfiles=True,
            configuration=case.configuration,
            workspace=case.workspace,
            lockfile_repository=repository,
        ),
    )

    assert report.startswith("Dry run;"), "dry runs should still report the plan"
    assert {
        path: path.read_bytes() for path in case.workspace_manifests
    } == original_workspace, "dry runs must preserve workspace manifest bytes"
    assert {path: path.read_bytes() for path in case.fixtures} == original_fixtures, (
        "dry runs must preserve configured fixture bytes"
    )
    assert not repository.regenerated, "dry runs must not regenerate lockfiles"


def test_cli_rejects_unsupported_writer_before_loading_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI reports the platform limit before its metadata adapter runs."""
    missing_workspace = tmp_path / "not-created"
    monkeypatch.setattr(
        bump_toml_files,
        "_supports_bounded_directory_operations",
        lambda: False,
    )

    with pytest.raises(
        LadingError,
        match="lading bump cannot write manifests on this platform",
    ):
        cli.bump("1.2.3", workspace_root=missing_workspace)

    assert not missing_workspace.exists(), (
        "the CLI platform check must precede workspace loading"
    )
