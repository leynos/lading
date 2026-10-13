"""Adapt the release asset port to the allowlisted ``gh`` programme."""

import collections.abc as cabc
import dataclasses as dc
import json
import sys
import typing as typ
from pathlib import Path

from cuprum import (
    Program,
    ProgramCatalogue,
    ProjectSettings,
    RunOutputOptions,
    scoped,
    sh,
)
from release_port import ReleaseAssetStore, ReleaseOperationError
from release_span import record_gh_span

GH = Program("gh")
_RELEASE_PROJECT = ProjectSettings(
    name="lading-release",
    programs=(GH,),
    documentation_locations=("docs/developers-guide.md#publishing-a-release",),
    noise_rules=(),
)
RELEASE_CATALOGUE = ProgramCatalogue(projects=(_RELEASE_PROJECT,))

if typ.TYPE_CHECKING:
    import io


@dc.dataclass(frozen=True, slots=True)
class _CommandOutcome:
    """Captured result of one allowlisted process invocation."""

    exit_code: int
    stdout: str
    stderr: str


class _CommandRunner(typ.Protocol):
    """The process operation injected into the GitHub adapter."""

    def __call__(self, arguments: cabc.Sequence[str]) -> _CommandOutcome:
        """Run an allowlisted command and return its captured outcome.

        The adapter supplies the production implementation; tests can inject a
        recorder without starting a process.
        """
        ...


def _asset_name(asset: object) -> str:
    """Return one asset filename or raise a domain error for malformed data."""
    if isinstance(asset, dict):
        name = asset.get("name")
        if isinstance(name, str):
            return name
    message = "gh release view returned an asset without a filename"
    raise ReleaseOperationError(message)


def _decode_asset_names(stdout: str) -> frozenset[str]:
    """Decode attached filenames from ``gh``'s JSON response."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        message = f"gh release view returned invalid asset JSON: {error}"
        raise ReleaseOperationError(message) from error
    if isinstance(payload, dict):
        assets = payload.get("assets")
        if isinstance(assets, list):
            return frozenset(_asset_name(asset) for asset in assets)
    message = "gh release view returned malformed asset JSON"
    raise ReleaseOperationError(message)


def run_gh(
    arguments: cabc.Sequence[str],
    *,
    log: io.TextIOBase | typ.TextIO = sys.stderr,
) -> _CommandOutcome:
    """Run ``gh`` through the release catalogue and report the result."""
    with record_gh_span(log) as set_exit:
        with scoped(catalogue=RELEASE_CATALOGUE):
            command = sh.make(GH, catalogue=RELEASE_CATALOGUE)(*arguments)
            result = command.run_sync(output=RunOutputOptions(capture=True))
        set_exit(result.exit_code)
    return _CommandOutcome(
        exit_code=result.exit_code,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )


@dc.dataclass(frozen=True, slots=True)
class GitHubReleaseAssets(ReleaseAssetStore):
    """Implement release filename inspection and upload through ``gh``."""

    run: _CommandRunner = run_gh

    def attached_names(self, tag: str) -> frozenset[str]:
        """Return attached asset names from the release's JSON response."""
        result = self.run(("release", "view", tag, "--json", "assets"))
        self._require_success("view", result)
        return _decode_asset_names(result.stdout)

    def upload_missing(
        self, tag: str, distributions: cabc.Sequence[Path]
    ) -> tuple[Path, ...]:
        """Upload the pending paths, returning them after a successful call."""
        if not distributions:
            return ()
        arguments = ("release", "upload", tag, *(str(path) for path in distributions))
        self._require_success("upload", self.run(arguments))
        return tuple(distributions)

    @staticmethod
    def _require_success(verb: str, result: _CommandOutcome) -> None:
        """Translate a failed ``gh`` invocation into a domain error."""
        if result.exit_code == 0:
            return
        detail = result.stderr.strip() or result.stdout.strip()
        message = (
            f"gh release {verb} failed with exit code {result.exit_code}: {detail}"
        )
        raise ReleaseOperationError(message)
