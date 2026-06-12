"""In-process metrics accumulation for :mod:`lading`.

Backend choice (issue #68): a process-local accumulator flushed to one
structured log line at interpreter exit. A lading invocation is a
short-lived CLI process, so exporters such as ``prometheus_client`` or
``statsd`` would add a runtime dependency and a network target without a
scraper to consume them. Log aggregation already ingests lading's output,
so the summary line is the operationally useful boundary, and the
in-process registry gives tests a deterministic seam.

Counters are keyed by metric name plus a sorted tuple of label pairs.

Examples
--------
>>> from lading.utils import metrics
>>> metrics.reset()
>>> metrics.increment_counter("demo.events", kind="example")
>>> metrics.counter_value("demo.events", kind="example")
1
"""

from __future__ import annotations

import atexit
import collections
import json
import logging
import threading

_LOGGER = logging.getLogger(__name__)
_LOCK = threading.Lock()
type _CounterKey = tuple[str, tuple[tuple[str, str], ...]]
_COUNTERS: collections.Counter[_CounterKey] = collections.Counter()
_summary_hook_registered = threading.Event()


def _counter_key(name: str, labels: dict[str, str]) -> _CounterKey:
    """Return the registry key for ``name`` with sorted ``labels``."""
    return (name, tuple(sorted(labels.items())))


def increment_counter(name: str, *, amount: int = 1, **labels: str) -> None:
    """Increment the counter ``name`` for the supplied label values.

    Examples
    --------
    >>> increment_counter("demo.total", subcommand="package")
    """
    with _LOCK:
        _COUNTERS[_counter_key(name, labels)] += amount


def counter_value(name: str, **labels: str) -> int:
    """Return the current value of ``name`` for the supplied labels."""
    with _LOCK:
        return _COUNTERS[_counter_key(name, labels)]


def snapshot() -> dict[_CounterKey, int]:
    """Return a copy of the counter registry for assertions."""
    with _LOCK:
        return dict(_COUNTERS)


def reset() -> None:
    """Clear all recorded metrics; intended for test isolation."""
    with _LOCK:
        _COUNTERS.clear()


def emit_summary() -> None:
    """Log the accumulated counters as one structured summary line.

    Emits nothing when no metrics were recorded, so quiet runs stay quiet.
    """
    with _LOCK:
        if not _COUNTERS:
            return
        rendered = [
            {"metric": name, "labels": dict(labels), "value": value}
            for (name, labels), value in sorted(_COUNTERS.items())
        ]
    _LOGGER.info("lading metrics summary: %s", json.dumps(rendered))


def register_summary_atexit() -> None:
    """Register :func:`emit_summary` to run at interpreter exit.

    Call this once from application bootstrap (the CLI entry point) so the
    exit-time flush is an explicit lifecycle decision rather than a hidden
    side effect of importing this module. Repeated calls register the hook at
    most once.
    """
    with _LOCK:
        if _summary_hook_registered.is_set():
            return
        _summary_hook_registered.set()
        atexit.register(emit_summary)


__all__ = [
    "counter_value",
    "emit_summary",
    "increment_counter",
    "register_summary_atexit",
    "reset",
    "snapshot",
]
