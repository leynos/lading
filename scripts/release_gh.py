"""Run ``gh`` through the release catalogue; the uploader's process edge.

This is the driven adapter behind the ``UploadRunner`` port: the one module of
the release uploader that starts a process, and with it the only module that
imports cuprum. It imports the port type rather than defining it -- the type
lives in ``release_port``, so the policy module that reads a
``CommandOutcome`` does not depend on this module or on cuprum.

Keeping the catalogue, the programme, and the translation together here means a
change to how ``gh`` is invoked -- such as the move to a new cuprum release --
touches one small module rather than the logic that decides what to upload.
"""

from __future__ import annotations

import collections.abc as cabc
import sys
import typing as typ

from cuprum import (
    Program,
    ProgramCatalogue,
    ProjectSettings,
    RunOutputOptions,
    scoped,
    sh,
)
from release_port import CommandOutcome
from release_span import record_gh_span

GH = Program("gh")
_RELEASE_PROJECT = ProjectSettings(
    name="lading-release",
    programs=(GH,),
    documentation_locations=("docs/developers-guide.md#release-workflow",),
    noise_rules=(),
)
RELEASE_CATALOGUE = ProgramCatalogue(projects=(_RELEASE_PROJECT,))

if typ.TYPE_CHECKING:
    import io


def run_gh(
    arguments: cabc.Sequence[str],
    *,
    log: io.TextIOBase | typ.TextIO = sys.stderr,
) -> CommandOutcome:
    """Run ``gh`` through the release catalogue and report the result.

    This is the production edge: the only place the script starts a process.
    The invocation is timed and reported as a span record through ``log``,
    which is where the process edge's own telemetry belongs -- the caller that
    decides what to upload should not have to know the process was spanned.

    Parameters
    ----------
    arguments : cabc.Sequence[str]
        The arguments to pass to ``gh``, without the programme name.
    log : io.TextIOBase | typ.TextIO
        Where the span record is written. Errors go to stderr, so the record
        is written there too rather than into the output stream.

    Returns
    -------
    CommandOutcome
        The exit status and captured streams.
    """
    with record_gh_span(log) as set_exit:
        with scoped(catalogue=RELEASE_CATALOGUE):
            # capture=True is cuprum's default, but it is stated here because
            # the whole point of the call is to keep gh's diagnostic: without
            # capture both streams come back None and a failure reports no
            # reason at all.
            command = sh.make(GH, catalogue=RELEASE_CATALOGUE)(*arguments)
            result = command.run_sync(output=RunOutputOptions(capture=True))
        set_exit(result.exit_code)
    return CommandOutcome(
        exit_code=result.exit_code,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )
