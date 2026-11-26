r"""A command runner that hides registry credentials from what it relays.

:func:`redacting_runner` wraps the production subprocess adapter with the secret
values the command-line entry point resolved. It is selected only for a
digest-checked run (``--expect-digests``); every other run uses the plain
runner, whatever the environment holds.

The secrets are hidden from what lading *shows*: the mirrored console output,
the logged command line and environment, and spawn errors. The captured streams
it returns are exactly what the child wrote, because lading parses them (for
example ``cargo metadata`` JSON). Messages that embed captured output are
redacted where they are built, through :func:`lading.utils.redaction.redact_active`.

Examples
--------
>>> runner = redacting_runner(("s3cret",))
>>> runner(["echo", "token s3cret"], echo_stdout=False)  # doctest: +SKIP
(0, 'token s3cret\\n', '')
"""

import collections.abc as cabc
import typing as typ
from pathlib import Path

from lading.runtime.subprocess_runner import SubprocessContext, run_logged
from lading.utils.redaction import registry_token_values

if typ.TYPE_CHECKING:
    from lading.runtime.runner import CommandRunner


def redacting_runner(secrets: cabc.Sequence[str]) -> CommandRunner:
    """Return a runner whose relayed output and logs never show ``secrets``.

    Parameters
    ----------
    secrets : cabc.Sequence[str]
        Exact secret values, typically from
        :func:`~lading.utils.redaction.registry_token_values`. Token values
        in a call's own ``env`` are hidden as well.

    Returns
    -------
    CommandRunner
        A runner with the subprocess adapter's behaviour, except that the
        console mirror, logged command line and spawn errors hide the secrets.
        The captured streams it returns are unaltered.
    """

    def run(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        """Execute ``command`` through the subprocess adapter."""
        hidden = (*secrets, *registry_token_values(env or {}))
        context = SubprocessContext(
            cwd=cwd, env=env, echo_stdout=echo_stdout, secrets=hidden
        )
        return run_logged(command, context)

    return run
