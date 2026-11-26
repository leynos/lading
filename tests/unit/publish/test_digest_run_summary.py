"""Unit tests for rendering archive outcomes in the publish summary."""

import typing as typ

from lading.commands.publish_archive_check import ArchiveVerifier
from lading.commands.publish_archive_outcomes import LoggingObserver
from lading.commands.publish_digest_run import archive_summary
from lading.commands.publish_digests import ExpectedDigests

from .conftest import make_crate

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_summary_names_the_digest_source_then_each_outcome(tmp_path: Path) -> None:
    """Presentation lives in the summary code, not in the verifier."""
    verifier = ArchiveVerifier(
        ExpectedDigests("digests.txt", {}),
        lambda root: root,
        lambda name, version: None,
        LoggingObserver(),
    )
    crate = make_crate(tmp_path, "alpha")
    verifier.mark_unverified(crate, "no archive")

    lines = archive_summary(verifier)

    assert lines == (
        "Archive checks against digests.txt:",
        "- alpha 0.1.0: unverified (no archive)",
    ), "the header names the file and each crate gets one line"
