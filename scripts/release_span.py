"""Emit a span record for one ``gh`` invocation, with no exporter to run.

ADR-004 fixes the repository's operational boundary: a short-lived command
records what it did as a structured line in its own job log, because there is
no collector, no scrape endpoint, and no daemon lifetime to accumulate into.
This module applies that boundary to the uploader's process edge. It records
the same shape a span-based tooling would carry -- an operation name, a
duration, the exit status when there is one, and a bounded failure category --
and writes it as one JSON line, so a consumer that reads job logs today needs
no new transport and a consumer that later wants real spans has a defined
mapping to translate.

Two properties are load-bearing and are tested rather than documented:

- **Every attribute is bounded.** The operation is one of a closed set, the
  status is an integer or empty, and the failure category is a member of
  :class:`FailureCategory`. No argument, path, tag, credential, or captured
  output ever reaches a field, so a span's cost and cardinality cannot grow
  with the data it carries.
- **A span always closes.** The status is recorded from a ``finally`` block, so
  a non-zero exit, an exception, and a success all produce a record. A span
  that vanishes on the interesting path is worse than no span.

No trace context is propagated. There is no tracer in this repository to
produce or consume one -- :mod:`lading.utils.metrics` is an in-process counter
accumulator and this uploader is a standalone PEP 723 script that cannot import
the package -- so a context field would be a value this code invented and no
consumer could read.
"""

from __future__ import annotations

import contextlib
import enum
import json
import time
import typing as typ

if typ.TYPE_CHECKING:
    import io


class FailureCategory(enum.StrEnum):
    """Why a ``gh`` invocation did not complete, as a closed set.

    Bounded on purpose: an exception message names a path, a tag, or a
    credential as often as not, so it must not become a metric label or a span
    attribute. The category says which kind of thing went wrong; the job log's
    own error line says which instance.
    """

    NONE = "none"
    NON_ZERO_EXIT = "non-zero-exit"
    RAISED = "raised"


#: The operation names this module can record. One member today, and closed so
#: that a reader can enumerate the set.
_OPERATION = "gh.invoke"

#: The span record's schema version, so a later change to the fields is
#: distinguishable from a consumer's parse error.
_SCHEMA = 1


def _emit(
    log: io.TextIOBase | typ.TextIO,
    *,
    operation: str,
    duration_seconds: float,
    exit_code: int | None,
    category: FailureCategory,
) -> None:
    """Write one span record as a single line.

    Parameters
    ----------
    log : io.TextIOBase | typ.TextIO
        The sink the record is written to, normally ``sys.stderr``.
    operation : str
        The bounded operation name.
    duration_seconds : float
        How long the invocation took.
    exit_code : int | None
        The process exit status, or ``None`` when no process result was
        produced.
    category : FailureCategory
        The bounded failure category.
    """
    record = {
        "operation": operation,
        "schema": _SCHEMA,
        "duration_seconds": round(duration_seconds, 3),
        "exit_code": "" if exit_code is None else exit_code,
        "failure_category": str(category),
    }
    print(f"release_span {json.dumps(record, sort_keys=True)}", file=log)


@contextlib.contextmanager
def record_gh_span(
    log: io.TextIOBase | typ.TextIO,
    *,
    operation: str = _OPERATION,
    clock: typ.Callable[[], float] = time.monotonic,
) -> typ.Iterator[typ.Callable[[int], None]]:
    """Time one ``gh`` invocation and always write a span record.

    Parameters
    ----------
    log : io.TextIOBase | typ.TextIO
        The sink the record is written to.
    operation : str
        The bounded operation name to record.
    clock : typ.Callable[[], float]
        The monotonic clock, injected so a test can state the duration.

    Yields
    ------
    typ.Callable[[int], None]
        Records the process exit status. A caller that never calls it is
        treated as having raised rather than exited, which is the honest
        reading: no status was observed.

    Examples
    --------
    >>> import io
    >>> sink = io.StringIO()
    >>> with record_gh_span(sink, clock=iter([1.0, 1.5]).__next__) as set_exit:
    ...     set_exit(0)
    >>> '"exit_code": 0' in sink.getvalue()
    True
    """
    recorded: list[int] = []
    started = clock()
    try:
        yield recorded.append
    except BaseException:
        # A raised invocation leaves no status to report, and the exception
        # itself is re-raised: this records the span, it does not swallow the
        # failure. The category distinguishes it from a clean non-zero exit.
        _emit(
            log,
            operation=operation,
            duration_seconds=clock() - started,
            exit_code=None,
            category=FailureCategory.RAISED,
        )
        raise
    _emit(
        log,
        operation=operation,
        duration_seconds=clock() - started,
        exit_code=recorded[0] if recorded else None,
        category=(
            FailureCategory.NONE
            if recorded and recorded[0] == 0
            else FailureCategory.NON_ZERO_EXIT
        ),
    )
