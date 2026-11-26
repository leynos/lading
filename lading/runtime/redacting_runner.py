r"""A command runner that hides registry credentials from subprocess output.

:func:`redacting_runner` wraps the production subprocess adapter with the secret
values the command-line entry point read from its environment. It is used only
when there is something to hide, so a run without a registry token takes the
unwrapped path and behaves exactly as before.

Examples
--------
>>> runner = redacting_runner(("s3cret",))
>>> runner(["echo", "token s3cret"], echo_stdout=False)  # doctest: +SKIP
(0, 'token <redacted>\\n', '')
"""

import collections.abc as cabc
import typing as typ
from pathlib import Path

from lading.runtime.subprocess_runner import SubprocessContext, run_logged
from lading.utils.redaction import registry_token_values

if typ.TYPE_CHECKING:
    from lading.runtime.runner import CommandRunner


def redacting_runner(secrets: cabc.Sequence[str]) -> CommandRunner:
    """Return a runner whose relayed and captured output never shows ``secrets``.

    Parameters
    ----------
    secrets : cabc.Sequence[str]
        Exact secret values, typically from
        :func:`~lading.utils.redaction.registry_token_values`. Token values
        in a call's own ``env`` are hidden as well.

    Returns
    -------
    CommandRunner
        A runner with the subprocess adapter's behaviour, minus the secrets.
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
