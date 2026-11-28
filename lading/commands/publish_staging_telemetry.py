"""Telemetry seams for publish staging.

Staging records one outcome and duration per attempt. This module owns the
recorder protocol, the production recorder over :mod:`lading.utils.metrics`,
the :class:`StagingTelemetry` value object that carries the clock and
recorder, and the helpers that keep a failing clock or recorder from
disturbing a publish.

It is separate from :mod:`lading.commands.publish_staging` so the copy and
cleanup lifecycle does not also own metric vocabulary. Nothing here classifies
an attempt: the staging boundary decides the outcome and passes it in.
"""

import collections.abc as cabc
import dataclasses as dc
import logging
import time
import typing as typ

from lading.utils import metrics

LOGGER = logging.getLogger(__name__)

#: Counts each staging attempt by ``outcome``: ``success``, ``rejected`` when
#: the configuration cannot be used, ``inspection_failed`` when the workspace
#: cannot be read to check it, or ``failed`` for any other error.
STAGING_METRIC = "publish.staging"

#: Observes staging duration, from exclusion validation to a finished copy.
STAGING_DURATION_METRIC = "publish.staging.duration"


class StageRecorder(typ.Protocol):
    """Records one finished staging attempt.

    Implementations receive the outcome and elapsed time after the copy
    returns or raises. The staging boundary decides neither how an outcome is
    stored nor where it is reported.
    """

    def record(self, *, outcome: str, duration_seconds: float) -> None:
        """Record ``outcome`` and ``duration_seconds`` for one attempt."""
        ...


class _MetricsStageRecorder:
    """Record staging attempts in the process metric summary."""

    def record(self, *, outcome: str, duration_seconds: float) -> None:
        """Record the staging outcome and duration in the metric registry."""
        metrics.observe_duration(STAGING_DURATION_METRIC, duration_seconds)
        metrics.increment_counter(STAGING_METRIC, outcome=outcome)


#: Production recorder for :class:`StageRecorder` consumers.
_DEFAULT_STAGE_RECORDER: typ.Final[StageRecorder] = _MetricsStageRecorder()


@dc.dataclass(frozen=True, slots=True)
class StagingTelemetry:
    """The clock and recorder a staging attempt reports through.

    Attributes
    ----------
    clock : cabc.Callable[[], float]
        Monotonic time source. Defaults to :func:`time.perf_counter`.
    recorder : StageRecorder
        Receives each finished attempt. Defaults to the process metric
        summary.
    """

    clock: cabc.Callable[[], float] = time.perf_counter
    recorder: StageRecorder = _DEFAULT_STAGE_RECORDER


#: Production telemetry for callers that do not inject their own.
DEFAULT_STAGING_TELEMETRY: typ.Final[StagingTelemetry] = StagingTelemetry()


def attempt_start(telemetry: StagingTelemetry) -> float | None:
    """Return the attempt's start time, or :data:`None` if the clock failed.

    A caller-supplied clock is telemetry, not part of staging. Losing it must
    not stop a publish, so the failure is reported and the duration skipped.

    Returns
    -------
    float | None
        The start time, or :data:`None` when the clock failed.
    """
    try:
        return telemetry.clock()
    except Exception:  # telemetry must never block staging
        LOGGER.warning(
            "Staging clock failed; the duration will not be recorded",
            exc_info=True,
        )
        return None


def record_attempt(
    telemetry: StagingTelemetry, outcome: str, started_at: float | None
) -> None:
    """Record ``outcome`` without letting telemetry replace the result.

    The recorder runs in a ``finally`` block, where an exception would replace
    the staging error the caller needs or fail a staging that succeeded. A
    telemetry failure is therefore reported and discarded.
    """
    if started_at is None:
        # The start time was never measured, so a duration would be a lie.
        return
    try:
        duration = telemetry.clock() - started_at
        telemetry.recorder.record(outcome=outcome, duration_seconds=duration)
    except Exception:  # telemetry must never replace a result
        LOGGER.warning(
            "Staging telemetry failed; the staging outcome stands",
            exc_info=True,
        )
