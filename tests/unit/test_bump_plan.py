"""Unit tests for the immutable bump plan and its proposal builder.

The plan is the boundary that guarantees every target is validated and every
content transform has succeeded before the first workspace write. These tests
exercise composition of repeated targets, deterministic commit order, and the
no-mutation-on-rejection promise.
"""

import dataclasses as dc
import os
from pathlib import Path

import pytest

from lading.commands import bump, bump_toml
from lading.commands.bump_plan import (
    PlanBuilder,
    WriteCategory,
    changes_from_plan,
    commit_plan,
)
from lading.utils.confinement import (
    ConfinedPath,
    ConfineOptions,
    UnsafeBumpTargetError,
    confine_relative_path,
)
from lading.workspace import WorkspaceCrate, WorkspaceGraph
from tests.helpers.workspace_builders import _make_config, _make_workspace


def _confined(root: Path, relative: str) -> ConfinedPath:
    """Return a confined may-create path for ``relative`` beneath ``root``."""
    return confine_relative_path(
        root,
        relative,
        key="test",
        options=ConfineOptions(may_create=True),
    )


@dc.dataclass(frozen=True, slots=True)
class _WriteCall:
    """One recorded atomic write."""

    path: Path
    content: str


def test_plan_builder_composes_repeated_targets(tmp_path: Path) -> None:
    """A path targeted twice yields one write with both categories."""
    document = tmp_path / "README.md"
    document.write_text("initial\n", encoding="utf-8")
    root = tmp_path.resolve()
    path = _confined(root, "README.md")
    builder = PlanBuilder(root=root)

    builder.propose(path, "documentation\n", WriteCategory.DOCUMENTATION)
    builder.propose(path, "readme\n", WriteCategory.README)
    plan = builder.build(target_version="1.2.3", lockfiles=None)

    assert len(plan.proposals) == 1, "repeated targets must compose into one write"
    proposal = plan.proposals[0]
    assert proposal.content == "readme\n", (
        "the later proposal must supply the final content"
    )
    assert proposal.categories == frozenset({
        WriteCategory.DOCUMENTATION,
        WriteCategory.README,
    }), "both stages must remain visible in the change categories"


def test_plan_builder_skips_already_current_content(tmp_path: Path) -> None:
    """Proposing the current on-disk content records no write."""
    (tmp_path / "README.md").write_text("same\n", encoding="utf-8")
    root = tmp_path.resolve()
    builder = PlanBuilder(root=root)

    builder.propose(_confined(root, "README.md"), "same\n", WriteCategory.README)
    plan = builder.build(target_version="1.2.3", lockfiles=None)

    assert not plan.proposals, "an unchanged file must not appear in the plan"


def test_builder_read_honours_earlier_proposals(tmp_path: Path) -> None:
    """A later stage reads the earlier proposed content, not the disk state."""
    (tmp_path / "README.md").write_text("disk\n", encoding="utf-8")
    root = tmp_path.resolve()
    path = _confined(root, "README.md")
    builder = PlanBuilder(root=root)

    builder.propose(path, "proposed\n", WriteCategory.DOCUMENTATION)

    assert builder.read(path) == "proposed\n", (
        "later stages must compose from proposed content"
    )


def test_commit_plan_writes_in_deterministic_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commit order is root manifest, members, documentation, then README."""
    root = tmp_path.resolve()
    (root / "crates" / "beta").mkdir(parents=True)
    (root / "crates" / "alpha").mkdir()
    (root / "docs").mkdir()
    writes: list[_WriteCall] = []

    def record(path: Path, content: str) -> None:
        """Record the atomic write without touching the filesystem."""
        writes.append(_WriteCall(path=path, content=content))

    monkeypatch.setattr(bump_toml, "write_atomic_text", record)
    builder = PlanBuilder(root=root)
    builder.propose(
        _confined(root, "crates/beta/Cargo.toml"), "b", WriteCategory.MANIFEST
    )
    builder.propose(_confined(root, "README.md"), "r", WriteCategory.README)
    builder.propose(_confined(root, "Cargo.toml"), "root", WriteCategory.MANIFEST)
    builder.propose(_confined(root, "docs/guide.md"), "d", WriteCategory.DOCUMENTATION)
    builder.propose(
        _confined(root, "crates/alpha/Cargo.toml"), "a", WriteCategory.MANIFEST
    )

    commit_plan(builder.build(target_version="1.2.3", lockfiles=None))

    expected_order = [
        root / "Cargo.toml",
        root / "crates" / "alpha" / "Cargo.toml",
        root / "crates" / "beta" / "Cargo.toml",
        root / "docs" / "guide.md",
        root / "README.md",
    ]
    assert [call.path for call in writes] == expected_order, (
        "commit order must be deterministic across runs"
    )


def test_changes_from_plan_keeps_overlapping_categories(tmp_path: Path) -> None:
    """A path in both documentation and README appears in both report lists."""
    root = tmp_path.resolve()
    (root / "crates" / "alpha").mkdir(parents=True)
    path = _confined(root, "crates/alpha/README.md")
    builder = PlanBuilder(root=root)
    builder.propose(path, "a", WriteCategory.DOCUMENTATION)
    builder.propose(path, "b", WriteCategory.README)
    plan = builder.build(target_version="1.2.3", lockfiles=None)

    changes = changes_from_plan(plan)

    assert changes.documents == (path.path,), (
        "the documentation category must be reported"
    )
    assert changes.transposed_readmes == (path.path,), (
        "the README category must be reported for the same path"
    )


def test_root_member_manifest_is_written_once_with_merged_selectors(
    tmp_path: Path,
) -> None:
    """A root package that is also a member receives one combined write."""
    root_manifest = tmp_path / "Cargo.toml"
    root_manifest.write_text(
        '[package]\nname = "root"\nversion = "0.1.0"\n\n'
        '[workspace]\nmembers = ["root"]\n\n'
        '[workspace.package]\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    crate = WorkspaceCrate(
        name="root",
        version="0.1.0",
        manifest_path=root_manifest,
        root_path=tmp_path,
        publish=True,
        readme_is_workspace=False,
        dependencies=(),
    )
    options = bump.BumpOptions(
        rebuild_lockfiles=False,
        configuration=_make_config(),
        workspace=WorkspaceGraph(workspace_root=tmp_path, crates=(crate,)),
    )

    message = bump.run(tmp_path, "2.0.0", options=options)

    text = root_manifest.read_text(encoding="utf-8")
    assert text.count('version = "2.0.0"') == 2, (
        "the root package and workspace.package versions must both be updated once"
    )
    assert message == "Updated version to 2.0.0 in 1 manifest(s):\n- Cargo.toml", (
        "a root member must be reported as a single manifest change"
    )


def test_dry_run_and_live_report_the_same_changes(tmp_path: Path) -> None:
    """Dry and live runs compute the same change set."""
    dry_root = tmp_path / "dry"
    live_root = tmp_path / "live"
    messages: list[str] = []
    for root, is_dry in ((dry_root, True), (live_root, False)):
        root.mkdir()
        workspace = _make_workspace(root)
        messages.append(
            bump.run(
                root,
                "1.2.3",
                options=bump.BumpOptions(
                    dry_run=is_dry,
                    rebuild_lockfiles=False,
                    configuration=_make_config(),
                    workspace=workspace,
                ),
            )
        )

    dry_message, live_message = messages
    assert dry_message.startswith("Dry run; "), (
        "the dry run must identify itself in the summary"
    )
    assert (
        dry_message.removeprefix("Dry run; ").replace("would update", "Updated")
        == live_message
    ), "dry and live runs must report the same change set"
    dry_manifest = (dry_root / "Cargo.toml").read_text(encoding="utf-8")
    live_manifest = (live_root / "Cargo.toml").read_text(encoding="utf-8")
    assert "1.2.3" not in dry_manifest, "the dry run must not write the manifest"
    assert "1.2.3" in live_manifest, "the live run must write the manifest"


def test_rejected_plan_leaves_every_target_unchanged(tmp_path: Path) -> None:
    """A documentation glob escape aborts before any manifest write."""
    workspace_root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = _make_workspace(workspace_root)
    sentinel = outside / "README.md"
    sentinel.write_text("external\n", encoding="utf-8")
    configuration = _make_config(documentation_globs=("../outside/README.md",))
    before = {
        crate.manifest_path: crate.manifest_path.read_text(encoding="utf-8")
        for crate in workspace.crates
    }
    before[workspace_root / "Cargo.toml"] = (workspace_root / "Cargo.toml").read_text(
        encoding="utf-8"
    )

    with pytest.raises(UnsafeBumpTargetError):
        bump.run(
            workspace_root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=configuration,
                workspace=workspace,
            ),
        )

    for path, content in before.items():
        assert path.read_text(encoding="utf-8") == content, (
            f"preflight rejection must leave {path} unchanged"
        )
    assert sentinel.read_text(encoding="utf-8") == "external\n", (
        "the external sentinel must be untouched"
    )


def test_plan_builder_keys_by_filesystem_identity(tmp_path: Path) -> None:
    """Two names for one file compose as one proposal on any filesystem."""
    root = tmp_path.resolve()
    (root / "README.md").write_text("disk\n", encoding="utf-8")
    path = _confined(root, "README.md")
    try:
        os.link(root / "README.md", root / "readme.md")
    except OSError as exc:  # pragma: no cover - capability
        pytest.skip(f"hard links are unavailable here: {exc}")
    builder = PlanBuilder(root=root)

    builder.propose(path, "proposed\n", WriteCategory.README)

    alias = ConfinedPath(root=root, parts=("readme.md",))
    assert builder.read(alias) == "proposed\n", (
        "an alias of the same file must read the earlier proposal"
    )


def test_plan_builder_drops_a_proposal_that_restores_the_original(
    tmp_path: Path,
) -> None:
    """A later stage that restores the disk content removes the proposal."""
    document = tmp_path / "README.md"
    document.write_text("original\n", encoding="utf-8")
    root = tmp_path.resolve()
    path = _confined(root, "README.md")
    builder = PlanBuilder(root=root)

    builder.propose(path, "rewritten\n", WriteCategory.DOCUMENTATION)
    builder.propose(path, "original\n", WriteCategory.README)
    plan = builder.build(target_version="1.2.3", lockfiles=None)

    assert not plan.proposals, (
        "a proposal that restores the original file must not be written"
    )
    assert not builder.has_changes(WriteCategory.DOCUMENTATION), (
        "the restored path must not retain a change category"
    )


def test_distinct_missing_destinations_keep_distinct_identities(
    tmp_path: Path,
) -> None:
    """Two absent READMEs under different parents are separate writes."""
    root = tmp_path.resolve()
    (root / "crates" / "Foo").mkdir(parents=True)
    lower = root / "crates" / "foo"
    try:
        lower.mkdir()
    except FileExistsError:
        pytest.skip("the filesystem is case-insensitive")
    if lower.resolve() == (root / "crates" / "Foo").resolve():
        pytest.skip("the filesystem is case-insensitive")
    builder = PlanBuilder(root=root)

    builder.propose(
        _confined(root, "crates/Foo/README.md"), "upper\n", WriteCategory.README
    )
    builder.propose(
        _confined(root, "crates/foo/README.md"), "lower\n", WriteCategory.README
    )

    plan = builder.build(target_version="1.2.3", lockfiles=None)
    assert [proposal.path.display for proposal in plan.proposals] == [
        "crates/Foo/README.md",
        "crates/foo/README.md",
    ], "distinct missing destinations must remain distinct proposals"


def test_readme_report_order_is_sorted_by_path(tmp_path: Path) -> None:
    """The README report is sorted by path, not by commit rank."""
    root = tmp_path.resolve()
    for relative in ("crates/a", "crates/z"):
        (root / relative).mkdir(parents=True)
    builder = PlanBuilder(root=root)
    builder.propose(
        _confined(root, "crates/z/README.md"), "z\n", WriteCategory.DOCUMENTATION
    )
    builder.propose(
        _confined(root, "crates/z/README.md"), "z adapted\n", WriteCategory.README
    )
    builder.propose(
        _confined(root, "crates/a/README.md"), "a adopted\n", WriteCategory.README
    )
    plan = builder.build(target_version="1.2.3", lockfiles=None)

    changes = changes_from_plan(plan)

    assert [path.as_posix() for path in changes.transposed_readmes] == [
        (root / "crates/a/README.md").as_posix(),
        (root / "crates/z/README.md").as_posix(),
    ], "the README report must be sorted by path"
