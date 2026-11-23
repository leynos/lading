"""Tests for stale configured manifest references on bump retries."""

import collections.abc as cabc
import dataclasses as dc
import pathlib

import pytest

from lading.commands import bump_manifest_rewrites
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
    expected_error: str | None
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
                expected_error=r"contains non-target version '2\.0\.0-rc\.1'",
                expected_new_text=None,
            ),
            id="prerelease-target-prefix",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="path",
                current_value="target/alpha-2.0.0+build.1",
                template="{crate}-{version}",
                names=frozenset({"alpha"}),
                pre_bump_versions={"alpha": "2.0.0"},
                target_version="2.0.0",
                expected_error=r"contains non-target version '2\.0\.0\+build\.1'",
                expected_new_text=None,
            ),
            id="build-metadata-target-prefix",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="path",
                current_value="target/alpha-1.0.0",
                template="{crate}-{version}",
                names=frozenset({"alpha"}),
                pre_bump_versions={"alpha": "2.0.0"},
                target_version="2.0.0",
                expected_error=(
                    r"crate 'alpha'.*'1\.0\.0'.*previous version cannot be recovered"
                ),
                expected_new_text=None,
            ),
            id="stale-crate-version-after-target",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="path",
                current_value="alpha-2.0.0-1.2.3-2.0.0-1.2.3",
                template="{crate}-{version}-{version}",
                names=frozenset({"alpha"}),
                pre_bump_versions={"alpha": "2.0.0-1.2.3"},
                target_version="2.0.0-1.2.3",
                expected_error=None,
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
                expected_error=None,
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
                expected_error=(
                    r"non-target version '0\.8\.0'.*one or more updated "
                    r"workspace crates are already at '2\.0\.0'"
                ),
                expected_new_text=None,
            ),
            id="version-only-mixed-retry-unrecoverable-version",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="version",
                current_value="1.0.0",
                template="{version}",
                names=frozenset({"alpha", "beta"}),
                pre_bump_versions={"alpha": "2.0.0", "beta": "1.0.0"},
                target_version="2.0.0",
                expected_error=None,
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
                expected_error=None,
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
                expected_error=(
                    r"one or more updated workspace crates are already at '2\.0\.0'"
                ),
                expected_new_text=None,
            ),
            id="version-only-unattributed-stale-rejection",
        ),
        pytest.param(
            _RewriteRetryScenario(
                value_key="version",
                current_value="2.0.0",
                template="{version}",
                names=frozenset({"alpha", "beta"}),
                pre_bump_versions={"alpha": "2.0.0", "beta": "2.0.0"},
                target_version="2.0.0",
                expected_error=None,
                expected_new_text=None,
            ),
            id="version-only-exact-target-no-op",
        ),
    ],
)
def test_string_rewrite_retry_staleness_contract(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
    scenario: _RewriteRetryScenario,
) -> None:
    """Retried rewrites reject stale text and preserve exact target values."""
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

    if scenario.expected_error is not None:
        with pytest.raises(
            bump_manifest_rewrites.ManifestRewriteError,
            match=scenario.expected_error,
        ):
            plan_fixture_rewrites(tmp_path, (group,), versions=versions)
        return

    plans = plan_fixture_rewrites(tmp_path, (group,), versions=versions)
    if scenario.expected_new_text is None:
        assert not plans, "an exact target template rendering must be a no-op"
    else:
        assert len(plans) == 1, "one stale version should produce one rewrite plan"
        assert plans[0].new_text == scenario.expected_new_text, (
            "a stale value should be replaced with the target rendering"
        )
