"""Read configured manifest rewrite metric observations for tests."""

from lading.commands import bump_manifest_rewrite_metrics
from lading.utils import metrics


def manifest_rewrite_counter_value(**labels: str) -> int:
    """Return the run counter value for the supplied rewrite labels."""
    return metrics.counter_value(
        bump_manifest_rewrite_metrics.MANIFEST_REWRITE_RUN_METRIC,
        **labels,
    )


def manifest_rewrite_duration_stats(**labels: str) -> metrics.DurationStats:
    """Return duration statistics for the supplied rewrite labels."""
    return metrics.duration_stats(
        bump_manifest_rewrite_metrics.MANIFEST_REWRITE_DURATION_METRIC,
        **labels,
    )
