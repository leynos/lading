"""Contract tests for the CodeScene uploader inputs lading's workflows pass.

At the approved pin the shared uploader treats its committed
``cli-manifest.json`` as the trust anchor for the cs-coverage archive, and it
*rejects* a non-empty ``installer-checksum`` with a hard failure rather than
ignoring it. A workflow that still passes the input therefore breaks the
upload step as soon as the pin moves, and the ``CODESCENE_CLI_SHA256``
repository variable that fed it could only ever repeat the manifest digest.

Four concerns are asserted, each in its own test so a failure names the defect
rather than a bundle:

* no workflow passes the deprecated input;
* no workflow references the variable that fed it;
* every uploader reference is pinned to one approved full SHA;
* the dispatch workflow that refreshed the variable is gone.

The workflow directory is read rather than a fixed list of files being named,
so a workflow added later is covered without anyone remembering to extend
these tests. Every assertion that ranges over a collection checks the
collection has content first: a contract over an empty collection is satisfied
by deleting the thing it guards.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import pytest

WORKFLOW_DIRECTORY: typ.Final = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows"
)

pytestmark = pytest.mark.skipif(
    not WORKFLOW_DIRECTORY.is_dir(),
    reason=(
        "workflow directory not present in this working copy (for example "
        "inside mutmut's mutants/ sandbox, which does not copy .github/)"
    ),
)

#: Full SHA of the approved ``upload-codescene-coverage`` pin.
APPROVED_UPLOADER_PIN: typ.Final = "a5765019912a8ab6882b12db049c7cde635f3a85"
#: Input the uploader rejects outright at the approved pin.
DEPRECATED_INPUT: typ.Final = "installer-checksum"
#: Repository variable whose only consumer was the deprecated input.
DEPRECATED_VARIABLE: typ.Final = "CODESCENE_CLI_SHA256"
#: Dispatch workflow that refreshed the now-unread repository variable.
REFRESH_WORKFLOW: typ.Final = "get-codescene-sha.yml"

UPLOADER_REFERENCE: typ.Final = re.compile(
    r"leynos/shared-actions/\.github/actions/upload-codescene-coverage@(\S+)"
)


def _workflows() -> dict[str, str]:
    """Return every workflow file name mapped to its text.

    Returns
    -------
        The name and contents of each ``.yml`` or ``.yaml`` document in
        ``.github/workflows``, in sorted order.

    Examples
    --------
    >>> sorted(_workflows()) == sorted(_workflows())
    True
    """
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(WORKFLOW_DIRECTORY.iterdir())
        if path.suffix in {".yml", ".yaml"}
    }


def _assert_no_workflow_mentions(needle: str, reason: str) -> None:
    """Assert that no workflow mentions ``needle``.

    Both containment clauses have the same shape, so they share one assertion
    rather than being copied. ``reason`` names why the mention is wrong, and
    each caller stays a single test so a failure still names one defect.
    """
    workflows = _workflows()
    assert workflows, (
        "no workflow files were examined, so this contract would pass vacuously"
    )
    offenders = sorted(name for name, text in workflows.items() if needle in text)
    assert not offenders, f"{needle} {reason}; remove it from {', '.join(offenders)}"


def test_no_workflow_passes_the_deprecated_installer_checksum() -> None:
    """The uploader rejects a non-empty value, so no workflow may pass it."""
    _assert_no_workflow_mentions(
        DEPRECATED_INPUT,
        f"is deprecated and rejected by the uploader at {APPROVED_UPLOADER_PIN}",
    )


def test_no_workflow_references_the_deprecated_checksum_variable() -> None:
    """The variable existed only to feed the rejected input, so it must go."""
    _assert_no_workflow_mentions(
        DEPRECATED_VARIABLE,
        "fed the deprecated installer checksum and has no remaining consumer",
    )


def test_every_uploader_reference_is_pinned_to_the_approved_sha() -> None:
    """One approved SHA, asserted as an allowlist rather than as a floor.

    A floor would require ordering SHAs, which cannot be computed from a
    checkout. Naming the approved pin keeps the contract hermetic and fails
    closed on any other value, including a tag or a branch name.
    """
    references = {
        name: match.group(1)
        for name, text in _workflows().items()
        for match in UPLOADER_REFERENCE.finditer(text)
    }
    assert references, (
        "no upload-codescene-coverage reference was found, so this contract "
        "would pass vacuously; lading is expected to send coverage to CodeScene"
    )
    wrong = {
        name: pin for name, pin in references.items() if pin != APPROVED_UPLOADER_PIN
    }
    assert not wrong, (
        "every upload-codescene-coverage reference must be pinned to "
        f"{APPROVED_UPLOADER_PIN}; found {wrong}"
    )


def test_the_checksum_refresh_workflow_is_absent() -> None:
    """Nothing consumes the variable it wrote, so the workflow is dead code."""
    refresher = WORKFLOW_DIRECTORY / REFRESH_WORKFLOW
    assert not refresher.exists(), (
        f"{REFRESH_WORKFLOW} refreshed {DEPRECATED_VARIABLE}, which no workflow "
        "reads any more; delete it rather than leaving a dispatch that writes "
        "an unused repository variable"
    )
