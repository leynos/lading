"""Unit tests for the _validate_lockfile_freshness pre-flight helper.

These exercise the pre-flight freshness domain step through the
:class:`lading.commands.lockfile.LockfileInspectionRepository` port (issue
#82): tests inject a recording repository double instead of a command runner,
so discovery, classification, and remediation messaging are verified without
touching git or cargo.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ
from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from lading.commands import lockfile, publish, publish_preflight

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from syrupy.assertion import SnapshotAssertion


@dc.dataclass
class _RecordingLockfileRepository:
    """In-memory ``LockfileInspectionRepository`` double for pre-flight tests."""

    tracked: tuple[Path, ...]
    freshness: cabc.Mapping[Path, lockfile.LockfileFreshness] | None = None
    default_freshness: lockfile.LockfileFreshness = dc.field(
        default_factory=lambda: lockfile.LockfileFreshness(is_fresh=True)
    )
    discovered_roots: list[Path] = dc.field(default_factory=list)
    validated_manifests: list[Path] = dc.field(default_factory=list)

    def discover_tracked_lockfiles(self, workspace_root: Path) -> tuple[Path, ...]:
        """Record the discovery call and return the configured lockfiles."""
        self.discovered_roots.append(workspace_root)
        return self.tracked

    def validate_lockfile_freshness(
        self, manifest_path: Path
    ) -> lockfile.LockfileFreshness:
        """Record the validation call and return the configured freshness."""
        self.validated_manifests.append(manifest_path)
        if self.freshness is not None and manifest_path in self.freshness:
            return self.freshness[manifest_path]
        return self.default_freshness


_STALE_DETAIL = "the lock file Cargo.lock needs to be updated but --locked was passed"

_outcome = st.sampled_from(("fresh", "stale", "error"))


def _repository_for_outcomes(
    tmp_path: Path, outcomes: list[str]
) -> tuple[_RecordingLockfileRepository, list[Path]]:
    """Build a recording repository mapping each outcome to a tracked lockfile.

    Each outcome at index ``i`` yields the lockfile ``tmp_path/pkgi/Cargo.lock``
    and a ``freshness`` entry keyed on its adjacent ``Cargo.toml``: ``fresh``
    passes, ``stale`` is flagged for repair, and ``error`` is an unexpected
    cargo failure. Returns the repository and the ordered lockfile paths.
    """
    freshness_for = {
        "fresh": lockfile.LockfileFreshness(is_fresh=True),
        "stale": lockfile.LockfileFreshness(
            is_fresh=False, is_stale=True, detail=_STALE_DETAIL
        ),
        "error": lockfile.LockfileFreshness(is_fresh=False, detail="boom"),
    }
    lockfiles: list[Path] = []
    freshness: dict[Path, lockfile.LockfileFreshness] = {}
    for i, outcome in enumerate(outcomes):
        lockfile_path = tmp_path / f"pkg{i}" / "Cargo.lock"
        lockfiles.append(lockfile_path)
        freshness[lockfile_path.parent / "Cargo.toml"] = freshness_for[outcome]
    repository = _RecordingLockfileRepository(
        tracked=tuple(lockfiles), freshness=freshness
    )
    return repository, lockfiles


def _stale_lockfiles_error_message(workspace_root: Path) -> str:
    """Return the raised error for a root and nested stale lockfile pair.

    Drives ``_validate_lockfile_freshness`` through a recording port double
    whose tracked lockfiles are all stale, and returns the resulting
    ``PublishPreflightError`` message for assertion or snapshotting.
    """
    root_lockfile = workspace_root / "Cargo.lock"
    nested_lockfile = workspace_root / "tests" / "ui_lints" / "Cargo.lock"
    repository = _RecordingLockfileRepository(
        tracked=(root_lockfile, nested_lockfile),
        default_freshness=lockfile.LockfileFreshness(
            is_fresh=False, is_stale=True, detail=_STALE_DETAIL
        ),
    )

    with pytest.raises(
        publish.PublishPreflightError,
        match="Tracked Cargo\\.lock files are stale",
    ) as excinfo:
        publish_preflight._validate_lockfile_freshness(
            workspace_root, repository=repository
        )

    return str(excinfo.value)


def test_validate_lockfile_freshness_passes_when_all_lockfiles_are_fresh(
    tmp_path: Path,
) -> None:
    """Fresh tracked lockfiles allow preflight to continue via the port."""
    root_lockfile = tmp_path / "Cargo.lock"
    nested_lockfile = tmp_path / "tests" / "ui_lints" / "Cargo.lock"
    repository = _RecordingLockfileRepository(tracked=(root_lockfile, nested_lockfile))

    publish_preflight._validate_lockfile_freshness(tmp_path, repository=repository)

    assert repository.discovered_roots == [tmp_path]
    assert repository.validated_manifests == [
        root_lockfile.parent / "Cargo.toml",
        nested_lockfile.parent / "Cargo.toml",
    ]


def test_validate_lockfile_freshness_reports_stale_lockfiles(tmp_path: Path) -> None:
    """Stale lockfiles are collected and reported with repair commands."""
    message = _stale_lockfiles_error_message(tmp_path)
    root_lockfile = tmp_path / "Cargo.lock"
    nested_lockfile = tmp_path / "tests" / "ui_lints" / "Cargo.lock"

    assert str(root_lockfile) in message
    assert str(nested_lockfile) in message
    assert "lading bump" in message
    assert (
        f"cargo generate-lockfile --manifest-path {tmp_path / 'Cargo.toml'}" in message
    )
    assert (
        "cargo generate-lockfile --manifest-path "
        f"{tmp_path / 'tests' / 'ui_lints' / 'Cargo.toml'}"
    ) in message


def test_validate_lockfile_freshness_error_snapshot(
    snapshot: SnapshotAssertion,
) -> None:
    """Stale lockfile remediation output is locked by snapshot."""
    message = _stale_lockfiles_error_message(Path("/workspace root"))

    assert message == snapshot(), (
        "stale lockfile remediation message drifted from its recorded snapshot"
    )


def test_validate_lockfile_freshness_surfaces_cargo_failures(tmp_path: Path) -> None:
    """Cargo failures unrelated to stale lockfiles abort with cargo details."""
    root_lockfile = tmp_path / "Cargo.lock"
    repository = _RecordingLockfileRepository(
        tracked=(root_lockfile,),
        default_freshness=lockfile.LockfileFreshness(
            is_fresh=False, detail="failed to download registry index"
        ),
    )

    with pytest.raises(
        publish.PublishPreflightError,
        match="failed to download registry index",
    ):
        publish_preflight._validate_lockfile_freshness(tmp_path, repository=repository)


def test_validate_lockfile_freshness_classifies_every_lockfile(
    tmp_path: Path,
) -> None:
    """All tracked lockfiles are probed even when the first is already stale.

    Issue #83 evaluated short-circuiting on the first stale lockfile and
    rejected it: every tracked lockfile must be classified so the aggregated
    error lists each stale path for a single-pass repair. This pins the
    full-classification behaviour by asserting every tracked manifest is
    probed through the port, not just that the first stale result aborts.
    """
    lockfiles = (
        tmp_path / "Cargo.lock",
        tmp_path / "a" / "Cargo.lock",
        tmp_path / "b" / "Cargo.lock",
    )
    repository = _RecordingLockfileRepository(
        tracked=lockfiles,
        default_freshness=lockfile.LockfileFreshness(
            is_fresh=False, is_stale=True, detail=_STALE_DETAIL
        ),
    )

    with pytest.raises(
        publish.PublishPreflightError,
        match="Tracked Cargo\\.lock files are stale",
    ) as excinfo:
        publish_preflight._validate_lockfile_freshness(tmp_path, repository=repository)

    # No short-circuit: every tracked lockfile is probed despite the first
    # being stale (issue #83).
    expected_manifests = [path.parent / "Cargo.toml" for path in lockfiles]
    assert repository.validated_manifests == expected_manifests, (
        "every tracked lockfile must be probed without short-circuiting; "
        f"expected {expected_manifests}, got {repository.validated_manifests}"
    )
    message = str(excinfo.value)
    for path in lockfiles:
        assert str(path) in message, (
            f"stale lockfile {path} missing from aggregated error: {message}"
        )


@given(outcomes=st.lists(_outcome, min_size=1, max_size=6))
# tmp_path is used only to build Path objects (never written to), so reusing
# the same function-scoped fixture across Hypothesis examples is safe here.
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_validate_lockfile_freshness_probes_every_lockfile_until_error(
    tmp_path: Path, outcomes: list[str]
) -> None:
    """Property: every lockfile is classified in order until an error aborts.

    Varies the count, order, and per-lockfile outcome (fresh/stale/error).
    With no hard ``error``, every tracked lockfile is probed in order and no
    stale result short-circuits classification (a purely fresh set returns
    cleanly; any stale lockfile still aborts with the aggregated stale error,
    but only after every lockfile is probed). A hard ``error`` aborts
    immediately at the first such lockfile (issue #83).
    """
    repository, lockfiles = _repository_for_outcomes(tmp_path, outcomes)
    expected_manifests = [path.parent / "Cargo.toml" for path in lockfiles]
    first_error = next(
        (i for i, outcome in enumerate(outcomes) if outcome == "error"), None
    )

    if first_error is None:
        stale_paths = [
            path
            for path, outcome in zip(lockfiles, outcomes, strict=True)
            if outcome == "stale"
        ]
        fresh_paths = [
            path
            for path, outcome in zip(lockfiles, outcomes, strict=True)
            if outcome == "fresh"
        ]
        if stale_paths:
            with pytest.raises(
                publish.PublishPreflightError,
                match="Tracked Cargo\\.lock files are stale",
            ) as excinfo:
                publish_preflight._validate_lockfile_freshness(
                    tmp_path, repository=repository
                )
            # pkg indices stay single-digit (max_size=6), so no pkgN path is a
            # substring of another and plain containment is unambiguous. Raising
            # max_examples' list size to >=11 would reintroduce pkg1-vs-pkg10
            # ambiguity and would need full-line assertions instead.
            message = str(excinfo.value)
            for path in stale_paths:
                assert str(path) in message, (
                    f"stale lockfile {path} missing from aggregated error: {message}"
                )
            for path in fresh_paths:
                assert str(path) not in message, (
                    f"fresh lockfile {path} wrongly reported as stale: {message}"
                )
        else:
            publish_preflight._validate_lockfile_freshness(
                tmp_path, repository=repository
            )
        assert repository.validated_manifests == expected_manifests, (
            "every tracked lockfile must be probed in order without "
            f"short-circuiting; expected {expected_manifests}, got "
            f"{repository.validated_manifests}"
        )
    else:
        with pytest.raises(publish.PublishPreflightError, match="boom"):
            publish_preflight._validate_lockfile_freshness(
                tmp_path, repository=repository
            )
        expected_prefix = expected_manifests[: first_error + 1]
        assert repository.validated_manifests == expected_prefix, (
            f"classification must stop at the first error (index {first_error}); "
            f"expected {expected_prefix}, got {repository.validated_manifests}"
        )
