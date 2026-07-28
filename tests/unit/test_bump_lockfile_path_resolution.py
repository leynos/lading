"""Tests for projecting Cargo manifests to lockfile paths."""

from __future__ import annotations

import string
import tempfile
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from lading.commands import bump_lockfiles


def test_resolve_lockfile_paths_reports_dry_run_targets(tmp_path: Path) -> None:
    """Dry-run reporting can resolve lockfiles without invoking Cargo."""
    lockfiles = bump_lockfiles.resolve_lockfile_paths(
        tmp_path,
        ("Cargo.toml", "crates/nested/Cargo.toml"),
    )

    assert lockfiles == (
        tmp_path / "Cargo.lock",
        tmp_path / "crates/nested/Cargo.lock",
    )


@pytest.mark.parametrize(
    ("manifest", "expected_message"),
    [
        ("../outside/Cargo.toml", "within the workspace"),
        ("Cargo.lock", "Cargo.toml file"),
        ("crates/nested/foo.toml", "Cargo.toml file"),
    ],
)
def test_resolve_lockfile_paths_rejects_invalid_targets(
    tmp_path: Path,
    manifest: str,
    expected_message: str,
) -> None:
    """Configured manifests must stay in-workspace and name Cargo.toml."""
    with pytest.raises(
        bump_lockfiles.LockfileRegenerationError, match=expected_message
    ):
        bump_lockfiles.resolve_lockfile_paths(tmp_path, (manifest,))


_SEGMENT = st.text(
    alphabet=string.ascii_lowercase + string.digits + "_-",
    min_size=1,
    max_size=10,
)


@st.composite
def _inside_dir(draw: st.DrawFn) -> list[str]:
    """Draw a relative directory path that stays within the workspace.

    Alongside real segments and redundant ``.`` segments, this emits safe
    ``..`` parent-traversal segments that never ascend above the workspace
    root after normalisation: a ``..`` is only produced while a prior real
    segment remains to cancel it (the running segment balance never drops
    below zero). Cases such as ``crate/../Cargo.toml`` are therefore
    exercised without allowing traversal above the root.
    """
    length = draw(st.integers(min_value=0, max_value=4))
    components: list[str] = []
    depth = 0
    for _ in range(length):
        options = [_SEGMENT, st.just(".")]
        if depth > 0:
            options.append(st.just(".."))
        segment = draw(st.one_of(*options))
        if segment == "..":
            depth -= 1
        elif segment != ".":
            depth += 1
        components.append(segment)
    return components


# Relative directory paths that stay inside the workspace, optionally with
# redundant "." segments and safe ".." traversals which normalise away.
_INSIDE_DIR = _inside_dir()


def _manifest_string(components: list[str]) -> str:
    """Render a manifest path string from directory ``components``."""
    return "/".join((*components, "Cargo.toml"))


@given(dirs=st.lists(_INSIDE_DIR, max_size=6))
@settings(max_examples=60, deadline=None)
def test_inside_manifests_resolve_to_sibling_lockfiles(
    dirs: list[list[str]],
) -> None:
    """Any in-workspace manifest string yields a sibling Cargo.lock path.

    Also pins the ordering and deduplication invariants: the workspace root
    lockfile is always first, and resolved paths are unique.
    """
    with tempfile.TemporaryDirectory(prefix="lading-bump-lockfiles-") as tmp:
        workspace_root = Path(tmp)
        manifests = [_manifest_string(components) for components in dirs]

        lockfiles = bump_lockfiles.resolve_lockfile_paths(workspace_root, manifests)

        resolved_root = workspace_root.resolve()
        assert lockfiles[0] == resolved_root / "Cargo.lock", (
            "workspace root Cargo.lock must be the first resolved lockfile"
        )
        assert len(set(lockfiles)) == len(lockfiles), (
            "resolved lockfiles must be unique"
        )
        for lockfile_path in lockfiles:
            assert lockfile_path.name == "Cargo.lock", (
                f"resolved path must be a sibling Cargo.lock: {lockfile_path}"
            )
            assert lockfile_path.parent == lockfile_path.parent.resolve(), (
                f"lockfile parent must be a normalised path: {lockfile_path}"
            )
            assert lockfile_path.is_relative_to(resolved_root), (
                f"lockfile must stay within the workspace root: {lockfile_path}"
            )
        expected = tuple(
            dict.fromkeys(
                [resolved_root / "Cargo.lock"]
                + [
                    workspace_root.joinpath(*components, "Cargo.toml").resolve().parent
                    / "Cargo.lock"
                    for components in dirs
                ]
            )
        )
        assert lockfiles == expected, (
            "resolved lockfiles must preserve manifest execution order "
            "without duplicates"
        )


@given(spellings=st.lists(st.sampled_from(["Cargo.toml", "./Cargo.toml"]), max_size=4))
@settings(max_examples=20, deadline=None)
def test_root_manifest_spellings_deduplicate_to_one_invocation(
    spellings: list[str],
) -> None:
    """Every spelling of the root manifest produces exactly one root entry."""
    with tempfile.TemporaryDirectory(prefix="lading-bump-lockfiles-") as tmp:
        workspace_root = Path(tmp)

        lockfiles = bump_lockfiles.resolve_lockfile_paths(workspace_root, spellings)

        assert lockfiles == (workspace_root.resolve() / "Cargo.lock",)


@given(
    escape_depth=st.integers(min_value=1, max_value=3),
    suffix=st.lists(_SEGMENT, max_size=2),
)
@settings(max_examples=30, deadline=None)
def test_escaping_manifests_are_rejected(
    escape_depth: int,
    suffix: list[str],
) -> None:
    """Any manifest path escaping the workspace root raises an error."""
    with tempfile.TemporaryDirectory(prefix="lading-bump-lockfiles-") as tmp:
        workspace_root = Path(tmp) / "workspace"
        workspace_root.mkdir()
        assume(suffix[:1] != ["workspace"])
        escaping = "/".join(([".."] * escape_depth) + [*suffix, "Cargo.toml"])

        with pytest.raises(
            bump_lockfiles.LockfileRegenerationError,
            match="must stay within the workspace",
        ):
            bump_lockfiles.resolve_lockfile_paths(workspace_root, (escaping,))
