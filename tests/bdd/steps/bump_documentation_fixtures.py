"""Fixtures for the bump documentation-pass BDD scenarios.

The mixed sample and the expected unchanged fence bodies live together so the
scenario's assertions read the same source text the fixture writes.
"""

import typing as typ
from pathlib import Path

from pytest_bdd import given

__all__ = [
    "MIXED_DOCUMENTATION_SAMPLE",
    "given_workspace_readme_mixed_sample",
    "mixed_fence_body",
]

MIXED_FENCE_BODIES: typ.Final[dict[str, str]] = {
    "metadata": '[package.metadata.mytool]\nmode = "warn"\n',
    "unrelated-application": '[package]\nname = "my-app"\nversion = "0.1.0"\n',
    "inherited-version": '[package]\nname = "alpha"\nversion.workspace = true\n',
}

MIXED_DOCUMENTATION_SAMPLE: typ.Final[str] = f"""\
# Usage

The metadata table this workspace's members read:

```toml
{MIXED_FENCE_BODIES["metadata"]}```

A downstream application looks like this:

```toml
{MIXED_FENCE_BODIES["unrelated-application"]}```

Depend on the workspace crate:

```toml
[dependencies]
alpha = "0.1.0"
```

A member inherits the workspace version:

```toml
{MIXED_FENCE_BODIES["inherited-version"]}```
"""


def mixed_fence_body(name: str) -> str:
    """Return the source body of the named mixed-document fence."""
    return MIXED_FENCE_BODIES[name]


@given("the workspace README contains a mixed TOML documentation sample")
def given_workspace_readme_mixed_sample(workspace_directory: Path) -> None:
    """Write a README mixing eligible and ineligible TOML fences."""
    (workspace_directory / "README.md").write_text(
        MIXED_DOCUMENTATION_SAMPLE, encoding="utf-8", newline="\n"
    )
