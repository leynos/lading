"""Record bounded metrics for configured manifest rewrite stages."""

import dataclasses as dc
import time

from lading.utils import metrics

MANIFEST_REWRITE_RUN_METRIC = "bump.manifest_rewrite.run"
MANIFEST_REWRITE_DURATION_METRIC = "bump.manifest_rewrite.duration"


@dc.dataclass(frozen=True, slots=True)
class ManifestRewriteMetricLabels:
    """Bounded labels describing one manifest rewrite outcome."""

    phase: str
    outcome: str
    dry_run: bool
    error_category: str


def record_manifest_rewrite_metrics(
    started_at: float,
    labels: ManifestRewriteMetricLabels,
) -> None:
    """Record one bounded rewrite outcome and its plan-to-apply time."""
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
    """Count one successfully prepared rewrite plan."""
    metrics.increment_counter(
        MANIFEST_REWRITE_RUN_METRIC,
        phase="planning",
        outcome="planned",
        dry_run=str(dry_run).lower(),
        error_category="none",
    )
