"""Tests for Cargo dependency matching in manifest rewrites."""

import collections.abc as cabc
import logging
import pathlib
import typing as typ

import pytest
from tomlkit import parse as parse_toml
from tomlkit.container import OutOfOrderTableProxy

from lading.commands import bump_manifest_rewrite_dependencies, bump_manifest_rewrites
from lading.config import ManifestRewriteConfig
from tests.helpers.manifest_rewrites import plan_fixture_rewrites

type _ManifestWriter = cabc.Callable[[pathlib.Path, str, str], pathlib.Path]

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def _rstest_bdd_dependency_plan(
    root: pathlib.Path,
) -> bump_manifest_rewrites.ManifestRewritePlan:
    """Build a rewrite plan for a multi-section prerelease manifest."""
    manifest = root / "fixtures/standalone/Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
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
        encoding="utf-8",
    )
    return plan_fixture_rewrites(
        root,
        (ManifestRewriteConfig(paths=("fixtures/standalone/Cargo.toml",)),),
        versions=bump_manifest_rewrites.ManifestRewriteVersions(
            updated_crate_names=frozenset({"rstest-bdd", "rstest-bdd-harness"}),
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
        "the workspace dependency should be rewritten"
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


@pytest.mark.parametrize(
    "case",
    [
        pytest.param((">=1.0.0", "2.0.0", ""), id="inclusive-lower-bound"),
        pytest.param(("<3.0.0", "2.0.0", "2.0.0"), id="strict-upper-rejects-target"),
        pytest.param((">1.0.0", "2.0.0", "2.0.0"), id="strict-lower-rejects-target"),
        pytest.param((">0.5.0", "2.0.0", "2.0.0"), id="strict-lower-rewritten"),
        pytest.param((">=0.6.0-beta4, <0.7", "0.6.0", ""), id="retained-upper-accepts"),
        pytest.param((">=1.0.0, <2", "2.0.0", "2.0.0"), id="retained-upper-rejects"),
        pytest.param(("=1.0.0", "2.0.0", ""), id="equality-rewrites-to-target"),
    ],
)
def test_rewriting_reports_excluding_comparator_bounds(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    case: tuple[str, str, str],
) -> None:
    """An incompatible rewritten or retained comparator is reported once."""
    requirement, target, expected_warning = case
    manifest = tmp_path / "fixture" / "Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text(f'[dependencies]\nalpha = "{requirement}"\n', encoding="utf-8")
    group = ManifestRewriteConfig(paths=("fixture/Cargo.toml",))

    with caplog.at_level(
        logging.WARNING, logger=bump_manifest_rewrite_dependencies.__name__
    ):
        (plan,) = plan_fixture_rewrites(
            tmp_path,
            (group,),
            bump_manifest_rewrites.ManifestRewriteVersions(
                updated_crate_names=frozenset({"alpha"}),
                pre_bump_versions={"alpha": "1.0.0"},
                target_version=target,
            ),
        )

    assert manifest.exists(), "the planner must not delete the manifest"
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    if expected_warning:
        assert len(warnings) == 1, "an excluding comparator must be reported once"
        assert expected_warning in warnings[0].getMessage(), (
            "the warning must name the requested target version"
        )
    else:
        assert not warnings, "compatible comparators must not warn"
    assert requirement in plan.original_text, (
        "planning must preserve the original requirement text"
    )
