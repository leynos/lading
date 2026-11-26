"""Test string rewrites at SemVer boundaries."""

import collections.abc as cabc
import dataclasses as dc
import pathlib
import typing as typ

import pytest
from tomlkit import parse as parse_toml

from lading.config import ManifestRewriteConfig, StringValueRewriteConfig
from tests.helpers.manifest_rewrites import (
    manifest_rewrite_versions,
    plan_fixture_rewrites,
)

type _ManifestWriter = cabc.Callable[[pathlib.Path, str, str], pathlib.Path]

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument


@dc.dataclass(frozen=True, slots=True)
class _MetadataRewriteCase:
    """Source text and rendered versions for one string rewrite boundary."""

    content: str
    template: str
    pre_bump_version: str
    target_version: str
    expected_values: tuple[tuple[str, str], ...] = ()


def _rewrite_metadata_values(
    root: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
    case: _MetadataRewriteCase,
) -> TOMLDocument:
    """Plan a selected metadata rewrite and return its parsed TOML result."""
    write_fixture_manifest(root, "fixture/Cargo.toml", case.content)
    group = ManifestRewriteConfig(
        paths=("fixture/Cargo.toml",),
        dependencies=False,
        string_values=(
            StringValueRewriteConfig(table=("metadata",), template=case.template),
        ),
    )
    versions = manifest_rewrite_versions(
        names=frozenset({"alpha"}),
        pre_bump_versions={"alpha": case.pre_bump_version},
        target_version=case.target_version,
    )
    plans = plan_fixture_rewrites(root, (group,), versions=versions)
    assert len(plans) == 1, "the selected metadata values should produce one plan"
    return parse_toml(plans[0].new_text)


def test_version_rewrite_does_not_match_inside_build_metadata(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Build metadata stays intact while standalone references still change."""
    document = _rewrite_metadata_values(
        tmp_path,
        write_fixture_manifest,
        _MetadataRewriteCase(
            content=(
                "[metadata]\n"
                'artifact = "9.0.0+0.6.0-beta4"\n'
                'tagged-build = "release-9.0.0+0.6.0-beta4"\n'
                'plus-tagged-build = "release+9.0.0+0.6.0-beta4"\n'
                'tagged-build-prefix = "9.0.0+build-0.6.0-beta4"\n'
                'plus-delimiter = "release+0.6.0-beta4"\n'
                'fixture = "published-0.6.0-beta4"\n'
            ),
            template="{version}",
            pre_bump_version="0.6.0-beta4",
            target_version="0.6.0",
        ),
    )

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


def test_crate_version_template_preserves_semver_metadata_with_trailing_path(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A path delimiter outside the template cannot expose build metadata."""
    document = _rewrite_metadata_values(
        tmp_path,
        write_fixture_manifest,
        _MetadataRewriteCase(
            content=(
                "[metadata]\n"
                'nested = "9.8.7+alpha-1.2.3/"\n'
                'standalone = "alpha-1.2.3/"\n'
            ),
            template="{crate}-{version}/",
            pre_bump_version="1.2.3",
            target_version="2.0.0",
        ),
    )

    assert document["metadata"]["nested"] == "9.8.7+alpha-1.2.3/", (
        "the version placeholder inside SemVer build metadata must stay intact"
    )
    assert document["metadata"]["standalone"] == "alpha-2.0.0/", (
        "the same configured template must rewrite a standalone staged path"
    )


def test_version_rewrite_does_not_match_inside_semver_prerelease(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Keep nested prerelease versions intact while updating standalone values."""
    document = _rewrite_metadata_values(
        tmp_path,
        write_fixture_manifest,
        _MetadataRewriteCase(
            content=(
                "[metadata]\n"
                'nested = "9.0.0-1.0.0"\n'
                'nested-after-prerelease = "9.0.0-alpha-1.0.0"\n'
                'standalone = "alpha-1.0.0"\n'
            ),
            template="{version}",
            pre_bump_version="1.0.0",
            target_version="2.0.0",
        ),
    )

    assert document["metadata"]["nested"] == "9.0.0-1.0.0", (
        "a full version used as a SemVer prerelease must remain intact"
    )
    assert document["metadata"]["nested-after-prerelease"] == ("9.0.0-alpha-1.0.0"), (
        "versions after existing prerelease identifiers must remain intact"
    )
    assert document["metadata"]["standalone"] == "alpha-2.0.0", (
        "a hyphenated standalone reference must still be rewritten"
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _MetadataRewriteCase(
                content=(
                    '[metadata]\nnested = "9.0.0-1.0.0"\nstandalone = "release-1.0.0"\n'
                ),
                template="-{version}",
                pre_bump_version="1.0.0",
                target_version="2.0.0",
                expected_values=(
                    ("nested", "9.0.0-1.0.0"),
                    ("standalone", "release-2.0.0"),
                ),
            ),
            id="separator-prefixed-prerelease",
        ),
        pytest.param(
            _MetadataRewriteCase(
                content=(
                    '[metadata]\nsemver = "1.2.3+0.1.0"\nstandalone = "release+0.1.0"\n'
                ),
                template="+{version}",
                pre_bump_version="0.1.0",
                target_version="0.2.0",
                expected_values=(
                    ("semver", "1.2.3+0.1.0"),
                    ("standalone", "release+0.2.0"),
                ),
            ),
            id="plus-prefixed-build-metadata",
        ),
        pytest.param(
            _MetadataRewriteCase(
                content=(
                    '[metadata]\nnested = "9.0.0-rc.alpha-1.0.0"\n'
                    'standalone = "alpha-1.0.0"\n'
                ),
                template="{crate}-{version}",
                pre_bump_version="1.0.0",
                target_version="2.0.0",
                expected_values=(
                    ("nested", "9.0.0-rc.alpha-1.0.0"),
                    ("standalone", "alpha-2.0.0"),
                ),
            ),
            id="crate-qualified-dotted-prerelease",
        ),
        pytest.param(
            _MetadataRewriteCase(
                content=(
                    '[metadata]\nnested = "9.0.0-rc.1.0.0"\nstandalone = ".1.0.0"\n'
                ),
                template=".{version}",
                pre_bump_version="1.0.0",
                target_version="2.0.0",
                expected_values=(
                    ("nested", "9.0.0-rc.1.0.0"),
                    ("standalone", ".2.0.0"),
                ),
            ),
            id="dot-prefixed-template",
        ),
        pytest.param(
            _MetadataRewriteCase(
                content=(
                    '[metadata]\nnested = "9.0.0-x.alpha-1.0.0+build"\n'
                    'standalone = "alpha-1.0.0+build"\n'
                ),
                template="{version}",
                pre_bump_version="1.0.0+build",
                target_version="2.0.0+build",
                expected_values=(
                    ("nested", "9.0.0-x.alpha-1.0.0+build"),
                    ("standalone", "alpha-2.0.0+build"),
                ),
            ),
            id="prerelease-with-build-metadata",
        ),
        pytest.param(
            _MetadataRewriteCase(
                content=(
                    '[metadata]\nnewline = "2.0.0-\\n1.0.0"\nnested = "2.0.0-1.0.0"\n'
                ),
                template="{version}",
                pre_bump_version="1.0.0",
                target_version="3.0.0",
                expected_values=(
                    ("newline", "2.0.0-\n3.0.0"),
                    ("nested", "2.0.0-1.0.0"),
                ),
            ),
            id="newline-after-prerelease-separator",
        ),
    ],
)
def test_templates_preserve_semver_component_boundaries(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
    case: _MetadataRewriteCase,
) -> None:
    """Template separators must not rewrite nested SemVer components."""
    document = _rewrite_metadata_values(tmp_path, write_fixture_manifest, case)
    for key, expected_value in case.expected_values:
        assert document["metadata"][key] == expected_value, (
            f"configured template boundary case changed metadata field {key!r}"
        )


def test_crate_path_rewrite_can_cross_semver_metadata_boundary(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A path template crossing metadata is not itself a SemVer component."""
    document = _rewrite_metadata_values(
        tmp_path,
        write_fixture_manifest,
        _MetadataRewriteCase(
            content='[metadata]\npath = "3.0.0+alpha/1.0.0"\n',
            template="{crate}/{version}",
            pre_bump_version="1.0.0",
            target_version="2.0.0",
        ),
    )

    assert document["metadata"]["path"] == "3.0.0+alpha/2.0.0", (
        "a configured path rendering spanning slash must still be rewritten"
    )
