"""Run ``gh`` through the release catalogue; the uploader's only process edge.

This is the driven adapter behind the ``UploadRunner`` port in
``release_wheel_upload``: the one module of the release uploader that starts a
process. Keeping the catalogue, the programme, and the outcome record together
here means a change to how ``gh`` is invoked -- such as the move to a new
cuprum release -- touches one small module rather than the logic that decides
what to upload.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc

from cuprum import (
    Program,
    ProgramCatalogue,
    ProjectSettings,
    RunOutputOptions,
    scoped,
    sh,
)

GH = Program("gh")
_RELEASE_PROJECT = ProjectSettings(
    name="lading-release",
    programs=(GH,),
    documentation_locations=("docs/developers-guide.md#release-workflow",),
    noise_rules=(),
)
RELEASE_CATALOGUE = ProgramCatalogue(projects=(_RELEASE_PROJECT,))


@dc.dataclass(frozen=True, slots=True)
class CommandOutcome:
    """What a ``gh`` invocation reported back.

    This is the whole of the command dependency's return contract, so a test
    runner can satisfy it without a process.
    """

    exit_code: int
    stdout: str = ""
    stderr: str = ""


def run_gh(arguments: cabc.Sequence[str]) -> CommandOutcome:
    """Run ``gh`` through the release catalogue and report the result.

    This is the production edge: the only place the script starts a process.

    Parameters
    ----------
    arguments : cabc.Sequence[str]
        The arguments to pass to ``gh``, without the programme name.

    Returns
    -------
    CommandOutcome
        The exit status and captured streams.
    """
    with scoped(catalogue=RELEASE_CATALOGUE):
        # capture=True is cuprum's default, but it is stated here because the
        # whole point of the call is to keep gh's diagnostic: without capture
        # both streams come back None and a failure reports no reason at all.
        command = sh.make(GH, catalogue=RELEASE_CATALOGUE)(*arguments)
        result = command.run_sync(output=RunOutputOptions(capture=True))
    return CommandOutcome(
        exit_code=result.exit_code,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )
