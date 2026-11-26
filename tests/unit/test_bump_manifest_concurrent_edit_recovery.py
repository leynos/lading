"""Rollback preserves concurrent source edits observed before a bump write."""

from pathlib import Path
from unittest import mock

from tomlkit import parse as parse_toml

from lading.commands import bump_manifests
from lading.commands.bump_manifest_rewrites import ManifestRewriteWriteError
from tests.helpers.manifest_rewrite_recovery import (
    _make_partial_write_failure_case,
    _run_partial_write_failure,
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
    original_update_manifest = bump_manifests._update_manifest

    def edit_beta_before_update(
        manifest_path: Path,
        selectors: tuple[tuple[str, ...], ...],
        target_version: str,
        update_context: bump_manifests._ManifestUpdateContext,
    ) -> bump_manifests._ManifestUpdateResult:
        """Insert an external edit before the selected manifest is parsed."""
        if manifest_path == beta_manifest:
            manifest_path.write_text(parsed_beta_text, encoding="utf-8")
        return original_update_manifest(
            manifest_path, selectors, target_version, update_context
        )

    with mock.patch.object(
        bump_manifests, "_update_manifest", side_effect=edit_beta_before_update
    ):

        def assert_beta_was_bumped(_path: Path) -> None:
            """Prove beta changed before the injected fixture write fails."""
            beta_text = beta_manifest.read_text(encoding="utf-8")
            assert parse_toml(beta_text)["package"]["version"] == "1.2.3", (
                "beta should be bumped before the second fixture write"
            )
            assert "# edited after planning" in beta_text, (
                "the concurrent comment should survive the beta manifest update"
            )

        failure = _run_partial_write_failure(
            case, before_failure=assert_beta_was_bumped
        )

    assert isinstance(failure, ManifestRewriteWriteError), (
        "the injected second fixture write must produce a write failure"
    )
    assert failure.failed_path == case.fixtures[1].resolve(), (
        "the error must identify the second fixture manifest"
    )
    assert failure.completed_writes == 1, (
        "the error must report the first fixture as the only completed write"
    )
    assert beta_manifest.read_text(encoding="utf-8") == parsed_beta_text, (
        "rollback should restore the text parsed by this update, including edits "
        "that landed after planning"
    )
