"""Tests for stale configured manifest references on bump retries."""

import collections.abc as cabc
import pathlib
import re

import pytest

from lading.commands import bump_manifest_rewrites
from lading.config import ManifestRewriteConfig, StringValueRewriteConfig
from tests.helpers.manifest_rewrites import (
    manifest_rewrite_versions,
    plan_fixture_rewrites,
)

type _ManifestWriter = cabc.Callable[[pathlib.Path, str, str], pathlib.Path]


@pytest.mark.parametrize(
    "stale_suffix",
    ["-rc.1", "+build.1"],
    ids=["prerelease", "build-metadata"],
)
def test_retry_rejects_target_prefix_of_stale_version_suffix(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
    stale_suffix: str,
) -> None:
    """Target prefixes inside stale prerelease and build text are not masked."""
    write_fixture_manifest(
        tmp_path,
        "fixtures/standalone/Cargo.toml",
        f'[metadata]\npath = "target/alpha-2.0.0{stale_suffix}"\n',
    )
    group = ManifestRewriteConfig(
        paths=("fixtures/standalone/Cargo.toml",),
        dependencies=False,
        string_values=(StringValueRewriteConfig(table=("metadata",)),),
    )

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match=re.escape(f"contains non-target version '2.0.0{stale_suffix}'"),
    ):
        plan_fixture_rewrites(
            tmp_path,
            (group,),
            versions=manifest_rewrite_versions(
                names=frozenset({"alpha"}),
                pre_bump_versions={"alpha": "2.0.0"},
                target_version="2.0.0",
            ),
        )


def test_retry_rejects_stale_string_when_member_is_already_at_target(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A retry must not silently leave fixture text at an unknown old version."""
    write_fixture_manifest(
        tmp_path,
        "fixtures/standalone/Cargo.toml",
        '[metadata]\npath = "target/alpha-1.0.0"\n',
    )
    group = ManifestRewriteConfig(
        paths=("fixtures/standalone/Cargo.toml",),
        dependencies=False,
        string_values=(StringValueRewriteConfig(table=("metadata",)),),
    )

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match=r"crate 'alpha'.*'1\.0\.0'.*previous version cannot be recovered",
    ):
        plan_fixture_rewrites(
            tmp_path,
            (group,),
            versions=manifest_rewrite_versions(
                names=frozenset({"alpha"}),
                pre_bump_versions={"alpha": "2.0.0"},
                target_version="2.0.0",
            ),
        )


def test_retry_accepts_exact_target_with_repeated_version_placeholder(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Repeated version fields cannot make current template text look stale."""
    target_version = "2.0.0-1.2.3"
    current_text = f"alpha-{target_version}-{target_version}"
    write_fixture_manifest(
        tmp_path,
        "fixtures/standalone/Cargo.toml",
        f'[metadata]\npath = "{current_text}"\n',
    )
    group = ManifestRewriteConfig(
        paths=("fixtures/standalone/Cargo.toml",),
        dependencies=False,
        string_values=(
            StringValueRewriteConfig(
                table=("metadata",), template="{crate}-{version}-{version}"
            ),
        ),
    )
    versions = manifest_rewrite_versions(
        names=frozenset({"alpha"}),
        pre_bump_versions={"alpha": target_version},
        target_version=target_version,
    )

    assert not plan_fixture_rewrites(tmp_path, (group,), versions=versions), (
        "an exact target template rendering must produce an unchanged plan"
    )


def test_version_only_template_does_not_blame_another_crates_old_version(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A version-only template cannot identify which crate owns stale text."""
    write_fixture_manifest(
        tmp_path,
        "fixtures/standalone/Cargo.toml",
        '[metadata]\nversion = "1.0.0"\n',
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
        pre_bump_versions={"alpha": "1.0.0", "beta": "2.0.0"},
        target_version="2.0.0",
    )

    plans = plan_fixture_rewrites(tmp_path, (group,), versions=versions)

    assert len(plans) == 1, "the old alpha version should produce one rewrite plan"
    assert plans[0].new_text == '[metadata]\nversion = "2.0.0"\n', (
        "a version-only template should rewrite old values without stale ownership"
    )
