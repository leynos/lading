"""Tests for the release uploader's ``gh`` adapter.

The adapter is the only module that starts a process, so these tests are the
ones that cross the command boundary: the argv the release actually runs, and
the capture that carries ``gh``'s diagnostic back to its caller.
"""

import collections.abc as cabc
import types
import typing as typ
from pathlib import Path

import pytest

from tests.helpers.script_imports import import_script_module

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from cmd_mox import CmdMox


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
    allowlist = release_gh.RELEASE_CATALOGUE.allowlist

    assert str(release_gh.GH) == "gh", f"the programme is {release_gh.GH}"
    assert set(allowlist) == {release_gh.GH}, f"the catalogue admits {allowlist}"


def test_release_asset_adapter_reads_names_from_json(
    release_gh: types.ModuleType,
) -> None:
    """GitHub JSON parsing and command construction stay in the adapter."""
    calls: list[tuple[str, ...]] = []

    def run(arguments: cabc.Sequence[str]) -> types.SimpleNamespace:
        calls.append(tuple(arguments))
        return types.SimpleNamespace(
            exit_code=0,
            stdout='{"assets":[{"name":"one.whl"},{"name":"two.tar.gz"}]}',
            stderr="",
        )

    assets = release_gh.GitHubReleaseAssets(run=run)

    assert assets.attached_names("v1.2.3") == {"one.whl", "two.tar.gz"}, (
        "the adapter must return every asset filename"
    )
    assert calls == [("release", "view", "v1.2.3", "--json", "assets")], (
        "asset inspection must request JSON asset records"
    )


def test_release_asset_adapter_uploads_only_the_given_paths(
    release_gh: types.ModuleType, tmp_path: Path
) -> None:
    """The GitHub adapter owns upload argv and returns uploaded paths."""
    wheel = tmp_path / "one.whl"
    sdist = tmp_path / "two.tar.gz"
    calls: list[tuple[str, ...]] = []

    def run(arguments: cabc.Sequence[str]) -> types.SimpleNamespace:
        calls.append(tuple(arguments))
        return types.SimpleNamespace(exit_code=0, stdout="", stderr="")

    assets = release_gh.GitHubReleaseAssets(run=run)

    assert assets.upload_missing("v1.2.3", (wheel, sdist)) == (wheel, sdist), (
        "successful upload must return the attached paths"
    )
    assert calls == [("release", "upload", "v1.2.3", str(wheel), str(sdist))], (
        "GitHub upload must contain only the supplied paths"
    )


@pytest.mark.parametrize(
    "stdout",
    ["not-json", "{}", '{"assets":[{}]}'],
    ids=["invalid-json", "missing-assets", "missing-name"],
)
def test_release_asset_adapter_rejects_malformed_asset_responses(
    release_gh: types.ModuleType, stdout: str
) -> None:
    """Malformed GitHub responses fail at the adapter boundary."""
    result = types.SimpleNamespace(exit_code=0, stdout=stdout, stderr="")
    assets = release_gh.GitHubReleaseAssets(run=lambda _arguments: result)

    with pytest.raises(release_gh.ReleaseOperationError):
        assets.attached_names("v1.2.3")


def test_release_asset_adapter_translates_command_failures(
    release_gh: types.ModuleType,
) -> None:
    """A failed ``gh`` command becomes the domain operation error."""
    result = types.SimpleNamespace(
        exit_code=1, stdout="", stderr="HTTP 404: release not found"
    )
    assets = release_gh.GitHubReleaseAssets(run=lambda _arguments: result)

    with pytest.raises(release_gh.ReleaseOperationError, match="release not found"):
        assets.attached_names("v1.2.3")
