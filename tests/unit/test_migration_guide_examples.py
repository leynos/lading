"""Contract tests for the marked install examples in the 0.4.0 migration guide.

The guide names the shipped version in its install commands. Marking those
fences and loading them here keeps the published commands aligned with
``pyproject.toml`` instead of letting either drift.
"""

import re
from pathlib import Path

from tests.helpers.tested_examples import load_tested_examples

_GUIDE = Path("docs/v0-4-0-migration-guide.md")
_PRERELEASE_EXAMPLES = (
    "migration-upgrade-uv-prerelease",
    "migration-upgrade-pip-prerelease",
)


def _project_versions() -> tuple[str, str]:
    """Return the project version and its final-release spelling."""
    content = Path("pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', content, re.MULTILINE)
    assert match is not None, "pyproject.toml must declare a project version"
    version = match.group(1)
    final = re.match(r"(\d+\.\d+\.\d+)", version)
    assert final is not None, f"project version {version!r} must start with X.Y.Z"
    return version, final.group(1)


def test_prerelease_examples_are_marked_and_pin_the_shipped_version() -> None:
    """The marked install commands pin the prerelease this tree ships."""
    examples = load_tested_examples(_GUIDE)
    version, _final = _project_versions()

    for identifier in _PRERELEASE_EXAMPLES:
        example = examples.get(identifier)
        assert example is not None, (
            f"the migration guide must mark the {identifier!r} example"
        )
        assert example.language == "bash", f"{identifier!r} must be a shell example"
        assert f"lading=={version}" in example.body, (
            f"{identifier!r} must pin lading=={version}"
        )


def test_every_marked_example_has_content() -> None:
    """A marked example cannot be empty or header-only."""
    examples = load_tested_examples(_GUIDE)

    assert examples, "the migration guide must carry marked examples"
    for example in examples.values():
        assert example.body.strip(), (
            f"tested-example {example.identifier!r} must have a body"
        )
