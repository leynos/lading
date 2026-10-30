"""Tests for the filesystem-backed Hypothesis settings factory."""

from hypothesis import HealthCheck, settings

from tests.helpers.hypothesis_settings import filesystem_property_settings


def test_filesystem_property_settings_disables_the_deadline() -> None:
    """The factory turns off the per-example deadline."""
    configured = filesystem_property_settings(max_examples=20)

    assert configured.deadline is None, (
        "filesystem-backed properties should not be timed against the disk"
    )


def test_filesystem_property_settings_preserves_max_examples() -> None:
    """The caller's example budget is passed through unchanged."""
    assert filesystem_property_settings(max_examples=20).max_examples == 20, (
        "a small example budget should pass through unchanged"
    )
    assert filesystem_property_settings(max_examples=80).max_examples == 80, (
        "a large example budget should pass through unchanged"
    )


def test_filesystem_property_settings_defaults_to_no_suppression() -> None:
    """Suppression is opted into by the caller, not by the factory."""
    configured = filesystem_property_settings(max_examples=20)

    assert configured.suppress_health_check == (), (
        "filesystem access alone should not suppress health checks"
    )


def test_filesystem_property_settings_passes_suppression_through() -> None:
    """An explicit health-check suppression is preserved exactly."""
    configured = filesystem_property_settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )

    assert configured.suppress_health_check == (HealthCheck.function_scoped_fixture,), (
        "the caller's suppression choice should pass through unchanged"
    )


def test_filesystem_property_settings_leaves_the_default_deadline_alone() -> None:
    """Opting in is per property; the global default still enforces timing."""
    filesystem_property_settings(max_examples=20)

    default_settings = settings.default
    assert default_settings is not None, "Hypothesis should register a default profile"
    assert default_settings.deadline is not None, (
        "the shared default must keep its deadline so in-memory properties "
        "still detect regressions"
    )
