"""Exercise a marked example from the users' guide.

The documentation style guide requires user-facing fences to be executable and
tested from the shipped text. This module loads the marked
``bump-documentation-diff`` console example and runs its command against a
temporary Cargo workspace, comparing the published output with the real one.
"""

import subprocess
from pathlib import Path

import pytest

from tests.helpers.cli_entrypoints import ENTRY_POINT_KINDS, entry_point_argv
from tests.helpers.tested_examples import load_tested_examples

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_TIMEOUT_SECONDS = 120
_EXAMPLE_IDENTIFIER = "bump-documentation-diff"


def _write_workspace(workspace: Path) -> None:
    """Create the single-package workspace the documented example assumes."""
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "lib.rs").write_bytes(b"")
    (workspace / "Cargo.toml").write_bytes(
        b'[package]\nname = "alpha"\nversion = "0.1.0"\n'
    )
    (workspace / "README.md").write_bytes(
        b'```toml\n[dependencies]\nalpha = "0.1.0"\n```\n'
    )
    (workspace / "lading.toml").write_bytes(
        b"[bump]\nrebuild_lockfiles = false\n\n"
        b'[bump.documentation]\nglobs = ["README.md"]\n'
    )


@pytest.fixture(params=ENTRY_POINT_KINDS)
def entry_point(request: pytest.FixtureRequest) -> list[str]:
    """Return the argv prefix that starts one supported entry point."""
    return entry_point_argv(request.param)


def test_users_guide_bump_diff_example(tmp_path: Path, entry_point: list[str]) -> None:
    """The documented command produces the documented output."""
    examples = load_tested_examples(
        REPOSITORY_ROOT / "docs" / "users-guide.md", require_markers=False
    )
    example = examples.get(_EXAMPLE_IDENTIFIER)
    assert example is not None, f"the users' guide must mark {_EXAMPLE_IDENTIFIER}"
    body = example.body.split("\n")
    assert body, "the example fence must not be empty"
    assert body[0].startswith("$ "), (
        f"the example must start with a shell prompt, got {body[0]!r}"
    )
    command_parts = body[0][2:].split()
    assert command_parts[0] == "lading", (
        f"the example must invoke lading, got {body[0]!r}"
    )

    workspace = tmp_path / "workspace"
    _write_workspace(workspace)

    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        [
            *entry_point,
            "--workspace-root",
            str(workspace),
            *command_parts[1:],
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        timeout=_TIMEOUT_SECONDS,
    )

    assert result.returncode == 0, f"the example command failed: {result.stderr}"
    assert result.stdout.strip() == "\n".join(body[1:]).strip(), (
        "the published example output must match the command's real output"
    )
    assert (workspace / "README.md").read_bytes() == (
        b'```toml\n[dependencies]\nalpha = "0.1.0"\n```\n'
    ), "the dry-run example must not modify the documentation file"
