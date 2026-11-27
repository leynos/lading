"""Direct tests for the per-workspace advisory bump lock.

The lock is the only thing that serializes two cooperating live bump runs for
one workspace. These tests pin its exclusion, thread-local re-entry, private
directory contract, passwd-home resolution, and unlock-failure reporting. The
pipeline ordering tests use the lock as a collaborator; they are not a
substitute for exercising it here.
"""

import os
import pathlib
import stat
import threading
import time
import typing as typ

import pytest

from lading.commands import bump_workspace_write_lock
from lading.utils import file_lock


@pytest.fixture
def lock_directory(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> pathlib.Path:
    """Redirect the lock directory into ``tmp_path`` for the test."""
    directory = tmp_path / "state" / "locks"
    monkeypatch.setattr(
        bump_workspace_write_lock,
        "_workspace_lock_directory",
        lambda: directory,
    )
    return directory


def _workspace(tmp_path: pathlib.Path, name: str) -> pathlib.Path:
    """Create and return a distinct workspace root."""
    root = tmp_path / name
    root.mkdir()
    return root


def test_a_second_thread_cannot_take_the_held_lock(
    tmp_path: pathlib.Path,
    lock_directory: pathlib.Path,
) -> None:
    """The same root is exclusive across threads until the holder exits."""
    root = _workspace(tmp_path, "workspace")
    entered = threading.Event()
    release = threading.Event()
    outcomes: list[str] = []

    def hold() -> None:
        """Hold the workspace lock until the contender has tried it."""
        with bump_workspace_write_lock.workspace_write_lock(root):
            outcomes.append("held")
            entered.set()
            release.wait(timeout=10)
        outcomes.append("released")

    def contend() -> None:
        """Attempt the same lock while the holder still owns it."""
        entered.wait(timeout=10)
        try:
            with bump_workspace_write_lock.workspace_write_lock(root):
                outcomes.append("acquired-early")
        except OSError:
            outcomes.append("refused")

    holder = threading.Thread(target=hold)
    contender = threading.Thread(target=contend)
    holder.start()
    contender.start()
    contender.join(timeout=10)
    release.set()
    holder.join(timeout=10)

    assert outcomes == ["held", "refused", "released"], (
        "the second thread must be refused while the first holds the lock"
    )
    with bump_workspace_write_lock.workspace_write_lock(root):
        outcomes.append("acquired-after-release")

    assert outcomes[-1] == "acquired-after-release", (
        "the lock must be available once the holder releases it"
    )
    assert lock_directory.exists(), "the lock directory must be created on demand"


def test_a_different_workspace_root_does_not_conflict(
    tmp_path: pathlib.Path,
    lock_directory: pathlib.Path,
) -> None:
    """Distinct roots take distinct lock files and do not exclude each other."""
    first = _workspace(tmp_path, "first")
    second = _workspace(tmp_path, "second")

    with (
        bump_workspace_write_lock.workspace_write_lock(first),
        bump_workspace_write_lock.workspace_write_lock(second),
    ):
        held = len(list(lock_directory.glob("*.lock")))

    assert held == 2, "each workspace root must have its own lock file"


def test_nested_use_on_one_thread_reuses_the_lock(
    tmp_path: pathlib.Path,
    lock_directory: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nested acquisition on the same thread locks once and unlocks once."""
    root = _workspace(tmp_path, "workspace")
    calls: list[bool] = []
    original = file_lock.set_exclusive_lock

    def record(handle: typ.IO[bytes], *, locked: bool) -> None:
        """Record the lock state each call requests."""
        calls.append(locked)
        original(handle, locked=locked)

    monkeypatch.setattr(file_lock, "set_exclusive_lock", record)

    # Two acquisitions on one thread must reuse the first lock, not take a
    # second one.
    with (
        bump_workspace_write_lock.workspace_write_lock(root),
        bump_workspace_write_lock.workspace_write_lock(root),
    ):
        pass

    assert calls == [True, False], (
        "a nested acquisition must reuse the held lock instead of relocking"
    )


def test_the_created_lock_directory_is_private(
    tmp_path: pathlib.Path,
    lock_directory: pathlib.Path,
) -> None:
    """A newly created lock directory is mode 0700 for the current account."""
    root = _workspace(tmp_path, "workspace")

    with bump_workspace_write_lock.workspace_write_lock(root):
        pass

    mode = stat.S_IMODE(lock_directory.stat().st_mode)
    assert mode == 0o700, "the lock directory must be private to the account"


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions")
def test_a_group_accessible_lock_directory_is_rejected(
    tmp_path: pathlib.Path,
    lock_directory: pathlib.Path,
) -> None:
    """A pre-existing group-accessible directory fails closed."""
    lock_directory.mkdir(parents=True)
    lock_directory.chmod(0o750)
    root = _workspace(tmp_path, "workspace")

    with (
        pytest.raises(PermissionError, match="not private"),
        bump_workspace_write_lock.workspace_write_lock(root),
    ):
        pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership")
def test_a_foreign_owned_lock_directory_is_rejected(
    tmp_path: pathlib.Path,
    lock_directory: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A directory owned by another account is rejected on inspection."""
    lock_directory.mkdir(parents=True)
    lock_directory.chmod(0o700)
    root = _workspace(tmp_path, "workspace")
    real_getuid = os.getuid
    monkeypatch.setattr(
        bump_workspace_write_lock.os, "getuid", lambda: real_getuid() + 1
    )

    with (
        pytest.raises(PermissionError, match="not private"),
        bump_workspace_write_lock.workspace_write_lock(root),
    ):
        pass


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(("missing", None), id="missing-passwd-entry"),
        pytest.param(("home", "relative/home"), id="non-absolute-passwd-home"),
    ],
)
@pytest.mark.skipif(os.name == "nt", reason="passwd lookup is POSIX-only")
def test_an_unusable_passwd_home_raises_permission_error(
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str | None],
) -> None:
    """A missing or non-absolute passwd home cannot name a stable directory."""
    failure_kind, home = case
    pwd_module = bump_workspace_write_lock.pwd

    if failure_kind == "missing":

        def raise_key_error(uid: int) -> object:
            """Report a UID with no passwd entry."""
            message = "no such user"
            raise KeyError(message)

        monkeypatch.setattr(pwd_module, "getpwuid", raise_key_error)
    else:

        class _Entry:
            """Minimal passwd record carrying the configured home."""

            pw_dir = home

        monkeypatch.setattr(pwd_module, "getpwuid", lambda uid: _Entry())

    with pytest.raises(PermissionError, match="stable per-user"):
        bump_workspace_write_lock._workspace_lock_directory()


def test_unlock_failure_is_attached_to_the_primary_error(
    tmp_path: pathlib.Path,
    lock_directory: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing unlock annotates the error raised by the locked block."""
    root = _workspace(tmp_path, "workspace")
    original = file_lock.set_exclusive_lock

    def fail_unlock(handle: typ.IO[bytes], *, locked: bool) -> None:
        """Allow the acquisition but reject the release."""
        if not locked:
            message = "unlock rejected"
            raise OSError(message)
        original(handle, locked=locked)

    monkeypatch.setattr(file_lock, "set_exclusive_lock", fail_unlock)

    message = "body failed"
    with (
        pytest.raises(ValueError, match=message) as failure,
        bump_workspace_write_lock.workspace_write_lock(root),
    ):
        raise ValueError(message)

    notes = getattr(failure.value, "__notes__", [])
    assert any("Could not release workspace write lock" in note for note in notes), (
        "the unlock failure must be attached as a note to the primary error"
    )


def test_a_failing_body_releases_the_lock(
    tmp_path: pathlib.Path,
    lock_directory: pathlib.Path,
) -> None:
    """An exception inside the block still releases the held lock."""
    root = _workspace(tmp_path, "workspace")

    message = "boom"
    with (
        pytest.raises(ValueError, match=message),
        bump_workspace_write_lock.workspace_write_lock(root),
    ):
        raise ValueError(message)

    deadline = time.monotonic() + 10
    with bump_workspace_write_lock.workspace_write_lock(root):
        pass
    assert time.monotonic() < deadline, "the released lock must be reacquirable"
