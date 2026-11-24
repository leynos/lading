"""Recovery behavior after partial configured manifest rewrite failures."""

from pathlib import Path
from unittest import mock

import pytest
from tomlkit import parse as parse_toml

from lading.commands import (
    bump,
    bump_manifest_rewrite_application,
    bump_manifests,
    bump_pipeline,
)
from lading.commands.bump_manifest_rewrites import (
    ManifestRewriteError,
    ManifestRewriteWriteError,
)
from lading.workspace import WorkspaceCrate, WorkspaceGraph
from tests.helpers.manifest_rewrite_metrics import (
    manifest_rewrite_counter_value,
    manifest_rewrite_duration_stats,
)
from tests.helpers.manifest_rewrite_recovery import (
    _make_partial_write_failure_case,
    _PartialWriteFailureCase,
)

pytestmark = pytest.mark.usefixtures("reset_process_metrics")


class _InjectedMemberManifestError(Exception):
    """Specific failure used to stop a bump after an earlier member write."""


def _run_partial_write_failure(
    case: _PartialWriteFailureCase,
) -> ManifestRewriteError:
    """Fail the second fixture write after allowing the first atomic write."""
    write_atomic_text = bump_manifest_rewrite_application.write_atomic_text

    def fail_second_write(
        path: Path,
        text: str,
        *,
        workspace_root: Path | None = None,
    ) -> None:
        """Fail the second fixture write and forward the first atomic write."""
        if path == case.fixtures[1].resolve():
            raise OSError
        write_atomic_text(path, text, workspace_root=workspace_root)

    with (
        mock.patch.object(
            bump_manifest_rewrite_application,
            "write_atomic_text",
            side_effect=fail_second_write,
        ),
        pytest.raises(ManifestRewriteError, match="1 of 2") as failure,
    ):
        bump.run(
            case.root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=case.configuration,
                workspace=case.workspace,
            ),
        )
    return failure.value


def _assert_partial_write_state(
    case: _PartialWriteFailureCase,
    error: ManifestRewriteError,
) -> None:
    """Assert partial fixture progress and workspace rollback after failure."""
    first_after_failure = case.fixtures[0].read_text(encoding="utf-8")
    assert 'alpha = "1.2.3"' in first_after_failure, (
        "the first fixture should retain its completed dependency rewrite"
    )
    assert "../alpha-1.2.3" in first_after_failure, (
        "the first fixture should retain its completed string rewrite"
    )
    assert case.fixtures[1].read_bytes() == case.second_fixture_original, (
        "the failed manifest should retain its original bytes"
    )
    assert {
        path: path.read_bytes() for path in case.workspace_manifests
    } == case.workspace_manifests, (
        "a failed fixture rewrite must restore the workspace's pre-bump versions"
    )
    assert error.failed_path == case.fixtures[1].resolve(), (
        "the exception should identify the manifest whose write failed"
    )
    assert error.completed_writes == 1, (
        "the exception should expose the number of completed fixture writes"
    )
    assert error.planned_writes == 2, (
        "the exception should expose the number of planned fixture writes"
    )


def _run_partial_case_bump(
    case: _PartialWriteFailureCase,
    workspace: WorkspaceGraph | None = None,
) -> None:
    """Apply the target bump and verify its dependency and path rewrites."""
    bump.run(
        case.root,
        "1.2.3",
        options=bump.BumpOptions(
            rebuild_lockfiles=False,
            configuration=case.configuration,
            workspace=case.workspace if workspace is None else workspace,
        ),
    )
    for fixture in case.fixtures:
        retried_text = fixture.read_text(encoding="utf-8")
        assert 'alpha = "1.2.3"' in retried_text, (
            f"retry should update dependencies in {fixture}"
        )
        assert "../alpha-1.2.3" in retried_text, (
            f"retry should update staged package paths in {fixture}"
        )


def _run_bump_with_member_failure(
    case: _PartialWriteFailureCase,
    failed_manifest: Path,
) -> None:
    """Raise while updating a later member after earlier writes complete."""
    original_update_manifest = bump_pipeline._update_manifest

    def fail_selected_manifest(
        manifest_path: Path,
        selectors: tuple[tuple[str, ...], ...],
        target_version: str,
        options: bump.BumpOptions,
    ) -> bump_manifests._ManifestUpdateResult:
        """Inject a failure for the selected crate manifest."""
        if manifest_path == failed_manifest:
            raise _InjectedMemberManifestError
        return original_update_manifest(
            manifest_path,
            selectors,
            target_version,
            options,
        )

    with (
        mock.patch.object(
            bump_pipeline,
            "_update_manifest",
            side_effect=fail_selected_manifest,
        ),
        pytest.raises(_InjectedMemberManifestError),
    ):
        bump.run(
            case.root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=case.configuration,
                workspace=case.workspace,
            ),
        )


def _refresh_workspace_versions(case: _PartialWriteFailureCase) -> WorkspaceGraph:
    """Load package versions from disk to model a retry's fresh metadata."""
    return WorkspaceGraph(
        workspace_root=case.workspace.workspace_root,
        crates=tuple(
            WorkspaceCrate(
                name=crate.name,
                version=str(
                    parse_toml(crate.manifest_path.read_text(encoding="utf-8"))[
                        "package"
                    ]["version"]
                ),
                manifest_path=crate.manifest_path,
                root_path=crate.root_path,
                publish=crate.publish,
                readme_is_workspace=crate.readme_is_workspace,
                dependencies=crate.dependencies,
            )
            for crate in case.workspace.crates
        ),
    )


def _assert_member_failure_preserved_inputs(
    case: _PartialWriteFailureCase,
    fixture_originals: dict[Path, bytes],
) -> None:
    """Require failed member processing to preserve rewrite source versions."""
    assert {
        path: path.read_bytes() for path in case.workspace_manifests
    } == case.workspace_manifests, (
        "a member-stage failure must restore every changed workspace manifest"
    )
    assert {path: path.read_bytes() for path in case.fixtures} == fixture_originals, (
        "the pending rewrite must remain untouched after a member failure"
    )


def test_partial_write_failure_restores_workspace_and_reports_progress(
    tmp_path: Path,
) -> None:
    """A failed rewrite restores workspace manifests and reports write progress."""
    case = _make_partial_write_failure_case(tmp_path)
    failure = _run_partial_write_failure(case)
    _assert_partial_write_state(case, failure)
    assert (
        manifest_rewrite_counter_value(
            phase="application",
            outcome="failed",
            dry_run="false",
            error_category="write",
        )
        == 1
    ), "the failed rewrite application should be counted once"
    assert (
        manifest_rewrite_duration_stats(
            outcome="failed", dry_run="false", error_category="write"
        ).count
        == 1
    ), "the failed rewrite duration should be recorded once"


def test_partial_write_failure_can_be_retried(tmp_path: Path) -> None:
    """Restored workspace versions let the same bump finish on retry."""
    case = _make_partial_write_failure_case(tmp_path)
    failure = _run_partial_write_failure(case)
    _assert_partial_write_state(case, failure)
    _run_partial_case_bump(case)


def test_member_stage_failure_restores_versions_for_rewrite_retry(
    tmp_path: Path,
) -> None:
    """A later member failure restores versions used by the pending rewrite."""
    case = _make_partial_write_failure_case(tmp_path)
    fixture_originals = {path: path.read_bytes() for path in case.fixtures}
    beta_manifest = next(
        crate.manifest_path for crate in case.workspace.crates if crate.name == "beta"
    )
    _run_bump_with_member_failure(case, beta_manifest)
    _assert_member_failure_preserved_inputs(case, fixture_originals)
    _run_partial_case_bump(case, _refresh_workspace_versions(case))


def test_rewrite_failure_preserves_member_manifest_edited_after_bump_write(
    tmp_path: Path,
) -> None:
    """Rollback leaves a member manifest changed by another writer in place."""
    case = _make_partial_write_failure_case(tmp_path)
    alpha_manifest = next(
        crate.manifest_path for crate in case.workspace.crates if crate.name == "alpha"
    )
    concurrent_edit = "\n# edited while the bump was running\n"
    write_atomic_text = bump_manifest_rewrite_application.write_atomic_text

    def edit_member_then_fail_fixture(
        path: Path,
        text: str,
        *,
        workspace_root: Path | None = None,
    ) -> None:
        """Add an external edit after member writes, then fail fixture two."""
        if path == case.fixtures[1].resolve():
            with alpha_manifest.open("a", encoding="utf-8", newline="") as handle:
                handle.write(concurrent_edit)
            raise OSError
        write_atomic_text(path, text, workspace_root=workspace_root)

    with (
        mock.patch.object(
            bump_manifest_rewrite_application,
            "write_atomic_text",
            side_effect=edit_member_then_fail_fixture,
        ),
        pytest.raises(ManifestRewriteWriteError) as failure,
    ):
        bump.run(
            case.root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=case.configuration,
                workspace=case.workspace,
            ),
        )

    assert alpha_manifest.read_text(encoding="utf-8").endswith(concurrent_edit), (
        "rollback must leave a concurrent edit in the member manifest intact"
    )
    assert (
        case.workspace_manifests[case.root / "Cargo.toml"]
        == (case.root / "Cargo.toml").read_bytes()
    ), "unmodified-by-another-writer workspace manifests still roll back"
    assert str(alpha_manifest) in str(failure.value), (
        "the rollback error must identify the manifest it left in place"
    )
    assert "left the current contents in place" in str(failure.value), (
        "the rollback error must explain why the manifest was not restored"
    )


def test_rewrite_failure_rolls_back_to_text_parsed_after_a_concurrent_edit(
    tmp_path: Path,
) -> None:
    """Rollback keeps edits observed between planning and manifest update."""
    case = _make_partial_write_failure_case(tmp_path)
    beta_manifest = next(
        crate.manifest_path for crate in case.workspace.crates if crate.name == "beta"
    )
    original_beta_text = case.workspace_manifests[beta_manifest].decode("utf-8")
    concurrent_edit = "\n# edited after planning and before this manifest update\n"
    parsed_beta_text = original_beta_text + concurrent_edit
    original_update_manifest = bump_pipeline._update_manifest
    write_atomic_text = bump_manifest_rewrite_application.write_atomic_text

    def edit_beta_before_update(
        manifest_path: Path,
        selectors: tuple[tuple[str, ...], ...],
        target_version: str,
        options: bump.BumpOptions,
    ) -> bump_manifests._ManifestUpdateResult:
        """Insert an external edit before the selected manifest is parsed."""
        if manifest_path == beta_manifest:
            manifest_path.write_text(parsed_beta_text, encoding="utf-8")
        return original_update_manifest(
            manifest_path, selectors, target_version, options
        )

    def fail_second_fixture(
        path: Path,
        text: str,
        *,
        workspace_root: Path | None = None,
    ) -> None:
        """Let the first fixture write complete, then fail the second."""
        if path == case.fixtures[1].resolve():
            raise OSError
        write_atomic_text(path, text, workspace_root=workspace_root)

    with (
        mock.patch.object(
            bump_pipeline,
            "_update_manifest",
            side_effect=edit_beta_before_update,
        ),
        mock.patch.object(
            bump_manifest_rewrite_application,
            "write_atomic_text",
            side_effect=fail_second_fixture,
        ),
        pytest.raises(ManifestRewriteError),
    ):
        bump.run(
            case.root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=case.configuration,
                workspace=case.workspace,
            ),
        )

    assert beta_manifest.read_text(encoding="utf-8") == parsed_beta_text, (
        "rollback should restore the text parsed by this update, including edits "
        "that landed after planning"
    )


def test_reused_workspace_graph_uses_current_manifest_versions(
    tmp_path: Path,
) -> None:
    """String rewrites use source versions even when metadata is reused."""
    case = _make_partial_write_failure_case(tmp_path)
    alpha_manifest = next(
        crate.manifest_path for crate in case.workspace.crates if crate.name == "alpha"
    )
    alpha_text = alpha_manifest.read_text(encoding="utf-8")
    alpha_manifest.write_text(
        alpha_text.replace('version = "0.1.0"', "version.workspace = true", 1),
        encoding="utf-8",
    )
    assert "version.workspace = true" in alpha_manifest.read_text(encoding="utf-8"), (
        "the fixture must exercise an inherited workspace version"
    )
    for target_version in ("1.2.3", "1.2.4"):
        bump.run(
            case.root,
            target_version,
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=case.configuration,
                workspace=case.workspace,
            ),
        )

    for fixture in case.fixtures:
        text = fixture.read_text(encoding="utf-8")
        assert 'alpha = "1.2.4"' in text, (
            "dependency requirements should use the second target version"
        )
        assert "../alpha-1.2.4" in text, (
            "string rewrites should use versions from manifests read this run"
        )
