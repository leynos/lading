"""Tests for Cargo dependency matching in manifest rewrites."""

import logging
import pathlib
import typing as typ

from tomlkit import parse as parse_toml
from tomlkit.container import OutOfOrderTableProxy

from lading.commands import bump_manifest_rewrite_dependencies, bump_manifest_rewrites
from lading.config import ManifestRewriteConfig
from tests.helpers.manifest_rewrites import plan_fixture_rewrites

if typ.TYPE_CHECKING:
    import pytest


def test_explicit_package_name_overrides_a_matching_dependency_alias(
    tmp_path: pathlib.Path,
) -> None:
    """An explicit package name determines which crate the alias denotes."""
    manifest = tmp_path / "fixtures/standalone/Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        '[dependencies]\nrstest-bdd = { package = "serde", version = "0.6.0-beta4" }\n',
        encoding="utf-8",
    )
    plans = plan_fixture_rewrites(
        tmp_path,
        (ManifestRewriteConfig(paths=("fixtures/standalone/Cargo.toml",)),),
        bump_manifest_rewrites.ManifestRewriteVersions(
            updated_crate_names=frozenset({"rstest-bdd"}),
            pre_bump_versions={"rstest-bdd": "0.6.0-beta4"},
            target_version="0.6.0",
        ),
    )

    assert not plans, "a non-workspace package alias must not be rewritten"
    document = parse_toml(manifest.read_text(encoding="utf-8"))
    assert document["dependencies"]["rstest-bdd"]["version"] == "0.6.0-beta4", (
        "the key must not override an explicit package name"
    )


def test_rewrites_inline_dependency_sections_and_aliases(
    tmp_path: pathlib.Path,
) -> None:
    """Inline direct, target, and workspace tables receive dependency rewrites."""
    manifest = tmp_path / "fixtures/standalone/Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        'dependencies = { alpha = "^1.0.0", '
        'renamed = { package = "alpha", version = "~1.0.0" } }\n'
        'target = { "cfg(unix)" = { dependencies = { alpha = "1.0.0" } } }\n'
        'workspace = { dependencies = { alpha = "1.0.0" } }\n',
        encoding="utf-8",
    )
    plans = plan_fixture_rewrites(
        tmp_path,
        (ManifestRewriteConfig(paths=("fixtures/standalone/Cargo.toml",)),),
        bump_manifest_rewrites.ManifestRewriteVersions(
            updated_crate_names=frozenset({"alpha"}),
            pre_bump_versions={"alpha": "1.0.0"},
            target_version="2.0.0",
        ),
    )

    assert len(plans) == 1, "inline dependency tables must produce a rewrite plan"
    document = parse_toml(plans[0].new_text)
    assert document["dependencies"]["alpha"] == "^2.0.0", (
        "the inline direct requirement must preserve its operator"
    )
    assert document["dependencies"]["renamed"]["version"] == "~2.0.0", (
        "an inline package alias must use the updated package name"
    )
    assert document["target"]["cfg(unix)"]["dependencies"]["alpha"] == "2.0.0", (
        "inline target dependency tables must be traversed"
    )
    assert document["workspace"]["dependencies"]["alpha"] == "2.0.0", (
        "inline workspace dependency tables must be traversed"
    )


def test_rewrites_interleaved_dotted_dependency_table_proxy() -> None:
    """Interleaved dotted keys keep matching package versions up to date."""
    document = parse_toml(
        "[dependencies]\n"
        'alpha.version = "1.0.0"\n'
        'beta.version = "1.0.0"\n'
        'alpha.package = "alpha"\n'
    )
    alpha = document["dependencies"]["alpha"]
    assert isinstance(alpha, OutOfOrderTableProxy), (
        "interleaved dotted keys should exercise tomlkit's proxy representation"
    )

    changed = bump_manifest_rewrite_dependencies.rewrite_dependency_requirements(
        document, {"alpha"}, "2.0.0"
    )
    rewritten_alpha = document["dependencies"]["alpha"]

    assert changed, "a matching proxy-backed dependency should be rewritten"
    assert rewritten_alpha["version"] == "2.0.0", (
        "the workspace crate version should update through the table proxy"
    )
    assert document["dependencies"]["beta"]["version"] == "1.0.0", (
        "the unrelated proxy-backed dependency should remain unchanged"
    )
    assert rewritten_alpha["package"] == "alpha", (
        "the package alias metadata should remain unchanged"
    )


def test_rewrites_dependency_version_in_standard_table() -> None:
    """Regular dependency tables continue to update their version field."""
    document = parse_toml('[dependencies.alpha]\nversion = "1.0.0"\n')

    changed = bump_manifest_rewrite_dependencies.rewrite_dependency_requirements(
        document, {"alpha"}, "2.0.0"
    )

    assert changed, "the standard dependency table should report a rewrite"
    assert document["dependencies"]["alpha"]["version"] == "2.0.0", (
        "the standard dependency table version should be updated"
    )


def test_rewrite_preserves_upper_bound_of_compound_requirement() -> None:
    """Rewriting a prerelease requirement retains its upper comparator."""
    document = parse_toml('[dependencies]\nrstest-bdd = ">=0.6.0-beta4, <0.7"\n')

    changed = bump_manifest_rewrite_dependencies.rewrite_dependency_requirements(
        document, {"rstest-bdd"}, "0.6.0"
    )

    assert changed, "the matching workspace crate requirement should change"
    assert document["dependencies"]["rstest-bdd"] == ">=0.6.0, <0.7", (
        "rewriting the lower comparator must retain the excluded upper range"
    )


def test_rewrite_planning_logs_collected_comparator_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The manifest rewrite policy logs warning data returned by TOML helpers."""
    document = parse_toml('[dependencies]\nalpha = "<0.7"\n')
    caplog.set_level(
        logging.WARNING, logger=bump_manifest_rewrite_dependencies.__name__
    )

    changed = bump_manifest_rewrite_dependencies.rewrite_dependency_requirements(
        document, {"alpha"}, "0.6.0"
    )

    assert changed, "the dependency requirement should still be rewritten"
    assert "preserved Cargo comparator(s) <0.6.0" in caplog.text, (
        "the planning boundary should log the returned comparator warning"
    )
