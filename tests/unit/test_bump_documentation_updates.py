"""Integration tests for documentation updates in :mod:`lading.commands.bump`."""

import dataclasses as dc
import logging
import pathlib
import tempfile
import typing as typ

import pytest

from lading import config
from lading.commands import bump, bump_docs, bump_pipeline
from lading.commands.bump_readme import ReadmeTranspositionError
from lading.exceptions import LadingError
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


def test_documentation_updates_reject_external_symlink_targets(
    tmp_path: pathlib.Path,
) -> None:
    """Neither glob resolution nor direct updates may escape via a symlink."""
    documentation_directory = tmp_path / "docs"
    documentation_directory.mkdir()

    with tempfile.TemporaryDirectory(dir=tmp_path.parent) as outside_directory:
        outside_document = pathlib.Path(outside_directory) / "guide.md"
        original = '```toml\n[dependencies]\nalpha = "0.1.0"\n```\n'
        outside_document.write_text(original, encoding="utf-8")
        link = documentation_directory / "guide.md"
        link.symlink_to(outside_document)
        documentation = config.DocumentationConfig(globs=("docs/*.md",))

        with pytest.raises(LadingError, match="within the workspace"):
            bump_docs.resolve_documentation_targets(tmp_path, documentation)

        with pytest.raises(LadingError, match="within the workspace"):
            bump_docs.update_documentation_files(
                bump_docs.DocumentationTargets(tmp_path, (link,)),
                "1.2.3",
                {"alpha"},
                dry_run=False,
            )

        assert link.is_symlink(), "the escaping documentation link must remain"
        assert outside_document.read_text(encoding="utf-8") == original, (
            "documentation updates must not overwrite a file outside the workspace"
        )


def test_documentation_targets_resolve_relative_workspace_root(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Glob resolution and containment use one canonical workspace root."""
    readme = tmp_path / "README.md"
    readme.write_text("# Fixture workspace\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)

    targets = bump_docs.resolve_documentation_targets(
        pathlib.Path(tmp_path.name),
        config.DocumentationConfig(globs=("README.md",)),
    )

    assert targets.workspace_root == tmp_path.resolve(), (
        "relative workspace roots should be canonicalized in the returned record"
    )
    assert targets.paths == (readme.resolve(),), (
        "globbing from the canonical root should select relative-root files"
    )


@dc.dataclass(frozen=True, slots=True)
class _ReadmeTransposeScenario:
    """Parameters for README transposition tests."""

    test_id: str
    exclude: tuple[str, ...] = ()
    check_version_unchanged: bool = False


def test_readme_transposition_failure_is_logged_at_pipeline_boundary(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """README failures are logged once with the affected crate name."""
    workspace, _manifests = _build_workspace_with_internal_deps(
        tmp_path,
        specs=(_CrateSpec(name="alpha", readme_workspace=True),),
    )
    configuration = _make_config()
    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    with (
        caplog.at_level(logging.ERROR, logger=bump_pipeline._log.name),
        pytest.raises(ReadmeTranspositionError),
    ):
        bump_pipeline._process_readme_transposition(context, dry_run=False)

    assert caplog.messages == ["README transposition failed for crate 'alpha'"], (
        "the pipeline boundary must log the README failure once with the "
        "affected crate name"
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
