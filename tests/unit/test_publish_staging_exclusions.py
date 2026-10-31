"""Unit tests for ``publish.stage_exclude`` (issue #262).

Staging copies the entire workspace, so a populated ``target/`` or ``.git``
makes the copy cost depend on what happens to be in the tree rather than on
the sources being packaged. These tests cover the copy filter itself, the
default applied at the staging boundary, and the refusal to exclude a
manifest the publication cannot proceed without.
"""

from pathlib import Path

import pytest

from lading.commands import publish, publish_plan, publish_staging
from tests.unit.conftest import (
    PreparationFixtures,
    PrepareWorkspaceFixtures,
    _CrateSpec,
)

_POPULATED_PATHS = (
    "Cargo.toml",
    "crates/alpha/Cargo.toml",
    "crates/alpha/src/lib.rs",
    ".git/config",
    "crates/alpha/.git/config",
    "target/debug/artifact",
    "venv/marker",
    "crates/alpha/venv/marker",
)


def _populate_workspace(root: Path) -> None:
    """Create a workspace tree with sources, build output, and git metadata."""
    for relative in _POPULATED_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("marker\n", encoding="utf-8")


def _copy_with_exclusions(tmp_path: Path, stage_exclude: tuple[str, ...]) -> Path:
    """Copy a populated workspace and return the staged root."""
    workspace_root = tmp_path / "workspace"
    _populate_workspace(workspace_root)
    build_directory = tmp_path / "staging"
    build_directory.mkdir()
    return publish_staging._copy_workspace_tree(
        workspace_root,
        build_directory,
        preserve_symlinks=True,
        stage_exclude=stage_exclude,
    )


def test_git_is_omitted_at_any_depth(tmp_path: Path) -> None:
    """A basename pattern removes ``.git`` wherever it appears."""
    staging_root = _copy_with_exclusions(tmp_path, (".git",))

    assert not (staging_root / ".git").exists(), (
        "the workspace's .git directory must not be copied"
    )
    assert not (staging_root / "crates" / "alpha" / ".git").exists(), (
        "a nested .git directory must be omitted as well"
    )
    assert (staging_root / "crates" / "alpha" / "Cargo.toml").is_file(), (
        "omitting .git must not remove the sources beside it"
    )


def test_build_output_is_retained_by_default(tmp_path: Path) -> None:
    """``target/`` is copied unless a caller explicitly excludes it."""
    staging_root = _copy_with_exclusions(tmp_path, (".git",))

    assert (staging_root / "target" / "debug" / "artifact").is_file(), (
        "the default exclusions must leave target/ in the staged tree"
    )


def test_basename_patterns_match_at_any_depth(tmp_path: Path) -> None:
    """A pattern without a slash matches a directory name at every level."""
    staging_root = _copy_with_exclusions(tmp_path, ("venv",))

    assert not (staging_root / "venv").exists(), "a top-level venv must be omitted"
    assert not (staging_root / "crates" / "alpha" / "venv").exists(), (
        "a nested venv must be omitted by the same basename pattern"
    )
    assert (staging_root / "target" / "debug" / "artifact").is_file(), (
        "basename matching must not remove unrelated entries"
    )


def test_anchored_patterns_match_the_relative_path(tmp_path: Path) -> None:
    """A pattern containing a slash is anchored at the workspace root."""
    staging_root = _copy_with_exclusions(tmp_path, ("crates/alpha/venv",))

    assert (staging_root / "venv" / "marker").is_file(), (
        "an anchored pattern must not match a same-named directory elsewhere"
    )
    assert not (staging_root / "crates" / "alpha" / "venv").exists(), (
        "the anchored pattern must remove the named directory"
    )


def test_anchored_wildcards_span_separators(tmp_path: Path) -> None:
    """A ``*`` in an anchored pattern may match across ``/``, as documented."""
    staging_root = _copy_with_exclusions(tmp_path, ("crates/*/lib.rs",))

    assert not (staging_root / "crates" / "alpha" / "src" / "lib.rs").exists(), (
        "crates/*/lib.rs must match crates/alpha/src/lib.rs"
    )
    assert (staging_root / "crates" / "alpha" / "Cargo.toml").is_file(), (
        "the wildcard must not remove the sibling manifest"
    )


def test_empty_patterns_copy_everything(tmp_path: Path) -> None:
    """An empty exclusion list opts out of every omission."""
    staging_root = _copy_with_exclusions(tmp_path, ())

    assert (staging_root / ".git" / "config").is_file(), (
        "an explicit empty list must copy .git"
    )
    assert (staging_root / "venv" / "marker").is_file(), (
        "an explicit empty list must copy other excluded-by-default entries"
    )


def test_filtering_preserves_symlinks(tmp_path: Path) -> None:
    """Excluding entries must not change how symlinks are copied."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "data.txt").write_text("payload", encoding="utf-8")
    (workspace_root / "alias.txt").symlink_to("data.txt")
    ignored = workspace_root / "ignored"
    ignored.mkdir()
    (ignored / "marker").write_text("marker", encoding="utf-8")
    build_directory = tmp_path / "staging"
    build_directory.mkdir()

    staging_root = publish_staging._copy_workspace_tree(
        workspace_root,
        build_directory,
        preserve_symlinks=True,
        stage_exclude=("ignored",),
    )

    staged_alias = staging_root / "alias.txt"
    assert staged_alias.is_symlink(), "filtering must not dereference a symlink"
    assert staged_alias.read_text(encoding="utf-8") == "payload", (
        "the preserved symlink must resolve inside the staged tree"
    )
    assert not (staging_root / "ignored").exists(), (
        "the excluded directory must not appear in the staged tree"
    )


def _plan_with_roles(
    fixtures: PreparationFixtures,
) -> publish_plan.PublishPlan:
    """Return a plan with a publishable, manifest-skipped, and config-skipped crate."""
    root = fixtures.tmp_path / "workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    publishable = fixtures.make_crate(root, "alpha")
    manifest_skipped = fixtures.make_crate(root, "beta", _CrateSpec(publish=False))
    configuration_skipped = fixtures.make_crate(root, "gamma")
    workspace = fixtures.make_workspace(
        root, publishable, manifest_skipped, configuration_skipped
    )
    return publish.plan_publication(workspace, fixtures.make_config(exclude=("gamma",)))


def test_staged_workspace_omits_git_by_default(
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
    tmp_path: Path,
) -> None:
    """Staging applies the default exclusion when no pattern is supplied."""
    plan = _plan_with_roles(preparation_fixtures)
    (plan.workspace_root / ".git").mkdir()
    (plan.workspace_root / ".git" / "config").write_text("", encoding="utf-8")
    (plan.workspace_root / "target").mkdir()
    (plan.workspace_root / "target" / "artifact").write_text("", encoding="utf-8")
    options = publish.PublishOptions(build_directory=tmp_path / "staging")

    with publish_staging.staged_workspace(plan, options=options) as staged:
        assert not (staged.staging_root / ".git").exists(), (
            "default staging must omit .git"
        )
        assert (staged.staging_root / "target" / "artifact").is_file(), (
            "default staging must retain target/"
        )


def _enter_staged_workspace(
    plan: publish_plan.PublishPlan,
    options: publish.PublishOptions,
    pattern: str,
) -> None:
    """Enter the staging context manager for a pattern expected to be rejected."""
    with publish_staging.staged_workspace(
        plan, options=options, stage_exclude=(pattern,)
    ):
        pass


@pytest.mark.parametrize(
    ("pattern", "expected_fragment"),
    [
        pytest.param(
            "alpha",
            "crate 'alpha' manifest 'alpha/Cargo.toml'",
            id="publishable_crate_directory",
        ),
        pytest.param(
            "alpha/Cargo.toml",
            "crate 'alpha' manifest 'alpha/Cargo.toml'",
            id="publishable_crate_manifest",
        ),
        pytest.param(
            "beta",
            "crate 'beta' manifest 'beta/Cargo.toml'",
            id="manifest_skipped_crate",
        ),
        pytest.param(
            "gamma",
            "crate 'gamma' manifest 'gamma/Cargo.toml'",
            id="configuration_skipped_crate",
        ),
        pytest.param(
            "Cargo.toml",
            "the workspace manifest 'Cargo.toml'",
            id="workspace_manifest",
        ),
    ],
)
def test_stage_exclude_rejects_removing_required_manifests(
    prepare_workspace_fixtures: PrepareWorkspaceFixtures,
    preparation_fixtures: PreparationFixtures,
    tmp_path: Path,
    pattern: str,
    expected_fragment: str,
) -> None:
    """An exclusion that removes a manifest is refused before staging begins."""
    plan = _plan_with_roles(preparation_fixtures)
    build_directory = tmp_path / "staging"
    options = publish.PublishOptions(build_directory=build_directory)

    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        _enter_staged_workspace(plan, options, pattern)

    assert expected_fragment in str(excinfo.value), (
        "the rejection must name the pattern's effect and the affected manifest"
    )
    assert not build_directory.exists(), (
        "a rejected exclusion must not create a staging directory"
    )
