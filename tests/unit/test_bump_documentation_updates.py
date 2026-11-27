"""Integration tests for documentation updates in :mod:`lading.commands.bump`."""

import dataclasses as dc
import pathlib
import typing as typ

import pytest

from lading.commands import bump, bump_docs, bump_pipeline
from lading.commands.bump_readme import ReadmeTranspositionError
from tests.helpers.path_normalization import normalized
from tests.helpers.workspace_builders import (
    _build_workspace_with_internal_deps,
    _CrateSpec,
    _load_version,
    _make_config,
    _make_workspace,
)

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


@dc.dataclass(frozen=True, slots=True)
class _ReadmeTransposeScenario:
    """Parameters for README transposition tests."""

    test_id: str
    exclude: tuple[str, ...] = ()
    check_version_unchanged: bool = False


def _toml_fence(snippet: str) -> str:
    """Return ``snippet`` wrapped in a fenced TOML code block."""
    return f"```toml\n{snippet}```\n"


def _rewrite_fence(text: str) -> tuple[str, bool]:
    """Rewrite fenced TOML for the ``alpha`` crate to version ``2.0.0``."""
    return bump_docs.rewrite_markdown_toml_fences(text, {"alpha"}, "2.0.0")


def _assert_alpha_rewritten(text: str) -> None:
    """Assert the documentation pass rewrites ``alpha`` inside ``text``."""
    updated, changed = _rewrite_fence(text)
    assert changed is True, f"the documentation pass must rewrite {text!r}"
    assert 'alpha = "2.0.0"' in updated, updated


def test_documentation_pass_rewrites_workspace_and_target_tables() -> None:
    """The two dependency locations reported in issue #370 are rewritten."""
    _assert_alpha_rewritten(_toml_fence('[workspace.dependencies]\nalpha = "1.0.0"\n'))
    _assert_alpha_rewritten(
        _toml_fence("[target.'cfg(unix)'.dependencies]\nalpha = \"1.0.0\"\n")
    )


@pytest.mark.parametrize(
    "section", ["dependencies", "dev-dependencies", "build-dependencies"]
)
def test_documentation_pass_rewrites_workspace_sections(section: str) -> None:
    """Every canonical section is rewritten under ``[workspace]``."""
    _assert_alpha_rewritten(_toml_fence(f'[workspace.{section}]\nalpha = "1.0.0"\n'))


@pytest.mark.parametrize(
    "selector",
    ["'cfg(unix)'", '"cfg(windows)"', "x86_64-unknown-linux-gnu"],
    ids=["single-quoted-cfg", "double-quoted-cfg", "bare-triple"],
)
@pytest.mark.parametrize(
    "section", ["dependencies", "dev-dependencies", "build-dependencies"]
)
def test_documentation_pass_rewrites_target_selector_variants(
    selector: str, section: str
) -> None:
    """Target-specific tables are rewritten for every selector and section."""
    _assert_alpha_rewritten(
        _toml_fence(f'[target.{selector}.{section}]\nalpha = "1.0.0"\n')
    )


def test_readme_transposition_failure_propagates_from_preparation(
    tmp_path: pathlib.Path,
) -> None:
    """A README failure propagates unchanged for the CLI to diagnose."""
    workspace, _manifests = _build_workspace_with_internal_deps(
        tmp_path,
        specs=(_CrateSpec(name="alpha", readme_workspace=True),),
    )
    configuration = _make_config()
    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    with pytest.raises(ReadmeTranspositionError) as excinfo:
        bump_pipeline._prepare_plan(context, "1.2.3")

    assert "Workspace README.md is required" in str(excinfo.value), (
        "preparation must propagate the actionable README diagnostic"
    )


def test_run_updates_documentation_snippets(
    tmp_path: pathlib.Path, snapshot: SnapshotAssertion
) -> None:
    """Documentation TOML fences are rewritten to reference the new version."""
    workspace = _make_workspace(tmp_path)
    readme_path = tmp_path / "README.md"
    readme_path.write_text(
        """# Sample\n\n```toml\n[dependencies]\nalpha = \"0.1.0\"\n```\n""",
        encoding="utf-8",
    )
    configuration = _make_config(documentation_globs=("README.md",))

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    assert message == snapshot, "the bump summary drifted from its recorded snapshot"
    updated_readme = readme_path.read_text(encoding="utf-8")
    assert 'alpha = "1.2.3"' in updated_readme, (
        f"expected README.md rewritten to the new version: {updated_readme!r}"
    )


@pytest.mark.parametrize(
    "scenario",
    [
        _ReadmeTransposeScenario(
            test_id="included_crate",
        ),
        _ReadmeTransposeScenario(
            test_id="excluded_crate",
            exclude=("alpha",),
            check_version_unchanged=True,
        ),
    ],
    ids=lambda s: s.test_id,
)
def test_run_transposes_workspace_readme_to_crates(
    tmp_path: pathlib.Path,
    scenario: _ReadmeTransposeScenario,
    snapshot: SnapshotAssertion,
) -> None:
    """README adoption follows crate opt-in regardless of version-bump exclusion."""
    workspace, _manifests = _build_workspace_with_internal_deps(
        tmp_path,
        specs=(_CrateSpec(name="alpha", readme_workspace=True),),
    )
    (tmp_path / "README.md").write_text(
        "# Sample\n\nSee [Guide](docs/guide.md).\n",
        encoding="utf-8",
    )
    configuration = _make_config(exclude=scenario.exclude)

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    crate_readme = tmp_path / "crates" / "alpha" / "README.md"
    assert message == snapshot, "the bump summary drifted from its recorded snapshot"
    scrubbed = normalized(
        crate_readme.read_text(encoding="utf-8"),
        "../../",
        placeholder="<link-prefix>",
    )
    assert scrubbed == snapshot(name="crate_readme"), (
        "expected crate README to rewrite relative links to the crate location"
    )
    if scenario.check_version_unchanged:
        assert (
            _load_version(tmp_path / "crates" / "alpha" / "Cargo.toml", ("package",))
            == "0.1.0"
        ), "expected crate version to remain 0.1.0 when bump excludes the crate"
