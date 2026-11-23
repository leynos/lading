"""Tests for configured non-member manifest rewrites."""

import collections.abc as cabc
import pathlib
import typing as typ

import pytest
from tomlkit import parse as parse_toml

from lading.commands import bump_manifest_rewrites
from lading.config import ManifestRewriteConfig, StringValueRewriteConfig
from tests.helpers.manifest_rewrites import (
    manifest_rewrite_versions,
    plan_fixture_rewrites,
)

type _ManifestWriter = cabc.Callable[[pathlib.Path, str, str], pathlib.Path]

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def _rstest_bdd_dependency_plan(
    root: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> bump_manifest_rewrites.ManifestRewritePlan:
    """Build a rewrite plan for a multi-section prerelease manifest."""
    write_fixture_manifest(
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
    return plan_fixture_rewrites(
        root,
        (ManifestRewriteConfig(paths=("fixtures/standalone/Cargo.toml",)),),
        versions=manifest_rewrite_versions(
            names=frozenset({"rstest-bdd", "rstest-bdd-harness"}),
            pre_bump_versions={
                "rstest-bdd": "0.6.0-beta4",
                "rstest-bdd-harness": "0.6.0-beta4",
            },
            target_version="0.6.0",
        ),
    )[0]


def test_updates_dependency_sections_and_package_aliases(
    tmp_path: pathlib.Path,
    snapshot: SnapshotAssertion,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Dependency keys, package aliases, target, and workspace tables update."""
    plan = _rstest_bdd_dependency_plan(tmp_path, write_fixture_manifest)
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
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Path-only, inherited, unrelated, and patch entries remain untouched."""
    document = parse_toml(
        _rstest_bdd_dependency_plan(tmp_path, write_fixture_manifest).new_text
    )
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
    tmp_path: pathlib.Path,
    snapshot: SnapshotAssertion,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Template replacement respects crate/version boundaries and TOML trivia."""
    manifest = write_fixture_manifest(
        tmp_path,
        "tests/fixtures/Cargo.toml",
        """[patch.crates-io]
rstest-bdd = { path = '../../../target/rstest-bdd-0.6.0-beta4' } # keep comment
rstest-bdd-harness = { path = '../../../target/rstest-bdd-harness-0.6.0-beta4' }
rstest-bdd-harness-gpui = { path = 'target/rstest-bdd-harness-gpui-0.6.0-beta4' }
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

    (plan,) = plan_fixture_rewrites(
        tmp_path,
        (group,),
        versions=manifest_rewrite_versions(
            names=names, pre_bump_versions=versions, target_version="0.6.0"
        ),
    )

    assert plan.path == manifest.resolve(), (
        "the rewrite plan must identify its manifest"
    )
    snapshot_text = plan.new_text.replace("../../../target/", "$STAGING_")
    snapshot_text = snapshot_text.replace("'target/", "'$STAGING_")
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
        "target/rstest-bdd-harness-gpui-0.6.0"
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


def test_rejects_ambiguous_string_template_replacements(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Colliding old values cannot select one crate's new value arbitrarily."""
    manifest = write_fixture_manifest(
        tmp_path, "fixture/Cargo.toml", '[metadata]\nartifact = "a12.0.0"\n'
    )
    group = ManifestRewriteConfig(
        paths=("fixture/Cargo.toml",),
        dependencies=False,
        string_values=(
            StringValueRewriteConfig(table=("metadata",), template="{crate}{version}"),
        ),
    )
    versions = manifest_rewrite_versions(
        names=frozenset({"a", "a1"}),
        pre_bump_versions={"a": "12.0.0", "a1": "2.0.0"},
        target_version="3.0.0",
    )

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="different replacements",
    ) as failure:
        plan_fixture_rewrites(tmp_path, (group,), versions=versions)

    assert failure.value.failed_path == manifest.resolve(), (
        "the planning error should identify the affected manifest"
    )
    assert (
        manifest.read_text(encoding="utf-8") == '[metadata]\nartifact = "a12.0.0"\n'
    ), "a rejected replacement plan must not write the manifest"


def test_direct_string_values_and_missing_selectors_are_noops(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Rules without a field visit direct values and absent selectors do nothing."""
    manifest = write_fixture_manifest(
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

    (plan,) = plan_fixture_rewrites(tmp_path, groups)

    assert 'artifact = "alpha-2.0.0"' in plan.new_text, (
        "direct string values must be rewritten when no field is selected"
    )
    assert "number = 42" in plan.new_text, "non-string values must remain unchanged"
    assert manifest.read_text(encoding="utf-8") == plan.original_text, (
        "planning must not write the selected manifest"
    )


def test_rewrites_direct_and_nested_inline_string_tables(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Selected inline tables support direct and field-based string rewrites."""
    write_fixture_manifest(
        tmp_path,
        "fixture/Cargo.toml",
        '[package]\nname = "fixture"\n'
        'metadata = { artifact = "alpha-1.0.0" }\n'
        '[custom]\nvalues = { artifact = "alpha-1.0.0" }\n',
    )
    group = ManifestRewriteConfig(
        paths=("fixture/Cargo.toml",),
        dependencies=False,
        string_values=(
            StringValueRewriteConfig(table=("package", "metadata")),
            StringValueRewriteConfig(table=("custom",), field="artifact"),
        ),
    )

    (plan,) = plan_fixture_rewrites(tmp_path, (group,))

    document = parse_toml(plan.new_text)
    assert document["package"]["metadata"]["artifact"] == "alpha-2.0.0", (
        "a selector ending at an inline table must rewrite its direct strings"
    )
    assert document["custom"]["values"]["artifact"] == "alpha-2.0.0", (
        "field selection must visit inline sub-tables"
    )


def test_planner_rewrites_supplied_text_without_filesystem_access() -> None:
    """The rewrite policy consumes source text without opening its path."""
    source = bump_manifest_rewrites.ManifestRewriteSource(
        path=pathlib.Path("unopened/Cargo.toml"),
        content='[dependencies]\nalpha = "1.0.0"\n',
        dependencies=True,
        string_values=(),
    )

    (plan,) = bump_manifest_rewrites.plan_manifest_rewrites(
        (source,), manifest_rewrite_versions()
    )

    assert plan.path == pathlib.Path("unopened/Cargo.toml"), (
        "the planner should preserve the supplied source identity"
    )
    assert plan.new_text == '[dependencies]\nalpha = "2.0.0"\n', (
        "the planner should use only supplied text and version metadata"
    )
