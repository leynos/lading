"""Public lockfile regeneration façade for ``lading bump``.

The implementation is split by responsibility while this module retains the
established public imports and the bump-side repository port and adapter.
"""

import collections.abc as cabc
import dataclasses as dc
import typing as typ
from pathlib import Path

from lading.commands.bump_lockfile_manifests import merge_discovered_manifests
from lading.commands.bump_lockfile_paths import (
    LockfileRegenerationError as LockfileRegenerationError,
)
from lading.commands.bump_lockfile_paths import (
    resolve_lockfile_paths,
)
from lading.commands.bump_lockfile_planning import (
    LockfilePlan,
    plan_lockfiles,
)
from lading.commands.bump_lockfile_regeneration import (
    regenerate_lockfiles,
    regenerate_planned,
)

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner


@dc.dataclass(frozen=True, slots=True)
class CargoLockfileRepository:
    """Cargo-backed :class:`LockfileRepository` bound to a command runner.

    Attributes
    ----------
    runner : CommandRunner | None
        Runner used for Git discovery and Cargo invocations; ``None`` selects
        the default subprocess runner.
    environ : cabc.Mapping[str, str] | None
        Environment checked for ``CARGO_RESOLVER_LOCKFILE_PATH`` before a plan
        is built. ``None`` reads :data:`os.environ`; the CLI supplies the
        process environment explicitly.
    """

    runner: CommandRunner | None = None
    environ: cabc.Mapping[str, str] | None = None

    def plan_lockfiles(
        self,
        workspace_root: Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> LockfilePlan:
        """Plan lockfile destinations before any manifest is rewritten.

        Parameters
        ----------
        workspace_root :
            Canonical workspace root.
        lockfile_manifests :
            Configured manifests relative to *workspace_root*. Git-tracked
            lockfile manifests are merged before validation.

        Returns
        -------
        LockfilePlan
            Effective workspace-root manifests and their lockfile
            destinations.

        Raises
        ------
        BumpPreflightError
            If a target is unsafe, missing, or Cargo reports an unexpected
            workspace root, or ``CARGO_RESOLVER_LOCKFILE_PATH`` is set.
        """
        return plan_lockfiles(
            workspace_root,
            lockfile_manifests,
            runner=self.runner,
            environ=self.environ,
        )

    def regenerate_planned(self, plan: LockfilePlan) -> tuple[Path, ...]:
        """Regenerate the lockfiles named by a validated plan.

        Parameters
        ----------
        plan :
            Destination plan produced by :meth:`plan_lockfiles`.

        Returns
        -------
        tuple[Path, ...]
            The validated lockfile destinations.

        Raises
        ------
        LockfileRegenerationError
            After every manifest is attempted, if any ``cargo update`` failed.
        """
        return regenerate_planned(plan, runner=self.runner)

    def resolve_lockfile_paths(
        self,
        workspace_root: Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[Path, ...]:
        """Return the lockfile paths a regeneration run would touch.

        Parameters
        ----------
        workspace_root :
            Absolute path to the Cargo workspace root.
        lockfile_manifests :
            Configured manifest paths relative to *workspace_root*. Manifests
            implied by git-tracked lockfiles are merged before the
            workspace-root ``Cargo.toml`` is prepended and de-duplicated.

        Returns
        -------
        tuple[Path, ...]
            The lockfile paths that a regeneration run would touch,
            in manifest execution order.

        Raises
        ------
        LockfileRegenerationError
            If a configured manifest is outside the workspace or is not named
            ``Cargo.toml``.

        Examples
        --------
        Given ``fixtures/minimal/Cargo.toml`` beside the discovered lockfile,
        configured paths remain ahead of discovered paths:

        ```python
        def git_runner(command, **kwargs):
            return 0, "fixtures/minimal/Cargo.lock", ""

        repository = CargoLockfileRepository(runner=git_runner)
        paths = repository.resolve_lockfile_paths(
            Path("/workspace"),
            ("crates/ui/Cargo.toml",),
        )
        [path.as_posix() for path in paths]
        # [
        #     "/workspace/Cargo.lock",
        #     "/workspace/crates/ui/Cargo.lock",
        #     "/workspace/fixtures/minimal/Cargo.lock",
        # ]
        ```
        """
        merged_manifests = merge_discovered_manifests(
            workspace_root,
            lockfile_manifests,
            runner=self.runner,
            emit_discovery_observability=False,
        )
        return resolve_lockfile_paths(workspace_root, merged_manifests)

    def regenerate_lockfiles(
        self,
        workspace_root: Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[Path, ...]:
        """Regenerate lockfiles via ``cargo update --workspace``.

        Parameters
        ----------
        workspace_root :
            Absolute path to the Cargo workspace root.
        lockfile_manifests :
            Configured manifest paths relative to *workspace_root*. Manifests
            implied by git-tracked lockfiles are merged before the
            workspace-root ``Cargo.toml`` is prepended and de-duplicated.

        Returns
        -------
        tuple[Path, ...]
            Paths to every ``Cargo.lock`` regenerated, in manifest
            execution order.

        Raises
        ------
        LockfileRegenerationError
            If any configured manifest path is invalid (outside the
            workspace or not named ``Cargo.toml``), or — after every
            manifest has been attempted — if ``cargo update --workspace``
            failed. A lone workspace-root failure re-raises the original
            cargo error unchanged; multiple failures raise one aggregated
            error listing each failed manifest with a repair command.

        Examples
        --------
        A runner with no discovered lockfiles and successful Cargo updates
        returns the root and configured lockfile paths:

        ```python
        def runner(command, **kwargs):
            return 0, "", ""

        repository = CargoLockfileRepository(runner=runner)
        paths = repository.regenerate_lockfiles(
            Path("/workspace"),
            ("fixtures/minimal/Cargo.toml",),
        )
        [path.as_posix() for path in paths]
        # ["/workspace/Cargo.lock", "/workspace/fixtures/minimal/Cargo.lock"]
        ```
        """
        merged_manifests = merge_discovered_manifests(
            workspace_root, lockfile_manifests, runner=self.runner
        )
        return regenerate_lockfiles(
            workspace_root, merged_manifests, runner=self.runner
        )


class LockfileRepository(typ.Protocol):
    """Port for projecting and regenerating Cargo lockfiles after a bump.

    The bump domain depends on this protocol rather than on a command
    runner, keeping execution infrastructure out of the public bump options
    (issue #82).
    """

    def resolve_lockfile_paths(
        self,
        workspace_root: Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[Path, ...]:
        """Return the lockfile paths a regeneration run would touch.

        Parameters
        ----------
        workspace_root :
            Absolute path to the Cargo workspace root.
        lockfile_manifests :
            Configured manifest paths relative to *workspace_root*. The
            workspace-root ``Cargo.toml`` is always prepended and
            de-duplicated.

        Returns
        -------
        tuple[Path, ...]
            The lockfile paths that a regeneration run would touch,
            in manifest execution order.

        Raises
        ------
        LockfileRegenerationError
            If a configured manifest is outside the workspace or is not named
            ``Cargo.toml``.
        """

    def plan_lockfiles(
        self,
        workspace_root: Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> LockfilePlan:
        """Plan lockfile destinations before any manifest is rewritten.

        Parameters
        ----------
        workspace_root :
            Canonical workspace root.
        lockfile_manifests :
            Configured manifests relative to *workspace_root*.

        Returns
        -------
        LockfilePlan
            Effective workspace-root manifests and their lockfile
            destinations.

        Raises
        ------
        BumpPreflightError
            If a target is unsafe, missing, or Cargo reports an unexpected
            workspace root.
        """

    def regenerate_planned(self, plan: LockfilePlan) -> tuple[Path, ...]:
        """Regenerate the lockfiles named by a validated plan.

        Parameters
        ----------
        plan :
            Destination plan produced by :meth:`plan_lockfiles`.

        Returns
        -------
        tuple[Path, ...]
            The validated lockfile destinations.

        Raises
        ------
        LockfileRegenerationError
            After every manifest is attempted, if any ``cargo update`` failed.
        """

    def regenerate_lockfiles(
        self,
        workspace_root: Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[Path, ...]:
        """Regenerate lockfiles and return the rewritten paths.

        Parameters
        ----------
        workspace_root :
            Absolute path to the Cargo workspace root.
        lockfile_manifests :
            Configured manifest paths relative to *workspace_root*. The
            workspace-root ``Cargo.toml`` is always prepended and
            de-duplicated.

        Returns
        -------
        tuple[Path, ...]
            Paths to every ``Cargo.lock`` regenerated, in manifest
            execution order.

        Raises
        ------
        LockfileRegenerationError
            If any configured manifest path is invalid (outside the
            workspace or not named ``Cargo.toml``), or — after every
            manifest has been attempted — if ``cargo update --workspace``
            failed. A lone workspace-root failure re-raises the original
            cargo error unchanged; multiple failures raise one aggregated
            error listing each failed manifest with a repair command.
        """
