"""Integration tests for the injected LockfileRepository port in :mod:`lading.commands.bump`."""  # ruff: ignore[line-too-long] fixed one-line module docstring; wrapping it trips pydocstyle D205/D209

import pathlib

from lading.commands import bump
from tests.helpers.recording_lockfile_repository import RecordingLockfileRepository
from tests.helpers.workspace_builders import _make_config, _make_workspace


def test_run_uses_injected_lockfile_repository(tmp_path: pathlib.Path) -> None:
    """Bump reaches lockfile operations only through the repository port."""
    workspace = _make_workspace(tmp_path)
    repository = RecordingLockfileRepository()

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            configuration=_make_config(),
            workspace=workspace,
            lockfile_repository=repository,
        ),
    )

    assert repository.regenerated == [(tmp_path.resolve(), ())], (
        "a real bump must regenerate lockfiles through the repository port"
    )
    assert not repository.resolved, (
        "a real bump must not resolve lockfile paths without regenerating"
    )
    assert "Cargo.lock (lockfile)" in message, (
        "the bump summary must report the regenerated lockfile"
    )


def test_dry_run_projects_through_lockfile_repository(
    tmp_path: pathlib.Path,
) -> None:
    """Dry runs project lockfile paths without regenerating."""
    workspace = _make_workspace(tmp_path)
    repository = RecordingLockfileRepository()

    bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            dry_run=True,
            configuration=_make_config(),
            workspace=workspace,
            lockfile_repository=repository,
        ),
    )

    assert repository.resolved == [(tmp_path.resolve(), ())], (
        "a dry run must project lockfile paths through the repository port"
    )
    assert not repository.regenerated, "a dry run must not regenerate any lockfiles"
