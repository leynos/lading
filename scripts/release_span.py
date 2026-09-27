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

import collections.abc as cabc
import contextlib
import dataclasses as dc
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


@dc.dataclass(frozen=True, slots=True)
class SpanRecord:
    """One invocation's span, in the fields a consumer may rely on.

    A record rather than five positional arguments, because the whole point of
    the shape is that it is closed: a new field is a visible change to this
    class and to the boundedness test, not a sixth argument a caller can pass
    to carry whatever it has to hand. The empty string for an unobserved status
    is the record's own convention -- ``None`` would serialize as JSON's
    ``null`` and read as a status that was reported as missing.
    """

    operation: str
    duration_seconds: float
    exit_code: int | None
    category: FailureCategory
    schema: int = _SCHEMA

    def as_json(self) -> str:
        """Render the record as the single line the job log carries.

        Returns
        -------
        str
            The line, prefix included.
        """
        body = {
            "operation": self.operation,
            "schema": self.schema,
            "duration_seconds": round(self.duration_seconds, 3),
            "exit_code": "" if self.exit_code is None else self.exit_code,
            "failure_category": str(self.category),
        }
        return f"release_span {json.dumps(body, sort_keys=True)}"


def _emit(
    log: io.TextIOBase | typ.TextIO,
    record: SpanRecord,
) -> None:
    """Write one span record as a single line.

    Parameters
    ----------
    log : io.TextIOBase | typ.TextIO
        The sink the record is written to, normally ``sys.stderr``.
    record : SpanRecord
        The invocation to record.
    """
    print(record.as_json(), file=log)


@contextlib.contextmanager
def record_gh_span(
    log: io.TextIOBase | typ.TextIO,
    *,
    operation: str = _OPERATION,
    clock: cabc.Callable[[], float] = time.monotonic,
) -> cabc.Iterator[cabc.Callable[[int], None]]:
    """Time one ``gh`` invocation and always write a span record.

    Parameters
    ----------
    log : io.TextIOBase | typ.TextIO
        The sink the record is written to.
    operation : str
        The bounded operation name to record.
    clock : cabc.Callable[[], float]
        The monotonic clock, injected so a test can state the duration.

    Yields
    ------
    cabc.Callable[[int], None]
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
            SpanRecord(
                operation=operation,
                duration_seconds=clock() - started,
                exit_code=None,
                category=FailureCategory.RAISED,
            ),
        )
        raise
    _emit(
        log,
        SpanRecord(
            operation=operation,
            duration_seconds=clock() - started,
            exit_code=recorded[0] if recorded else None,
            category=(
                FailureCategory.NONE
                if recorded and recorded[0] == 0
                else FailureCategory.NON_ZERO_EXIT
            ),
        ),
    )
