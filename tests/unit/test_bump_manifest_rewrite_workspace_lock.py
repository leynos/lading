"""The fixture rewrite stage holds the shared workspace write lock."""

import collections.abc as cabc
import contextlib
import threading
import typing as typ
from pathlib import Path

import pytest

from lading.commands import (
    bump,
    bump_manifest_rewrite_application,
    bump_pipeline,
    bump_pipeline_manifest_sources,
    bump_workspace_write_lock,
)
from lading.commands.bump_manifest_rewrites import ManifestRewriteError
from tests.helpers.manifest_rewrite_recovery import _make_partial_write_failure_case
from tests.helpers.recording_lockfile_repository import RecordingLockfileRepository

WORKSPACE_LOCK_BUSY = "workspace write lock is busy"

if typ.TYPE_CHECKING:
    from lading.commands.bump_manifest_rewrite_application import (
        _ManifestRewriteWriteCallbacks,
    )
    from lading.commands.bump_manifests import _BumpContext
    from lading.commands.bump_pipeline_manifest_rewrites import (
        _ManifestRewriteStage,
    )


@pytest.fixture(autouse=True)
def isolate_workspace_write_lock_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep lock files in this test's temporary directory."""
    monkeypatch.setattr(
        bump_workspace_write_lock,
        "_workspace_lock_directory",
        lambda: tmp_path / "workspace-write-locks",
    )


def test_workspace_lock_covers_version_recheck_and_fixture_rewrites(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cooperating writer is refused through validation and application."""
    case = _make_partial_write_failure_case(tmp_path)
    lock_attempts: list[tuple[str, bool, BaseException | None]] = []
    original_apply = bump_manifest_rewrite_application.apply_manifest_rewrites
    original_validate = bump_pipeline_manifest_sources._validate_post_bump_versions
    validation_count = 0

    def observe_lock_contention(stage: str) -> None:
        """Try the same advisory lock from another thread and record its result."""
        acquired: list[bool] = []
        failures: list[BaseException] = []

        def acquire_from_another_thread() -> None:
            """Attempt the lock as a separate Lading writer would."""
            try:
                with bump_workspace_write_lock.workspace_write_lock(
                    case.root.resolve()
                ):
                    acquired.append(True)
            except OSError as error:
                failures.append(error)

        writer_thread = threading.Thread(target=acquire_from_another_thread)
        writer_thread.start()
        writer_thread.join(timeout=2)
        assert not writer_thread.is_alive(), (
            "lock contention checks must finish promptly"
        )
        lock_attempts.append((stage, bool(acquired), failures[0] if failures else None))

    def validate_under_lock(
        context: _BumpContext,
        stage: _ManifestRewriteStage,
        target_version: str,
    ) -> None:
        """Check the second post-bump validation still owns the lock."""
        nonlocal validation_count
        validation_count += 1
        original_validate(context, stage, target_version)
        if validation_count == 2:
            observe_lock_contention("version recheck")

    def apply_with_lock_observer(
        plans: cabc.Sequence[bump_manifest_rewrite_application.ManifestRewritePlan],
        *,
        workspace_root: Path,
        dry_run: bool,
        write_callbacks: _ManifestRewriteWriteCallbacks | None = None,
    ) -> tuple[Path, ...]:
        """Check the workspace lock remains held while fixture writes run."""
        observe_lock_contention("fixture rewrites")
        return original_apply(
            plans,
            workspace_root=workspace_root,
            dry_run=dry_run,
            write_callbacks=write_callbacks,
        )

    monkeypatch.setattr(
        bump_pipeline_manifest_sources,
        "_validate_post_bump_versions",
        validate_under_lock,
    )
    monkeypatch.setattr(
        bump_manifest_rewrite_application,
        "apply_manifest_rewrites",
        apply_with_lock_observer,
    )
    bump.run(
        case.root,
        "1.2.3",
        options=bump.BumpOptions(
            rebuild_lockfiles=False,
            configuration=case.configuration,
            workspace=case.workspace,
        ),
    )

    assert [label for label, _acquired, _error in lock_attempts] == [
        "version recheck",
        "fixture rewrites",
    ], "the stage should recheck versions and apply fixtures under one lock"
    assert all(not acquired for _label, acquired, _error in lock_attempts), (
        "cooperating writers must be refused throughout validation and application"
    )
    assert all(isinstance(error, OSError) for _, _, error in lock_attempts), (
        "the non-blocking workspace lock should report contention as an I/O error"
    )
    with bump_workspace_write_lock.workspace_write_lock(case.root.resolve()):
        pass
    fixture_text = case.fixtures[0].read_text(encoding="utf-8")
    assert 'alpha = "1.2.3"' in fixture_text, (
        "the fixture plan should be applied while holding the workspace lock"
    )


def test_workspace_lock_failure_restores_member_manifests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock conflict before fixture writes follows workspace rollback."""
    case = _make_partial_write_failure_case(tmp_path)
    fixture_originals = {path: path.read_bytes() for path in case.fixtures}
    workspace_originals = {path: path.read_bytes() for path in case.workspace_manifests}
    repository = RecordingLockfileRepository()
    original_lock = bump_workspace_write_lock.workspace_write_lock
    original_crate_stage = bump_pipeline._process_crate_manifests
    should_fail_next_lock = False

    @contextlib.contextmanager
    def fail_after_member_stage(
        workspace_root: Path,
    ) -> cabc.Generator[bump_workspace_write_lock.AtomicWriteIdentity]:
        """Refuse the first lock request after member manifests are written."""
        nonlocal should_fail_next_lock
        if should_fail_next_lock:
            should_fail_next_lock = False
            raise BlockingIOError(WORKSPACE_LOCK_BUSY)
        with original_lock(workspace_root) as identity:
            yield identity

    def mark_member_stage_complete(
        context: _BumpContext,
        target_version: str,
        stage: _ManifestRewriteStage,
        changed_manifests: set[Path],
    ) -> None:
        """Arm one lock failure after all member writes have completed."""
        nonlocal should_fail_next_lock
        original_crate_stage(context, target_version, stage, changed_manifests)
        should_fail_next_lock = True

    monkeypatch.setattr(
        bump_workspace_write_lock,
        "workspace_write_lock",
        fail_after_member_stage,
    )
    monkeypatch.setattr(
        bump_pipeline,
        "_process_crate_manifests",
        mark_member_stage_complete,
    )

    with pytest.raises(ManifestRewriteError, match=WORKSPACE_LOCK_BUSY):
        bump.run(
            case.root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=True,
                configuration=case.configuration,
                workspace=case.workspace,
                lockfile_repository=repository,
            ),
        )

    assert {path: path.read_bytes() for path in case.workspace_manifests} == (
        workspace_originals
    ), "member and workspace manifest writes must roll back after lock contention"
    assert {path: path.read_bytes() for path in case.fixtures} == fixture_originals, (
        "a failed stage lock must not modify configured fixtures"
    )
    assert not repository.regenerated, "lock contention must stop before lockfile work"
