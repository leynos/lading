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

from lading.runtime.credentials import credential_file_tokens
from lading.runtime.subprocess_runner import SubprocessContext, run_logged
from lading.utils.redaction import (
    add_active_secrets,
    ordered_secrets,
    registry_token_values,
)

if typ.TYPE_CHECKING:
    from lading.runtime.runner import CommandRunner


def invocation_cargo_home(
    default: Path | None, cwd: Path | None, env: cabc.Mapping[str, str] | None
) -> Path | None:
    """Return the Cargo home one command runs with, as seen from lading.

    The command's own ``CARGO_HOME`` wins over ``default`` (the entry point's).
    A relative home is relative to the command's working directory, as Cargo
    resolves it, so it is joined to ``cwd`` rather than made absolute once.

    Returns
    -------
    Path | None
        The home to read credentials from, or ``None`` when there is none.

    Examples
    --------
    >>> invocation_cargo_home(Path("chome"), Path("/w/member"), None)
    PosixPath('/w/member/chome')
    >>> invocation_cargo_home(None, Path("/w"), {"CARGO_HOME": "/opt/cargo"})
    PosixPath('/opt/cargo')
    """
    configured = (env or {}).get("CARGO_HOME")
    home = Path(configured) if configured else default
    if home is None or cwd is None:
        return home
    return cwd / home


def redacting_runner(
    secrets: cabc.Sequence[str], *, cargo_home: Path | None = None
) -> CommandRunner:
    """Return a runner whose relayed output and logs never show ``secrets``.

    Parameters
    ----------
    secrets : cabc.Sequence[str]
        Exact secret values, typically from
        :func:`~lading.utils.redaction.registry_token_values`. Token values
        in a call's own ``env`` are hidden as well.
    cargo_home : Path | None
        The entry point's Cargo home, possibly relative. Before each command
        starts, the credentials file of the home that command runs with (see
        :func:`invocation_cargo_home`) is read, and its tokens are hidden from
        that command's output and added to the active secrets for later
        messages.

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
        home = invocation_cargo_home(cargo_home, cwd, env)
        found = (*registry_token_values(env or {}), *credential_file_tokens(home))
        add_active_secrets(found)
        hidden = ordered_secrets((*secrets, *found))
        context = SubprocessContext(
            cwd=cwd, env=env, echo_stdout=echo_stdout, secrets=hidden
        )
        return run_logged(command, context)

    return run
