"""Find where Cargo writes a crate's packaged archive.

:class:`CargoTargetLocator` asks ``cargo metadata`` for the target directory of
a crate root through the command runner, once per root, because a crate can
carry its own ``.cargo/config.toml`` and so its own ``build.target-dir``;
reading another crate's directory could match a stale archive. It honours
``CARGO_TARGET_DIR`` and Cargo configuration, so lading never assumes
``target/``. It is a separate unit so the lookup and its cache can be tested
apart from the outcome logic that uses it.

Examples
--------
>>> locator = CargoTargetLocator(
...     lambda command, **_: (0, '{"target_directory": "/t"}', "")
... )
>>> str(locator(Path("/ws/alpha")))
'/t'
"""

import dataclasses as dc
import json
import typing as typ
from pathlib import Path

from lading.commands.publish_errors import ArchiveUnavailableError
from lading.utils.process import command_detail
from lading.utils.secret_scope import redact_active

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner

_METADATA_COMMAND = ("cargo", "metadata", "--format-version", "1", "--no-deps")


@dc.dataclass(slots=True)
class CargoTargetLocator:
    """Resolve, and remember, each crate root's Cargo target directory.

    Parameters
    ----------
    runner : CommandRunner
        Runs ``cargo metadata`` in the crate root.
    """

    runner: CommandRunner
    _cache: dict[Path, Path] = dc.field(default_factory=dict, init=False)

    def __call__(self, crate_root: Path) -> Path:
        """Return ``crate_root``'s target directory, asking Cargo once.

        Returns
        -------
        Path
            The directory Cargo writes ``package/<name>-<version>.crate`` under.
        """
        if crate_root not in self._cache:
            self._cache[crate_root] = self._ask(crate_root)
        return self._cache[crate_root]

    def _ask(self, crate_root: Path) -> Path:
        """Run ``cargo metadata`` in ``crate_root`` and read ``target_directory``."""
        # The JSON is read here, not shown: echoing it would bury the publish
        # log under the whole workspace's metadata for every crate.
        exit_code, stdout, stderr = self.runner(
            _METADATA_COMMAND, cwd=crate_root, env=None, echo_stdout=False
        )
        try:
            return Path(json.loads(stdout)["target_directory"])
        except (ValueError, KeyError, TypeError) as exc:
            # Redacted before it is cut: a sink that hid the secret later
            # would find only a fragment of it, and leave that showing.
            detail = redact_active(command_detail(stdout, stderr))
            message = (
                "Cannot find the Cargo target directory to locate the packaged "
                f"archive (cargo metadata exited {exit_code}): {detail[:200]}"
            )
            raise ArchiveUnavailableError(message) from exc
