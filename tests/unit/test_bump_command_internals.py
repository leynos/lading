"""Internal unit tests for the :mod:`lading.commands.bump` module internals."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import string
import typing as typ
from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings
from tomlkit import parse as parse_toml

from lading.workspace import WorkspaceCrate, WorkspaceDependency, WorkspaceGraph
from tests.helpers.workspace_builders import (
    _load_version,
    _make_config,
)

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion
from lading.commands import bump, bump_output


@dc.dataclass(frozen=True, slots=True)
class UpdateCrateTestParams:
    """Parameters for test_update_crate_manifest parametrized tests."""

    test_id: str
    dependency_spec: tuple[str, str]
    exclude_crates: tuple[str, ...]
    expected_package_version: str
    expected_alpha_version: str


def _make_test_crate_with_dependency(
    tmp_path: Path,
    *,
    crate_name: str = "beta",
    crate_version: str = "0.1.0",
    dependency: tuple[str, str] = ("alpha", "0.1.0"),
) -> WorkspaceCrate:
    """Create a test crate manifest with a single dependency.

    Args:
        tmp_path: Directory where the manifest will be created.
        crate_name: Name of the crate to create.
        crate_version: Version of the crate.
        dependency: Tuple of (dependency_name, dependency_version).

    """
    dependency_name, dependency_version = dependency
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        f"""
        [package]
        name = "{crate_name}"
        version = "{crate_version}"

        [dependencies]
        {dependency_name} = "{dependency_version}"
        """,
        encoding="utf-8",
    )
    return WorkspaceCrate(
        id=f"{crate_name}-id",
        name=crate_name,
        version=crate_version,
        manifest_path=manifest_path,
        root_path=manifest_path.parent,
        publish=True,
        readme_is_workspace=False,
        dependencies=(
            WorkspaceDependency(
                package_id=f"{dependency_name}-id",
                name=dependency_name,
                manifest_name=dependency_name,
                kind=None,
            ),
        ),
    )


def _make_workspace_with_alpha_dependency(
    tmp_path: Path,
    *,
    dependency: tuple[str, str] = ("alpha", "0.1.0"),
) -> tuple[WorkspaceCrate, WorkspaceGraph]:
    """Create a workspace with beta crate depending on alpha crate.

    Args:
        tmp_path: Directory where manifests will be created.
        dependency: Tuple of (dependency_name, dependency_version) for beta's
            dependency.

    Returns
    -------
        Tuple of (beta_crate, workspace_graph).

    """
    beta_crate = _make_test_crate_with_dependency(tmp_path, dependency=dependency)

    alpha_manifest = tmp_path / "alpha" / "Cargo.toml"
    alpha_manifest.parent.mkdir(parents=True, exist_ok=True)
    alpha_manifest.write_text(
        '[package]\nname = "alpha"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    alpha_crate = WorkspaceCrate(
        id="alpha-id",
        name="alpha",
        version="0.1.0",
        manifest_path=alpha_manifest,
        root_path=alpha_manifest.parent,
        publish=True,
        readme_is_workspace=False,
        dependencies=(),
    )

    workspace = WorkspaceGraph(
        workspace_root=tmp_path,
        crates=(beta_crate, alpha_crate),
    )

    return beta_crate, workspace


def _parse_manifest_versions(
    manifest_path: Path,
) -> tuple[str, str]:
    """Extract package version and alpha dependency version from manifest.

    Args:
        manifest_path: Path to the Cargo.toml manifest.

    Returns
    -------
        Tuple of (package_version, alpha_dependency_version).

    """
    document = parse_toml(manifest_path.read_text(encoding="utf-8"))
    package_version = document["package"]["version"]
    alpha_version = document["dependencies"]["alpha"].value
    return package_version, alpha_version


def test_determine_package_selectors_respects_exclusions() -> None:
    """Excluded crates produce no package selectors."""
    assert bump._determine_package_selectors("beta", {"beta"}) == ()


def test_determine_package_selectors_includes_package_for_active_crates() -> None:
    """Active crates receive the package selector tuple."""
    assert bump._determine_package_selectors("beta", set()) == (("package",),)


def test_should_skip_crate_update_requires_selectors_or_dependencies() -> None:
    """Skipping occurs only when both selectors and dependency sections are empty."""
    assert bump._should_skip_crate_update((), {}) is True
    assert (
        bump._should_skip_crate_update((("package",),), {"dependencies": ("alpha",)})
        is False
    )


def test_build_changes_description_counts_sections(tmp_path: Path) -> None:
    """Descriptions enumerate manifest and documentation counts."""
    changes = bump.BumpChanges(
        manifests=(tmp_path / "Cargo.toml", tmp_path / "member" / "Cargo.toml"),
        documents=(tmp_path / "README.md",),
        transposed_readmes=(tmp_path / "crates" / "alpha" / "README.md",),
    )
    assert (
        bump._build_changes_description(changes)
        == "2 manifest(s) and 1 documentation file(s) and 1 readme file(s)"
    )

    changes = bump.BumpChanges(
        manifests=(tmp_path / "Cargo.toml",),
        documents=(tmp_path / "README.md",),
        transposed_readmes=(tmp_path / "crates" / "alpha" / "README.md",),
        lockfiles=(tmp_path / "Cargo.lock",),
    )
    assert bump._build_changes_description(changes) == (
        "1 manifest(s) and 1 documentation file(s) and "
        "1 readme file(s) and 1 lockfile(s)"
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


@pytest.mark.parametrize(
    "params",
    [
        UpdateCrateTestParams(
            test_id="excluded_crate_skips_version_bump",
            dependency_spec=("alpha", "0.1.0"),
            exclude_crates=("beta",),
            expected_package_version="0.1.0",
            expected_alpha_version="1.2.3",
        ),
        UpdateCrateTestParams(
            test_id="updates_version_and_dependencies",
            dependency_spec=("alpha", "^0.1.0"),
            exclude_crates=(),
            expected_package_version="1.2.3",
            expected_alpha_version="^1.2.3",
        ),
    ],
    ids=lambda p: p.test_id,
)
def test_update_crate_manifest(tmp_path: Path, params: UpdateCrateTestParams) -> None:
    """Crate manifest updates handle exclusions and dependency rewrites."""
    crate, workspace = _make_workspace_with_alpha_dependency(
        tmp_path,
        dependency=params.dependency_spec,
    )
    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(
            configuration=_make_config(exclude=params.exclude_crates),
            workspace=workspace,
        ),
    )

    changed = bump._update_crate_manifest(
        crate,
        "1.2.3",
        context,
    )

    assert changed is True
    package_version, alpha_version = _parse_manifest_versions(crate.manifest_path)
    assert package_version == params.expected_package_version
    assert alpha_version == params.expected_alpha_version


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


def test_update_manifest_writes_when_changed(tmp_path: Path) -> None:
    """Applying a new version persists changes to disk."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n')
    changed = bump._update_manifest(
        manifest_path, (("package",),), "1.0.0", bump.BumpOptions()
    )
    assert changed is True
    assert _load_version(manifest_path, ("package",)) == "1.0.0"


def test_update_manifest_preserves_inline_comment(tmp_path: Path) -> None:
    """Inline comments survive manifest rewrites."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        '[package]\nversion = "0.1.0"  # keep me\n', encoding="utf-8"
    )
    changed = bump._update_manifest(
        manifest_path, (("package",),), "1.2.3", bump.BumpOptions()
    )
    assert changed is True
    text = manifest_path.read_text(encoding="utf-8")
    assert "# keep me" in text
    document = parse_toml(text)
    assert document["package"]["version"] == "1.2.3"


def test_update_manifest_skips_when_unchanged(tmp_path: Path) -> None:
    """No write occurs when the manifest already records the target version."""
    manifest_path = tmp_path / "Cargo.toml"
    original = '[package]\nname = "demo"\nversion = "0.1.0"\n'
    manifest_path.write_text(original)
    changed = bump._update_manifest(
        manifest_path, (("package",),), "0.1.0", bump.BumpOptions()
    )
    assert changed is False
    assert manifest_path.read_text() == original


def test_select_table_returns_nested_table() -> None:
    """Select nested tables using dotted selectors."""
    document = parse_toml('[workspace]\n[workspace.package]\nversion = "0.1.0"\n')
    table = bump._select_table(document, ("workspace", "package"))
    assert table is document["workspace"]["package"]


def test_select_table_returns_none_for_missing() -> None:
    """Selectors that do not resolve to tables return ``None``."""
    document = parse_toml("[workspace]\nmembers = []\n")
    table = bump._select_table(document, ("workspace", "package"))
    assert table is None


def test_assign_version_handles_absent_table() -> None:
    """``_assign_version`` tolerates missing tables."""
    assert bump._assign_version(None, "1.0.0") is False


def test_assign_version_updates_value() -> None:
    """Assign a new version when the stored value differs."""
    table = parse_toml('[package]\nname = "demo"\nversion = "0.1.0"\n')["package"]
    assert bump._assign_version(table, "2.0.0") is True
    assert table["version"] == "2.0.0"


def test_assign_version_detects_existing_value() -> None:
    """Return ``False`` when the version already matches."""
    table = parse_toml('[package]\nversion = "0.1.0"\n')["package"]
    assert bump._assign_version(table, "0.1.0") is False


def test_value_matches_accepts_plain_strings() -> None:
    """Strings compare directly when checking for version matches."""
    assert bump._value_matches("1.0.0", "1.0.0") is True
    assert bump._value_matches("1.0.0", "2.0.0") is False


def test_value_matches_handles_toml_items() -> None:
    """TOML items compare via their stored string value."""
    document = parse_toml('version = "3.0.0"')
    item = document["version"]
    assert bump._value_matches(item, "3.0.0") is True
    assert bump._value_matches(item, "4.0.0") is False


def test_select_table_handles_out_of_order_package() -> None:
    """Out-of-order tables (OutOfOrderTableProxy) are accepted."""
    # When [package.metadata.docs.rs] appears after other tables,
    # tomlkit returns an OutOfOrderTableProxy instead of a Table
    document = parse_toml(
        '[package]\nname = "x"\nversion = "0.1.0"\n'
        "[dependencies]\n"
        'foo = "1"\n'
        "[package.metadata.docs.rs]\n"
        "all-features = true\n"
    )
    table = bump._select_table(document, ("package",))
    assert table is not None
    assert table.get("version") == "0.1.0"


def test_assign_version_works_with_out_of_order_table() -> None:
    """Version assignment works with OutOfOrderTableProxy."""
    document = parse_toml(
        '[package]\nname = "x"\nversion = "0.1.0"\n'
        "[dependencies]\n"
        'foo = "1"\n'
        "[package.metadata.docs.rs]\n"
        "all-features = true\n"
    )
    table = bump._select_table(document, ("package",))
    assert bump._assign_version(table, "2.0.0") is True
    assert table.get("version") == "2.0.0"


def test_update_dependency_sections_with_workspace_flag() -> None:
    """The include_workspace_sections flag updates [workspace.dependencies]."""
    document = parse_toml(
        '[dependencies]\nalpha = "0.1.0"\n\n'
        '[workspace.dependencies]\nalpha = "^0.1.0"\n'
    )
    changed = bump._update_dependency_sections(
        document,
        {"dependencies": ("alpha",)},
        "1.0.0",
        include_workspace_sections=True,
    )
    assert changed is True
    assert document["dependencies"]["alpha"].value == "1.0.0"
    assert document["workspace"]["dependencies"]["alpha"].value == "^1.0.0"


def test_update_dependency_sections_without_workspace_flag() -> None:
    """Without the flag, [workspace.dependencies] is not updated."""
    document = parse_toml(
        '[dependencies]\nalpha = "0.1.0"\n\n'
        '[workspace.dependencies]\nalpha = "^0.1.0"\n'
    )
    changed = bump._update_dependency_sections(
        document,
        {"dependencies": ("alpha",)},
        "1.0.0",
        include_workspace_sections=False,
    )
    assert changed is True
    assert document["dependencies"]["alpha"].value == "1.0.0"
    # workspace.dependencies should remain unchanged
    assert document["workspace"]["dependencies"]["alpha"].value == "^0.1.0"


def test_update_dependency_sections_workspace_only() -> None:
    """When only workspace sections exist, they are updated with the flag."""
    document = parse_toml('[workspace.dependencies]\nalpha = "0.1.0"\n')
    changed = bump._update_dependency_sections(
        document,
        {"dependencies": ("alpha",)},
        "2.0.0",
        include_workspace_sections=True,
    )
    assert changed is True
    assert document["workspace"]["dependencies"]["alpha"].value == "2.0.0"


def test_update_dependency_sections_workspace_dev_and_build() -> None:
    """Workspace dev-dependencies and build-dependencies are updated."""
    document = parse_toml(
        '[workspace.dev-dependencies]\nalpha = "~0.1.0"\n\n'
        '[workspace.build-dependencies]\nbeta = { version = "0.1.0" }\n'
    )
    changed = bump._update_dependency_sections(
        document,
        {"dev-dependencies": ("alpha",), "build-dependencies": ("beta",)},
        "3.0.0",
        include_workspace_sections=True,
    )
    assert changed is True
    assert document["workspace"]["dev-dependencies"]["alpha"].value == "~3.0.0"
    build_deps = document["workspace"]["build-dependencies"]
    assert build_deps["beta"]["version"].value == "3.0.0"


# ---------------------------------------------------------------------------
# Crate-set derivation (issue #97)
# ---------------------------------------------------------------------------

_crate_name = st.text(
    alphabet=string.ascii_lowercase + string.digits + "-_",
    min_size=1,
    max_size=12,
)


_DependencyKind = typ.Literal["normal", "dev", "build"] | None
_DependencyEdge = tuple[str, _DependencyKind]
_DependencyEdges = cabc.Mapping[str, tuple[_DependencyEdge, ...]]


def _synthetic_workspace(
    names: cabc.Iterable[str],
    *,
    root: Path = Path("/ws"),
    dependencies: _DependencyEdges | None = None,
) -> WorkspaceGraph:
    """Build an in-memory workspace graph for the supplied crate names.

    ``dependencies`` maps a crate name to its outgoing edges, each a
    ``(target crate name, kind)`` pair where ``kind`` is ``None``/``"normal"``,
    ``"dev"``, or ``"build"``. Every edge becomes an (unaliased)
    :class:`WorkspaceDependency` attached to the dependent crate, so the graph
    exercises normal, dev, and build dependency-section routing. ``root``
    anchors every crate's ``manifest_path`` so callers can point the graph at a
    real temporary tree.
    """
    dependency_map = dependencies or {}
    crates = tuple(
        WorkspaceCrate(
            id=f"{name}-id",
            name=name,
            version="0.1.0",
            manifest_path=root / name / "Cargo.toml",
            root_path=root / name,
            publish=True,
            readme_is_workspace=False,
            dependencies=tuple(
                WorkspaceDependency(
                    package_id=f"{target}-id",
                    name=target,
                    manifest_name=target,
                    kind=kind,
                )
                for target, kind in dependency_map.get(name, ())
            ),
        )
        for name in names
    )
    return WorkspaceGraph(workspace_root=root, crates=crates)


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


_INITIAL_VERSION: typ.Final = "0.1.0"
_TARGET_VERSION: typ.Final = "1.2.3"

# The manifest sections a dependency ``kind`` routes to, mirroring Cargo's
# canonical vocabulary. Kept independent of the production
# ``_DEPENDENCY_SECTION_BY_KIND`` map so this test is a genuine reference.
_SECTION_BY_KIND: typ.Final[dict[_DependencyKind, str]] = {
    None: "dependencies",
    "normal": "dependencies",
    "dev": "dev-dependencies",
    "build": "build-dependencies",
}
_SECTION_ORDER: typ.Final = ("dependencies", "dev-dependencies", "build-dependencies")
_DEPENDENCY_KINDS: typ.Final[tuple[_DependencyKind, ...]] = (
    None,
    "normal",
    "dev",
    "build",
)


def _write_synthetic_manifests(
    root: Path,
    names: cabc.Iterable[str],
    dependencies: _DependencyEdges,
) -> None:
    """Write one ``Cargo.toml`` per crate under ``root`` matching the graph.

    Each manifest is seeded at ``_INITIAL_VERSION``; every dependency edge is
    written as a plain-string version requirement under the section its ``kind``
    selects (``[dependencies]``, ``[dev-dependencies]``, or
    ``[build-dependencies]``), mirroring the :class:`WorkspaceDependency`
    entries built by ``_synthetic_workspace``.
    """
    for name in names:
        crate_dir = root / name
        crate_dir.mkdir(parents=True, exist_ok=True)
        lines = ["[package]", f'name = "{name}"', f'version = "{_INITIAL_VERSION}"']
        by_section: dict[str, list[str]] = {}
        for target, kind in dependencies.get(name, ()):
            by_section.setdefault(_SECTION_BY_KIND[kind], []).append(target)
        for section in _SECTION_ORDER:
            targets = by_section.get(section)
            if not targets:
                continue
            lines += ["", f"[{section}]"]
            lines += [f'{target} = "{_INITIAL_VERSION}"' for target in sorted(targets)]
        crate_dir.joinpath("Cargo.toml").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )


def _read_manifest_versions(
    manifest_path: Path,
) -> tuple[str, dict[str, dict[str, str]]]:
    """Return the package version and per-section dependency versions.

    The second element maps each present dependency section to a ``{name:
    version}`` mapping, so callers can assert that a ``kind`` was routed to the
    correct manifest table.
    """
    document = parse_toml(manifest_path.read_text(encoding="utf-8"))
    package_version = str(document["package"]["version"])
    section_versions: dict[str, dict[str, str]] = {}
    for section in _SECTION_ORDER:
        table = document.get(section)
        if table is None:
            continue
        section_versions[section] = {
            key: str(value.value if hasattr(value, "value") else value)
            for key, value in table.items()
        }
    return package_version, section_versions


def _draw_dependency_edges(
    data: st.DataObject,
    names: cabc.Sequence[str],
    updated_pivot: str,
) -> dict[str, dict[str, _DependencyKind]]:
    """Draw a per-crate ``{target: kind}`` edge map for the workspace.

    Edges never self-reference, ``kind`` varies across normal/dev/build, and one
    edge is forced onto ``updated_pivot`` so the "depends on an updated crate"
    branch is covered on every example.
    """
    edges: dict[str, dict[str, _DependencyKind]] = {}
    for name in names:
        candidates = [other for other in names if other != name]
        targets = data.draw(
            st.sets(st.sampled_from(candidates), max_size=len(candidates)),
            label=f"deps[{name}]",
        )
        edges[name] = {
            target: data.draw(
                st.sampled_from(_DEPENDENCY_KINDS), label=f"kind[{name}->{target}]"
            )
            for target in sorted(targets)
        }
    dependent = data.draw(
        st.sampled_from([name for name in names if name != updated_pivot]),
        label="dependent",
    )
    if updated_pivot not in edges[dependent]:
        edges[dependent][updated_pivot] = data.draw(
            st.sampled_from(_DEPENDENCY_KINDS), label="dependent_kind"
        )
    return edges


def _assert_crate_manifest_update(
    crate: WorkspaceCrate,
    crate_edges: cabc.Mapping[str, _DependencyKind],
    context: bump._BumpContext,
    *,
    updated_names: cabc.Set[str],
    excluded: bool,
) -> None:
    """Apply the manifest update for ``crate`` and check it against reference."""
    depends_on_updated = any(target in updated_names for target in crate_edges)
    expect_skipped = excluded and not depends_on_updated

    outcome = bump._apply_crate_manifest_update(crate, _TARGET_VERSION, context)

    assert outcome.was_skipped is expect_skipped
    assert outcome.was_updated is (not expect_skipped)

    package_version, section_versions = _read_manifest_versions(crate.manifest_path)
    assert package_version == (_INITIAL_VERSION if excluded else _TARGET_VERSION)
    for target, kind in crate_edges.items():
        section = _SECTION_BY_KIND[kind]
        expected = _TARGET_VERSION if target in updated_names else _INITIAL_VERSION
        assert section_versions[section][target] == expected


@given(data=st.data())
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_manifest_selection_matches_naive_reference(
    tmp_path: Path,
    data: st.DataObject,
) -> None:
    """``_apply_crate_manifest_update`` honours the context crate sets.

    Reference semantics (issue #97): a non-excluded crate's package version is
    rewritten to the target; an excluded crate's package version is left
    untouched; a dependency edge is rewritten only when the crate it targets is
    itself being updated, and only in the manifest section its ``kind`` selects.
    The manifest pass must consume ``context.excluded`` and
    ``context.updated_crate_names`` — derived once in
    ``_initialize_bump_context`` — rather than re-deriving them per crate.

    ``tmp_path`` is a function-scoped fixture reused across Hypothesis
    examples; each example rewrites every manifest to its initial state before
    processing, so the shared directory cannot leak state between examples.
    """
    names = sorted(
        data.draw(st.sets(_crate_name, min_size=2, max_size=6), label="names")
    )

    # Force at least one updated (non-excluded) crate and at least one excluded
    # crate so every example exercises both branches of the skip rule.
    updated_pivot = data.draw(st.sampled_from(names), label="updated_pivot")
    other_names = [name for name in names if name != updated_pivot]
    excluded_pivot = data.draw(st.sampled_from(other_names), label="excluded_pivot")
    extra_excluded = data.draw(
        st.sets(st.sampled_from(other_names), max_size=len(other_names)),
        label="extra_excluded",
    )
    exclude_set = {excluded_pivot} | extra_excluded
    exclude = tuple(sorted(exclude_set))

    edges = _draw_dependency_edges(data, names, updated_pivot)
    dependencies: _DependencyEdges = {
        name: tuple(sorted(targets.items())) for name, targets in edges.items()
    }

    updated_names = {name for name in names if name not in exclude_set}
    assert exclude_set, "example must exclude at least one crate"
    assert any(
        target in updated_names for targets in edges.values() for target in targets
    ), "example must contain a dependency on an updated crate"

    _write_synthetic_manifests(tmp_path, names, dependencies)
    workspace = _synthetic_workspace(names, root=tmp_path, dependencies=dependencies)
    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(
            configuration=_make_config(exclude=exclude),
            workspace=workspace,
        ),
    )

    crates_by_name = {crate.name: crate for crate in workspace.crates}
    for name in names:
        _assert_crate_manifest_update(
            crates_by_name[name],
            edges[name],
            context,
            updated_names=updated_names,
            excluded=name in exclude_set,
        )
