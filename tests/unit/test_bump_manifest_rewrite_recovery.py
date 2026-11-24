"""Recovery behavior after partial configured manifest rewrite failures."""

import dataclasses as dc
import typing as typ
from pathlib import Path
from unittest import mock

import pytest

from lading import config as config_module
from lading.commands import bump, bump_manifest_rewrite_application
from lading.commands.bump_manifest_rewrites import ManifestRewriteError
from lading.config import ManifestRewriteConfig, StringValueRewriteConfig
from tests.helpers.manifest_rewrite_metrics import (
    manifest_rewrite_counter_value,
    manifest_rewrite_duration_stats,
)
from tests.helpers.workspace_builders import _make_workspace

if typ.TYPE_CHECKING:
    from lading.workspace.models import WorkspaceGraph

pytestmark = pytest.mark.usefixtures("reset_process_metrics")


@dc.dataclass(frozen=True, slots=True)
class _PartialWriteFailureCase:
    """Inputs and original bytes for a configured partial-write scenario."""

    root: Path
    workspace: WorkspaceGraph
    configuration: config_module.LadingConfig
    fixtures: tuple[Path, ...]
    workspace_manifests: dict[Path, bytes]
    second_fixture_original: bytes


def _make_partial_write_failure_case(root: Path) -> _PartialWriteFailureCase:
    """Create a workspace with two configured manifests and versioned paths."""
    workspace = _make_workspace(root)
    workspace_manifests = {
        root / "Cargo.toml": (root / "Cargo.toml").read_bytes(),
        **{
            crate.manifest_path: crate.manifest_path.read_bytes()
            for crate in workspace.crates
        },
    }
    fixtures = tuple(
        root / f"tests/fixtures/{name}/Cargo.toml" for name in ("first", "second")
    )
    for fixture in fixtures:
        fixture.parent.mkdir(parents=True)
        fixture.write_text(
            '[dependencies]\nalpha = "0.1.0"\n\n'
            '[patch.crates-io]\nalpha = { path = "../alpha-0.1.0" }\n',
            encoding="utf-8",
        )
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(
            manifest_rewrites=(
                ManifestRewriteConfig(
                    paths=tuple(path.relative_to(root).as_posix() for path in fixtures),
                    string_values=(
                        StringValueRewriteConfig(
                            table=("patch", "crates-io"), field="path"
                        ),
                    ),
                ),
            )
        )
    )
    return _PartialWriteFailureCase(
        root=root,
        workspace=workspace,
        configuration=configuration,
        fixtures=fixtures,
        workspace_manifests=workspace_manifests,
        second_fixture_original=fixtures[1].read_bytes(),
    )


def _run_partial_write_failure(
    case: _PartialWriteFailureCase,
) -> ManifestRewriteError:
    """Fail the second fixture write after allowing the first atomic write."""
    write_atomic_text = bump_manifest_rewrite_application.write_atomic_text

    def fail_second_write(path: Path, text: str) -> None:
        """Fail the second fixture write and forward the first atomic write."""
        if path == case.fixtures[1].resolve():
            raise OSError
        write_atomic_text(path, text)

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

    bump.run(
        case.root,
        "1.2.3",
        options=bump.BumpOptions(
            rebuild_lockfiles=False,
            configuration=case.configuration,
            workspace=case.workspace,
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
