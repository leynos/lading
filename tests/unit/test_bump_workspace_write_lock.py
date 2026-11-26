"""Test the stable per-user identity of workspace write locks."""

import os
import stat
import typing as typ
from pathlib import Path

import pytest

from lading.commands import bump_workspace_write_lock
from lading.utils import file_lock

if os.name != "nt":
    import pwd

HOME_UNAVAILABLE = "home is unavailable"
UNLOCK_FAILURE_MESSAGE = "unlock failed"
WRITE_FAILURE_MESSAGE = "write failed"


def test_lock_directory_is_independent_of_xdg_cache_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different process cache and home settings resolve one lock directory."""
    if os.name == "nt":
        pytest.skip("Windows bounded writes do not use the workspace lock")
    stable_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    monkeypatch.setenv("HOME", "/home/first-process")
    monkeypatch.setenv("XDG_CACHE_HOME", "/cache/first-process")
    first_directory = bump_workspace_write_lock._workspace_lock_directory()
    monkeypatch.setenv("HOME", "/home/second-process")
    monkeypatch.setenv("XDG_CACHE_HOME", "/cache/second-process")

    second_directory = bump_workspace_write_lock._workspace_lock_directory()

    expected_directory = (
        stable_home / ".local" / "state" / "lading" / "workspace-write-locks"
    )
    assert first_directory == expected_directory, (
        "the lock directory should be stable for this operating-system user"
    )
    assert second_directory == first_directory, (
        "per-process XDG settings must not split a workspace lock identity"
    )


def test_lock_directory_reports_unavailable_home_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing account home becomes an actionable permission failure."""

    def unavailable_home() -> Path:
        """Simulate a host without a discoverable home directory."""
        raise RuntimeError(HOME_UNAVAILABLE)

    if os.name == "nt":
        monkeypatch.setattr(Path, "home", unavailable_home)
    else:

        def unavailable_account_home(_user_id: int) -> None:
            """Simulate a missing password database entry."""
            raise KeyError(HOME_UNAVAILABLE)

        monkeypatch.setattr(pwd, "getpwuid", unavailable_account_home)

    with pytest.raises(
        PermissionError, match="stable per-user workspace write lock directory"
    ):
        bump_workspace_write_lock._workspace_lock_directory()


def test_lock_directory_requires_a_passwd_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A numeric UID without an account entry reports the requirement."""
    if os.name == "nt":
        pytest.skip("Windows resolves its state directory from Path.home()")

    def unavailable_account_home(_user_id: int) -> None:
        """Simulate a container UID with no passwd entry."""
        raise KeyError(HOME_UNAVAILABLE)

    monkeypatch.setattr(pwd, "getpwuid", unavailable_account_home)

    with pytest.raises(PermissionError, match="no passwd entry"):
        bump_workspace_write_lock._workspace_lock_directory()


def test_workspace_lock_key_uses_filesystem_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filesystem aliases share a key while distinct directories do not."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    assert bump_workspace_write_lock._lock_key_for_identity(
        bump_workspace_write_lock._workspace_identity(first)
    ) != (
        bump_workspace_write_lock._lock_key_for_identity(
            bump_workspace_write_lock._workspace_identity(second)
        )
    ), "distinct workspace directories must receive distinct lock keys"
    shared_metadata = os.stat_result((
        stat.S_IFDIR | 0o700,
        42,
        23,
        1,
        1,
        1,
        0,
        0,
        0,
        0,
    ))
    original_stat = Path.stat

    def stat_alias(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        """Give two spellings the same simulated filesystem identity."""
        if path in {first, second}:
            return shared_metadata
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", stat_alias)

    assert bump_workspace_write_lock._lock_key_for_identity(
        bump_workspace_write_lock._workspace_identity(first)
    ) == (
        bump_workspace_write_lock._lock_key_for_identity(
            bump_workspace_write_lock._workspace_identity(second)
        )
    ), "aliases of one directory must acquire the same lock"


def test_workspace_lock_reuses_resolved_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unresolved alias of a held workspace lock must not deadlock."""
    monkeypatch.setattr(
        bump_workspace_write_lock,
        "_workspace_lock_directory",
        lambda: tmp_path / "locks",
    )
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    alias = tmp_path / "workspace-link"
    try:
        alias.symlink_to(workspace_root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")

    with (
        bump_workspace_write_lock.workspace_write_lock(workspace_root),
        bump_workspace_write_lock.workspace_write_lock(alias) as inner,
        bump_workspace_write_lock.workspace_write_lock(
            workspace_root.resolve()
        ) as resolved,
    ):
        assert inner == resolved, "resolved aliases must reuse the held lock"


def test_workspace_write_lock_preserves_write_error_when_unlock_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlock failure is attached to the write error instead of replacing it."""
    monkeypatch.setattr(
        bump_workspace_write_lock,
        "_workspace_lock_directory",
        lambda: tmp_path / "locks",
    )
    real_set_lock = file_lock.set_exclusive_lock

    def fail_unlock(handle: typ.IO[bytes], *, locked: bool) -> None:
        """Allow acquisition, then fail the release operation."""
        if not locked:
            raise OSError(UNLOCK_FAILURE_MESSAGE)
        real_set_lock(handle, locked=locked)

    monkeypatch.setattr(file_lock, "set_exclusive_lock", fail_unlock)

    with (
        pytest.raises(RuntimeError, match=WRITE_FAILURE_MESSAGE) as error,
        bump_workspace_write_lock.workspace_write_lock(tmp_path.resolve()),
    ):
        raise RuntimeError(WRITE_FAILURE_MESSAGE)

    assert any(UNLOCK_FAILURE_MESSAGE in note for note in error.value.__notes__), (
        "unlock failure should remain available as diagnostic context"
    )


def test_workspace_write_lock_propagates_unlock_error_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unlock failure remains visible when the protected work succeeded."""
    monkeypatch.setattr(
        bump_workspace_write_lock,
        "_workspace_lock_directory",
        lambda: tmp_path / "locks",
    )

    def fail_unlock(_handle: typ.IO[bytes], *, locked: bool) -> None:
        """Fail only release, after allowing the protected block to run."""
        if not locked:
            raise OSError(UNLOCK_FAILURE_MESSAGE)

    monkeypatch.setattr(file_lock, "set_exclusive_lock", fail_unlock)

    with (
        pytest.raises(OSError, match=UNLOCK_FAILURE_MESSAGE),
        bump_workspace_write_lock.workspace_write_lock(tmp_path.resolve()),
    ):
        pass
