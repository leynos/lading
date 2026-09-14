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

The source is a closed enumeration rather than free text because it is also a
metric label, and an unbounded label set would make the exit summary
unaggregatable.

Examples
--------
```python
from lading.commands.publish_skip import resolve_skip_preflight

decision = resolve_skip_preflight(None, configured=True)
decision.skip, decision.source.description
```
"""

from __future__ import annotations

import dataclasses as dc
import enum


class SkipPreflightSource(enum.StrEnum):
    """The input that supplied a pre-flight skip decision.

    The member values are metric label values; :attr:`description` is the
    human-readable phrase the publish log prints.
    """

    CONFIGURATION = "configuration"
    COMMAND_LINE = "command-line"
    ENVIRONMENT = "environment"
    IN_PROCESS = "in-process"

    @property
    def description(self) -> str:
        """The phrase naming this source in operator-facing output.

        Examples
        --------
        >>> SkipPreflightSource.ENVIRONMENT.description
        'the LADING_SKIP_PREFLIGHT environment variable'
        """
        return _SOURCE_DESCRIPTIONS[self]


_SOURCE_DESCRIPTIONS: dict[SkipPreflightSource, str] = {
    SkipPreflightSource.CONFIGURATION: "the lading.toml [preflight] skip setting",
    SkipPreflightSource.COMMAND_LINE: "the --skip-preflight command-line flag",
    SkipPreflightSource.ENVIRONMENT: "the LADING_SKIP_PREFLIGHT environment variable",
    SkipPreflightSource.IN_PROCESS: "an in-process caller",
}


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
        The input that supplied ``skip``.

    Examples
    --------
    >>> decision = SkipPreflightDecision(
    ...     skip=True, source=SkipPreflightSource.COMMAND_LINE
    ... )
    >>> decision.source.description
    'the --skip-preflight command-line flag'
    """

    skip: bool
    source: SkipPreflightSource


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
        labelled :attr:`SkipPreflightSource.CONFIGURATION`.

    Examples
    --------
    >>> resolve_skip_preflight(None, configured=False).skip
    False
    >>> resolve_skip_preflight(None, configured=True).source.description
    'the lading.toml [preflight] skip setting'
    >>> explicit = SkipPreflightDecision(
    ...     skip=False, source=SkipPreflightSource.COMMAND_LINE
    ... )
    >>> resolve_skip_preflight(explicit, configured=True).skip
    False
    """
    if override is not None:
        return override
    return SkipPreflightDecision(
        skip=configured, source=SkipPreflightSource.CONFIGURATION
    )


__all__ = [
    "SkipPreflightDecision",
    "SkipPreflightSource",
    "resolve_skip_preflight",
]
