"""Test string rewrites at SemVer boundaries."""

import collections.abc as cabc
import pathlib

from tomlkit import parse as parse_toml

from lading.config import ManifestRewriteConfig, StringValueRewriteConfig
from tests.helpers.manifest_rewrites import (
    manifest_rewrite_versions,
    plan_fixture_rewrites,
)

type _ManifestWriter = cabc.Callable[[pathlib.Path, str, str], pathlib.Path]


def test_version_rewrite_does_not_match_inside_build_metadata(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Build metadata stays intact while standalone references still change."""
    write_fixture_manifest(
        tmp_path,
        "fixture/Cargo.toml",
        "[metadata]\n"
        'artifact = "9.0.0+0.6.0-beta4"\n'
        'tagged-build = "release-9.0.0+0.6.0-beta4"\n'
        'plus-tagged-build = "release+9.0.0+0.6.0-beta4"\n'
        'tagged-build-prefix = "9.0.0+build-0.6.0-beta4"\n'
        'plus-delimiter = "release+0.6.0-beta4"\n'
        'fixture = "published-0.6.0-beta4"\n',
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
        pre_bump_versions={"alpha": "0.6.0-beta4"},
        target_version="0.6.0",
    )

    plans = plan_fixture_rewrites(tmp_path, (group,), versions=versions)

    assert len(plans) == 1, "standalone values should still produce a plan"
    document = parse_toml(plans[0].new_text)
    assert document["metadata"]["artifact"] == "9.0.0+0.6.0-beta4", (
        "build metadata must not be mistaken for a standalone version"
    )
    assert document["metadata"]["tagged-build"] == "release-9.0.0+0.6.0-beta4", (
        "a SemVer build suffix must shield its metadata from standalone rewrites"
    )
    assert document["metadata"]["plus-tagged-build"] == ("release+9.0.0+0.6.0-beta4"), (
        "a plus-delimited SemVer core must shield its build metadata too"
    )
    assert document["metadata"]["tagged-build-prefix"] == ("9.0.0+build-0.6.0-beta4"), (
        "a version later in SemVer build metadata must also remain unchanged"
    )
    assert document["metadata"]["plus-delimiter"] == "release+0.6.0", (
        "a plus sign outside SemVer build metadata is a valid boundary"
    )
    assert document["metadata"]["fixture"] == "published-0.6.0", (
        "a hyphen before a standalone version remains a valid boundary"
    )


def test_version_rewrite_does_not_match_inside_semver_prerelease(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Keep nested prerelease versions intact while updating standalone values."""
    write_fixture_manifest(
        tmp_path,
        "fixture/Cargo.toml",
        "[metadata]\n"
        'nested = "9.0.0-1.0.0"\n'
        'nested-after-prerelease = "9.0.0-alpha-1.0.0"\n'
        'standalone = "alpha-1.0.0"\n',
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
        pre_bump_versions={"alpha": "1.0.0"},
        target_version="2.0.0",
    )

    plans = plan_fixture_rewrites(tmp_path, (group,), versions=versions)

    assert len(plans) == 1, "the standalone version should still produce a plan"
    document = parse_toml(plans[0].new_text)
    assert document["metadata"]["nested"] == "9.0.0-1.0.0", (
        "a full version used as a SemVer prerelease must remain intact"
    )
    assert document["metadata"]["nested-after-prerelease"] == ("9.0.0-alpha-1.0.0"), (
        "versions after existing prerelease identifiers must remain intact"
    )
    assert document["metadata"]["standalone"] == "alpha-2.0.0", (
        "a hyphenated standalone reference must still be rewritten"
    )


def test_separator_prefixed_version_rewrite_preserves_nested_prerelease(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A template hyphen must not turn a prerelease identifier into a match."""
    write_fixture_manifest(
        tmp_path,
        "fixture/Cargo.toml",
        '[metadata]\nnested = "9.0.0-1.0.0"\nstandalone = "release-1.0.0"\n',
    )
    group = ManifestRewriteConfig(
        paths=("fixture/Cargo.toml",),
        dependencies=False,
        string_values=(
            StringValueRewriteConfig(table=("metadata",), template="-{version}"),
        ),
    )
    versions = manifest_rewrite_versions(
        names=frozenset({"alpha"}),
        pre_bump_versions={"alpha": "1.0.0"},
        target_version="2.0.0",
    )

    (plan,) = plan_fixture_rewrites(tmp_path, (group,), versions=versions)

    document = parse_toml(plan.new_text)
    assert document["metadata"]["nested"] == "9.0.0-1.0.0", (
        "a version inside a SemVer prerelease must remain unchanged"
    )
    assert document["metadata"]["standalone"] == "release-2.0.0", (
        "a hyphen-prefixed standalone version must still be rewritten"
    )


def test_plus_prefixed_version_template_preserves_build_metadata(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A template plus cannot hide a SemVer build-metadata boundary."""
    write_fixture_manifest(
        tmp_path,
        "fixture/Cargo.toml",
        '[metadata]\nsemver = "1.2.3+0.1.0"\nstandalone = "release+0.1.0"\n',
    )
    group = ManifestRewriteConfig(
        paths=("fixture/Cargo.toml",),
        dependencies=False,
        string_values=(
            StringValueRewriteConfig(table=("metadata",), template="+{version}"),
        ),
    )
    versions = manifest_rewrite_versions(
        names=frozenset({"alpha"}),
        pre_bump_versions={"alpha": "0.1.0"},
        target_version="0.2.0",
    )

    plans = plan_fixture_rewrites(tmp_path, (group,), versions=versions)

    assert len(plans) == 1, "the standalone plus-prefixed value should change"
    document = parse_toml(plans[0].new_text)
    assert document["metadata"]["semver"] == "1.2.3+0.1.0", (
        "build metadata must remain unchanged when the template begins with plus"
    )
    assert document["metadata"]["standalone"] == "release+0.2.0", (
        "a plus-delimited standalone reference must still be rewritten"
    )


def test_crate_path_rewrite_can_cross_semver_metadata_boundary(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A path template crossing metadata is not itself a SemVer component."""
    write_fixture_manifest(
        tmp_path,
        "fixture/Cargo.toml",
        '[metadata]\npath = "3.0.0+alpha/1.0.0"\n',
    )
    group = ManifestRewriteConfig(
        paths=("fixture/Cargo.toml",),
        dependencies=False,
        string_values=(
            StringValueRewriteConfig(table=("metadata",), template="{crate}/{version}"),
        ),
    )
    versions = manifest_rewrite_versions(
        names=frozenset({"alpha"}),
        pre_bump_versions={"alpha": "1.0.0"},
        target_version="2.0.0",
    )

    (plan,) = plan_fixture_rewrites(tmp_path, (group,), versions=versions)

    document = parse_toml(plan.new_text)
    assert document["metadata"]["path"] == "3.0.0+alpha/2.0.0", (
        "a configured path rendering spanning slash must still be rewritten"
    )


def test_crate_version_rewrite_does_not_match_inside_dotted_prerelease(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Keep a crate-qualified value in dotted prerelease text unchanged."""
    write_fixture_manifest(
        tmp_path,
        "fixture/Cargo.toml",
        '[metadata]\nnested = "9.0.0-rc.alpha-1.0.0"\nstandalone = "alpha-1.0.0"\n',
    )
    group = ManifestRewriteConfig(
        paths=("fixture/Cargo.toml",),
        dependencies=False,
        string_values=(
            StringValueRewriteConfig(table=("metadata",), template="{crate}-{version}"),
        ),
    )
    versions = manifest_rewrite_versions(
        names=frozenset({"alpha"}),
        pre_bump_versions={"alpha": "1.0.0"},
        target_version="2.0.0",
    )

    plans = plan_fixture_rewrites(tmp_path, (group,), versions=versions)

    assert len(plans) == 1, "the standalone version should still produce a plan"
    document = parse_toml(plans[0].new_text)
    assert document["metadata"]["nested"] == "9.0.0-rc.alpha-1.0.0", (
        "a crate-qualified rendering inside a dotted prerelease must remain intact"
    )
    assert document["metadata"]["standalone"] == "alpha-2.0.0", (
        "the same crate-qualified rendering outside SemVer must be rewritten"
    )
