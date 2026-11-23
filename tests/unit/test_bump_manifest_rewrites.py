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


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(("2.0.0+1.0.0", 1, None), id="already-target"),
        pytest.param(("1.0.0", 2, "2.0.0+1.0.0"), id="duplicate-selectors"),
    ],
)
def test_string_rewrites_protect_complete_target_renderings(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
    case: tuple[str, int, str | None],
) -> None:
    """Do not rewrite old-version text inside an already-target rendering."""
    current_value, selector_count, expected_value = case
    write_fixture_manifest(
        tmp_path,
        "fixture/Cargo.toml",
        f'[metadata]\nartifact = "{current_value}"\n',
    )
    selector = StringValueRewriteConfig(
        table=("metadata",),
        template="{version}",
    )
    group = ManifestRewriteConfig(
        paths=("fixture/Cargo.toml",),
        dependencies=False,
        string_values=(selector,) * selector_count,
    )
    versions = manifest_rewrite_versions(
        names=frozenset({"alpha"}),
        pre_bump_versions={"alpha": "1.0.0"},
        target_version="2.0.0+1.0.0",
    )

    plans = plan_fixture_rewrites(tmp_path, (group,), versions=versions)

    if expected_value is None:
        assert not plans, "an already-target string must remain an unchanged no-op"
    else:
        assert len(plans) == 1, "the repeated selector should produce one plan"
        document = parse_toml(plans[0].new_text)
        assert document["metadata"]["artifact"] == expected_value, (
            "a repeated selector must not rewrite text inside the target version"
        )


def test_version_rewrite_does_not_match_inside_build_metadata(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A version embedded in build metadata is not a standalone version."""
    write_fixture_manifest(
        tmp_path,
        "fixture/Cargo.toml",
        '[metadata]\nartifact = "9.0.0+1.2.3"\nfixture = "published-1.2.3"\n',
    )
    group = ManifestRewriteConfig(
        paths=("fixture/Cargo.toml",),
        dependencies=False,
        string_values=(
            StringValueRewriteConfig(table=("metadata",), template="{version}"),
        ),
    )
    versions = manifest_rewrite_versions(
        names=frozenset({"alpha"}),
        pre_bump_versions={"alpha": "1.2.3"},
        target_version="2.0.0",
    )

    plans = plan_fixture_rewrites(tmp_path, (group,), versions=versions)

    assert len(plans) == 1, "the standalone version should still produce a plan"
    document = parse_toml(plans[0].new_text)
    assert document["metadata"]["artifact"] == "9.0.0+1.2.3", (
        "build metadata must not be mistaken for a standalone version"
    )
    assert document["metadata"]["fixture"] == "published-2.0.0", (
        "a hyphen before a standalone version remains a valid boundary"
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


def test_rejects_old_rendering_that_collides_with_a_target_rendering(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Reject old text that the protection matcher must treat as a target."""
    original_text = '[metadata]\nartifact = "lib12.0.0"\n'
    manifest = write_fixture_manifest(tmp_path, "fixture/Cargo.toml", original_text)
    group = ManifestRewriteConfig(
        paths=("fixture/Cargo.toml",),
        dependencies=False,
        string_values=(
            StringValueRewriteConfig(table=("metadata",), template="{crate}{version}"),
        ),
    )
    versions = manifest_rewrite_versions(
        names=frozenset({"lib", "lib1"}),
        pre_bump_versions={"lib": "12.0.0", "lib1": "1.0.0"},
        target_version="2.0.0",
    )
    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match=r"maps workspace crates 'lib' and 'lib1'.*'lib12\.0\.0'",
    ):
        plan_fixture_rewrites(tmp_path, (group,), versions=versions)

    assert manifest.read_text(encoding="utf-8") == original_text, (
        "planning must not write"
    )


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
    unopened_path = pathlib.PurePath("unopened/Cargo.toml")
    source = bump_manifest_rewrites.ManifestRewriteSource(
        path=typ.cast("pathlib.Path", unopened_path),
        content='[dependencies]\nalpha = "1.0.0"\n',
        dependencies=True,
        string_values=(),
    )

    (plan,) = bump_manifest_rewrites.plan_manifest_rewrites(
        (source,), manifest_rewrite_versions()
    )

    assert plan.path is unopened_path, (
        "the planner should preserve the supplied source identity"
    )
    assert not hasattr(unopened_path, "read_text"), (
        "the supplied path must be incapable of filesystem reads"
    )
    assert plan.new_text == '[dependencies]\nalpha = "2.0.0"\n', (
        "the planner should use only supplied text and version metadata"
    )
