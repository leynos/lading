"""Resolution of the publish pre-flight skip decision.

``lading publish`` normally rebuilds and retests the workspace before it
packages anything. A caller that has already run those checks — a continuous
integration job that runs the suite in an earlier step, for example — can
suppress the repeat with ``[preflight] skip`` in ``lading.toml``, with the
``--skip-preflight`` command-line flag, or with the environment variable that
backs it.

This module owns only the decision: which input asked for the skip, and
whether it asked for it at all. Command-line and environment parsing belong to
:mod:`lading.cli`, and the checks themselves belong to
:mod:`lading.commands.publish_preflight`. Keeping the decision separate lets
every caller log the same provenance sentence, so a publish log never reads as
though the skipped checks ran.

Examples
--------
```python
from lading.commands.publish_skip import resolve_skip_preflight

decision = resolve_skip_preflight(None, configured=True)
decision.skip, decision.source
```
"""

from __future__ import annotations

import dataclasses as dc

CONFIGURATION_SOURCE = "the lading.toml [preflight] skip setting"
"""Provenance label used when the configuration file supplies the decision."""


@dc.dataclass(frozen=True, slots=True)
class SkipPreflightDecision:
    """A pre-flight skip decision together with the input that supplied it.

    The same type carries an explicit caller override and the resolved
    outcome: an override is simply a decision whose source is not the
    configuration file.

    Parameters
    ----------
    skip:
        When :data:`True`, the pre-flight's auxiliary builds, ``cargo check``,
        and ``cargo test`` are suppressed.
    source:
        Human-readable description of the input that supplied ``skip``, used
        verbatim in the publish log so operators can see why the checks did
        not run.

    Examples
    --------
    >>> SkipPreflightDecision(skip=True, source="the --skip-preflight flag").skip
    True
    """

    skip: bool
    source: str


def resolve_skip_preflight(
    override: SkipPreflightDecision | None, *, configured: bool
) -> SkipPreflightDecision:
    """Resolve an explicit override against the configured default.

    Parameters
    ----------
    override : SkipPreflightDecision | None
        An explicit decision from a caller, or :data:`None` when no caller
        expressed one.
    configured : bool
        The ``[preflight] skip`` value from the active configuration.

    Returns
    -------
    SkipPreflightDecision
        ``override`` when one was supplied, otherwise the configured value
        labelled with :data:`CONFIGURATION_SOURCE`.

    Examples
    --------
    >>> resolve_skip_preflight(None, configured=False).skip
    False
    >>> resolve_skip_preflight(None, configured=True).source
    'the lading.toml [preflight] skip setting'
    >>> explicit = SkipPreflightDecision(skip=False, source="the caller")
    >>> resolve_skip_preflight(explicit, configured=True).skip
    False
    """
    if override is not None:
        return override
    return SkipPreflightDecision(skip=configured, source=CONFIGURATION_SOURCE)


__all__ = [
    "CONFIGURATION_SOURCE",
    "SkipPreflightDecision",
    "resolve_skip_preflight",
]
