"""Resolve the process environment for the command-line entry point.

The entry point is the composition root: it is the one place that reads the
ambient environment and hands the result inward. For ``lading publish
--expect-digests`` that result is a
:class:`~lading.commands.publish_cargo_config.CargoEnvironment` (default
registry, Cargo home, registry token values) and, for the duration of the
command, a scope that hides those token values from everything lading prints.
"""

import collections.abc as cabc
import os
import sys
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from lading.commands.publish_cargo_config import CargoEnvironment
from lading.runtime.credentials import credential_file_tokens
from lading.utils.redaction import redact_active, registry_token_values, use_secrets

# Cargo's own variable naming the registry `cargo publish` targets by default.
# `--expect-digests` can only compare against crates.io, so it is read to refuse
# a run that would publish elsewhere.
CARGO_REGISTRY_DEFAULT_ENV_VAR = "CARGO_REGISTRY_DEFAULT"


def hidden_for(tokens: cabc.Sequence[str]) -> AbstractContextManager[object]:
    """Hide registry credentials from everything this invocation shows.

    Only a digest-checked publish (``--expect-digests``) activates them, so any
    other invocation behaves as it did before the option existed. The scope
    wraps the whole of ``main``, error printing included.

    Returns
    -------
    AbstractContextManager[object]
        :func:`~lading.utils.redaction.use_secrets` for the resolved values, or
        a no-op.
    """
    if not any(token.partition("=")[0] == "--expect-digests" for token in tokens):
        return nullcontext()
    return use_secrets(cargo_environment(os.environ).secrets)


def print_error(message: str) -> None:
    """Write ``message`` to stderr, hiding any active secret."""
    print(redact_active(message), file=sys.stderr)


def cargo_home(environment: cabc.Mapping[str, str]) -> Path:
    """Return Cargo's home directory: ``CARGO_HOME``, else ``~/.cargo``."""
    configured = environment.get("CARGO_HOME")
    return Path(configured) if configured else Path.home() / ".cargo"


def cargo_environment(environment: cabc.Mapping[str, str]) -> CargoEnvironment:
    """Resolve the Cargo environment a digest-checked publish depends on.

    Read here, at the composition root, so no other module touches the ambient
    environment: the default registry, Cargo's home, and every registry token
    value from the environment or the credentials file.

    Returns
    -------
    CargoEnvironment
        The resolved environment.
    """
    home = cargo_home(environment)
    secrets = {*registry_token_values(environment), *credential_file_tokens(home)}
    return CargoEnvironment(
        default_registry=environment.get(CARGO_REGISTRY_DEFAULT_ENV_VAR),
        cargo_home=home,
        secrets=tuple(sorted(secrets, key=lambda value: (-len(value), value))),
    )
