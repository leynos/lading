"""Link traversal tests for staging exclusion validation.

A required manifest can be reached through a symbolic link, in a parent
directory or in the link text itself, and staging copies each link as a link.
These tests cover the dependency walk that keeps every such manifest readable
in the staged tree.
"""

import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from lading.commands import publish, publish_stage_exclude, publish_staging
from tests.unit.conftest import PreparationFixtures
from tests.unit.publish_staging_plan_fixtures import validation_rejects


def test_stage_exclude_rejects_a_symlinked_manifest(
    preparation_fixtures: PreparationFixtures, tmp_path: Path
) -> None:
    """A manifest is checked at the path staging copies, not its target."""
    root = preparation_fixtures.tmp_path / "workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    crate = preparation_fixtures.make_crate(root, "alpha")
    target = tmp_path / "shared-Cargo.toml"
    target.write_text(crate.manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
    crate.manifest_path.unlink()
    crate.manifest_path.symlink_to(target)
    plan = publish.plan_publication(
        preparation_fixtures.make_workspace(root, crate),
        preparation_fixtures.make_config(),
    )

    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_stage_exclude._validate_stage_exclude(
            plan, ("alpha",), preserve_symlinks=True
        )

    assert "crate 'alpha' manifest 'alpha/Cargo.toml'" in str(excinfo.value), (
        "the rejection must name the link path rather than its target"
    )


def test_stage_exclude_rejects_a_preserved_link_into_an_excluded_directory(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A kept link dangles when staging would prune the file it points at."""
    root = preparation_fixtures.tmp_path / "workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    crate = preparation_fixtures.make_crate(root, "alpha")
    shared = root / "shared"
    shared.mkdir()
    (shared / "Cargo.toml").write_text(
        crate.manifest_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    crate.manifest_path.unlink()
    crate.manifest_path.symlink_to("../shared/Cargo.toml")
    plan = publish.plan_publication(
        preparation_fixtures.make_workspace(root, crate),
        preparation_fixtures.make_config(),
    )

    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_stage_exclude._validate_stage_exclude(
            plan, ("shared",), preserve_symlinks=True
        )

    assert "symlink target 'shared/Cargo.toml'" in str(excinfo.value), (
        "the rejection must name the target staging would not copy"
    )
    # Copying through the link puts a real file in the staged tree, so the
    # same pattern is harmless and must not be refused.
    publish_stage_exclude._validate_stage_exclude(
        plan, ("shared",), preserve_symlinks=False
    )


def test_stage_exclude_rejects_an_excluded_intermediate_link(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A pruned link in the chain breaks the preserved manifest too."""
    root = preparation_fixtures.tmp_path / "workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    crate = preparation_fixtures.make_crate(root, "alpha")
    (root / "manifests").mkdir()
    (root / "manifests" / "workspace.toml").write_text(
        crate.manifest_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / "intermediate.toml").symlink_to("manifests/workspace.toml")
    crate.manifest_path.unlink()
    crate.manifest_path.symlink_to("../intermediate.toml")
    plan = publish.plan_publication(
        preparation_fixtures.make_workspace(root, crate),
        preparation_fixtures.make_config(),
    )

    # ``resolve()`` reaches the final target, so only walking the chain shows
    # that staging would prune the link the manifest names directly.
    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_stage_exclude._validate_stage_exclude(
            plan, ("intermediate.toml",), preserve_symlinks=True
        )

    assert "symlink target 'intermediate.toml'" in str(excinfo.value), (
        "the rejection must name the intermediate link staging would not copy"
    )
    # The final target is still checked, and an unrelated pattern still passes.
    with pytest.raises(publish_staging.PublishPreparationError):
        publish_stage_exclude._validate_stage_exclude(
            plan, ("manifests",), preserve_symlinks=True
        )
    publish_stage_exclude._validate_stage_exclude(
        plan, ("src",), preserve_symlinks=True
    )


def test_stage_exclude_rejects_a_link_through_an_excluded_directory(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A pruned directory named by a link in the path breaks the manifest too."""
    root = preparation_fixtures.tmp_path / "workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    crate = preparation_fixtures.make_crate(root, "alpha")
    (root / "manifests").mkdir()
    (root / "manifests" / "workspace.toml").write_text(
        crate.manifest_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / "alias").symlink_to("manifests")
    crate.manifest_path.unlink()
    crate.manifest_path.symlink_to("../alias/workspace.toml")
    plan = publish.plan_publication(
        preparation_fixtures.make_workspace(root, crate),
        preparation_fixtures.make_config(),
    )

    # ``alias/workspace.toml`` is not itself a link, so only following the
    # directory link shows that staging would prune what it names.
    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_stage_exclude._validate_stage_exclude(
            plan, ("manifests",), preserve_symlinks=True
        )

    assert "symlink target 'manifests/workspace.toml'" in str(excinfo.value), (
        "the rejection must name the file the directory link resolves to"
    )
    # An unrelated pattern still passes.
    publish_stage_exclude._validate_stage_exclude(
        plan, ("src",), preserve_symlinks=True
    )


def test_stage_exclude_rejects_a_directory_link_target_below_the_link(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A directory link carries the rest of the path into its target."""
    root = preparation_fixtures.tmp_path / "workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (root / "manifests").mkdir()
    (root / "alias").symlink_to("manifests")
    # Writing through the link leaves the manifest at ``manifests/alpha`` while
    # the plan records the path staging copies, ``alias/alpha/Cargo.toml``.
    crate = preparation_fixtures.make_crate(root / "alias", "alpha")
    plan = publish.plan_publication(
        preparation_fixtures.make_workspace(root, crate),
        preparation_fixtures.make_config(),
    )

    # The manifest is reached as ``alias/alpha/Cargo.toml``, so the dependency
    # staging must copy is ``manifests/alpha/Cargo.toml``: a pattern naming the
    # target directory removes it while matching none of the link's own path.
    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_stage_exclude._validate_stage_exclude(
            plan, ("manifests/alpha",), preserve_symlinks=True
        )

    assert "symlink target 'manifests/alpha/Cargo.toml'" in str(excinfo.value), (
        "the rejection must name the manifest the directory link resolves to"
    )
    # An unrelated pattern still passes.
    publish_stage_exclude._validate_stage_exclude(
        plan, ("src",), preserve_symlinks=True
    )
    # The refused pattern would really have dangled the staged manifest: the
    # link survives the copy, but its target directory does not.
    staging_parent = preparation_fixtures.tmp_path / "staging"
    staging_parent.mkdir()
    staging_root = publish_staging._copy_workspace_tree(
        root,
        staging_parent,
        preserve_symlinks=True,
        stage_exclude=("manifests/alpha",),
    )
    staged_link = staging_root / "alias"
    assert staged_link.is_symlink(), "the directory link must be copied as a link"
    assert not (staged_link / "alpha" / "Cargo.toml").exists(), (
        "the refused pattern would leave the staged manifest dangling"
    )


def test_stage_exclude_follows_a_parent_link_before_a_dotdot_target(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A ``..`` in a link's text is applied to the physical parent directory."""
    root = preparation_fixtures.tmp_path / "workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (root / "crates" / "a").mkdir(parents=True)
    (root / "crates" / "shared").mkdir()
    (root / "alias").symlink_to("crates/a")
    crate = preparation_fixtures.make_crate(root, "alias")
    (root / "crates" / "shared" / "Cargo.toml").write_text(
        crate.manifest_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    crate.manifest_path.unlink()
    # The kernel resolves this to ``crates/shared/Cargo.toml`` because
    # ``alias`` names ``crates/a``; a lexical join would say ``shared``.
    crate.manifest_path.symlink_to("../shared/Cargo.toml")
    plan = publish.plan_publication(
        preparation_fixtures.make_workspace(root, crate),
        preparation_fixtures.make_config(),
    )

    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_stage_exclude._validate_stage_exclude(
            plan, ("crates/shared",), preserve_symlinks=True
        )

    assert "symlink target 'crates/shared/Cargo.toml'" in str(excinfo.value), (
        "the rejection must name the target the kernel resolves, not a "
        "lexical join through the link's parent"
    )
    # An unrelated pattern still passes.
    publish_stage_exclude._validate_stage_exclude(
        plan, ("src",), preserve_symlinks=True
    )


def test_stage_exclude_rejects_a_directory_the_link_text_leaves(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A component a later ``..`` removes is still traversed by the kernel."""
    root = preparation_fixtures.tmp_path / "workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (root / "manifests").mkdir()
    (root / "bridge").mkdir()
    crate = preparation_fixtures.make_crate(root, "alpha")
    (root / "manifests" / "Cargo.toml").write_text(
        crate.manifest_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    crate.manifest_path.unlink()
    # The kernel enters ``bridge`` before applying the second ``..``, so the
    # link is unreadable once that directory is pruned, even though the
    # lexical target is ``manifests/Cargo.toml``.
    crate.manifest_path.symlink_to("../bridge/../manifests/Cargo.toml")
    plan = publish.plan_publication(
        preparation_fixtures.make_workspace(root, crate),
        preparation_fixtures.make_config(),
    )

    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_stage_exclude._validate_stage_exclude(
            plan, ("bridge",), preserve_symlinks=True
        )

    assert "symlink target 'bridge'" in str(excinfo.value), (
        "the rejection must name the directory the link text traverses"
    )
    # An unrelated pattern still passes.
    publish_stage_exclude._validate_stage_exclude(
        plan, ("src",), preserve_symlinks=True
    )
    # The refused pattern really would break the staged link: the target file
    # is copied, but the directory the text enters first is not.
    staging_parent = preparation_fixtures.tmp_path / "staging"
    staging_parent.mkdir()
    staging_root = publish_staging._copy_workspace_tree(
        root,
        staging_parent,
        preserve_symlinks=True,
        stage_exclude=("bridge",),
    )
    assert (staging_root / "manifests" / "Cargo.toml").is_file(), (
        "the target file must be copied for the check to be meaningful"
    )
    assert not (staging_root / "alpha" / "Cargo.toml").exists(), (
        "the pruned directory makes the staged link unreadable"
    )


def test_stage_exclude_ignores_a_self_referencing_manifest_link(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A looping manifest link is absent, not an unreadable workspace."""
    root = preparation_fixtures.tmp_path / "workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    crate = preparation_fixtures.make_crate(root, "alpha")
    crate.manifest_path.unlink()
    crate.manifest_path.symlink_to("Cargo.toml")
    plan = publish.plan_publication(
        preparation_fixtures.make_workspace(root, crate),
        preparation_fixtures.make_config(),
    )

    # ``Path.exists`` reports a looping link as absent, so the walk never
    # follows it and no inspection failure is raised.
    publish_stage_exclude._validate_stage_exclude(
        plan, ("shared",), preserve_symlinks=True
    )


_DIRECTORY_LINK_PATTERNS = (
    "Cargo.toml",
    "alias",
    "alias/alpha",
    "manifests",
    "manifests/alpha",
    "manifests/alpha/Cargo.toml",
    "src",
)


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(patterns=st.lists(st.sampled_from(_DIRECTORY_LINK_PATTERNS), max_size=3))
def test_validation_rejects_every_pattern_that_dangles_a_directory_link(
    preparation_fixtures: PreparationFixtures,
    tmp_path: Path,
    patterns: list[str],
) -> None:
    """A pruned directory link never leaves a required manifest dangling."""
    workspace_root = Path(tempfile.mkdtemp(dir=tmp_path, prefix="workspace-"))
    staging_parent = Path(tempfile.mkdtemp(dir=tmp_path, prefix="staging-"))
    (workspace_root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (workspace_root / "manifests").mkdir()
    (workspace_root / "alias").symlink_to("manifests")
    crate = preparation_fixtures.make_crate(workspace_root / "alias", "alpha")
    plan = publish.plan_publication(
        preparation_fixtures.make_workspace(workspace_root, crate),
        preparation_fixtures.make_config(),
    )
    stage_exclude = tuple(patterns)

    rejected = validation_rejects(plan, stage_exclude, preserve_symlinks=True)
    staging_root = publish_staging._copy_workspace_tree(
        workspace_root,
        staging_parent,
        preserve_symlinks=True,
        stage_exclude=stage_exclude,
    )
    manifest = staging_root / "alias" / "alpha" / "Cargo.toml"

    assert manifest.is_file() or rejected, (
        f"patterns {stage_exclude!r} removed the manifest staging must keep "
        f"without being refused (rejected: {rejected})"
    )
