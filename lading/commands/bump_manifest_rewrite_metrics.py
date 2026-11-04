"""Record bounded metrics for configured manifest rewrite stages."""

import dataclasses as dc
import time

from lading.utils import metrics

MANIFEST_REWRITE_RUN_METRIC = "bump.manifest_rewrite.run"
MANIFEST_REWRITE_DURATION_METRIC = "bump.manifest_rewrite.duration"


@dc.dataclass(frozen=True, slots=True)
class ManifestRewriteMetricLabels:
    """Bounded labels describing one manifest rewrite outcome.

    Attributes
    ----------
    phase : str
        Rewrite stage: ``"planning"`` or ``"application"``.
    outcome : str
        Result: ``"planned"``, ``"failed"``, ``"applied"``, or
        ``"skipped"``. Planning records use ``"planned"`` or ``"failed"``;
        application records use ``"applied"``, ``"skipped"``, or
        ``"failed"``.
    dry_run : bool
        Whether the bump command is running without writes. Recorded as the
        bounded string labels ``"true"`` and ``"false"``.
    error_category : str
        Failure category: ``"none"``, ``"planning"``, or ``"write"``.
    """

    phase: str
    outcome: str
    dry_run: bool
    error_category: str


def record_manifest_rewrite_metrics(
    started_at: float,
    labels: ManifestRewriteMetricLabels,
) -> None:
    """Record one rewrite outcome and its plan-to-application duration.

    The counter and duration use the bounded values documented by
    :class:`ManifestRewriteMetricLabels`. The duration includes planning and
    application, measured from the supplied monotonic start time.

    Parameters
    ----------
    started_at : float
        Start time returned by :func:`time.perf_counter` before planning
        begins.
    labels : ManifestRewriteMetricLabels
        Phase, outcome, dry-run state, and error category for this run.
    """
    dry_run_label = str(labels.dry_run).lower()
    metrics.increment_counter(
        MANIFEST_REWRITE_RUN_METRIC,
        phase=labels.phase,
        outcome=labels.outcome,
        dry_run=dry_run_label,
        error_category=labels.error_category,
    )
    metrics.observe_duration(
        MANIFEST_REWRITE_DURATION_METRIC,
        time.perf_counter() - started_at,
        outcome=labels.outcome,
        dry_run=dry_run_label,
        error_category=labels.error_category,
    )


def record_manifest_rewrite_planned(*, dry_run: bool) -> None:
    """Count a successfully prepared rewrite plan.

    Records one ``planning`` / ``planned`` counter with the ``none`` error
    category. The dry-run label is the bounded string ``"true"`` or
    ``"false"``; this function does not record a duration.

    Parameters
    ----------
    dry_run : bool
        Whether the bump command is running without writes.
    """
    metrics.increment_counter(
        MANIFEST_REWRITE_RUN_METRIC,
        phase="planning",
        outcome="planned",
        dry_run=str(dry_run).lower(),
        error_category="none",
    )
