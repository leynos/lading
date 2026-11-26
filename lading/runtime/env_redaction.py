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


def redact_environment(env: cabc.Mapping[str, object]) -> dict[str, str]:
    """Return ``env`` with sensitive values replaced by placeholders.

    Returns
    -------
    dict[str, str]
        The mapping sorted by key, secret-looking values masked.
    """
    redacted: dict[str, str] = {}
    for key, value in env.items():
        redacted[key] = "<redacted>" if _should_redact_env_key(key) else str(value)
    return dict(sorted(redacted.items()))


def _should_redact_env_key(key: str) -> bool:
    """Return True when ``key`` likely contains secret material."""
    upper_key = key.upper()
    return any(token in upper_key for token in _ENV_REDACTION_TOKENS)
