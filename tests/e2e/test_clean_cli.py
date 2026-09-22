"""End-to-end cover for `lading clean` as a user runs it.

The unit cases drive :func:`lading.commands.clean.run` and the Cyclopts app in
process, which leaves the boundary a user actually crosses untested: argument
parsing from a real argv, the configuration bootstrap in :func:`lading.cli.main`,
the summary reaching standard output, and the exit status. These run the
command as a separate process against a real directory and then look at the
filesystem, because a command that deletes is only described truthfully by
what is left on disk afterwards.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from lading.commands.publish_staging import STAGING_PREFIX

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _staging_tree(location: Path, suffix: str) -> Path:
    """Create a staging directory the way an earlier publish left one.

    Returns
    -------
    Path
        The directory created.
    """
    tree = location / f"{STAGING_PREFIX}{suffix}"
    tree.mkdir(parents=True)
    (tree / "Cargo.toml").write_bytes(b"x" * 64)
    return tree


def _run_clean(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run `lading clean` as a separate process and capture its result.

    The workspace root is an empty directory of its own, so the configuration
    bootstrap runs without a `lading.toml` and cannot pick up this
    repository's.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The finished process.
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    argv = [
        sys.executable,
        "-m",
        "lading.cli",
        "--workspace-root",
        str(workspace_root),
        "clean",
        *arguments,
    ]
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        argv,
        capture_output=True,
        text=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        timeout=120,
    )


def test_a_report_leaves_the_staging_directory_in_place(tmp_path: Path) -> None:
    """The default run reports what it found and deletes nothing."""
    location = tmp_path / "staging"
    tree = _staging_tree(location, "report")

    result = _run_clean(tmp_path, "--location", str(location))

    assert result.returncode == 0, f"the report failed: {result.stderr}"
    assert tree.is_dir(), "a report-only run removed the staging directory"
    assert "Found 1 staging directory" in result.stdout, (
        f"the report did not count the tree: {result.stdout!r}"
    )
    assert tree.name in result.stdout, "the report did not name the tree"
    assert "Pass --remove to delete them." in result.stdout, (
        "the report did not say how to remove what it found"
    )


def test_remove_deletes_only_the_staging_directories(tmp_path: Path) -> None:
    """`--remove` deletes the staged copies and nothing beside them.

    The unprefixed neighbour is what a careless glob would also take, so its
    survival is asserted alongside the removal rather than left to the unit
    cases.
    """
    location = tmp_path / "staging"
    tree = _staging_tree(location, "remove")
    neighbour = location / "not-a-staging-tree"
    neighbour.mkdir()

    result = _run_clean(tmp_path, "--location", str(location), "--remove")

    assert result.returncode == 0, f"the removal failed: {result.stderr}"
    assert not tree.exists(), "--remove left the staging directory on disk"
    assert neighbour.is_dir(), "--remove deleted a directory outside its scope"
    assert "Removed 1 staging directory" in result.stdout, (
        f"the removal was not reported: {result.stdout!r}"
    )
