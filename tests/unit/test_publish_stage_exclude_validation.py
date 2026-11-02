"""Unit tests for manifest protection when staging excludes paths.

An exclusion is meant to trim build output and version-control metadata, but
the same spelling can name a crate directory or a ``Cargo.toml``. Removing
either leaves a publication Cargo cannot complete, so staging refuses the
pattern before it copies anything. These tests cover that refusal, the promise
that it happens before a staging directory exists, and the property that the
check agrees with what the copy actually leaves behind.
"""

import dataclasses as dc
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from lading.commands import (
    publish,
    publish_plan,
    publish_stage_exclude,
    publish_staging,
)
from tests.unit.conftest import PreparationFixtures
from tests.unit.publish_staging_plan_fixtures import _plan_with_roles


def test_stage_exclude_rejects_a_pattern_matching_an_ancestor_directory(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """Naming a crate's parent directory removes its manifest too."""
    root = preparation_fixtures.tmp_path / "workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    crate = preparation_fixtures.make_crate(root / "crates", "alpha")
    workspace = preparation_fixtures.make_workspace(root, crate)
    plan = publish.plan_publication(workspace, preparation_fixtures.make_config())

    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_stage_exclude._validate_stage_exclude(
            plan, ("crates",), preserve_symlinks=True
        )

    assert "crate 'alpha' manifest 'crates/alpha/Cargo.toml'" in str(excinfo.value), (
        "the rejection must name the nested manifest the ancestor pattern removes"
    )


def test_stage_exclude_allows_a_manifest_outside_the_workspace_root(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A member outside the workspace root cannot be removed by staging."""
    plan = _plan_with_roles(preparation_fixtures)
    external = preparation_fixtures.make_crate(
        preparation_fixtures.tmp_path / "external", "delta"
    )
    plan = dc.replace(plan, publishable=(*plan.publishable, external))

    with pytest.raises(publish_staging.PublishPreparationError):
        publish_stage_exclude._validate_stage_exclude(
            plan, ("alpha",), preserve_symlinks=True
        )

    # Passing for the external crate alone proves the exemption is specific to
    # it, not that validation returned early for every pattern.
    publish_stage_exclude._validate_stage_exclude(
        plan, ("delta",), preserve_symlinks=True
    )


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

    assert "symlink target 'manifests'" in str(excinfo.value), (
        "the rejection must name the directory the link in the path names"
    )
    # An unrelated pattern still passes.
    publish_stage_exclude._validate_stage_exclude(
        plan, ("src",), preserve_symlinks=True
    )


_CANDIDATE_PATTERNS = (
    "Cargo.toml",
    "alpha",
    "crates",
    "crates/alpha",
    "crates/*",
    "src",
    "venv",
    "**/alpha",
    "*",
)
_REQUIRED_MANIFESTS = ("Cargo.toml", "crates/alpha/Cargo.toml")


def _validation_rejects(
    plan: publish_plan.PublishPlan, patterns: tuple[str, ...]
) -> bool:
    """Return whether the pre-copy check refuses ``patterns``."""
    try:
        publish_stage_exclude._validate_stage_exclude(
            plan, patterns, preserve_symlinks=False
        )
    except publish_staging.PublishPreparationError:
        return True
    return False


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(patterns=st.lists(st.sampled_from(_CANDIDATE_PATTERNS), max_size=3))
def test_validation_rejects_exactly_the_patterns_that_remove_a_manifest(
    preparation_fixtures: PreparationFixtures,
    tmp_path: Path,
    patterns: list[str],
) -> None:
    """The pre-copy check agrees with what the copy actually leaves out."""
    # Hypothesis reuses ``tmp_path`` for every example, so each one needs its
    # own directory rather than inheriting the previous example's tree.
    workspace_root = Path(tempfile.mkdtemp(dir=tmp_path, prefix="workspace-"))
    staging_parent = Path(tempfile.mkdtemp(dir=tmp_path, prefix="staging-"))
    (workspace_root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    crate = preparation_fixtures.make_crate(workspace_root / "crates", "alpha")
    for relative in ("crates/alpha/src/lib.rs", "venv/marker"):
        path = workspace_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("marker\n", encoding="utf-8")
    plan = publish.plan_publication(
        preparation_fixtures.make_workspace(workspace_root, crate),
        preparation_fixtures.make_config(),
    )
    stage_exclude = tuple(patterns)

    rejected = _validation_rejects(plan, stage_exclude)
    staging_root = publish_staging._copy_workspace_tree(
        workspace_root,
        staging_parent,
        preserve_symlinks=False,
        stage_exclude=stage_exclude,
    )
    removed = any(
        not (staging_root / relative).is_file() for relative in _REQUIRED_MANIFESTS
    )

    assert rejected is removed, (
        f"patterns {stage_exclude!r} must be refused exactly when the copy "
        f"would remove a required manifest (refused: {rejected}, removed: {removed})"
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
    preparation_fixtures: PreparationFixtures,
    pattern: str,
    expected_fragment: str,
) -> None:
    """An exclusion that removes a manifest is refused before staging begins."""
    plan = _plan_with_roles(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "staging"
    options = publish.PublishOptions(build_directory=build_directory)

    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        _enter_staged_workspace(plan, options, pattern)

    assert expected_fragment in str(excinfo.value), (
        "the rejection must name the pattern's effect and the affected manifest"
    )
    assert not build_directory.exists(), (
        "a rejected exclusion must not create a staging directory"
    )
