"""Record bounded metrics for configured manifest rewrite stages."""

import dataclasses as dc

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
    duration_seconds: float | None,
    labels: ManifestRewriteMetricLabels,
) -> None:
    """Record one rewrite outcome and its rewrite-only duration.

    The duration is the sum of configured-manifest planning and application
    intervals, excluding workspace and member manifest updates between them.
    A ``None`` duration records no metric, as used when rewriting is disabled.
    Counter and duration labels use the bounded values documented by
    :class:`ManifestRewriteMetricLabels`.

    Parameters
    ----------
    duration_seconds : float or None
        Sum of the time spent planning and applying configured rewrites, or
        ``None`` when no rewrite timing is enabled.
    labels : ManifestRewriteMetricLabels
        Phase, outcome, dry-run state, and error category for this run.
    """
    match duration_seconds:
        case None:
            return
        case elapsed_seconds:
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
                elapsed_seconds,
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
