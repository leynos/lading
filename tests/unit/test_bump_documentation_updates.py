"""Integration tests for documentation updates in :mod:`lading.commands.bump`."""

from __future__ import annotations

import dataclasses as dc
import pathlib
import typing as typ

import pytest

from lading.commands import bump
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

    assert message == snapshot
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
    assert message == snapshot
    assert crate_readme.read_text(encoding="utf-8") == (
        "# Sample\n\nSee [Guide](../../docs/guide.md).\n"
    ), "expected crate README to rewrite relative links to the crate location"
    if scenario.check_version_unchanged:
        assert (
            _load_version(tmp_path / "crates" / "alpha" / "Cargo.toml", ("package",))
            == "0.1.0"
        ), "expected crate version to remain 0.1.0 when bump excludes the crate"
