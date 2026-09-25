"""Tests for the release uploader's ``gh`` adapter.

The adapter is the only module that starts a process, so these tests are the
ones that cross the command boundary: the argv the release actually runs, and
the capture that carries ``gh``'s diagnostic back to its caller.
"""

from __future__ import annotations

import typing as typ

import pytest

from tests.helpers.script_imports import import_script_module

try:
    from cmd_mox import CmdMox
except ModuleNotFoundError:  # pragma: no cover - runtime fallback
    CmdMox = typ.Any  # type: ignore[assignment, misc]

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    import types


@pytest.fixture(name="release_gh")
def release_gh_fixture(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Import the uploader's ``gh`` adapter.

    Returns
    -------
    types.ModuleType
        The imported ``release_gh`` module.
    """
    return import_script_module(monkeypatch, "release_gh")


def test_run_gh_returns_the_diagnostic_it_captured(
    release_gh: types.ModuleType, cmd_mox: CmdMox
) -> None:
    """The production runner carries ``gh``'s own stderr back to its caller.

    Asserted on ``run_gh`` itself rather than through the error it feeds,
    because the capture is what the record's contract promises: a runner that
    discarded the stream would still produce the right exit code.
    """
    diagnostic = "HTTP 404: release not found"
    cmd_mox.mock("gh").with_args("release", "view").returns(
        exit_code=1, stderr=diagnostic
    )

    outcome = release_gh.run_gh(("release", "view"))

    assert outcome.exit_code == 1, outcome
    assert outcome.stderr.strip() == diagnostic, outcome


def test_the_catalogue_admits_gh_alone(release_gh: types.ModuleType) -> None:
    """The adapter's programme is ``gh`` and nothing else is permitted.

    The allowlist is the security boundary the release job depends on: a
    catalogue that admitted another programme would let a defect in the
    uploader run it with the release token in its environment.
    """
    assert str(release_gh.GH) == "gh"
    assert set(release_gh.RELEASE_CATALOGUE.allowlist) == {release_gh.GH}
