"""Mask secret-looking environment values before they are logged.

This is the key-based half of credential hygiene: it decides from a variable's
*name* whether its value may appear in a DEBUG log of subprocess environment
overrides. :mod:`lading.runtime.redaction` is the value-based half, which hides
known registry tokens from subprocess output.

Examples
--------
>>> redact_environment({"CARGO_REGISTRY_TOKEN": "abc", "PATH": "/usr/bin"})
{'CARGO_REGISTRY_TOKEN': '<redacted>', 'PATH': '/usr/bin'}
"""

import collections.abc as cabc
import logging

from lading.utils.redaction import marker_for, redact_text

_LOGGER = logging.getLogger("lading.runtime.subprocess_runner")

_ENV_REDACTION_TOKENS = (
    "TOKEN",
    "AUTH",
    "BEARER",
    "PASS",
    "CRED",
    "PASSPHRASE",
    "SECRET",
    "KEY",
)


def redact_environment(
    env: cabc.Mapping[str, object], secrets: cabc.Sequence[str] = ()
) -> dict[str, str]:
    """Return ``env`` with sensitive values replaced by placeholders.

    A value is hidden when its *name* looks secret, and also when it contains
    one of the known ``secrets``, so a token copied into an innocuous variable
    (``MIRROR_URL``) is hidden as well.

    Returns
    -------
    dict[str, str]
        The mapping sorted by key, secret-looking values masked.
    """
    # The masked placeholder is the one value redaction would use, so a known
    # secret that could overlap ``<redacted>`` cannot show through it either.
    marker = marker_for(secrets)
    redacted: dict[str, str] = {}
    for key, value in env.items():
        redacted[key] = (
            marker if _should_redact_env_key(key) else redact_text(str(value), secrets)
        )
    return dict(sorted(redacted.items()))


def log_subprocess_environment(
    env: cabc.Mapping[str, object] | None, secrets: cabc.Sequence[str] = ()
) -> None:
    """Log redacted environment overrides for subprocess execution."""
    if env:
        _LOGGER.debug(
            "Subprocess environment overrides: %s", redact_environment(env, secrets)
        )


def _should_redact_env_key(key: str) -> bool:
    """Return True when ``key`` likely contains secret material."""
    upper_key = key.upper()
    return any(token in upper_key for token in _ENV_REDACTION_TOKENS)
