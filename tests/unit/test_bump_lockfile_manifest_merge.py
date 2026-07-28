"""Tests for merging configured and discovered lockfile manifests."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from lading.commands import bump_lockfiles


@dc.dataclass(frozen=True, slots=True)
class _Invocation:
    """Recorded command invocation."""

    command: tuple[str, ...]
    cwd: Path | None


class _RecordingRunner:
    """Record command invocations and return a configured result."""

    def __init__(
        self,
        result: tuple[int, str, str] = (0, "", ""),
    ) -> None:
        self.result = result
        self.invocations: list[_Invocation] = []

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
    ) -> tuple[int, str, str]:
        """Record one command invocation."""
        self.invocations.append(_Invocation(command=tuple(command), cwd=cwd))
        return self.result


def _write_manifest(directory: Path) -> None:
    """Create ``directory`` with a placeholder ``Cargo.toml`` inside it."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "Cargo.toml").write_text(
        '[package]\nname = "placeholder"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )


@st.composite
def _manifest_merge_cases(
    draw: st.DrawFn,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Generate configured manifests and arbitrarily ordered tracked lockfiles."""
    directories = draw(
        st.lists(
            st.from_regex(r"[a-z][a-z0-9]{0,7}", fullmatch=True),
            min_size=1,
            max_size=8,
            unique=True,
        )
    )
    discovered_directories = draw(st.permutations(directories))
    configured_directories = draw(st.lists(st.sampled_from(directories), unique=True))
    configured_directories = draw(st.permutations(configured_directories))
    dot_prefixes = draw(
        st.lists(
            st.booleans(),
            min_size=len(configured_directories),
            max_size=len(configured_directories),
        )
    )
    configured = tuple(
        f"{'./' if has_dot_prefix else ''}{directory}/Cargo.toml"
        for directory, has_dot_prefix in zip(
            configured_directories, dot_prefixes, strict=True
        )
    )
    discovered = tuple(
        f"{directory}/Cargo.lock" for directory in discovered_directories
    )
    return configured, discovered


def test_merge_discovered_manifests_appends_tracked_manifests(
    tmp_path: Path,
) -> None:
    """Discovered tracked-lockfile manifests follow configured entries, sorted."""
    _write_manifest(tmp_path)
    _write_manifest(tmp_path / "crates/ui")
    _write_manifest(tmp_path / "fixtures/minimal")
    runner = _RecordingRunner(
        result=(
            0,
            "fixtures/minimal/Cargo.lock\nCargo.lock\ncrates/ui/Cargo.lock\n",
            "",
        )
    )

    merged = bump_lockfiles.merge_discovered_manifests(
        tmp_path,
        ("crates/ui/Cargo.toml",),
        runner=runner,
    )

    assert merged == (
        "crates/ui/Cargo.toml",
        "Cargo.toml",
        "fixtures/minimal/Cargo.toml",
    ), f"unexpected merged manifests: {merged!r}"
    assert runner.invocations == [
        _Invocation(
            command=("git", "ls-files", "**/Cargo.lock", "Cargo.lock"),
            cwd=tmp_path,
        )
    ], f"unexpected git invocations: {runner.invocations!r}"


def test_merge_discovered_manifests_deduplicates_configured_forms(
    tmp_path: Path,
) -> None:
    """A manifest configured under an equivalent spelling is not re-appended."""
    _write_manifest(tmp_path / "fixtures/minimal")
    runner = _RecordingRunner(result=(0, "fixtures/minimal/Cargo.lock\n", ""))

    merged = bump_lockfiles.merge_discovered_manifests(
        tmp_path,
        ("./fixtures/minimal/Cargo.toml",),
        runner=runner,
    )

    assert merged == ("./fixtures/minimal/Cargo.toml",), (
        f"equivalent configured manifest was re-appended: {merged!r}"
    )


def test_merge_discovered_manifests_returns_configured_outside_git(
    tmp_path: Path,
) -> None:
    """A non-git workspace degrades to the configured manifests unchanged."""
    runner = _RecordingRunner(
        result=(
            128,
            "",
            "fatal: not a git repository (or any of the parent directories): .git",
        )
    )

    merged = bump_lockfiles.merge_discovered_manifests(
        tmp_path,
        ("crates/ui/Cargo.toml",),
        runner=runner,
    )

    assert merged == ("crates/ui/Cargo.toml",), (
        f"non-git fallback changed configured manifests: {merged!r}"
    )


@given(case=_manifest_merge_cases())
@settings(max_examples=80, deadline=None)
def test_merge_discovered_manifests_preserves_order_and_deduplicates(
    case: tuple[tuple[str, ...], tuple[str, ...]],
) -> None:
    """Configured order wins while unique discoveries are appended sorted."""
    configured, discovered = case
    with tempfile.TemporaryDirectory() as temporary_directory:
        workspace_root = Path(temporary_directory)
        for lockfile in discovered:
            _write_manifest(workspace_root / Path(lockfile).parent)
        runner = _RecordingRunner(result=(0, "\n".join(discovered) + "\n", ""))

        merged = bump_lockfiles.merge_discovered_manifests(
            workspace_root,
            configured,
            runner=runner,
        )

        configured_paths = {
            (workspace_root / manifest).resolve() for manifest in configured
        }
        expected_discovered = tuple(
            sorted(
                Path(lockfile).with_name("Cargo.toml").as_posix()
                for lockfile in discovered
                if (workspace_root / Path(lockfile).with_name("Cargo.toml")).resolve()
                not in configured_paths
            )
        )
        assert merged == configured + expected_discovered, (
            f"merge did not preserve configured order and sorted discovery: {merged!r}"
        )
        assert len({
            (workspace_root / manifest).resolve() for manifest in merged
        }) == len(merged), f"merge retained equivalent manifest paths: {merged!r}"


@given(
    configured=st.lists(
        st.from_regex(r"(?:\./)?[a-z][a-z0-9]{0,7}/Cargo\.toml", fullmatch=True),
        max_size=8,
    ).map(tuple)
)
@settings(max_examples=50, deadline=None)
def test_merge_discovered_manifests_non_git_fallback_is_identity(
    configured: tuple[str, ...],
) -> None:
    """Non-git discovery preserves every configured entry and its order."""
    runner = _RecordingRunner(result=(128, "", "fatal: not a git repository"))
    with tempfile.TemporaryDirectory() as temporary_directory:
        merged = bump_lockfiles.merge_discovered_manifests(
            Path(temporary_directory),
            configured,
            runner=runner,
        )

    assert merged == configured, f"non-git fallback changed input: {merged!r}"
    assert len(runner.invocations) == 1, (
        f"non-git discovery should invoke git once: {runner.invocations!r}"
    )
