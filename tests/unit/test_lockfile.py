"""Unit tests for Cargo lockfile helper functions."""

from __future__ import annotations

import collections.abc as cabc
import string
import tempfile
from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from lading.commands import lockfile
from lading.utils import metrics

# ---------------------------------------------------------------------------
# Hypothesis strategies for _lockfiles_with_manifests property tests
# ---------------------------------------------------------------------------

_safe_component: st.SearchStrategy[str] = st.text(
    alphabet=string.ascii_lowercase + string.digits + "_-",
    min_size=1,
    max_size=16,
).filter(lambda s: s != "target")

_path_component: st.SearchStrategy[str] = st.one_of(st.just("target"), _safe_component)

_lockfile_line: st.SearchStrategy[str] = st.lists(
    _path_component, min_size=1, max_size=4
).map(lambda parts: "/".join(parts) + "/Cargo.lock")

_hypothesis_stdout: st.SearchStrategy[str] = st.lists(
    st.one_of(_lockfile_line, st.just(""), st.just("   ")),
    min_size=0,
    max_size=20,
).map("\n".join)

_HYPOTHESIS_WORKSPACE = Path("/repo")


@pytest.mark.usefixtures("_metrics_registry")
def test_discover_tracked_lockfiles_returns_empty_result(tmp_path: Path) -> None:
    """Empty git output produces no lockfiles and records no discovery metric."""
    (tmp_path / "Cargo.lock").write_text("", encoding="utf-8")

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Stub runner returning a successful git result with empty stdout."""
        assert command == ("git", "ls-files", "**/Cargo.lock", "Cargo.lock")
        assert cwd == tmp_path
        return 0, "", ""

    result = lockfile.discover_tracked_lockfiles(tmp_path, runner)
    assert result == (), (
        "git repo with no tracked lockfiles should return an empty tuple; "
        f"got {result!r}"
    )
    # A zero-count discovery must not record a counter, so quiet runs stay quiet.
    assert metrics.counter_value(lockfile.DISCOVERED_LOCKFILES_METRIC) == 0
    assert metrics.snapshot() == {}


def test_discover_tracked_lockfiles_filters_missing_manifests(tmp_path: Path) -> None:
    """Only tracked lockfiles next to Cargo.toml files are returned."""
    root_manifest = tmp_path / "Cargo.toml"
    root_manifest.write_text("[workspace]\n", encoding="utf-8")
    (tmp_path / "Cargo.lock").write_text("", encoding="utf-8")
    nested = tmp_path / "tests" / "ui_lints"
    nested.mkdir(parents=True)
    (nested / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (nested / "Cargo.lock").write_text("", encoding="utf-8")
    target = tmp_path / "target" / "debug"
    target.mkdir(parents=True)
    (target / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (target / "Cargo.lock").write_text("", encoding="utf-8")

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        assert command == ("git", "ls-files", "**/Cargo.lock", "Cargo.lock")
        assert cwd == tmp_path
        assert env is None
        return (
            0,
            (
                "Cargo.lock\n"
                "tests/ui_lints/Cargo.lock\n"
                "target/debug/Cargo.lock\n"
                "orphan/Cargo.lock\n"
            ),
            "",
        )

    result = lockfile.discover_tracked_lockfiles(tmp_path, runner)
    expected = (
        tmp_path / "Cargo.lock",
        nested / "Cargo.lock",
    )
    assert result == expected, (
        "only manifest-adjacent, non-target lockfiles should be returned; "
        f"expected {expected!r}, got {result!r}"
    )


def test_discover_tracked_lockfiles_accepts_manifest_probe(
    tmp_path: Path,
) -> None:
    """Manifest filtering is delegated to the injected probe."""
    probed: list[Path] = []

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        return 0, "Cargo.lock\nnested/Cargo.lock\n", ""

    def manifest_exists(manifest_path: Path) -> bool:
        probed.append(manifest_path)
        return (
            manifest_path.name == "Cargo.toml" and manifest_path.parent.name != "nested"
        )

    result = lockfile.discover_tracked_lockfiles(
        tmp_path,
        runner,
        manifest_exists=manifest_exists,
    )

    assert result == (tmp_path / "Cargo.lock",)
    assert probed == [tmp_path / "Cargo.toml", tmp_path / "nested" / "Cargo.toml"]


def test_discover_tracked_lockfiles_handles_non_git_directory(
    tmp_path: Path,
) -> None:
    """Non-git workspaces do not abort lockfile discovery."""
    (tmp_path / "Cargo.lock").write_text("", encoding="utf-8")

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        return 128, "", "fatal: not a git repository"

    result = lockfile.discover_tracked_lockfiles(tmp_path, runner)
    assert result == (), (
        "discovery should not abort on non-git errors; "
        f"expected empty tuple, got {result!r}"
    )


def test_discover_tracked_lockfiles_raises_on_git_failure(tmp_path: Path) -> None:
    """Git failures other than non-repositories are surfaced to callers."""

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        return 128, "", "fatal: bad revision"

    with pytest.raises(lockfile.LockfileDiscoveryError, match="bad revision"):
        lockfile.discover_tracked_lockfiles(tmp_path, runner)


def _validate_lockfile_freshness_for_result(
    tmp_path: Path, exit_code: int, stderr: str
) -> lockfile.LockfileFreshness:
    """Run lockfile freshness validation with a fake cargo exit code."""
    manifest = tmp_path / "Cargo.toml"

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        assert command == (
            "cargo",
            "metadata",
            "--locked",
            "--manifest-path",
            str(manifest),
            "--format-version=1",
        )
        assert cwd == manifest.parent
        return exit_code, "", stderr

    return lockfile.validate_lockfile_freshness(manifest, runner)


@pytest.mark.parametrize(
    "case",
    [
        (0, "", lockfile.LockfileFreshness(is_fresh=True)),
        (
            101,
            "the lock file Cargo.lock needs to be updated but --locked was passed",
            lockfile.LockfileFreshness(
                is_fresh=False,
                is_stale=True,
                detail=(
                    "the lock file Cargo.lock needs to be updated but "
                    "--locked was passed"
                ),
            ),
        ),
        (
            101,
            "failed to download registry index",
            lockfile.LockfileFreshness(
                is_fresh=False,
                is_stale=False,
                detail="failed to download registry index",
            ),
        ),
    ],
)
def test_validate_lockfile_freshness_parametrized(
    tmp_path: Path,
    case: tuple[int, str, lockfile.LockfileFreshness],
) -> None:
    """Cargo metadata output determines the lockfile freshness state."""
    exit_code, stderr, expected = case
    actual = _validate_lockfile_freshness_for_result(tmp_path, exit_code, stderr)
    assert actual == expected


@given(stdout=_hypothesis_stdout)
def test_no_returned_path_contains_target_component(stdout: str) -> None:
    """No returned lockfile path has 'target' as a relative path component."""
    result = lockfile._lockfiles_with_manifests(
        stdout,
        _HYPOTHESIS_WORKSPACE,
        manifest_exists=lambda _: True,
    )
    for path in result:
        relative_parts = path.relative_to(_HYPOTHESIS_WORKSPACE).parts
        assert "target" not in relative_parts, (
            f"Returned path {path} contains a 'target' component; "
            f"relative parts: {relative_parts}"
        )


@given(stdout=_hypothesis_stdout)
def test_all_returned_paths_have_adjacent_manifest(stdout: str) -> None:
    """Every returned path had manifest_exists approve its adjacent Cargo.toml."""
    approved: set[Path] = set()

    def manifest_exists(manifest_path: Path) -> bool:
        """Approve candidates whose path hash is even."""
        approved_result = hash(manifest_path) % 2 == 0
        if approved_result:
            approved.add(manifest_path)
        return approved_result

    returned = lockfile._lockfiles_with_manifests(
        stdout,
        _HYPOTHESIS_WORKSPACE,
        manifest_exists=manifest_exists,
    )
    for path in returned:
        expected_manifest = path.parent / "Cargo.toml"
        assert expected_manifest in approved, (
            f"Returned path {path} was not approved by manifest_exists; "
            f"adjacent manifest {expected_manifest} not in approved set"
        )


@given(stdout=_hypothesis_stdout)
def test_returned_paths_are_subset_of_git_stdout(stdout: str) -> None:
    """Every returned path corresponds to a non-empty git stdout line."""
    tracked_lines = {line.strip() for line in stdout.splitlines() if line.strip()}
    result = lockfile._lockfiles_with_manifests(
        stdout,
        _HYPOTHESIS_WORKSPACE,
        manifest_exists=lambda _: True,
    )
    for path in result:
        relative = str(path.relative_to(_HYPOTHESIS_WORKSPACE))
        assert relative in tracked_lines, (
            f"Returned path {path} (relative: {relative!r}) "
            f"does not appear in git stdout lines: {tracked_lines!r}"
        )


# ---------------------------------------------------------------------------
# End-to-end discovery invariants (issue #80)
# ---------------------------------------------------------------------------

_tree_entry = st.tuples(
    st.lists(_path_component, min_size=1, max_size=3),  # directory components
    st.booleans(),  # has adjacent Cargo.toml
    st.booleans(),  # appears in git ls-files output
)


def _stub_git_runner(stdout: str) -> cabc.Callable[..., tuple[int, str, str]]:
    """Return a runner producing ``stdout`` for git ls-files."""

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del cwd, env
        assert command[:2] == ("git", "ls-files")
        return 0, stdout, ""

    return runner


def _deduplicate_entries(
    entries: list[tuple[list[str], bool, bool]],
) -> dict[tuple[str, ...], tuple[bool, bool]]:
    """Collapse duplicate directory entries, keeping the first occurrence."""
    seen: dict[tuple[str, ...], tuple[bool, bool]] = {}
    for components, has_toml, tracked in entries:
        seen.setdefault(tuple(components), (has_toml, tracked))
    return seen


def _populate_workspace(
    workspace_root: Path,
    seen_dirs: dict[tuple[str, ...], tuple[bool, bool]],
) -> list[str]:
    """Materialise the workspace tree and return the tracked ls-files lines."""
    tracked_lines: list[str] = []
    for components, (has_toml, tracked) in seen_dirs.items():
        directory = workspace_root.joinpath(*components)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "Cargo.lock").write_text("", encoding="utf-8")
        if has_toml:
            (directory / "Cargo.toml").write_text("", encoding="utf-8")
        if tracked:
            tracked_lines.append("/".join((*components, "Cargo.lock")))
    return tracked_lines


def _assert_output_invariants(
    result: cabc.Sequence[Path],
    workspace_root: Path,
    tracked_lines: list[str],
) -> None:
    """Assert every returned path satisfies the four filtering invariants."""
    for path in result:
        relative = path.relative_to(workspace_root)
        assert path.name == "Cargo.lock"
        assert "target" not in relative.parts
        assert (path.parent / "Cargo.toml").exists()
        assert str(relative) in tracked_lines


def _expected_lockfiles(
    workspace_root: Path,
    seen_dirs: dict[tuple[str, ...], tuple[bool, bool]],
) -> set[Path]:
    """Return the lockfiles discovery must yield for the generated tree."""
    return {
        workspace_root.joinpath(*components, "Cargo.lock")
        for components, (has_toml, tracked) in seen_dirs.items()
        if tracked and has_toml and "target" not in components
    }


@given(entries=st.lists(_tree_entry, max_size=8))
@settings(max_examples=40, deadline=None)
def test_discover_tracked_lockfiles_invariants(
    entries: list[tuple[list[str], bool, bool]],
) -> None:
    """Discovery output satisfies all four filtering invariants.

    For generated workspace trees (random ``Cargo.lock``/``Cargo.toml``
    placements, ``target/`` subtrees at varying depths) and synthesised
    ``git ls-files`` output, every returned path: ends with ``Cargo.lock``,
    has no ``target`` component, has an adjacent ``Cargo.toml`` on disk, and
    was present in the git output. The result is also complete: every
    tracked lockfile satisfying the invariants is returned.
    """
    with tempfile.TemporaryDirectory(prefix="lading-hypothesis-") as tmp:
        workspace_root = Path(tmp)
        seen_dirs = _deduplicate_entries(entries)
        tracked_lines = _populate_workspace(workspace_root, seen_dirs)
        result = lockfile.discover_tracked_lockfiles(
            workspace_root, _stub_git_runner("\n".join(tracked_lines))
        )
        _assert_output_invariants(result, workspace_root, tracked_lines)
        assert set(result) == _expected_lockfiles(workspace_root, seen_dirs)


# ---------------------------------------------------------------------------
# Metrics instrumentation (issue #91)
# ---------------------------------------------------------------------------


@pytest.fixture
def _metrics_registry() -> cabc.Iterator[None]:
    """Isolate the metric registry for instrumentation tests."""
    metrics.reset()
    yield
    metrics.reset()


def _static_runner(
    exit_code: int, stdout: str, stderr: str
) -> cabc.Callable[..., tuple[int, str, str]]:
    """Return a runner producing a fixed result."""

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del command, cwd, env
        return exit_code, stdout, stderr

    return runner


# ---------------------------------------------------------------------------
# CargoLockfileInspectionRepository adapter (issue #82)
# ---------------------------------------------------------------------------


def _recording_runner(
    calls: list[tuple[tuple[str, ...], Path | None, cabc.Mapping[str, str] | None]],
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> cabc.Callable[..., tuple[int, str, str]]:
    """Return a runner recording each invocation's command, cwd, and env."""

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        calls.append((tuple(command), cwd, env))
        return exit_code, stdout, stderr

    return runner


def test_adapter_discovers_lockfiles_binding_env(tmp_path: Path) -> None:
    """The adapter discovers tracked lockfiles through its bound runner and env."""
    (tmp_path / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (tmp_path / "Cargo.lock").write_text("", encoding="utf-8")
    calls: list[tuple[tuple[str, ...], Path | None, cabc.Mapping[str, str] | None]] = []
    base_env = {"CARGO_TERM_COLOR": "never"}
    repository = lockfile.CargoLockfileInspectionRepository(
        runner=_recording_runner(calls, stdout="Cargo.lock\n"),
        env=base_env,
    )

    result = repository.discover_tracked_lockfiles(tmp_path)

    assert result == (tmp_path / "Cargo.lock",)
    assert calls == [
        (("git", "ls-files", "**/Cargo.lock", "Cargo.lock"), tmp_path, base_env)
    ]


def test_adapter_validates_freshness_binding_env(tmp_path: Path) -> None:
    """The adapter probes freshness through its bound runner, applying env."""
    manifest_path = tmp_path / "Cargo.toml"
    calls: list[tuple[tuple[str, ...], Path | None, cabc.Mapping[str, str] | None]] = []
    base_env = {"CARGO_TERM_COLOR": "never"}
    repository = lockfile.CargoLockfileInspectionRepository(
        runner=_recording_runner(calls),
        env=base_env,
    )

    result = repository.validate_lockfile_freshness(manifest_path)

    assert result.is_fresh
    assert len(calls) == 1
    command, cwd, env = calls[0]
    assert command[:3] == ("cargo", "metadata", "--locked")
    assert cwd == manifest_path.parent
    assert env == base_env


def test_adapter_without_env_leaves_runner_env_untouched(tmp_path: Path) -> None:
    """With no bound env the adapter forwards calls without injecting one."""
    (tmp_path / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (tmp_path / "Cargo.lock").write_text("", encoding="utf-8")
    calls: list[tuple[tuple[str, ...], Path | None, cabc.Mapping[str, str] | None]] = []
    repository = lockfile.CargoLockfileInspectionRepository(
        runner=_recording_runner(calls, stdout="Cargo.lock\n"),
    )

    repository.discover_tracked_lockfiles(tmp_path)

    assert calls[0][2] is None


def test_adapter_honours_injected_manifest_exists(tmp_path: Path) -> None:
    """A custom ``manifest_exists`` predicate filters discovery results."""
    calls: list[tuple[tuple[str, ...], Path | None, cabc.Mapping[str, str] | None]] = []
    repository = lockfile.CargoLockfileInspectionRepository(
        runner=_recording_runner(calls, stdout="Cargo.lock\n"),
        manifest_exists=lambda _manifest: False,
    )

    assert repository.discover_tracked_lockfiles(tmp_path) == ()


@pytest.mark.usefixtures("_metrics_registry")
def test_discovery_records_lockfile_count(tmp_path: Path) -> None:
    """Discovery increments the discovered-lockfiles counter by the count."""
    (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
    (tmp_path / "Cargo.lock").write_text("", encoding="utf-8")

    lockfile.discover_tracked_lockfiles(tmp_path, _static_runner(0, "Cargo.lock\n", ""))

    assert metrics.counter_value(lockfile.DISCOVERED_LOCKFILES_METRIC) == 1


@pytest.mark.usefixtures("_metrics_registry")
@pytest.mark.parametrize(
    ("exit_code", "stderr", "expected_state"),
    [
        (0, "", "fresh"),
        (
            101,
            "the lock file needs to be updated but --locked was passed",
            "stale",
        ),
        (101, "unrelated explosion", "failed"),
    ],
)
def test_validation_records_outcome_and_duration(
    tmp_path: Path, exit_code: int, stderr: str, expected_state: str
) -> None:
    """Validation counts each outcome state and observes a duration."""
    lockfile.validate_lockfile_freshness(
        tmp_path / "Cargo.toml", _static_runner(exit_code, "", stderr)
    )

    assert metrics.counter_value(lockfile.VALIDATE_METRIC, outcome=expected_state) == 1
    assert metrics.duration_stats(lockfile.VALIDATE_DURATION_METRIC).count == 1
