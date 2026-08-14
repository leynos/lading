"""Publish-side lockfile inspection port and its git/cargo adapter.

This module holds the hexagonal boundary for lockfile inspection (issue #82):
:class:`LockfileInspectionRepository` is the port the publish pre-flight domain
depends on, and :class:`CargoLockfileInspectionRepository` is the git- and
cargo-backed adapter bound to a command runner at the composition root. The
domain operations themselves live in :mod:`lading.commands.lockfile`; keeping
the adapter here leaves that module free of wiring concerns.

Examples
--------
```python
from pathlib import Path

repository = CargoLockfileInspectionRepository(runner=runner, env=base_env)
lockfiles = repository.discover_tracked_lockfiles(Path("."))
for lockfile_path in lockfiles:
    repository.validate_lockfile_freshness(lockfile_path.parent / "Cargo.toml")
```
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import typing as typ

from lading.commands.lockfile import (
    _manifest_exists,
    discover_tracked_lockfiles,
    validate_lockfile_freshness,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.commands.lockfile import LockfileFreshness, _ManifestExists
    from lading.runtime import CommandRunner


@dc.dataclass(frozen=True, slots=True)
class CargoLockfileInspectionRepository:
    """Git- and cargo-backed adapter for publish-side lockfile inspection.

    Binds a :class:`~lading.runtime.CommandRunner` (and optional environment
    overrides) so the publish pre-flight domain can discover tracked lockfiles
    and probe their freshness without holding a raw command runner (issue #82).
    The adapter applies ``env`` to any invocation that does not supply its own,
    matching the behaviour the pre-flight base environment previously wired in
    through an inline runner wrapper.

    Attributes
    ----------
    runner : CommandRunner
        Command runner used to execute the git discovery and cargo freshness
        probes.
    env : Mapping[str, str] | None, default None
        Environment overrides applied to any invocation that does not supply
        its own; ``None`` leaves each call's environment untouched.
    manifest_exists : Callable[[Path], bool], default _manifest_exists
        Predicate deciding whether a discovered lockfile has an adjacent
        ``Cargo.toml`` manifest; the default checks the filesystem.
    """

    runner: CommandRunner
    env: cabc.Mapping[str, str] | None = None
    manifest_exists: _ManifestExists = _manifest_exists

    def discover_tracked_lockfiles(self, workspace_root: Path) -> tuple[Path, ...]:
        """Return tracked Cargo.lock files with adjacent manifests.

        Parameters
        ----------
        workspace_root
            Path to the repository root searched for tracked lockfiles.

        Returns
        -------
        tuple[Path, ...]
            Git-tracked ``Cargo.lock`` files outside any ``target`` directory
            that have an adjacent ``Cargo.toml`` manifest.

        """
        return discover_tracked_lockfiles(
            workspace_root,
            self._bound_runner(),
            manifest_exists=self.manifest_exists,
        )

    def validate_lockfile_freshness(self, manifest_path: Path) -> LockfileFreshness:
        """Return Cargo's locked-mode freshness result for ``manifest_path``.

        Parameters
        ----------
        manifest_path
            Path to the Cargo manifest to validate under ``--locked``.

        Returns
        -------
        LockfileFreshness
            Structured result describing whether the lockfile is fresh, stale
            (Cargo says it needs updating under ``--locked``), or failed for
            another reason.

        """
        return validate_lockfile_freshness(manifest_path, self._bound_runner())

    def _bound_runner(self) -> CommandRunner:
        """Return ``runner`` with ``env`` applied when a call omits its own."""
        if self.env is None:
            return self.runner
        base_env = self.env
        base_runner = self.runner

        def runner_with_env(
            command: cabc.Sequence[str],
            *,
            cwd: Path | None = None,
            env: cabc.Mapping[str, str] | None = None,
            **runner_kwargs: bool,
        ) -> tuple[int, str, str]:
            """Invoke ``base_runner`` with ``base_env`` as the default env."""
            effective_env = base_env if env is None else env
            return base_runner(command, cwd=cwd, env=effective_env, **runner_kwargs)

        return runner_with_env


class LockfileInspectionRepository(typ.Protocol):
    """Port for discovering tracked lockfiles and probing their freshness.

    The publish pre-flight domain depends on this protocol rather than on a
    command runner, keeping VCS, filesystem, and cargo execution concerns out
    of the freshness-classification logic (issue #82). This is the publish-side
    counterpart to :class:`lading.commands.bump_lockfiles.LockfileRepository`,
    which owns bump-side lockfile projection and regeneration.
    """

    def discover_tracked_lockfiles(self, workspace_root: Path) -> tuple[Path, ...]:
        """Return tracked Cargo.lock files with adjacent manifests.

        Parameters
        ----------
        workspace_root
            Path to the repository root searched for tracked lockfiles.

        Returns
        -------
        tuple[Path, ...]
            Git-tracked ``Cargo.lock`` files outside any ``target`` directory
            that have an adjacent ``Cargo.toml`` manifest.

        """

    def validate_lockfile_freshness(self, manifest_path: Path) -> LockfileFreshness:
        """Return the freshness result for ``manifest_path``.

        Parameters
        ----------
        manifest_path
            Path to the Cargo manifest to validate.

        Returns
        -------
        LockfileFreshness
            The freshness result, distinguishing fresh, stale, and failed
            states.

        """
