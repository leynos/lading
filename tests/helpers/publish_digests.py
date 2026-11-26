"""In-process doubles for ``lading publish --expect-digests`` tests.

The publish pipeline reaches the outside world through two seams: the command
runner, which spawns ``cargo``, and the published-archive fetcher, which reads
crates.io. :class:`FakeCargo` and :class:`FakeRegistry` stand in for them, so
a test can say exactly which bytes ``cargo package`` and ``cargo publish``
leave in the staged target directory and which archives crates.io already
holds. Nothing here spawns a process or opens a socket.

The crates all live at version ``0.1.0`` directly under the workspace root.
:class:`PublishRig` assembles the doubles and a three-crate workspace
(``alpha``, ``beta``, ``gamma``, each depending on the one before) and runs the
publish command against them in process.
"""

import collections.abc as cabc
import dataclasses as dc
import hashlib
import json
import typing as typ
from pathlib import Path

from lading import config as config_module
from lading.commands import publish, publish_preflight
from lading.workspace import WorkspaceCrate, WorkspaceDependency, WorkspaceGraph

if typ.TYPE_CHECKING:
    import pytest

PLAIN_ARCHIVE = b"archive bytes as recorded by the credential-free job"
OTHER_ARCHIVE = b"archive bytes that someone changed afterwards"


def digest(payload: bytes) -> str:
    """Return the SHA-256 hex digest of ``payload``."""
    return hashlib.sha256(payload).hexdigest()


def digest_text(entries: cabc.Mapping[str, bytes]) -> str:
    """Return ``sha256sum`` output for ``{crate name: archive bytes}``."""
    return "".join(
        f"{digest(payload)}  {name}-0.1.0.crate\n" for name, payload in entries.items()
    )


@dc.dataclass(slots=True)
class FakeRegistry:
    """Stand-in for the crates.io archive download.

    Attributes
    ----------
    published : dict[str, bytes]
        The archive crates.io holds for each crate name at version ``0.1.0``.
    requests : list[tuple[str, str]]
        The ``(name, version)`` pairs asked for, in order.
    """

    published: dict[str, bytes] = dc.field(default_factory=dict)
    requests: list[tuple[str, str]] = dc.field(default_factory=list)

    def __call__(self, name: str, version: str) -> str | None:
        """Return the published archive's digest, or ``None`` if absent."""
        self.requests.append((name, version))
        payload = self.published.get(name)
        return None if payload is None else digest(payload)


@dc.dataclass(slots=True)
class FakeCargo:
    """Stand-in for ``cargo`` that writes archives where Cargo would.

    ``cargo package`` leaves ``packaged[name]`` in ``<target>/package``, and
    ``cargo publish`` (live or not) repackages and leaves ``uploaded[name]``,
    falling back to the packaged bytes. ``cargo metadata`` reports the target
    directory, which tests move away from ``target/`` to prove it is honoured.

    Attributes
    ----------
    packaged : dict[str, bytes]
        What ``cargo package`` writes, by crate name.
    uploaded : dict[str, bytes]
        What ``cargo publish`` writes when it differs from ``packaged``.
    target_name : str
        The target directory's name, beside the staged crates.
    crate_targets : dict[str, str]
        Per-crate target directory names that override ``target_name``, as a
        crate's own ``.cargo/config.toml`` would.
    stray_archives : dict[str, dict[str, bytes]]
        Extra ``{file name: bytes}`` archives ``cargo package`` leaves in a
        crate's target directory, standing in for stale files from elsewhere.
    failing : dict[tuple[str, str], tuple[int, str]]
        ``(subcommand, crate name)`` pairs that exit non-zero, with the exit
        code and stderr they produce.
    calls : list[tuple[tuple[str, ...], str]]
        Every command run, with the name of the directory it ran in.
    """

    packaged: dict[str, bytes] = dc.field(default_factory=dict)
    uploaded: dict[str, bytes] = dc.field(default_factory=dict)
    target_name: str = "target"
    crate_targets: dict[str, str] = dc.field(default_factory=dict)
    stray_archives: dict[str, dict[str, bytes]] = dc.field(default_factory=dict)
    failing: dict[tuple[str, str], tuple[int, str]] = dc.field(default_factory=dict)
    calls: list[tuple[tuple[str, ...], str]] = dc.field(default_factory=list)

    def subcommands(self, subcommand: str) -> list[tuple[str, ...]]:
        """Return every recorded ``cargo <subcommand>`` command line."""
        return [
            command
            for command, _directory in self.calls
            if command[:2] == ("cargo", subcommand)
        ]

    def crates_run(self, subcommand: str) -> list[str]:
        """Return the crates ``cargo <subcommand>`` ran for, in order."""
        return [
            directory
            for command, directory in self.calls
            if command[:2] == ("cargo", subcommand)
        ]

    def _target(self, directory: Path) -> Path:
        """Return the target directory Cargo reports for the crate in ``directory``."""
        name = self.crate_targets.get(directory.name, self.target_name)
        return directory.parent / name

    def _write_strays(self, directory: Path) -> None:
        """Leave the crate's configured stray archives beside its own."""
        package_dir = self._target(directory) / "package"
        package_dir.mkdir(parents=True, exist_ok=True)
        for filename, payload in self.stray_archives.get(directory.name, {}).items():
            (package_dir / filename).write_bytes(payload)

    def _write(self, directory: Path, payload: bytes | None) -> None:
        """Write ``payload`` as the crate's archive under the target directory."""
        if payload is None:
            return
        package_dir = self._target(directory) / "package"
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / f"{directory.name}-0.1.0.crate").write_bytes(payload)

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        """Run the fake cargo command in ``cwd``."""
        del env, echo_stdout
        if cwd is None:
            message = "FakeCargo needs the staged crate directory as cwd"
            raise AssertionError(message)
        command_tuple = tuple(command)
        self.calls.append((command_tuple, cwd.name))
        subcommand = command_tuple[1]
        if (subcommand, cwd.name) in self.failing:
            exit_code, stderr = self.failing[subcommand, cwd.name]
            return exit_code, "", stderr
        match subcommand:
            case "metadata":
                target = str(self._target(cwd))
                return 0, json.dumps({"target_directory": target}), ""
            case "package":
                self._write(cwd, self.packaged.get(cwd.name))
                self._write_strays(cwd)
            case "publish":
                self._write(
                    cwd, self.uploaded.get(cwd.name, self.packaged.get(cwd.name))
                )
        return 0, "", ""


def write_digests(path: Path, entries: cabc.Mapping[str, bytes]) -> Path:
    """Write the ``sha256sum`` file for ``entries`` and return its path."""
    path.write_text(digest_text(entries), encoding="utf-8")
    return path


CRATE_NAMES = ("alpha", "beta", "gamma")


def _crate(root: Path, name: str, depends_on: str | None) -> WorkspaceCrate:
    """Create ``name``'s directory and manifest and describe it."""
    crate_root = root / name
    crate_root.mkdir(parents=True, exist_ok=True)
    manifest = crate_root / "Cargo.toml"
    manifest.write_text(
        f'[package]\nname = "{name}"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    dependencies = ()
    if depends_on is not None:
        dependencies = (
            WorkspaceDependency(
                package_id=f"{depends_on}-id",
                name=depends_on,
                manifest_name=depends_on,
                kind=None,
            ),
        )
    return WorkspaceCrate(
        name=name,
        version="0.1.0",
        manifest_path=manifest,
        root_path=crate_root,
        publish=True,
        readme_is_workspace=False,
        dependencies=dependencies,
    )


@dc.dataclass(slots=True)
class PublishRig:
    """A three-crate workspace and the doubles a publish run needs.

    Attributes
    ----------
    root : Path
        The workspace root.
    build_directory : Path
        Where staging happens; it does not exist until a run stages.
    cargo : FakeCargo
        The command runner, which also records every command.
    registry : FakeRegistry
        The published-archive fetcher, which also records every request.
    """

    root: Path
    build_directory: Path
    cargo: FakeCargo
    registry: FakeRegistry

    @classmethod
    def create(cls, tmp_path: Path) -> PublishRig:
        """Build a rig whose crates all package to :data:`PLAIN_ARCHIVE`."""
        root = tmp_path / "workspace"
        cargo = FakeCargo(packaged=dict.fromkeys(CRATE_NAMES, PLAIN_ARCHIVE))
        rig = cls(root, tmp_path / "build", cargo, FakeRegistry())
        rig.workspace()  # Create the crate directories now, as staging copies them.
        return rig

    def workspace(self) -> WorkspaceGraph:
        """Return the workspace graph, creating its crate directories."""
        crates = tuple(
            _crate(self.root, name, previous)
            for previous, name in zip((None, *CRATE_NAMES), CRATE_NAMES, strict=False)
        )
        return WorkspaceGraph(workspace_root=self.root, crates=crates)

    def digests_file(self, entries: cabc.Mapping[str, bytes] | None = None) -> Path:
        """Write ``digests.txt`` under the workspace and return its path.

        By default every crate is expected to have the plain archive.

        Returns
        -------
        Path
            The file just written.
        """
        recorded = (
            dict.fromkeys(CRATE_NAMES, PLAIN_ARCHIVE) if entries is None else entries
        )
        return write_digests(self.root / "digests.txt", recorded)

    def run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        preflight: cabc.Callable[..., None] | None = None,
        **overrides: object,
    ) -> str:
        """Run ``lading publish`` in process and return its summary.

        ``overrides`` are :class:`~lading.commands.publish.PublishOptions`
        fields; the fake cargo, fake registry and build directory are wired in
        unless overridden. The pre-flight is replaced by a no-op, because it is
        not what these runs exercise unless ``preflight`` supplies a recorder.

        Returns
        -------
        str
            The publish summary.
        """
        monkeypatch.setattr(
            publish_preflight,
            "_run_preflight_checks",
            preflight if preflight is not None else lambda *_a, **_k: None,
        )
        wired = publish.PublishOptions(
            build_directory=self.build_directory,
            command_runner=self.cargo,
            published_digest_fetcher=self.registry,
        )
        options = dc.replace(wired, **overrides)
        return publish.run(
            self.root,
            config_module.LadingConfig(
                publish=config_module.PublishConfig(strip_patches="all")
            ),
            self.workspace(),
            options=options,
        )
