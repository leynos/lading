"""BDD coverage for examples published in the migration guide."""

from pathlib import Path

from pytest_bdd import given, then

from tests.helpers.markdown_examples import (
    MarkdownExample,
    load_markdown_examples,
)


@given(
    "the published 0.4.0 migration guide",
    target_fixture="migration_guide_examples",
)
def given_published_migration_guide_examples(
    repo_root: Path,
) -> tuple[MarkdownExample, ...]:
    """Load every marked example from the tracked migration guide."""
    return load_markdown_examples(repo_root / "docs" / "v0-4-0-migration-guide.md")


@then("its marked examples load completely")
def then_migration_guide_examples_load_completely(
    migration_guide_examples: tuple[MarkdownExample, ...],
) -> None:
    """Assert that the published guide's complete, multi-language set loaded."""
    assert len(migration_guide_examples) == 9, (
        "the migration guide's nine marked examples must all load"
    )
    assert {example.language for example in migration_guide_examples} == {
        "python",
        "text",
        "console",
        "bash",
    }, "the migration guide must load every documented example language"
