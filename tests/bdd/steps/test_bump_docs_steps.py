"""BDD steps for bump documentation and README content.

The documentation and README assertions live apart from the manifest and
lockfile steps so each BDD step module stays within the repository's file-size
limit.
"""

import typing as typ
from pathlib import Path

from pytest_bdd import given, parsers, then

from lading.commands import bump_readme


@then(parsers.parse('the documentation file "{relative_path}" contains "{expected}"'))
def then_documentation_contains(
    cli_run: dict[str, typ.Any], relative_path: str, expected: str
) -> None:
    """Assert that ``expected`` appears in the specified documentation file."""
    doc_path = cli_run["workspace"] / relative_path
    normalized_expected = expected.replace(r"\"", '"')
    contents = doc_path.read_text(encoding="utf-8")
    assert normalized_expected in contents, (
        f"the documentation file {relative_path!r} must contain {expected!r} "
        "after the bump"
    )


@then(parsers.parse('the crate "{crate_name}" README contains "{expected}"'))
def then_crate_readme_contains(
    cli_run: dict[str, typ.Any], crate_name: str, expected: str
) -> None:
    """Assert that the adopted crate README contains ``expected``."""
    readme_path = cli_run["workspace"] / "crates" / crate_name / "README.md"
    normalized_expected = expected.replace(r"\"", '"')
    contents = readme_path.read_text(encoding="utf-8")
    assert normalized_expected in contents, (
        f"the {crate_name!r} crate README must contain {expected!r} after the bump"
    )


@given(parsers.parse('the workspace README contains a relative link to "{target}"'))
def given_workspace_readme_relative_link(
    workspace_directory: Path,
    target: str,
) -> None:
    """Write a workspace README containing a relative Markdown link."""
    readme_path = workspace_directory / "README.md"
    readme_path.write_text(
        f"# Workspace README\n\nSee [migration guide]({target}).\n",
        encoding="utf-8",
    )


@given(
    parsers.parse(
        'the crate "{crate_name}" README already matches the workspace README'
    )
)
def given_crate_readme_matches_workspace(
    workspace_directory: Path,
    crate_name: str,
) -> None:
    """Write the already-transposed README expected for ``crate_name``."""
    workspace_readme = workspace_directory / "README.md"
    crate_root = workspace_directory / "crates" / crate_name
    crate_readme = crate_root / "README.md"
    rewritten_text, _ = bump_readme.rewrite_relative_links(
        workspace_readme.read_text(encoding="utf-8"),
        bump_readme.compute_link_prefix(crate_root.relative_to(workspace_directory)),
    )
    crate_readme.write_text(rewritten_text, encoding="utf-8")


@then(
    parsers.parse('the bump output contains a documentation diff for "{relative_path}"')
)
def then_bump_output_contains_documentation_diff(
    cli_run: dict[str, typ.Any], relative_path: str
) -> None:
    """Assert the dry-run output renders the documentation rewrite as a diff."""
    assert cli_run["returncode"] == 0, (
        f"a diff-enabled dry run must succeed; stderr:\n{cli_run['stderr']}"
    )
    stdout = cli_run["stdout"]
    assert "Documentation diff:" in stdout, (
        "the output must label the appended documentation diff"
    )
    assert f"--- a/{relative_path}" in stdout, (
        f"the diff must carry an original header for {relative_path!r}"
    )
    assert f"+++ b/{relative_path}" in stdout, (
        f"the diff must carry a proposed header for {relative_path!r}"
    )
    assert '-alpha = "0.1.0"' in stdout, (
        "the diff must show the version the dry run would replace"
    )
    assert '+alpha = "1.2.3"' in stdout, (
        "the diff must show the version the dry run would write"
    )
