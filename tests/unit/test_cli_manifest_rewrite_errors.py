"""CLI reporting tests for configured manifest rewrite failures."""

import logging
from pathlib import Path

import pytest

from lading import cli
from lading.commands.bump_manifest_rewrites import ManifestRewriteError


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(
            ManifestRewriteError.pre_bump_versions_unavailable(
                ("alpha",), Path("fixtures/Cargo.toml")
            ),
            id="missing-pre-bump-version",
        ),
    ],
)
def test_planning_errors_with_paths_are_not_reported_as_write_failures(
    error: ManifestRewriteError,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Planning errors remain plain errors without misleading write progress."""
    with caplog.at_level(logging.ERROR, logger=cli.__name__):
        cli._print_expected_command_error(error)

    assert not caplog.records, "planning errors should not emit write-failure events"
    captured = capsys.readouterr()
    assert "Manifest rewrite error:" in captured.err, (
        "planning failures should remain visible on standard error"
    )
    assert "manifest_rewrite failed:" not in captured.err, (
        "planning failures should not use the write-failure event label"
    )
    assert "completed=" not in captured.err, (
        "planning failures should not report write progress"
    )
    assert not captured.out, "planning failure diagnostics belong on standard error"
