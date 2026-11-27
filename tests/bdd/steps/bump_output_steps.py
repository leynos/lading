"""BDD steps for the bump report lines and documentation-file assertions."""

import typing as typ

from pytest_bdd import parsers, then

from .bump_documentation_fixtures import mixed_fence_body

_LISTED_PATH_LABELS = {
    "manifest": "updated manifest",
    "documentation": "changed documentation file",
    "README": "transposed README",
    "lockfile": "refreshed lockfile",
}


@then(
    parsers.re(
        r"the CLI output lists (?P<kind>manifest|documentation|README|lockfile) path"
        r' "(?P<expected>[^"]+)"'
    )
)
def then_cli_output_lists_path(
    cli_run: dict[str, typ.Any], kind: str, expected: str
) -> None:
    """Assert that the CLI output includes ``expected`` as a ``kind`` path line."""
    assert cli_run["returncode"] == 0, (
        f"listing a {kind} path requires a successful bump run; "
        f"stderr:\n{cli_run['stderr']}"
    )
    stdout_lines = [line.strip() for line in cli_run["stdout"].splitlines()]
    assert expected in stdout_lines, (
        f"the output must acknowledge the {_LISTED_PATH_LABELS[kind]} {expected!r}"
    )


@then(
    parsers.re(
        r"the CLI output lists (?P<kind>manifest|documentation|README|lockfile) path"
        r' "(?P<expected>[^"]+)" once'
    )
)
def then_cli_output_lists_path_once(
    cli_run: dict[str, typ.Any], kind: str, expected: str
) -> None:
    """Assert ``expected`` appears exactly once as a ``kind`` path line."""
    assert cli_run["returncode"] == 0, (
        f"listing a {kind} path requires a successful bump run; "
        f"stderr:\n{cli_run['stderr']}"
    )
    stdout_lines = [line.strip() for line in cli_run["stdout"].splitlines()]
    matches = [line for line in stdout_lines if line == expected]
    assert len(matches) == 1, (
        f"the output must acknowledge the {_LISTED_PATH_LABELS[kind]} "
        f"{expected!r} exactly once; got {len(matches)} of {stdout_lines!r}"
    )


def _documentation_text(cli_run: dict[str, typ.Any], relative_path: str) -> str:
    """Return the documentation file's complete text."""
    return (cli_run["workspace"] / relative_path).read_text(encoding="utf-8")


@then(parsers.parse('the documentation file "{relative_path}" contains "{expected}"'))
def then_documentation_contains(
    cli_run: dict[str, typ.Any], relative_path: str, expected: str
) -> None:
    """Assert that ``expected`` appears in the specified documentation file."""
    normalized_expected = expected.replace(r"\"", '"')
    contents = _documentation_text(cli_run, relative_path)
    assert normalized_expected in contents, (
        f"the documentation file {relative_path!r} must contain {expected!r} "
        "after the bump"
    )


@then(
    parsers.parse(
        'the documentation file "{relative_path}" does not contain "{unexpected}"'
    )
)
def then_documentation_does_not_contain(
    cli_run: dict[str, typ.Any], relative_path: str, unexpected: str
) -> None:
    """Assert that ``unexpected`` is absent from the documentation file."""
    normalized_unexpected = unexpected.replace(r"\"", '"')
    contents = _documentation_text(cli_run, relative_path)
    assert normalized_unexpected not in contents, (
        f"the documentation file {relative_path!r} must not contain "
        f"{unexpected!r} after the bump; got:\n{contents}"
    )


@then(
    parsers.parse(
        'the documentation file "{relative_path}" retains the fence "{fence_name}"'
    )
)
def then_documentation_retains_fence(
    cli_run: dict[str, typ.Any], relative_path: str, fence_name: str
) -> None:
    """Assert the named source fence is present byte-for-byte."""
    expected = mixed_fence_body(fence_name)
    contents = _documentation_text(cli_run, relative_path)
    assert expected in contents, (
        f"the {fence_name!r} fence must survive the bump unchanged in "
        f"{relative_path!r}; expected:\n{expected!r}\ngot:\n{contents}"
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
