"""Unit tests for member-pattern and exclusion semantics in discovery.

These cases pin Cargo's member-glob and exclusion behaviour: exclusions are
literal prefixes, a file-only glob match is still a match, and whitespace in a
literal member name is significant.
"""

import os
import typing as typ
from pathlib import Path

import pytest

from lading.utils.confinement import MissingBumpTargetError
from lading.workspace.confined_discovery import load_bump_workspace
from tests.unit.test_confined_discovery import (
    WorkspaceShape,
    _payload,
    _prepare_workspace,
    _RecordingRunner,
    _write_manifest,
)

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner


def _load(
    tmp_path: Path, payload: dict[str, typ.Any]
) -> tuple[typ.Any, _RecordingRunner]:
    """Load the workspace with a recording runner and return both."""
    runner = _RecordingRunner(payload=payload)
    graph = load_bump_workspace(tmp_path, runner=typ.cast("CommandRunner", runner))
    return graph, runner


def test_a_wildcard_exclusion_is_literal(tmp_path: Path) -> None:
    """Cargo does not expand wildcards in ``exclude`` entries."""
    _prepare_workspace(
        tmp_path,
        ["crates/*"],
        ["live"],
        options=WorkspaceShape(excludes=("crates/*",)),
    )
    (tmp_path / "crates" / "retired").mkdir()
    runner = _RecordingRunner(payload=_payload(tmp_path, ["live"]))

    with pytest.raises(MissingBumpTargetError) as excinfo:
        load_bump_workspace(tmp_path, runner=typ.cast("CommandRunner", runner))

    assert "crates/retired" in str(excinfo.value), (
        "a wildcard exclusion must not skip a real directory's manifest check"
    )


def test_a_literal_exclusion_skips_a_glob_match(tmp_path: Path) -> None:
    """A literal exclusion keeps Cargo from opening the excluded manifest."""
    _prepare_workspace(tmp_path, ["crates/*"], ["live"])
    retired = tmp_path / "crates" / "retired"
    retired.mkdir()
    if not hasattr(os, "mkfifo"):
        pytest.skip("os.mkfifo is unavailable on this platform")
    try:
        os.mkfifo(retired / "Cargo.toml")
    except OSError as exc:  # pragma: no cover - capability
        pytest.skip(f"cannot create a FIFO here: {exc}")
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\nexclude = ["crates/retired"]\n',
        encoding="utf-8",
    )
    graph, runner = _load(tmp_path, _payload(tmp_path, ["live"]))

    assert {crate.name for crate in graph.crates} == {"live"}, (
        "the excluded directory must not become a member"
    )
    assert runner.calls, "discovery must still reach Cargo"


def test_a_file_only_member_glob_does_not_fail(tmp_path: Path) -> None:
    """A member glob that matches only files has matched something."""
    _prepare_workspace(tmp_path, ["docs/*", "crates/alpha"], ["alpha"])
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "README.md").write_text("stray\n", encoding="utf-8")

    graph, runner = _load(tmp_path, _payload(tmp_path, ["alpha"]))

    assert {crate.name for crate in graph.crates} == {"alpha"}, (
        "a stray file under a member glob must not fail the workspace"
    )
    assert runner.calls, "discovery must still reach Cargo"


def test_a_whitespace_member_name_is_not_the_root(tmp_path: Path) -> None:
    """``" . "`` names a directory with spaces, not the workspace root."""
    spaced = tmp_path / " . "
    spaced.mkdir()
    _write_manifest(
        spaced / "Cargo.toml", '[package]\nname = "spaced"\nversion = "0.1.0"\n'
    )
    _write_manifest(tmp_path / "Cargo.toml", '[workspace]\nmembers = [" . "]\n')
    payload = {
        "workspace_root": str(tmp_path),
        "packages": [
            {
                "id": "spaced-id",
                "name": "spaced",
                "version": "0.1.0",
                "manifest_path": str(spaced / "Cargo.toml"),
                "dependencies": [],
                "publish": None,
            }
        ],
        "workspace_members": ["spaced-id"],
    }

    graph, _runner = _load(tmp_path, payload)

    assert {crate.name for crate in graph.crates} == {"spaced"}, (
        "the spaced directory must be the declared member, not the root"
    )
