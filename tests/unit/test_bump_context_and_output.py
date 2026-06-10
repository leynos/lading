"""Unit tests for bump context derivation and result-message formatting."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import hypothesis.strategies as st
from hypothesis import given

from lading.commands import bump, bump_output
from tests.helpers.bump_builders import _crate_name, _synthetic_workspace
from tests.helpers.workspace_builders import _make_config

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def test_build_changes_description_counts_sections(tmp_path: Path) -> None:
    """Descriptions enumerate manifest and documentation counts."""
    changes = bump.BumpChanges(
        manifests=(tmp_path / "Cargo.toml", tmp_path / "member" / "Cargo.toml"),
        documents=(tmp_path / "README.md",),
        transposed_readmes=(tmp_path / "crates" / "alpha" / "README.md",),
    )
    assert (
        bump_output._build_changes_description(changes)
        == "2 manifest(s), 1 documentation file(s), and 1 readme file(s)"
    )

    changes = bump.BumpChanges(
        manifests=(tmp_path / "Cargo.toml",),
        documents=(tmp_path / "README.md",),
        transposed_readmes=(tmp_path / "crates" / "alpha" / "README.md",),
        lockfiles=(tmp_path / "Cargo.lock",),
    )
    assert bump_output._build_changes_description(changes) == (
        "1 manifest(s), 1 documentation file(s), 1 readme file(s), and 1 lockfile(s)"
    )


def test_format_no_changes_message_mentions_dry_run() -> None:
    """No-change messaging adapts to dry-run context."""
    assert (
        bump_output._format_no_changes_message("1.2.3", dry_run=False)
        == "No manifest changes required; all versions already 1.2.3."
    )
    assert (
        bump_output._format_no_changes_message("1.2.3", dry_run=True)
        == "Dry run; no manifest changes required; all versions already 1.2.3."
    )


def test_format_header_labels_dry_run_requests() -> None:
    """Headers record whether the bump would be applied or was applied."""
    assert (
        bump_output._format_header("1 manifest(s)", "2.0.0", dry_run=False)
        == "Updated version to 2.0.0 in 1 manifest(s):"
    )
    assert (
        bump_output._format_header("1 manifest(s)", "2.0.0", dry_run=True)
        == "Dry run; would update version to 2.0.0 in 1 manifest(s):"
    )


def test_format_manifest_path_relative(tmp_path: Path) -> None:
    """Paths inside the workspace root are displayed relative to it."""
    workspace_root = tmp_path
    manifest_path = workspace_root / "Cargo.toml"
    manifest_path.write_text("", encoding="utf-8")
    assert (
        bump_output._format_manifest_path(manifest_path, workspace_root) == "Cargo.toml"
    )


def test_format_manifest_path_outside_workspace(tmp_path: Path) -> None:
    """Paths outside the workspace remain absolute for clarity."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest_path = tmp_path / "external" / "Cargo.toml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("", encoding="utf-8")
    assert bump_output._format_manifest_path(manifest_path, workspace_root) == str(
        manifest_path
    )


def test_format_result_message_handles_changes(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """The formatted result message reflects manifest counts and paths."""
    workspace_root = tmp_path
    manifest_paths = [
        workspace_root / "Cargo.toml",
        workspace_root / "member" / "Cargo.toml",
    ]
    documentation_paths = [workspace_root / "README.md"]
    readme_paths = [workspace_root / "crates" / "alpha" / "README.md"]
    assert (
        bump._format_result_message(
            bump.BumpChanges(),
            "1.2.3",
            dry_run=False,
            workspace_root=workspace_root,
        )
        == "No manifest changes required; all versions already 1.2.3."
    )
    assert bump._format_result_message(
        bump.BumpChanges(manifests=manifest_paths),
        "4.5.6",
        dry_run=False,
        workspace_root=workspace_root,
    ).splitlines() == snapshot(name="manifests_live")
    assert bump._format_result_message(
        bump.BumpChanges(manifests=manifest_paths),
        "4.5.6",
        dry_run=True,
        workspace_root=workspace_root,
    ).splitlines() == snapshot(name="manifests_dry_run")
    assert bump._format_result_message(
        bump.BumpChanges(
            manifests=manifest_paths,
            documents=documentation_paths,
        ),
        "7.8.9",
        dry_run=False,
        workspace_root=workspace_root,
    ).splitlines() == snapshot(name="manifests_and_docs")
    assert bump._format_result_message(
        bump.BumpChanges(
            manifests=manifest_paths,
            documents=documentation_paths,
            transposed_readmes=readme_paths,
        ),
        "7.8.9",
        dry_run=False,
        workspace_root=workspace_root,
    ).splitlines() == snapshot(name="all_changes")


def test_format_result_message_handles_readme_only_changes(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """README-only changes are reported as updates, not as no-ops."""
    workspace_root = tmp_path
    readme_paths = [workspace_root / "crates" / "alpha" / "README.md"]

    assert bump._format_result_message(
        bump.BumpChanges(transposed_readmes=readme_paths),
        "1.2.3",
        dry_run=False,
        workspace_root=workspace_root,
    ).splitlines() == snapshot(name="readme_only_live")


@given(
    names=st.sets(_crate_name, min_size=0, max_size=8),
    extra_excludes=st.sets(_crate_name, max_size=4),
    data=st.data(),
)
def test_bump_context_crate_sets_match_naive_derivation(
    names: set[str],
    extra_excludes: set[str],
    data: st.DataObject,
) -> None:
    """Context crate sets equal a reference computation for any workspace.

    The sets are derived once in ``_initialize_bump_context``; this guards
    behaviour parity through the issue #97 refactor that removed the
    per-crate recomputation.
    """
    excluded_members = data.draw(
        st.sets(st.sampled_from(sorted(names)), max_size=len(names))
        if names
        else st.just(set())
    )
    exclude = tuple(sorted(excluded_members | extra_excludes))
    workspace = _synthetic_workspace(sorted(names))

    context = bump._initialize_bump_context(
        Path("/ws"),
        bump.BumpOptions(
            configuration=_make_config(exclude=exclude),
            workspace=workspace,
        ),
    )

    assert context.excluded == frozenset(exclude)
    assert context.updated_crate_names == frozenset(
        crate.name for crate in workspace.crates if crate.name not in set(exclude)
    )


# ---------------------------------------------------------------------------
# Canonical bump_output formatting (issue #95)
# ---------------------------------------------------------------------------

_category_count = st.integers(min_value=0, max_value=3)


def _expected_description(categories: list[str]) -> str:
    """Return the reference Oxford-comma joining for ``categories``."""
    if len(categories) == 1:
        return categories[0]
    if len(categories) == 2:
        return " and ".join(categories)
    return f"{', '.join(categories[:-1])}, and {categories[-1]}"


@given(
    manifest_count=_category_count,
    document_count=_category_count,
    readme_count=_category_count,
    lockfile_count=_category_count,
)
def test_result_message_grammar_and_path_rendering(
    manifest_count: int,
    document_count: int,
    readme_count: int,
    lockfile_count: int,
) -> None:
    """Any category combination renders correct grammar and unique paths."""
    root = Path("/ws")
    manifests = tuple(root / f"m{i}" / "Cargo.toml" for i in range(manifest_count))
    documents = tuple(root / f"doc{i}.md" for i in range(document_count))
    readmes = tuple(root / f"r{i}" / "README.md" for i in range(readme_count))
    lockfiles = tuple(root / f"l{i}" / "Cargo.lock" for i in range(lockfile_count))
    changes = bump_output.BumpChanges(
        manifests=manifests,
        documents=documents,
        lockfiles=lockfiles,
        transposed_readmes=readmes,
    )

    message = bump_output._format_result_message(
        changes, "1.2.3", dry_run=False, workspace_root=root
    )

    if not (manifests or documents or readmes or lockfiles):
        assert message == "No manifest changes required; all versions already 1.2.3."
        return

    lines = message.splitlines()
    expected_body = [
        *(f"- {path.relative_to(root)}" for path in manifests),
        *(f"- {path.relative_to(root)} (documentation)" for path in documents),
        *(f"- {path.relative_to(root)} (readme)" for path in readmes),
        *(f"- {path.relative_to(root)} (lockfile)" for path in lockfiles),
    ]
    assert lines[1:] == expected_body

    categories = []
    if manifests:
        categories.append(f"{len(manifests)} manifest(s)")
    if documents:
        categories.append(f"{len(documents)} documentation file(s)")
    if readmes:
        categories.append(f"{len(readmes)} readme file(s)")
    if lockfiles:
        categories.append(f"{len(lockfiles)} lockfile(s)")
    expected_header = (
        f"Updated version to 1.2.3 in {_expected_description(categories)}:"
    )
    assert lines[0] == expected_header
    assert " and and " not in lines[0]
    if len(categories) >= 3:
        assert ", and " in lines[0]


def test_format_result_message_four_categories_snapshot(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """All four categories render with Oxford-comma grammar (dry-run and live)."""
    root = tmp_path
    changes = bump_output.BumpChanges(
        manifests=(root / "Cargo.toml",),
        documents=(root / "README.md",),
        lockfiles=(root / "Cargo.lock",),
        transposed_readmes=(root / "crates" / "alpha" / "README.md",),
    )

    live = bump_output._format_result_message(
        changes, "2.0.0", dry_run=False, workspace_root=root
    )
    dry = bump_output._format_result_message(
        changes, "2.0.0", dry_run=True, workspace_root=root
    )

    assert snapshot(name="live") == live.splitlines()
    assert snapshot(name="dry_run") == dry.splitlines()
