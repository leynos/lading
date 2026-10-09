"""Unit tests for the ``staged_workspace`` context manager's lifetime rules.

The context manager owns a staged copy for the duration of a publish. These
tests cover removal on success and on failure, retention when cleanup is
disabled, the claim a retained tree must give up, reporting of the retained
path, the tracking the signal handler depends on, and the two failure paths
that must not swallow information: a removal that fails, and a removal that
fails while a publish failure is already unwinding.
"""

import shutil
import tempfile
import typing as typ
from pathlib import Path

import pytest

from lading.commands import publish, publish_plan, publish_staging, staging_lock

if typ.TYPE_CHECKING:
    from tests.unit.conftest import PreparationFixtures, PrepareWorkspaceFixtures


def _plan_for(
    fx: PrepareWorkspaceFixtures, pf: PreparationFixtures
) -> publish_plan.PublishPlan:
    """Return a publication plan for a one-crate workspace under ``fx``."""
    workspace_root = fx.tmp_path / "workspace"
    workspace_root.mkdir()
    crate = pf.make_crate(workspace_root, "alpha")
    workspace = pf.make_workspace(workspace_root, crate)
    return publish.plan_publication(workspace, pf.make_config())


def test_staged_workspace_removes_the_tree_when_the_block_ends(
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A completed publish leaves nothing behind.

    The staged copy is the whole workspace, so retaining it after a successful
    run is what filled the host in issue #269.
    """
    plan = _plan_for(prepare_workspace_fixtures, preparation_fixtures)

    with publish_staging.staged_workspace(plan) as preparation:
        staging_root = preparation.staging_root
        assert staging_root.is_dir(), (
            "an active staged_workspace block must have a staged tree on disk"
        )
        build_directory = staging_root.parent

    assert not build_directory.exists(), f"{build_directory} survived the block"


@pytest.mark.parametrize(
    "raised",
    [RuntimeError, KeyboardInterrupt],
    ids=["failure", "interrupt"],
)
def test_staged_workspace_removes_the_tree_when_the_block_raises(
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
    raised: type[BaseException],
) -> None:
    """A failed or interrupted publish cleans up too.

    ``KeyboardInterrupt`` is covered separately because it does not inherit
    from ``Exception``, so an ``except Exception`` guard would miss it.
    """
    plan = _plan_for(prepare_workspace_fixtures, preparation_fixtures)
    build_directory: Path | None = None

    def stage_then_fail() -> None:
        """Stage a tree, then fail inside the block."""
        nonlocal build_directory
        with publish_staging.staged_workspace(plan) as preparation:
            build_directory = preparation.staging_root.parent
            raise raised

    with pytest.raises(raised):
        stage_then_fail()

    assert build_directory is not None, (
        "the block must record its build directory before the failure"
    )
    assert not build_directory.exists(), f"{build_directory} survived {raised}"


def test_staged_workspace_keeps_the_tree_when_cleanup_is_disabled(
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
    tmp_path: Path,
) -> None:
    """``--keep-staging`` retains the copy for debugging."""
    plan = _plan_for(prepare_workspace_fixtures, preparation_fixtures)
    build_directory = tmp_path / "kept"
    options = publish.PublishOptions(build_directory=build_directory, cleanup=False)

    with publish_staging.staged_workspace(plan, options=options) as preparation:
        staging_root = preparation.staging_root

    assert staging_root.is_dir(), "the staged copy must survive --keep-staging"


def test_a_retained_tree_gives_up_its_claim(
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Retaining the tree must not retain the claim on it.

    The claim says a publish is using the tree. Once the block has ended none
    is, and holding on costs twice: a descriptor stays open for the life of a
    long-running caller, and `lading clean --remove` skips a tree nothing is
    reading. This needs an automatically created build directory, because that
    is the only shape staging claims.
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    plan = _plan_for(prepare_workspace_fixtures, preparation_fixtures)
    options = publish.PublishOptions(cleanup=False)

    with publish_staging.staged_workspace(plan, options=options) as staged:
        build_directory = staged.staging_root.parent
        with staging_lock.hold_for_removal(build_directory) as free:
            assert not free, "staging did not claim the tree it created"

    try:
        assert build_directory.is_dir(), "the retained tree was removed"
        with staging_lock.hold_for_removal(build_directory) as free:
            assert free, "a retained tree kept its claim after the block ended"
    finally:
        shutil.rmtree(build_directory, ignore_errors=True)


def test_a_retained_tree_reports_where_it_is(
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Retaining without saying where would leave the user hunting for it."""
    plan = _plan_for(prepare_workspace_fixtures, preparation_fixtures)
    build_directory = tmp_path / "kept"
    options = publish.PublishOptions(build_directory=build_directory, cleanup=False)

    with (
        caplog.at_level("INFO", logger="lading.commands.publish_staging"),
        publish_staging.staged_workspace(plan, options=options) as preparation,
    ):
        retained = preparation.staging_root

    assert str(retained) in caplog.text, caplog.text


def test_an_active_tree_is_tracked_for_the_signal_handler(
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """The handler can only remove trees it is told about.

    Tracked while staged and untracked afterwards, so a later signal cannot
    try to remove a path this process no longer owns.
    """
    plan = _plan_for(prepare_workspace_fixtures, preparation_fixtures)

    with publish_staging.staged_workspace(plan) as preparation:
        build_directory = preparation.staging_root.parent
        assert build_directory in publish_staging._ACTIVE_STAGING_ROOTS, (
            "an active staged tree must be tracked so the signal handler can remove it"
        )

    assert build_directory not in publish_staging._ACTIVE_STAGING_ROOTS, (
        "a tree must be untracked once its block has ended"
    )


def test_a_failed_removal_stays_tracked_and_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A removal that fails must not be silently forgotten.

    Discarding the target first, or suppressing the error, leaves the tree on
    disk with nothing tracking it: no exit hook and no signal handler would
    ever try again, and no log line would say so.
    """
    cleanup_target = tmp_path / "staged"
    cleanup_target.mkdir()
    publish_staging._ACTIVE_STAGING_ROOTS.add(cleanup_target)

    def refuse(*_arguments: object, **_keywords: object) -> None:
        """Fail the removal the way a busy or read-only tree would."""
        message = "device or resource busy"
        raise OSError(message)

    monkeypatch.setattr(publish_staging.shutil, "rmtree", refuse)

    try:
        with caplog.at_level("ERROR", logger="lading.commands.publish_staging"):
            removed = publish_staging._remove_staged_tree_or_report(cleanup_target)

        assert removed is False, (
            "a failed removal must be reported as not removed, not raised"
        )
        assert cleanup_target in publish_staging._ACTIVE_STAGING_ROOTS, (
            "a tree that could not be removed must stay tracked"
        )
        assert str(cleanup_target) in caplog.text, caplog.text
    finally:
        publish_staging._ACTIVE_STAGING_ROOTS.discard(cleanup_target)


def test_a_failed_removal_does_not_mask_the_publish_failure(
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller must still see why the publish failed.

    An exception raised from the context manager's ``finally`` would replace
    the one already propagating, so a publish failure would surface as a
    filesystem error about a directory the caller never asked about.
    """
    plan = _plan_for(prepare_workspace_fixtures, preparation_fixtures)
    published = RuntimeError("cargo publish refused the crate")

    def refuse(*_arguments: object, **_keywords: object) -> None:
        """Fail the removal while the block is already unwinding."""
        message = "device or resource busy"
        raise OSError(message)

    retained: Path | None = None

    def publish_then_fail() -> None:
        """Stage a tree, then fail the way a publish would."""
        nonlocal retained
        with publish_staging.staged_workspace(plan) as preparation:
            retained = preparation.staging_root.parent
            monkeypatch.setattr(publish_staging.shutil, "rmtree", refuse)
            raise published

    try:
        with pytest.raises(RuntimeError) as raised:
            publish_then_fail()

        assert raised.value is published, (
            "a failed cleanup must re-raise the publish failure, not the "
            "filesystem error"
        )
    finally:
        # The removal was made to fail, so the tree is still there and still
        # tracked. Clear both, or the leak detector attributes it to this test.
        monkeypatch.undo()
        assert retained is not None, (
            "the staged tree must be recorded before the publish failure"
        )
        publish_staging._ACTIVE_STAGING_ROOTS.discard(retained)
        shutil.rmtree(retained, ignore_errors=True)
