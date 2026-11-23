"""BDD scenario tests for published migration guide examples."""

from pytest_bdd import scenario


@scenario(
    "features/migration_guide.feature",
    "The 0.4.0 migration guide exposes tested examples",
)
def test_v0_4_0_migration_guide_examples_load() -> None:
    """The BDD suite loads examples from the published migration guide."""
