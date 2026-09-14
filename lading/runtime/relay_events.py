"""Privacy-safe observability events for subprocess output relays.

Each event carries only the bounded fields that describe a relay decision:

- ``operation`` is always ``"relay_mirror"`` for decoded-output mirroring.
- ``stream`` is ``"stdout"`` or ``"stderr"`` for the relayed child stream.
- ``transition`` is ``"text_to_binary"`` when a parent text stream switches
  to its binary buffer, or ``"disable_mirroring"`` when it becomes unusable.
- ``error_category`` is ``"unicode_encode"`` for a parent encoding rejection
  or ``"broken_pipe"`` when the parent stream closes its pipe.

The event deliberately excludes subprocess payloads, decoded output, command
arguments, and every other unbounded value.
"""

from __future__ import annotations

import dataclasses as dc
import logging
import typing as typ

_LOGGER = logging.getLogger(__name__)

type StreamName = typ.Literal["stdout", "stderr"]
type RelayOperation = typ.Literal["relay_mirror"]
type RelayTransition = typ.Literal["text_to_binary", "disable_mirroring"]
type RelayErrorCategory = typ.Literal["unicode_encode", "broken_pipe"]


@dc.dataclass(frozen=True, slots=True)
class RelayEvent:
    """Describe one bounded relay-mirroring decision.

    Parameters
    ----------
    operation : RelayOperation
        ``"relay_mirror"`` identifies the decoded child-output mirroring
        operation.
    stream : StreamName
        ``"stdout"`` or ``"stderr"`` identifies the child stream being
        relayed.
    transition : RelayTransition
        ``"text_to_binary"`` records a switch to ``sink.buffer``;
        ``"disable_mirroring"`` records that the parent sink is disabled.
    error_category : RelayErrorCategory
        ``"unicode_encode"`` records parent text-encoding rejection;
        ``"broken_pipe"`` records a closed parent pipe.
    """

    operation: RelayOperation
    stream: StreamName
    transition: RelayTransition
    error_category: RelayErrorCategory


def emit_relay_event(
    operation: RelayOperation,
    stream: StreamName,
    transition: RelayTransition,
    error_category: RelayErrorCategory,
) -> None:
    """Log one privacy-safe relay observability event.

    Parameters
    ----------
    operation : RelayOperation
        ``"relay_mirror"`` for the decoded child-output mirroring operation.
    stream : StreamName
        ``"stdout"`` or ``"stderr"`` for the relayed child stream.
    transition : RelayTransition
        ``"text_to_binary"`` or ``"disable_mirroring"`` for the selected
        relay state change.
    error_category : RelayErrorCategory
        ``"unicode_encode"`` or ``"broken_pipe"`` for the cause category.

    Examples
    --------
    >>> emit_relay_event(
    ...     "relay_mirror", "stdout", "text_to_binary", "unicode_encode"
    ... )
    """
    event = RelayEvent(operation, stream, transition, error_category)
    _LOGGER.info("relay observability event: %s", event)
