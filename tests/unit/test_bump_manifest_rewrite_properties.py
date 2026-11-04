"""Property tests for configured non-member manifest rewrites."""

import pathlib
import string
import tempfile

import hypothesis.strategies as st
from hypothesis import given, settings
from tomlkit import parse as parse_toml

from lading.commands import bump_manifest_rewrites
from lading.config import ManifestRewriteConfig, StringValueRewriteConfig

_CRATE_NAME_CHARACTERS = string.ascii_letters + string.digits + "_-"
_VERSION_PREFIX_CHARACTERS = string.ascii_letters + string.digits + "."
_VERSION_SUFFIX_CHARACTERS = string.ascii_letters + string.digits + ".+-"


@given(
    crate_prefix=st.text(
        alphabet=_CRATE_NAME_CHARACTERS,
        min_size=1,
        max_size=8,
    ),
    version_suffix=st.text(
        alphabet=_VERSION_SUFFIX_CHARACTERS,
        min_size=1,
        max_size=8,
    ),
)
@settings(max_examples=100, deadline=None)
def test_string_rewrites_respect_token_boundaries_and_are_idempotent(
    crate_prefix: str,
    version_suffix: str,
) -> None:
    """Only whole crate-version tokens change, and a second plan is empty."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        workspace_root = pathlib.Path(temporary_directory)
        crate_name = "rstest-bdd"
        old_version = "0.6.0-beta4"
        old_reference = f"{crate_name}-{old_version}"
        manifest = workspace_root / "fixtures/standalone/Cargo.toml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "[metadata]\n"
            f'longer-crate = "{crate_prefix}{old_reference}/"\n'
            f'longer-version = "/{old_reference}{version_suffix}"\n'
            f'whole-token = "/{old_reference}/"\n',
            encoding="utf-8",
        )
        groups = (
            ManifestRewriteConfig(
                paths=("fixtures/standalone/Cargo.toml",),
                dependencies=False,
                string_values=(StringValueRewriteConfig(table=("metadata",)),),
            ),
        )
        versions = bump_manifest_rewrites.ManifestRewriteVersions(
            updated_crate_names={crate_name},
            pre_bump_versions={crate_name: old_version},
            target_version="0.6.0",
        )

        (plan,) = bump_manifest_rewrites.plan_manifest_rewrites(
            workspace_root, groups, (), versions
        )

        rewritten = parse_toml(plan.new_text)["metadata"]
        assert rewritten["longer-crate"] == f"{crate_prefix}{old_reference}/", (
            "a crate-version token inside a longer crate name must remain unchanged"
        )
        assert rewritten["longer-version"] == f"/{old_reference}{version_suffix}", (
            "a token inside a longer prerelease version must remain unchanged"
        )
        assert rewritten["whole-token"] == f"/{crate_name}-0.6.0/", (
            "a whole crate-version token must use the target version"
        )

        manifest.write_text(plan.new_text, encoding="utf-8")
        assert not bump_manifest_rewrites.plan_manifest_rewrites(
            workspace_root, groups, (), versions
        ), "planning the rewritten manifest again must produce no further changes"


@given(
    version_prefix=st.text(
        alphabet=_VERSION_PREFIX_CHARACTERS,
        min_size=1,
        max_size=8,
    ),
    crate_suffix=st.text(
        alphabet=_CRATE_NAME_CHARACTERS,
        min_size=1,
        max_size=8,
    ),
)
@settings(max_examples=100, deadline=None)
def test_string_rewrites_boundaries_for_version_and_crate_templates(
    version_prefix: str,
    crate_suffix: str,
) -> None:
    """Version and crate placeholders stay bounded at both template edges."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        workspace_root = pathlib.Path(temporary_directory)
        crate_name = "rstest-bdd"
        old_version = "0.6.0"
        manifest = workspace_root / "fixtures/standalone/Cargo.toml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "[metadata]\n"
            f'version-prefix = "{version_prefix}{old_version}"\n'
            f'version-token = "{old_version}"\n'
            f'crate-suffix = "{old_version}-{crate_name}{crate_suffix}"\n'
            f'crate-token = "{old_version}-{crate_name}"\n',
            encoding="utf-8",
        )
        groups = (
            ManifestRewriteConfig(
                paths=("fixtures/standalone/Cargo.toml",),
                dependencies=False,
                string_values=(
                    StringValueRewriteConfig(table=("metadata",), template="{version}"),
                    StringValueRewriteConfig(
                        table=("metadata",), template="{version}-{crate}"
                    ),
                ),
            ),
        )
        versions = bump_manifest_rewrites.ManifestRewriteVersions(
            updated_crate_names={crate_name},
            pre_bump_versions={crate_name: old_version},
            target_version="1.0.0",
        )

        (plan,) = bump_manifest_rewrites.plan_manifest_rewrites(
            workspace_root, groups, (), versions
        )

        rewritten = parse_toml(plan.new_text)["metadata"]
        assert rewritten["version-prefix"] == f"{version_prefix}{old_version}", (
            "a version embedded in a longer value must remain unchanged"
        )
        assert rewritten["version-token"] == "1.0.0", (
            "a whole version token must use the target version"
        )
        assert (
            rewritten["crate-suffix"] == f"{old_version}-{crate_name}{crate_suffix}"
        ), "a crate embedded in a longer name must remain unchanged"
        assert rewritten["crate-token"] == f"1.0.0-{crate_name}", (
            "a whole version-and-crate token must use the target version"
        )
