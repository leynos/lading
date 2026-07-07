"""Property-based tests for bump crate-set derivation (issue #97).

These tests guard that the ``excluded`` and ``updated_crate_names`` sets are
derived once in :func:`lading.commands.bump._initialize_bump_context` and that
:func:`~lading.commands.bump._apply_crate_manifest_update` consumes them,
rather than recomputing selection per crate.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import string
import typing as typ
from pathlib import Path

import hypothesis.strategies as st
from hypothesis import HealthCheck, given, settings
from tomlkit import parse as parse_toml

from lading.commands import bump
from lading.workspace import WorkspaceCrate, WorkspaceDependency, WorkspaceGraph
from tests.helpers.workspace_builders import _make_config

_crate_name = st.text(
    alphabet=string.ascii_lowercase + string.digits + "-_",
    min_size=1,
    max_size=12,
)


_DependencyKind = typ.Literal["normal", "dev", "build"] | None
_DependencyEdge = tuple[str, _DependencyKind]
_DependencyEdges = cabc.Mapping[str, tuple[_DependencyEdge, ...]]


@dc.dataclass(frozen=True, slots=True)
class _ExpectedManifestOutcome:
    """Reference expectations for a single crate's manifest update.

    Attributes
    ----------
    updated_names : cabc.Set[str]
        Crate names the bump is expected to update (non-excluded members).
    excluded : bool
        Whether the crate under assertion is itself excluded from the bump.
    """

    updated_names: cabc.Set[str]
    excluded: bool


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
    expected: _ExpectedManifestOutcome,
) -> None:
    """Apply the manifest update for ``crate`` and check it against reference."""
    depends_on_updated = any(target in expected.updated_names for target in crate_edges)
    expect_skipped = expected.excluded and not depends_on_updated

    outcome = bump._apply_crate_manifest_update(crate, _TARGET_VERSION, context)

    assert outcome.was_skipped is expect_skipped
    assert outcome.was_updated is (not expect_skipped)

    package_version, section_versions = _read_manifest_versions(crate.manifest_path)
    assert package_version == (
        _INITIAL_VERSION if expected.excluded else _TARGET_VERSION
    )
    for target, kind in crate_edges.items():
        section = _SECTION_BY_KIND[kind]
        expected_version = (
            _TARGET_VERSION if target in expected.updated_names else _INITIAL_VERSION
        )
        assert section_versions[section][target] == expected_version


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
            _ExpectedManifestOutcome(
                updated_names=updated_names,
                excluded=name in exclude_set,
            ),
        )
