"""Tests for configured non-member manifest rewrites."""

import pathlib
import typing as typ

import pytest
from tomlkit import parse as parse_toml

from lading.commands import bump_manifest_rewrites
from lading.config import ManifestRewriteConfig, StringValueRewriteConfig

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def _write_manifest(
    root: pathlib.Path, relative_path: str, content: str
) -> pathlib.Path:
    """Write a fixture manifest and return its path."""
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _versions(
    names: frozenset[str] = frozenset({"alpha"}),
    pre_bump_versions: dict[str, str] | None = None,
    target_version: str = "2.0.0",
) -> bump_manifest_rewrites.ManifestRewriteVersions:
    """Build a compact version context for rewrite tests."""
    return bump_manifest_rewrites.ManifestRewriteVersions(
        updated_crate_names=names,
        pre_bump_versions=(
            pre_bump_versions
            if pre_bump_versions is not None
            else dict.fromkeys(names, "1.0.0")
        ),
        target_version=target_version,
    )


def _plan(
    root: pathlib.Path,
    groups: tuple[ManifestRewriteConfig, ...],
    versions: bump_manifest_rewrites.ManifestRewriteVersions | None = None,
    members: tuple[pathlib.Path, ...] = (),
) -> tuple[bump_manifest_rewrites.ManifestRewritePlan, ...]:
    """Plan fixture changes using a compact default workspace context."""
    return bump_manifest_rewrites.plan_manifest_rewrites(
        root,
        groups,
        members,
        versions if versions is not None else _versions(),
    )


def test_resolves_globs_deduplicates_and_merges_groups(tmp_path: pathlib.Path) -> None:
    """Glob matches are sorted and overlapping groups merge their rules."""
    root = tmp_path
    _write_manifest(root, "Cargo.toml", "[workspace]\nmembers = []\n")
    first = _write_manifest(
        root,
        "fixtures/a/Cargo.toml",
        '[dependencies]\nalpha = "^1.0.0"\n[metadata]\npath = "alpha-1.0.0"\n',
    )
    second = _write_manifest(
        root, "fixtures/b/Cargo.toml", '[dependencies]\nalpha = "1.0.0"\n'
    )
    groups = (
        ManifestRewriteConfig(paths=("fixtures/*/Cargo.toml",)),
        ManifestRewriteConfig(
            paths=("fixtures/a/Cargo.toml",),
            dependencies=False,
            string_values=(StringValueRewriteConfig(table=("metadata",)),),
        ),
    )

    plans = _plan(root, groups)

    assert [plan.path for plan in plans] == [first.resolve(), second.resolve()], (
        "glob expansion must de-duplicate paths and preserve sorted candidates"
    )
    assert 'alpha = "^2.0.0"' in plans[0].new_text, (
        "the first selected dependency must use the target version"
    )
    assert 'path = "alpha-2.0.0"' in plans[0].new_text, (
        "the selected string value must use the target version"
    )
    assert 'alpha = "2.0.0"' in plans[1].new_text, (
        "the second glob match must receive dependency rewrites"
    )


@pytest.mark.parametrize(
    ("configured_path", "message"),
    [
        ("missing/Cargo.toml", "regular file"),
        ("missing/*.toml", "matched no files"),
        ("fixtures/not-a-manifest.toml", "Cargo.toml file"),
        ("../outside/Cargo.toml", "stay within the workspace"),
    ],
)
def test_rejects_invalid_configured_paths(
    tmp_path: pathlib.Path,
    configured_path: str,
    message: str,
) -> None:
    """Missing, unmatched, malformed, and escaping paths fail during planning."""
    group = ManifestRewriteConfig(paths=(configured_path,))

    with pytest.raises(bump_manifest_rewrites.ManifestRewriteError, match=message):
        _plan(tmp_path, (group,))


def test_rejects_glob_matches_that_are_not_regular_files(
    tmp_path: pathlib.Path,
) -> None:
    """A directory named Cargo.toml cannot pass candidate validation."""
    (tmp_path / "fixtures" / "Cargo.toml").mkdir(parents=True)
    group = ManifestRewriteConfig(paths=("fixtures/**/Cargo.toml",))

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError, match="regular file"
    ):
        _plan(tmp_path, (group,))


def test_skips_workspace_and_member_manifests(tmp_path: pathlib.Path) -> None:
    """Configured root and member paths remain owned by the regular stages."""
    root_manifest = _write_manifest(
        tmp_path, "Cargo.toml", '[dependencies]\nalpha = "1.0.0"\n'
    )
    member_manifest = _write_manifest(
        tmp_path, "crates/alpha/Cargo.toml", '[dependencies]\nalpha = "1.0.0"\n'
    )
    group = ManifestRewriteConfig(paths=("Cargo.toml", "crates/alpha/Cargo.toml"))

    plans = _plan(tmp_path, (group,), members=(member_manifest,))

    assert not plans, "workspace and member manifests must be skipped"
    assert root_manifest.read_text(encoding="utf-8").endswith('alpha = "1.0.0"\n'), (
        "the rewrite planner must leave the root manifest to its owner stage"
    )


def test_skips_missing_member_manifest_before_file_validation(
    tmp_path: pathlib.Path,
) -> None:
    """Configured member paths remain skipped even if the file is absent."""
    member_manifest = tmp_path / "crates/alpha/Cargo.toml"
    group = ManifestRewriteConfig(paths=("crates/alpha/Cargo.toml",))

    plans = _plan(tmp_path, (group,), members=(member_manifest,))

    assert not plans, "missing member manifests must be skipped before validation"


def _rstest_bdd_dependency_plan(
    root: pathlib.Path,
) -> bump_manifest_rewrites.ManifestRewritePlan:
    """Build a rewrite plan for a multi-section prerelease manifest."""
    _write_manifest(
        root,
        "fixtures/standalone/Cargo.toml",
        """[dependencies]
rstest-bdd = "^0.6.0-beta4"
renamed = { package = "rstest-bdd-harness", version = "~0.6.0-beta4" }
path-only = { package = "rstest-bdd", path = "../rstest-bdd" }
workspace-ref = { package = "rstest-bdd", version = "0.6.0-beta4", workspace = true }
unrelated = "0.6.0-beta4"

[dev-dependencies]
rstest-bdd = "0.6.0-beta4"

[build-dependencies]
rstest-bdd-harness = "0.6.0-beta4"

[target.'cfg(unix)'.dependencies]
rstest-bdd = "0.6.0-beta4"

[target.x86_64_unknown_linux_gnu.dev-dependencies]
rstest-bdd-harness = "0.6.0-beta4"

[workspace.dependencies]
rstest-bdd = "0.6.0-beta4"

[patch.crates-io]
rstest-bdd = { version = "0.6.0-beta4", path = "../patched" }

[replace]
"rstest-bdd:0.1.0" = { path = "../replaced" }
""",
    )
    return _plan(
        root,
        (ManifestRewriteConfig(paths=("fixtures/standalone/Cargo.toml",)),),
        versions=_versions(
            names=frozenset({"rstest-bdd", "rstest-bdd-harness"}),
            pre_bump_versions={
                "rstest-bdd": "0.6.0-beta4",
                "rstest-bdd-harness": "0.6.0-beta4",
            },
            target_version="0.6.0",
        ),
    )[0]


def test_updates_dependency_sections_and_package_aliases(
    tmp_path: pathlib.Path, snapshot: SnapshotAssertion
) -> None:
    """Dependency keys, package aliases, target, and workspace tables update."""
    plan = _rstest_bdd_dependency_plan(tmp_path)
    assert plan.new_text == snapshot(name="dependency_rewrites"), (
        "the rendered dependency manifest must match its focused snapshot"
    )
    document = parse_toml(plan.new_text)
    assert document["dependencies"]["rstest-bdd"] == "^0.6.0", (
        "the main dependency requirement must retain its compatible operator"
    )
    assert document["dependencies"]["renamed"]["version"] == "~0.6.0", (
        "an aliased dependency must retain its compatible operator"
    )
    assert document["dev-dependencies"]["rstest-bdd"] == "0.6.0", (
        "the development dependency section must be rewritten"
    )
    assert document["target"]["cfg(unix)"]["dependencies"]["rstest-bdd"] == "0.6.0", (
        "the target dependency section must be rewritten"
    )
    assert document["build-dependencies"]["rstest-bdd-harness"] == "0.6.0", (
        "the harness build dependency must be rewritten"
    )
    assert (
        document["target"]["x86_64_unknown_linux_gnu"]["dev-dependencies"][
            "rstest-bdd-harness"
        ]
        == "0.6.0"
    ), "the harness target dependency must be rewritten"
    assert document["workspace"]["dependencies"]["rstest-bdd"] == "0.6.0", (
        "the workspace dependency section must be rewritten"
    )


def test_leaves_unsupported_dependency_entries_unchanged(
    tmp_path: pathlib.Path,
) -> None:
    """Path-only, inherited, unrelated, and patch entries remain untouched."""
    document = parse_toml(_rstest_bdd_dependency_plan(tmp_path).new_text)
    assert document["dependencies"]["path-only"]["path"] == "../rstest-bdd", (
        "path-only dependencies must remain unchanged"
    )
    assert document["dependencies"]["workspace-ref"]["version"] == "0.6.0-beta4", (
        "workspace-inherited dependency versions must remain unchanged"
    )
    assert document["dependencies"]["unrelated"] == "0.6.0-beta4", (
        "dependencies for crates outside the updated workspace must remain unchanged"
    )
    assert document["patch"]["crates-io"]["rstest-bdd"]["version"] == "0.6.0-beta4", (
        "path dependencies must remain unchanged"
    )
    assert document["replace"]["rstest-bdd:0.1.0"]["path"] == "../replaced", (
        "replace entries must remain untouched"
    )


def test_rewrites_selected_strings_once_and_preserves_literal_style(
    tmp_path: pathlib.Path, snapshot: SnapshotAssertion
) -> None:
    """Template replacement respects crate/version boundaries and TOML trivia."""
    manifest = _write_manifest(
        tmp_path,
        "tests/fixtures/Cargo.toml",
        """[patch.crates-io]
rstest-bdd = { path = '../../../target/rstest-bdd-0.6.0-beta4' } # keep comment
rstest-bdd-harness = { path = '../../../target/rstest-bdd-harness-0.6.0-beta4' }
rstest-bdd-harness-gpui = {
    path = '../../../target/rstest-bdd-harness-gpui-0.6.0-beta4'
}
longer-version = { path = '../../../target/rstest-bdd-0.6.0-beta40' }
x-prefixed = { path = '../../../target/xrstest-bdd-0.6.0-beta4' }
literal = { path = '../../../target/rstest-bdd-0.6.0-beta4' }
""",
    )
    group = ManifestRewriteConfig(
        paths=("tests/fixtures/Cargo.toml",),
        dependencies=False,
        string_values=(
            StringValueRewriteConfig(table=("patch", "crates-io"), field="path"),
        ),
    )
    names = frozenset({"rstest-bdd", "rstest-bdd-harness", "rstest-bdd-harness-gpui"})
    versions = dict.fromkeys(names, "0.6.0-beta4")

    (plan,) = _plan(
        tmp_path,
        (group,),
        versions=_versions(
            names=names, pre_bump_versions=versions, target_version="0.6.0"
        ),
    )

    assert plan.path == manifest.resolve(), (
        "the rewrite plan must identify its manifest"
    )
    snapshot_text = plan.new_text.replace("../../../target/", "$STAGING_")
    assert snapshot_text == snapshot(name="selected_string_rewrites"), (
        "the rendered manifest must preserve its selected string contract"
    )
    document = parse_toml(plan.new_text)
    patch_paths = {
        key: value["path"] for key, value in document["patch"]["crates-io"].items()
    }
    assert patch_paths["rstest-bdd"] == "../../../target/rstest-bdd-0.6.0", (
        "the main crate path must receive the new version"
    )
    assert patch_paths["rstest-bdd-harness"] == (
        "../../../target/rstest-bdd-harness-0.6.0"
    ), "the harness path must receive the new version"
    assert patch_paths["rstest-bdd-harness-gpui"] == (
        "../../../target/rstest-bdd-harness-gpui-0.6.0"
    ), "the longer crate prefix must receive the new version"
    assert patch_paths["longer-version"] == (
        "../../../target/rstest-bdd-0.6.0-beta40"
    ), "a longer prerelease version must not be partially replaced"
    assert patch_paths["x-prefixed"] == "../../../target/xrstest-bdd-0.6.0-beta4", (
        "a crate-name character before the match must prevent replacement"
    )
    assert patch_paths["literal"] == "../../../target/rstest-bdd-0.6.0", (
        "the literal path value must receive the new version"
    )


def test_direct_string_values_and_missing_selectors_are_noops(
    tmp_path: pathlib.Path,
) -> None:
    """Rules without a field visit direct values and absent selectors do nothing."""
    manifest = _write_manifest(
        tmp_path,
        "fixture/Cargo.toml",
        '[metadata]\nartifact = "alpha-1.0.0"\nnumber = 42\n',
    )
    groups = (
        ManifestRewriteConfig(
            paths=("fixture/Cargo.toml",),
            dependencies=False,
            string_values=(StringValueRewriteConfig(table=("metadata",)),),
        ),
        ManifestRewriteConfig(
            paths=("fixture/Cargo.toml",),
            dependencies=False,
            string_values=(StringValueRewriteConfig(table=("missing",)),),
        ),
    )

    (plan,) = _plan(tmp_path, groups)

    assert 'artifact = "alpha-2.0.0"' in plan.new_text, (
        "direct string values must be rewritten when no field is selected"
    )
    assert "number = 42" in plan.new_text, "non-string values must remain unchanged"
    assert manifest.read_text(encoding="utf-8") == plan.original_text, (
        "planning must not write the selected manifest"
    )


def test_unchanged_manifest_is_omitted_and_dry_run_does_not_write(
    tmp_path: pathlib.Path,
) -> None:
    """No-op plans are empty and applying a plan in dry-run preserves bytes."""
    content = '[dependencies]\nalpha = "2.0.0"\n'
    manifest = _write_manifest(tmp_path, "fixture/Cargo.toml", content)
    group = ManifestRewriteConfig(paths=("fixture/Cargo.toml",))

    assert not _plan(tmp_path, (group,), _versions(target_version="2.0.0")), (
        "an unchanged target version must produce no rewrite plan"
    )
    plans = _plan(tmp_path, (group,), _versions(target_version="3.0.0"))
    original_bytes = manifest.read_bytes()

    changed_paths = bump_manifest_rewrites.apply_manifest_rewrites(plans, dry_run=True)

    assert changed_paths == (manifest.resolve(),), (
        "dry-run must return the planned manifest path"
    )
    assert manifest.read_bytes() == original_bytes, (
        "dry-run must preserve manifest bytes"
    )
