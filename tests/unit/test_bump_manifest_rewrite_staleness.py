"""Tests for known and unattributed values in configured manifest rewrites."""

import collections.abc as cabc
import dataclasses as dc
import pathlib

import pytest
from tomlkit import parse as parse_toml

from lading.config import ManifestRewriteConfig, StringValueRewriteConfig
from tests.helpers.manifest_rewrites import (
    manifest_rewrite_versions,
    plan_fixture_rewrites,
)

type _ManifestWriter = cabc.Callable[[pathlib.Path, str, str], pathlib.Path]


@dc.dataclass(frozen=True, slots=True)
class _RewriteRetryScenario:
    """Inputs and outcome for one retry-staleness example."""

    value_key: str
    current_value: str
    template: str
    names: frozenset[str]
    pre_bump_versions: dict[str, str]
    target_version: str
    expected_new_text: str | None


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            _RewriteRetryScenario(
                value_key="path",
                current_value="target/alpha-2.0.0-rc.1",
                template="{crate}-{version}",
                names=frozenset({"alpha"}),
                pre_bump_versions={"alpha": "2.0.0"},
                target_version="2.0.0",
                expected_new_text=None,
            ),
            id="unmatched-prerelease-version-is-preserved",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="path",
                current_value="target/alpha-2.0.0+build.1",
                template="{crate}-{version}",
                names=frozenset({"alpha"}),
                pre_bump_versions={"alpha": "2.0.0"},
                target_version="2.0.0",
                expected_new_text=None,
            ),
            id="unmatched-build-metadata-version-is-preserved",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="path",
                current_value="alpha-1.0.0-rc.1",
                template="{crate}-{version}",
                names=frozenset({"alpha"}),
                pre_bump_versions={"alpha": "2.0.0-rc.1"},
                target_version="2.0.0-rc.1",
                expected_new_text=None,
            ),
            id="unattributed-prerelease-version-is-preserved",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="path",
                current_value="alpha-2.0.0-rc.1.",
                template="{crate}-{version}",
                names=frozenset({"alpha"}),
                pre_bump_versions={"alpha": "2.0.0-rc.1"},
                target_version="2.0.1-rc.1",
                expected_new_text=None,
            ),
            id="trailing-period-is-not-a-version-identifier",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="path",
                current_value="alpha-2.0.0+build.1.",
                template="{crate}-{version}",
                names=frozenset({"alpha"}),
                pre_bump_versions={"alpha": "2.0.0+build.1"},
                target_version="2.0.1+build.1",
                expected_new_text=None,
            ),
            id="trailing-period-is-not-a-build-identifier",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="path",
                current_value="alpha-2.0.0-src",
                template="{crate}-{version}",
                names=frozenset({"alpha"}),
                pre_bump_versions={"alpha": "2.0.0"},
                target_version="3.0.0",
                expected_new_text=None,
            ),
            id="version-followed-by-hyphen-is-not-rewritten",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="path",
                current_value="alpha-2.0.0-src",
                template="{crate}-{version}-src",
                names=frozenset({"alpha"}),
                pre_bump_versions={"alpha": "2.0.0"},
                target_version="2.0.0",
                expected_new_text=None,
            ),
            id="exact-target-suffix-rendering-no-op",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="path",
                current_value="1.0.0-foo-alpha",
                template="{version}-{crate}",
                names=frozenset({"alpha", "foo-alpha"}),
                pre_bump_versions={"alpha": "2.0.0", "foo-alpha": "1.0.0"},
                target_version="2.0.0",
                expected_new_text='[metadata]\npath = "2.0.0-foo-alpha"\n',
            ),
            id="other-crate-pre-bump-rendering-is-recoverable",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="path",
                current_value="alpha12.0.0",
                template="{crate}{version}",
                names=frozenset({"alpha", "alpha1"}),
                pre_bump_versions={"alpha": "2.0.0", "alpha1": "1.0.0"},
                target_version="2.0.0",
                expected_new_text=None,
            ),
            id="other-crate-target-rendering-is-not-stale",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="path",
                current_value="0.9.0-alpha",
                template="{version}-{crate}",
                names=frozenset({"alpha", "foo-alpha"}),
                pre_bump_versions={"alpha": "2.0.0", "foo-alpha": "1.0.0"},
                target_version="2.0.0",
                expected_new_text=None,
            ),
            id="unrecognized-crate-version-is-preserved",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="path",
                current_value="target/alpha-1.0.0",
                template="{crate}-{version}",
                names=frozenset({"alpha"}),
                pre_bump_versions={"alpha": "2.0.0"},
                target_version="2.0.0",
                expected_new_text=None,
            ),
            id="historical-crate-version-after-target",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="path",
                current_value="alpha-2.0.0-1.2.3-2.0.0-1.2.3",
                template="{crate}-{version}-{version}",
                names=frozenset({"alpha"}),
                pre_bump_versions={"alpha": "2.0.0-1.2.3"},
                target_version="2.0.0-1.2.3",
                expected_new_text=None,
            ),
            id="repeated-version-target-no-op",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="version",
                current_value="1.0.0",
                template="{version}",
                names=frozenset({"alpha", "beta"}),
                pre_bump_versions={"alpha": "1.0.0", "beta": "2.0.0"},
                target_version="2.0.0",
                expected_new_text='[metadata]\nversion = "2.0.0"\n',
            ),
            id="version-only-mixed-old-version-rewrite",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="version",
                current_value="0.8.0",
                template="{version}",
                names=frozenset({"alpha", "beta"}),
                pre_bump_versions={"alpha": "2.0.0", "beta": "1.0.0"},
                target_version="2.0.0",
                expected_new_text=None,
            ),
            id="version-only-mixed-retry-leaves-unrelated-version-alone",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="version",
                current_value="1.0.0",
                template="{version}",
                names=frozenset({"alpha", "beta"}),
                pre_bump_versions={"alpha": "2.0.0", "beta": "1.0.0"},
                target_version="2.0.0",
                expected_new_text='[metadata]\nversion = "2.0.0"\n',
            ),
            id="version-only-mixed-retry-known-old-version-rewrite",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="version",
                current_value="0.8.0",
                template="{version}",
                names=frozenset({"alpha", "beta"}),
                pre_bump_versions={"alpha": "1.0.0", "beta": "1.5.0"},
                target_version="2.0.0",
                expected_new_text=None,
            ),
            id="version-only-initial-bump-unrelated-version-no-op",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="version",
                current_value="1.0.0",
                template="{version}",
                names=frozenset({"alpha", "beta"}),
                pre_bump_versions={"alpha": "2.0.0", "beta": "2.0.0"},
                target_version="2.0.0",
                expected_new_text=None,
            ),
            id="version-only-unattributed-version-is-not-rejected",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="version",
                current_value="2.0.0",
                template="{version}",
                names=frozenset({"alpha", "beta"}),
                pre_bump_versions={"alpha": "2.0.0", "beta": "2.0.0"},
                target_version="2.0.0",
                expected_new_text=None,
            ),
            id="version-only-exact-target-no-op",
        ),
    ],
)
def test_string_rewrite_preserves_unattributed_values(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
    scenario: _RewriteRetryScenario,
) -> None:
    """Known old values change; unmatched historical values remain untouched."""
    write_fixture_manifest(
        tmp_path,
        "fixtures/standalone/Cargo.toml",
        f'[metadata]\n{scenario.value_key} = "{scenario.current_value}"\n',
    )
    group = ManifestRewriteConfig(
        paths=("fixtures/standalone/Cargo.toml",),
        dependencies=False,
        string_values=(
            StringValueRewriteConfig(
                table=("metadata",),
                template=scenario.template,
            ),
        ),
    )
    versions = manifest_rewrite_versions(
        names=scenario.names,
        pre_bump_versions=scenario.pre_bump_versions,
        target_version=scenario.target_version,
    )

    plans = plan_fixture_rewrites(tmp_path, (group,), versions=versions)
    if scenario.expected_new_text is None:
        assert not plans, "unmatched selected historical text must remain unchanged"
    else:
        assert len(plans) == 1, "one known old version should produce one rewrite plan"
        assert plans[0].new_text == scenario.expected_new_text, (
            "a known old value should be replaced with the target rendering"
        )


def test_missing_string_table_skips_conflicting_template(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """An absent selector is a no-op before template conflict checks."""
    write_fixture_manifest(
        tmp_path,
        "fixtures/standalone/Cargo.toml",
        '[package]\nname = "fixture"\nversion = "0.1.0"\n',
    )
    group = ManifestRewriteConfig(
        paths=("fixtures/standalone/Cargo.toml",),
        dependencies=False,
        string_values=(
            StringValueRewriteConfig(table=("metadata",), template="{crate}{version}"),
        ),
    )
    versions = manifest_rewrite_versions(
        names=frozenset({"a", "a1"}),
        pre_bump_versions={"a": "11.0.0", "a1": "1.0.0"},
        target_version="12.0.0",
    )

    assert not plan_fixture_rewrites(tmp_path, (group,), versions=versions), (
        "a missing TOML table must skip otherwise-conflicting renderings"
    )


def test_version_only_retry_preserves_unrelated_values_and_rewrites_known_version(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A version-only rule rewrites known versions without rejecting others."""
    write_fixture_manifest(
        tmp_path,
        "fixtures/standalone/Cargo.toml",
        '[metadata]\nlegacy = "0.8.0"\ncurrent = "1.0.0"\n',
    )
    group = ManifestRewriteConfig(
        paths=("fixtures/standalone/Cargo.toml",),
        dependencies=False,
        string_values=(
            StringValueRewriteConfig(table=("metadata",), template="{version}"),
        ),
    )
    versions = manifest_rewrite_versions(
        names=frozenset({"alpha", "beta"}),
        pre_bump_versions={"alpha": "2.0.0", "beta": "1.0.0"},
        target_version="2.0.0",
    )

    (plan,) = plan_fixture_rewrites(tmp_path, (group,), versions=versions)

    rewritten = parse_toml(plan.new_text)["metadata"]
    assert rewritten["legacy"] == "0.8.0", (
        "unrelated selected version values must remain unchanged"
    )
    assert rewritten["current"] == "2.0.0", (
        "a selected version known from workspace metadata must be updated"
    )


def test_crate_qualified_historical_reference_does_not_block_known_rewrite(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Preserve unknown old text while rewriting a known crate rendering."""
    write_fixture_manifest(
        tmp_path,
        "fixtures/standalone/Cargo.toml",
        '[metadata]\nalpha = "archive/alpha-0.8.0"\nbeta = "staging/beta-1.0.0"\n',
    )
    group = ManifestRewriteConfig(
        paths=("fixtures/standalone/Cargo.toml",),
        dependencies=False,
        string_values=(
            StringValueRewriteConfig(table=("metadata",), template="{crate}-{version}"),
        ),
    )
    versions = manifest_rewrite_versions(
        names=frozenset({"alpha", "beta"}),
        pre_bump_versions={"alpha": "2.0.0", "beta": "1.0.0"},
        target_version="2.0.0",
    )

    (plan,) = plan_fixture_rewrites(tmp_path, (group,), versions=versions)

    rewritten = parse_toml(plan.new_text)["metadata"]
    assert rewritten["alpha"] == "archive/alpha-0.8.0", (
        "a historical value for an already-targeted crate must be preserved"
    )
    assert rewritten["beta"] == "staging/beta-2.0.0", (
        "a known pre-bump rendering must still be rewritten"
    )
