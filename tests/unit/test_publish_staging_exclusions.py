"""Unit tests for ``publish.stage_exclude`` (issue #262).

Staging copies the entire workspace, so a populated ``target/`` or ``.git``
makes the copy cost depend on what happens to be in the tree rather than on
the sources being packaged. These tests cover the copy filter itself and the
default applied at the staging boundary. The refusal to exclude a manifest
the publication cannot proceed without lives in
:mod:`tests.unit.test_publish_stage_exclude_validation`.
"""

import collections.abc as cabc
import contextlib
import logging
import typing as typ
from pathlib import Path

import pytest

from lading import stage_exclude_policy
from lading.commands import publish, publish_plan, publish_staging
from tests.unit.publish_staging_plan_fixtures import _plan_with_roles

if typ.TYPE_CHECKING:
    from tests.unit.conftest import PreparationFixtures

_POPULATED_PATHS = (
    "Cargo.toml",
    "crates/alpha/Cargo.toml",
    "crates/alpha/src/lib.rs",
    ".git/config",
    "crates/alpha/.git/config",
    "target/debug/artifact",
    "venv/marker",
    "crates/alpha/venv/marker",
    "crates/venv/marker",
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


@contextlib.contextmanager
def _stage_capturing_logs(
    plan: publish_plan.PublishPlan,
    options: publish.PublishOptions,
    caplog: pytest.LogCaptureFixture,
    *,
    stage_exclude: cabc.Sequence[str],
) -> cabc.Generator[publish_staging.StagedWorkspace]:
    """Stage ``plan`` under log capture and yield the staged workspace."""
    with (
        # DEBUG keeps every record the staging module emits, so a caller's
        # assertion decides which level mattered.
        caplog.at_level(logging.DEBUG, logger="lading.commands.publish_staging"),
        publish_staging.staged_workspace(
            plan, options=options, stage_exclude=stage_exclude
        ) as staged,
    ):
        yield staged


def test_staged_workspace_omits_git_by_default(
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


def test_staging_logs_the_effective_exclusions(
    preparation_fixtures: PreparationFixtures,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The exclusion actually applied is recorded when staging begins."""
    plan = _plan_with_roles(preparation_fixtures)
    options = publish.PublishOptions(
        build_directory=preparation_fixtures.tmp_path / "staging"
    )
    with _stage_capturing_logs(plan, options, caplog, stage_exclude=("venv",)):
        pass

    assert "Staging workspace with stage_exclude=('venv',)" in caplog.text, (
        "the log must name the patterns that shaped the staged tree"
    )


def test_staged_workspace_strips_a_trailing_slash(
    preparation_fixtures: PreparationFixtures,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A programmatic caller may spell a directory the way a file is spelled."""
    plan = _plan_with_roles(preparation_fixtures)
    target = plan.workspace_root / "target"
    target.mkdir()
    (target / "artifact").write_text("", encoding="utf-8")
    options = publish.PublishOptions(
        build_directory=preparation_fixtures.tmp_path / "staging"
    )
    with _stage_capturing_logs(
        plan, options, caplog, stage_exclude=("target/",)
    ) as staged:
        # Checked inside the block: cleanup removes the tree on exit, so a
        # later absence assertion would hold whether or not the copy honoured
        # the pattern.
        assert not (staged.staging_root / "target").exists(), (
            "a trailing slash must be stripped before the pattern is matched"
        )
    assert "stage_exclude=('target',)" in caplog.text, (
        "the log must name the normalized pattern rather than the raw spelling"
    )


def _staging_rejection(
    fixtures: PreparationFixtures,
    *,
    stage_exclude: cabc.Sequence[str],
) -> str:
    """Stage with an unusable pattern and return the rejection message."""
    plan = _plan_with_roles(fixtures)
    build_directory = fixtures.tmp_path / "staging"
    with (
        pytest.raises(publish_staging.PublishPreparationError) as excinfo,
        publish_staging.staged_workspace(
            plan,
            options=publish.PublishOptions(build_directory=build_directory),
            stage_exclude=stage_exclude,
        ),
    ):
        pass

    # Every rejection must happen before staging creates a directory, so the
    # invariant is asserted with the setup that makes it observable.
    assert not build_directory.exists(), (
        "an unusable pattern must not create a staging directory"
    )
    return str(excinfo.value)


@pytest.mark.parametrize(
    ("stage_exclude", "expected_fragment"),
    [
        pytest.param(
            ("./target",),
            "publish.stage_exclude[0] must not contain '.'",
            id="current_directory",
        ),
        pytest.param(
            ("/target",),
            "publish.stage_exclude[0] must be relative",
            id="absolute",
        ),
        pytest.param(
            ("crates//venv",),
            "publish.stage_exclude[0] must not contain '.'",
            id="empty_segment",
        ),
        pytest.param(
            ("!keep",),
            "publish.stage_exclude[0] must not start with '!'",
            id="negation",
        ),
    ],
)
def test_staged_workspace_rejects_unusable_patterns(
    preparation_fixtures: PreparationFixtures,
    stage_exclude: tuple[str, ...],
    expected_fragment: str,
) -> None:
    """The staging boundary applies the same spelling rules as the file."""
    message = _staging_rejection(preparation_fixtures, stage_exclude=stage_exclude)

    assert expected_fragment in message, (
        "the rejection must name the pattern's position and the reason"
    )


@pytest.mark.parametrize(
    ("stage_exclude", "should_warn"),
    [((".git",), True), ((), False)],
    ids=("excludes-git", "keeps-git"),
)
def test_live_publish_warns_only_when_git_is_excluded(
    preparation_fixtures: PreparationFixtures,
    caplog: pytest.LogCaptureFixture,
    stage_exclude: tuple[str, ...],
    *,
    should_warn: bool,
) -> None:
    """An irreversible upload without a checkout is flagged before copying."""
    plan = _plan_with_roles(preparation_fixtures)
    options = publish.PublishOptions(
        build_directory=preparation_fixtures.tmp_path / "staging", live=True
    )
    with _stage_capturing_logs(plan, options, caplog, stage_exclude=stage_exclude):
        pass

    assert (".gitignore" in caplog.text) is should_warn, (
        "the warning must appear exactly when the upload loses gitignore selection"
    )


def test_normalize_stage_exclude_rejects_a_bare_string() -> None:
    """A single string is a character sequence, not one pattern."""
    with pytest.raises(stage_exclude_policy.StageExcludeError) as excinfo:
        stage_exclude_policy.normalize_stage_exclude("target")

    assert "not a single string" in str(excinfo.value), (
        "the rejection must explain that a sequence is required"
    )


def test_staged_workspace_rejects_a_bare_string(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A caller passing one pattern rather than a list is refused, not split."""
    message = _staging_rejection(preparation_fixtures, stage_exclude="target")

    assert "not a single string" in message, (
        "the boundary must report the same rejection as the policy module"
    )


def test_leading_double_star_matches_at_any_depth(tmp_path: Path) -> None:
    """A ``**/`` prefix means "any depth", including the workspace root."""
    staging_root = _copy_with_exclusions(tmp_path, ("**/venv",))

    assert not (staging_root / "venv").exists(), (
        "a top-level venv must match the any-depth spelling"
    )
    assert not (staging_root / "crates" / "alpha" / "venv").exists(), (
        "a nested venv must match the same pattern"
    )
    assert (staging_root / "target" / "debug" / "artifact").is_file(), (
        "the any-depth spelling must not remove unrelated entries"
    )


def test_leading_double_star_matches_a_path_at_any_depth(tmp_path: Path) -> None:
    """A ``**/`` prefix lifts the anchoring of the whole remainder."""
    staging_root = _copy_with_exclusions(tmp_path, ("**/alpha/venv",))

    assert not (staging_root / "crates" / "alpha" / "venv").exists(), (
        "the remainder must match below an ancestor it does not name"
    )
    assert (staging_root / "venv" / "marker").is_file(), (
        "the any-depth spelling must not remove a top-level venv"
    )


def test_interior_double_star_still_needs_a_separator(tmp_path: Path) -> None:
    """An interior ``**`` is a plain ``*``: it does not span zero segments."""
    staging_root = _copy_with_exclusions(tmp_path, ("crates/**/venv",))

    assert not (staging_root / "crates" / "alpha" / "venv").exists(), (
        "the pattern must match a venv one directory below crates"
    )
    assert (staging_root / "crates" / "venv" / "marker").is_file(), (
        "an interior '**' must not match when no segment sits beneath it"
    )
