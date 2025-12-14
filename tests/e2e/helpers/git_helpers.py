"""Real Git helpers for end-to-end tests."""

from __future__ import annotations

import shutil
import typing as typ

from plumbum import local

if typ.TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path
else:  # pragma: no cover - runtime typing fallback
    Path = typ.Any  # type: ignore[assignment]


class GitCommandError(RuntimeError):
    """Raised when a git subprocess returns a non-zero exit status."""

    def __init__(
        self,
        command: tuple[str, ...],
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> None:
        """Format a descriptive error message for the failing git command."""
        rendered = " ".join(("git", *command))
        detail = (stderr or stdout).strip()
        message = f"{rendered} failed with exit code {exit_code}"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


def _run_git(repo_path: Path, *args: str) -> tuple[int, str, str]:
    with local.cwd(str(repo_path)):
        return local["git"].run(args, retcode=None)


def _run_git_checked(repo_path: Path, *args: str) -> tuple[str, str]:
    exit_code, stdout, stderr = _run_git(repo_path, *args)
    if exit_code != 0:
        raise GitCommandError(tuple(args), exit_code, stdout, stderr)
    return stdout, stderr


def git_init(repo_path: Path) -> None:
    """Initialise a git repository at ``repo_path``."""
    _run_git_checked(repo_path, "init")


def git_config_user(repo_path: Path) -> None:
    """Set local user.name/user.email so commits succeed in ephemeral repos."""
    _run_git_checked(repo_path, "config", "user.email", "e2e@example.invalid")
    _run_git_checked(repo_path, "config", "user.name", "Lading E2E")


def git_add_all(repo_path: Path) -> None:
    """Stage all changes in ``repo_path``."""
    _run_git_checked(repo_path, "add", "-A")


def git_commit(repo_path: Path, message: str) -> None:
    """Commit staged changes in ``repo_path``."""
    _run_git_checked(repo_path, "commit", "-m", message)


def git_status_porcelain(repo_path: Path) -> str:
    """Return `git status --porcelain` output."""
    stdout, _stderr = _run_git_checked(repo_path, "status", "--porcelain")
    return stdout


def git_is_clean(repo_path: Path) -> bool:
    """Return ``True`` when git reports no uncommitted changes."""
    return not git_status_porcelain(repo_path).strip()


def rmtree(path: Path) -> None:
    """Remove ``path`` recursively, ignoring missing paths."""
    shutil.rmtree(path, ignore_errors=True)
