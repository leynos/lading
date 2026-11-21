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


def test_filesystem_property_settings_leaves_the_active_defaults_alone() -> None:
    """Creating filesystem settings must not replace the active defaults.

    Hypothesis selects its built-in ``ci`` profile in CI environments, and that
    profile disables deadlines. The active default is therefore whatever
    profile the run selected, not necessarily the library's ``default``
    profile, so this test compares the active settings with themselves rather
    than requiring a particular deadline.
    """
    before = settings.default
    assert before is not None, "Hypothesis should have active default settings"
    deadline_before = before.deadline

    filesystem_property_settings(max_examples=20)

    after = settings.default
    assert after is before, "the factory must not replace the active defaults"
    assert after.deadline == deadline_before, (
        "the factory must not change the active default deadline"
    )
