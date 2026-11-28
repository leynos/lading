"""Integration tests for documentation previews during a bump."""

import pathlib
import typing as typ

import pytest

from lading import config as config_module
from lading.commands import bump, bump_docs, bump_pipeline, bump_plan, bump_plan_builder
from lading.utils.confinement import confine_relative_path
from tests.helpers import workspace_builders

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

    from lading.utils.confinement import ConfinedPath


def test_prepare_plan_records_exact_document_previews(
    tmp_path: pathlib.Path,
) -> None:
    """The plan retains the exact text read and the exact proposed write."""
    workspace = workspace_builders._make_workspace(tmp_path)
    readme_path = tmp_path / "README.md"
    original = '# Sample\n\n```toml\n[dependencies]\nalpha = "0.1.0"\n```\n'
    readme_path.write_bytes(original.encode("utf-8"))
    configuration = workspace_builders._make_config(documentation_globs=("README.md",))
    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    plan = bump_pipeline._prepare_plan(context, "1.2.3")

    (preview,) = plan.document_previews
    assert preview.path == readme_path, "the preview must name the rewritten file"
    assert preview.original == original, "the preview must retain the raw source"
    assert 'alpha = "1.2.3"' in preview.proposed, (
        "the proposal must carry the rewritten version"
    )


def test_prepare_documentation_updates_reads_each_source_once(
    tmp_path: pathlib.Path,
) -> None:
    """The rewrite and its preview share one read of each selected file."""
    readme_path = tmp_path / "README.md"
    original = '# Sample\n\n```toml\n[dependencies]\nalpha = "0.1.0"\n```\n'
    readme_path.write_bytes(original.encode("utf-8"))
    reads: list[pathlib.Path] = []

    def read_source(path: ConfinedPath) -> str:
        """Record each read and return the raw source the preview must retain."""
        reads.append(path.path)
        return path.path.read_text(encoding="utf-8", newline="")

    request = bump_docs.DocumentationUpdateRequest(
        target_version="1.2.3",
        updated_crates=frozenset({"alpha"}),
        read_source=read_source,
    )

    ((path, source, content),) = bump_docs._prepare_documentation_updates_with_sources(
        tmp_path,
        config_module.DocumentationConfig(globs=("README.md",)),
        request,
    )

    assert path.path == readme_path, "the update must name the selected file"
    assert reads == [readme_path], "each selected file must be read exactly once"
    assert source == original, "the preview source must be the text that was read"
    assert 'alpha = "1.2.3"' in content, "the rewrite must target the dependency"


def test_public_prepare_documentation_updates_keeps_two_value_rows(
    tmp_path: pathlib.Path,
) -> None:
    """The exported tuple rows keep their ``(path, content)`` shape."""
    readme_path = tmp_path / "README.md"
    readme_path.write_bytes(b'```toml\n[dependencies]\nalpha = "0.1.0"\n```\n')
    request = bump_docs.DocumentationUpdateRequest(
        target_version="1.2.3",
        updated_crates=frozenset({"alpha"}),
        read_source=lambda path: path.path.read_text(encoding="utf-8"),
    )

    updates = bump_docs.prepare_documentation_updates(
        tmp_path,
        config_module.DocumentationConfig(globs=("README.md",)),
        request,
    )

    ((path, content),) = updates
    assert path.path == readme_path, "the row must name the selected file"
    assert 'alpha = "1.2.3"' in content, "the row must carry the rewritten text"


def test_prepare_documentation_records_no_preview_without_a_write(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documentation stage records a preview only when its write remains."""
    workspace = workspace_builders._make_workspace(tmp_path)
    readme_path = tmp_path / "README.md"
    text = '```toml\n[dependencies]\nalpha = "1.2.3"\n```\n'
    readme_path.write_text(text, encoding="utf-8")
    confined = confine_relative_path(tmp_path, "README.md", key="test")
    configuration = workspace_builders._make_config(documentation_globs=("README.md",))
    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    monkeypatch.setattr(
        bump_docs,
        "_prepare_documentation_updates_with_sources",
        lambda *_args: ((confined, text, text),),
    )
    plan = bump_pipeline._prepare_plan(context, "1.2.3")

    assert not plan.document_previews, (
        "a proposal identical to the source must not record a diff"
    )


@pytest.mark.parametrize("crate_newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_transposition_restore_drops_the_documentation_preview(
    tmp_path: pathlib.Path,
    crate_newline: str,
) -> None:
    """A later README restore removes the write and its diff entry together."""
    workspace, _manifests = workspace_builders._build_workspace_with_internal_deps(
        tmp_path,
        specs=(workspace_builders._CrateSpec(name="alpha", readme_workspace=True),),
    )
    readme = '# Sample\n\n```toml\n[dependencies]\nalpha = "0.1.0"\n```\n'
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    crate_readme = tmp_path / "crates" / "alpha" / "README.md"
    crate_readme.write_bytes(readme.replace("\n", crate_newline).encode("utf-8"))
    configuration = workspace_builders._make_config(
        documentation_globs=("crates/*/README.md",)
    )
    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    plan = bump_pipeline._prepare_plan(context, "1.2.3")

    assert all(proposal.path.path != crate_readme for proposal in plan.proposals), (
        "transposition must restore the crate README, not rewrite it"
    )
    assert not plan.document_previews, (
        "a restored file must not keep a documentation diff"
    )


def test_prepare_plan_keeps_the_snapshot_it_read(
    tmp_path: pathlib.Path,
) -> None:
    """A file rewritten under preparation cannot suppress the planned write."""
    workspace = workspace_builders._make_workspace(tmp_path)
    readme_path = tmp_path / "README.md"
    readme_path.write_bytes(b'```toml\n[dependencies]\nalpha = "0.1.0"\n```\n')
    configuration = workspace_builders._make_config(documentation_globs=("README.md",))
    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(configuration=configuration, workspace=workspace),
    )
    real_read_raw = bump_plan_builder.PlanBuilder.read_raw

    def change_after_read(
        builder: bump_plan_builder.PlanBuilder, path: ConfinedPath
    ) -> str | None:
        """Return the real raw read, then move the file to the proposed text."""
        content = real_read_raw(builder, path)
        readme_path.write_bytes(b'```toml\n[dependencies]\nalpha = "1.2.3"\n```\n')
        return content

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bump_plan_builder.PlanBuilder, "read_raw", change_after_read)
        plan = bump_pipeline._prepare_plan(context, "1.2.3")

    original = '```toml\n[dependencies]\nalpha = "0.1.0"\n```\n'
    (preview,) = plan.document_previews
    assert preview.original == original, (
        "the preview must keep the source text preparation actually read"
    )
    assert any(proposal.path.path == readme_path for proposal in plan.proposals), (
        "the plan must still write the documentation file it read"
    )


def test_prepare_plan_records_crlf_original_and_writes_lf(
    tmp_path: pathlib.Path,
) -> None:
    """A CRLF source is previewed raw and written as the LF proposal."""
    workspace = workspace_builders._make_workspace(tmp_path)
    readme_path = tmp_path / "README.md"
    original = '# Sample\r\n\r\n```toml\r\n[dependencies]\r\nalpha = "0.1.0"\r\n```\r\n'
    readme_path.write_bytes(original.encode("utf-8"))
    configuration = workspace_builders._make_config(documentation_globs=("README.md",))
    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(configuration=configuration, workspace=workspace),
    )
    plan = bump_pipeline._prepare_plan(context, "1.2.3")

    (preview,) = plan.document_previews
    assert preview.original == original, "the preview must retain the CRLF source"
    assert "\r" not in preview.proposed, "the proposal must use LF endings"

    bump_plan.commit_plan(plan)

    assert preview.proposed.encode("utf-8") == readme_path.read_bytes(), (
        "the committed bytes must equal the preview's proposal"
    )


def test_overlapping_documentation_globs_yield_one_preview(
    tmp_path: pathlib.Path,
) -> None:
    """Overlapping glob patterns resolve to one update and one preview."""
    workspace = workspace_builders._make_workspace(tmp_path)
    readme_path = tmp_path / "README.md"
    readme_path.write_bytes(b'```toml\n[dependencies]\nalpha = "0.1.0"\n```\n')
    configuration = workspace_builders._make_config(
        documentation_globs=("README.md", "*.md")
    )
    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    plan = bump_pipeline._prepare_plan(context, "1.2.3")

    assert len(plan.document_previews) == 1, (
        "overlapping globs must not duplicate the documentation preview"
    )


def test_prepare_documentation_updates_propagates_reader_failure(
    tmp_path: pathlib.Path,
) -> None:
    """A reader failure aborts preparation instead of dropping a preview."""
    from lading.utils.confinement import BumpTargetExaminationError

    readme_path = tmp_path / "README.md"
    readme_path.write_bytes(b'```toml\n[dependencies]\nalpha = "0.1.0"\n```\n')

    def fail(_path: object) -> str:
        """Fail the read the way a vanished source would."""
        message = "boom"
        raise BumpTargetExaminationError(message)

    request = bump_docs.DocumentationUpdateRequest(
        target_version="1.2.3",
        updated_crates=frozenset({"alpha"}),
        read_source=fail,
    )

    with pytest.raises(BumpTargetExaminationError, match="boom"):
        bump_docs._prepare_documentation_updates_with_sources(
            tmp_path,
            config_module.DocumentationConfig(globs=("README.md",)),
            request,
        )


def test_run_dry_run_with_diff_keeps_documentation_bytes(
    tmp_path: pathlib.Path, snapshot: SnapshotAssertion
) -> None:
    """The bump summary appends a diff while the dry run leaves files alone."""
    workspace = workspace_builders._make_workspace(tmp_path)
    readme_path = tmp_path / "README.md"
    original = '# Sample\n\n```toml\n[dependencies]\nalpha = "0.1.0"\n```\n'
    readme_path.write_bytes(original.encode("utf-8"))
    configuration = workspace_builders._make_config(documentation_globs=("README.md",))

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            dry_run=True,
            diff=True,
            configuration=configuration,
            workspace=workspace,
        ),
    )

    assert message.splitlines() == snapshot, (
        "the summary and appended documentation diff must match the snapshot"
    )
    assert readme_path.read_bytes() == original.encode("utf-8"), (
        "the dry run must not write the documentation file bytes"
    )
