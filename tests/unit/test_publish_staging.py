"""Unit tests exercising publish staging utilities."""

import collections.abc as cabc
import dataclasses as dc
import shutil
import tempfile
import typing as typ
from pathlib import Path

import pytest

from lading.commands import publish, publish_plan, publish_staging, staging_lock
from tests.helpers.cwd import chdir_for_test
from tests.unit.conftest import (
    PreparationFixtures,
    PrepareWorkspaceFixtures,
    _CrateSpec,
)


class _CopyWorkspaceFailureCase(typ.NamedTuple):
    """Failure mode exercised while copying a workspace tree."""

    operation: str
    requires_staging_root: bool
    message: str


def test_normalize_build_directory_defaults_to_tempdir(tmp_path: Path) -> None:
    """Normalization creates a temporary directory when none is provided.

    The directory is removed here because nothing else will: this helper is
    below the level that registers cleanup, and this test alone left 3,925
    empty directories on a shared host (issue #269).
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    build_directory = publish_staging._normalize_build_directory(workspace_root, None)
    try:
        assert build_directory.exists(), (
            "a default build directory must exist on disk after normalization"
        )
        assert build_directory.is_absolute(), (
            "the build directory must be resolved to an absolute path"
        )
        assert not build_directory.is_relative_to(workspace_root), (
            "an automatic build directory must not sit under the workspace root"
        )
    finally:
        shutil.rmtree(build_directory, ignore_errors=True)


def test_normalize_build_directory_resolves_relative_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Relative build directories are resolved against the current directory."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    chdir_for_test(monkeypatch, tmp_path)

    build_directory = publish_staging._normalize_build_directory(
        workspace_root, Path("staging")
    )

    expected = (tmp_path / "staging").resolve()
    assert build_directory == expected, (
        "a relative build directory must resolve against the current directory"
    )
    assert build_directory.exists(), (
        "normalization must create the resolved build directory"
    )


def test_normalize_build_directory_rejects_workspace_descendants(
    tmp_path: Path,
) -> None:
    """Normalization rejects build directories nested under the workspace."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    build_directory = workspace_root / "target"

    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_staging._normalize_build_directory(workspace_root, build_directory)

    assert "cannot reside within the workspace root" in str(excinfo.value), (
        "a build directory nested under the workspace must be rejected with the "
        "documented reason"
    )


def test_normalize_build_directory_wraps_creation_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Build-directory creation failures use the staging error boundary."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    def fail_mkdir(*_args: object, **_kwargs: object) -> None:
        message = "permission denied"
        raise OSError(message)

    monkeypatch.setattr(publish_staging.Path, "mkdir", fail_mkdir)

    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_staging._normalize_build_directory(workspace_root, tmp_path / "staging")

    assert "Cannot create publish build directory" in str(excinfo.value), (
        "a failed mkdir must surface through the staging error boundary"
    )
    assert isinstance(excinfo.value.__cause__, OSError), (
        "the original OSError must be preserved as the cause"
    )


def test_copy_workspace_tree_mirrors_workspace_contents(tmp_path: Path) -> None:
    """Workspace files are cloned into the staging directory."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest = workspace_root / "Cargo.toml"
    manifest.write_text("[workspace]\n", encoding="utf-8")
    nested_dir = workspace_root / "crates" / "alpha"
    nested_dir.mkdir(parents=True)
    nested_file = nested_dir / "README.md"
    nested_file.write_text("# README\n", encoding="utf-8")

    build_directory = tmp_path / "staging"
    build_directory.mkdir()

    staging_root = publish_staging._copy_workspace_tree(
        workspace_root, build_directory, preserve_symlinks=True
    )

    assert staging_root == build_directory / workspace_root.name, (
        "the staged copy must live in a directory named after the workspace"
    )
    assert (staging_root / "Cargo.toml").read_text(encoding="utf-8") == (
        "[workspace]\n"
    ), "top-level workspace files must be copied into the staging tree"
    assert (staging_root / "crates" / "alpha" / "README.md").read_text(
        encoding="utf-8"
    ) == "# README\n", "nested workspace files must be copied into the staging tree"


def test_copy_workspace_tree_replaces_existing_clone(tmp_path: Path) -> None:
    """Existing staging directories are replaced with a fresh copy."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "marker.txt").write_text("fresh", encoding="utf-8")

    build_directory = tmp_path / "staging"
    existing_clone = build_directory / workspace_root.name
    existing_clone.mkdir(parents=True)
    stale_file = existing_clone / "stale.txt"
    stale_file.write_text("stale", encoding="utf-8")

    staging_root = publish_staging._copy_workspace_tree(
        workspace_root, build_directory, preserve_symlinks=True
    )

    assert staging_root == existing_clone, (
        "a re-copy must reuse the same staging root as the existing clone"
    )
    assert not stale_file.exists(), (
        "files from a previous clone must not survive the fresh copy"
    )
    assert (staging_root / "marker.txt").read_text(encoding="utf-8") == "fresh", (
        "the fresh copy must carry the current workspace contents"
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _CopyWorkspaceFailureCase(
                "rmtree", requires_staging_root=True, message="permission denied"
            ),
            id="staging_cleanup",
        ),
        pytest.param(
            _CopyWorkspaceFailureCase(
                "copytree", requires_staging_root=False, message="disk full"
            ),
            id="workspace_copy",
        ),
    ],
)
def test_copy_workspace_tree_wraps_filesystem_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: _CopyWorkspaceFailureCase,
) -> None:
    """Workspace-copy filesystem failures use the staging error boundary."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    build_directory = tmp_path / "staging"
    if case.requires_staging_root:
        (build_directory / workspace_root.name).mkdir(parents=True)
    else:
        build_directory.mkdir()

    failure = OSError(case.message)

    def fail_operation(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(publish_staging.shutil, case.operation, fail_operation)

    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_staging._copy_workspace_tree(
            workspace_root, build_directory, preserve_symlinks=True
        )

    assert "Cannot copy workspace into staging directory" in str(excinfo.value), (
        f"a {case.operation} failure must be reported through the staging error "
        "boundary"
    )
    assert excinfo.value.__cause__ is failure, (
        f"the OSError raised by {case.operation} must be preserved as the cause"
    )


def test_copy_workspace_tree_rejects_nested_clone(tmp_path: Path) -> None:
    """Copying into a directory under the workspace is prohibited."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_staging._copy_workspace_tree(
            workspace_root, workspace_root, preserve_symlinks=True
        )

    assert "cannot be nested inside the workspace root" in str(excinfo.value), (
        "copying the workspace into itself must be refused with the documented reason"
    )


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            {"preserve_symlinks": True, "expect_symlink": True},
            id="preserve",
        ),
        pytest.param(
            {"preserve_symlinks": False, "expect_symlink": False},
            id="dereference",
        ),
    ],
)
def test_copy_workspace_tree_symlink_handling(
    tmp_path: Path, scenario: dict[str, bool]
) -> None:
    """Workspace symlinks are preserved or dereferenced based on option."""
    preserve_symlinks = scenario["preserve_symlinks"]
    expect_symlink = scenario["expect_symlink"]
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    target = workspace_root / "data.txt"
    target.write_text("payload", encoding="utf-8")
    link = workspace_root / "alias.txt"
    link.symlink_to(target.name)

    build_directory = tmp_path / "staging"
    build_directory.mkdir()

    staging_root = publish_staging._copy_workspace_tree(
        workspace_root, build_directory, preserve_symlinks=preserve_symlinks
    )

    staged_link = staging_root / "alias.txt"
    assert staged_link.is_file(), (
        "the staged symlink must still resolve to a regular file"
    )
    assert staged_link.is_symlink() == expect_symlink, (
        "preserve_symlinks must decide whether the staged link stays a symlink"
    )
    if expect_symlink:
        assert staged_link.resolve(strict=True) == staging_root / "data.txt", (
            "a preserved symlink must still point at the staged data file"
        )
    assert staged_link.read_text(encoding="utf-8") == "payload", (
        "the staged link must expose the original file contents"
    )


def test_prepare_workspace_registers_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """Cleanup-enabled staging registers an atexit handler."""
    fx = prepare_workspace_fixtures
    pf = preparation_fixtures
    workspace_root = fx.tmp_path / "workspace"
    workspace_root.mkdir()
    crate = pf.make_crate(workspace_root, "alpha")
    workspace = pf.make_workspace(workspace_root, crate)
    plan = publish.plan_publication(workspace, pf.make_config())

    build_directory = fx.publish_options.build_directory
    assert build_directory is not None, (
        "the publish_options fixture must supply a build directory"
    )
    build_directory.mkdir(parents=True)
    marker = build_directory / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    registered: list[cabc.Callable[[], None]] = []

    def capture(callback: cabc.Callable[..., None], *arguments: object) -> None:
        registered.append(lambda: callback(*arguments))

    monkeypatch.setattr(publish_staging.atexit, "register", capture)

    options = publish.PublishOptions(build_directory=build_directory, cleanup=True)
    preparation = publish_staging.prepare_workspace(plan, options=options)

    assert len(registered) == 1, (
        "cleanup-enabled staging must register exactly one atexit handler"
    )
    cleanup = registered[0]
    assert callable(cleanup), "the registered atexit hook must be callable"
    assert preparation.staging_root.parent == build_directory, (
        "the staged copy must be placed inside the requested build directory"
    )
    assert build_directory.exists(), (
        "a caller-supplied build directory must still exist after staging"
    )

    cleanup()
    assert build_directory.exists(), (
        "cleanup must keep a caller-supplied build directory in place"
    )
    assert marker.read_text(encoding="utf-8") == "keep", (
        "cleanup must not remove files the caller put in the build directory"
    )
    assert not preparation.staging_root.exists(), (
        "cleanup must remove the staged workspace copy"
    )


def test_prepare_workspace_cleanup_removes_auto_created_build_directory(
    monkeypatch: pytest.MonkeyPatch,
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """Cleanup removes the full temporary build directory that staging creates."""
    fx = prepare_workspace_fixtures
    pf = preparation_fixtures
    workspace_root = fx.tmp_path / "workspace"
    workspace_root.mkdir()
    crate = pf.make_crate(workspace_root, "alpha")
    plan = publish.plan_publication(
        pf.make_workspace(workspace_root, crate), pf.make_config()
    )
    registered: list[cabc.Callable[[], None]] = []

    def capture(callback: cabc.Callable[..., None], *arguments: object) -> None:
        registered.append(lambda: callback(*arguments))

    monkeypatch.setattr(publish_staging.atexit, "register", capture)

    preparation = publish_staging.prepare_workspace(
        plan, options=publish.PublishOptions(cleanup=True)
    )
    build_directory = preparation.staging_root.parent

    assert len(registered) == 1, (
        "cleanup-enabled staging must register exactly one atexit handler"
    )
    registered[0]()

    assert not build_directory.exists(), (
        "cleanup must remove the build directory staging created automatically"
    )


@pytest.mark.parametrize(
    "crate_spec",
    [
        pytest.param(
            _CrateSpec(readme_workspace=True),
            id="opted_in_missing_workspace_readme",
        ),
        pytest.param(_CrateSpec(), id="no_readme_opt_in"),
    ],
)
def test_prepare_workspace_copies_workspace_readme_without_adopting_it_for_crates(
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
    crate_spec: _CrateSpec,
) -> None:
    """Staging copies the workspace README without creating crate READMEs."""
    fx = prepare_workspace_fixtures
    pf = preparation_fixtures
    workspace_root = fx.tmp_path / "workspace"
    workspace_root.mkdir()
    readme = workspace_root / "README.md"
    readme.write_text("Workspace README", encoding="utf-8")
    crate = pf.make_crate(workspace_root, "alpha", crate_spec)
    workspace = pf.make_workspace(workspace_root, crate)
    configuration = pf.make_config()
    plan = publish.plan_publication(workspace, configuration)

    preparation = publish_staging.prepare_workspace(plan, options=fx.publish_options)

    assert preparation.staging_root.exists(), (
        "staging must leave a workspace copy on disk"
    )
    assert (preparation.staging_root / readme.name).read_text(encoding="utf-8") == (
        "Workspace README"
    ), "the workspace README must be copied into the staging tree verbatim"
    staged_crate_readme = (
        preparation.staging_root
        / crate.root_path.relative_to(workspace_root)
        / "README.md"
    )
    assert not staged_crate_readme.exists(), (
        "staging must not adopt the workspace README as a crate README"
    )


def test_prepare_workspace_does_not_register_cleanup_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """Cleanup hook is not registered when the option is explicitly disabled.

    Cleanup is the default since issue #269, so opting out is what has to be
    stated; a test that relied on the old default would silently stop covering
    anything.
    """
    fx = prepare_workspace_fixtures
    pf = preparation_fixtures
    workspace_root = fx.tmp_path / "workspace"
    workspace_root.mkdir()
    crate = pf.make_crate(workspace_root, "alpha")
    workspace = pf.make_workspace(workspace_root, crate)
    plan = publish.plan_publication(workspace, pf.make_config())

    registered: list[cabc.Callable[[], None]] = []

    def capture(callback: cabc.Callable[..., None], *arguments: object) -> None:
        registered.append(lambda: callback(*arguments))

    monkeypatch.setattr(publish_staging.atexit, "register", capture)

    options = dc.replace(fx.publish_options, cleanup=False)
    publish_staging.prepare_workspace(plan, options=options)

    assert not registered, (
        "staging with cleanup disabled must not register an atexit handler"
    )


def _plan_for(
    fx: PrepareWorkspaceFixtures, pf: PreparationFixtures
) -> publish_plan.PublishPlan:
    """Return a publication plan for a one-crate workspace under ``fx``.

    Returns
    -------
    publish_plan.PublishPlan
        The plan to stage.
    """
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
