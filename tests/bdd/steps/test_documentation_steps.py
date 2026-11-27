"""Behavioural steps for dependency tables inside documentation fences.

These steps cover the locations `lading bump` rewrites in a Markdown TOML
fence, as opposed to the fence-rendering mechanics. They live together so the
fixture content and the assertions that read it stay in one place.
"""

import dataclasses as dc
import textwrap
import typing as typ
from pathlib import Path

from pytest_bdd import given, parsers, then


@dc.dataclass(frozen=True, slots=True)
class DocumentationFenceFixture:
    """The documentation fence a scenario wrote and the text it started with.

    Attributes
    ----------
    crate_name : str
        The crate whose dependency entries the fences declare.
    original_content : str
        The complete README text written before the bump ran.
    """

    crate_name: str
    original_content: str


def _dependency_fences(crate_name: str, version: str) -> str:
    """Return a README whose fences show a workspace and a target dependency."""
    return textwrap.dedent(
        f"""
        # Usage

        ```toml
        [workspace.dependencies]
        {crate_name} = "{version}"
        ```

        ```toml
        [target.'cfg(unix)'.dependencies]
        {crate_name} = "{version}"
        ```
        """
    ).lstrip()


@given(
    parsers.parse(
        "the workspace README contains workspace and target dependency snippets"
        ' for "{crate_name}"'
    ),
    target_fixture="documentation_fence",
)
def given_workspace_readme_workspace_and_target_snippets(
    workspace_directory: Path, crate_name: str
) -> DocumentationFenceFixture:
    """Write a README whose fences show a workspace and a target dependency."""
    content = _dependency_fences(crate_name, "0.1.0")
    (workspace_directory / "README.md").write_text(content, encoding="utf-8")
    return DocumentationFenceFixture(crate_name=crate_name, original_content=content)


@then(
    parsers.parse(
        'the workspace and target documentation fences record version "{version}"'
    )
)
def then_fences_record_version(
    cli_run: dict[str, typ.Any],
    documentation_fence: DocumentationFenceFixture,
    version: str,
) -> None:
    """Assert each documented dependency location carries ``version``."""
    contents = (cli_run["workspace"] / "README.md").read_text(encoding="utf-8")
    assert contents == _dependency_fences(documentation_fence.crate_name, version), (
        f"expected both fences rewritten to version {version!r}: {contents!r}"
    )


@then("the workspace and target documentation file is unchanged")
def then_documentation_file_unchanged(
    cli_run: dict[str, typ.Any],
    documentation_fence: DocumentationFenceFixture,
) -> None:
    """Assert the documentation file is byte-for-byte what the scenario wrote."""
    contents = (cli_run["workspace"] / "README.md").read_text(encoding="utf-8")
    assert contents == documentation_fence.original_content, (
        f"a dry run must not modify the documentation file: {contents!r}"
    )
