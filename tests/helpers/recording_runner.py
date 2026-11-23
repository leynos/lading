"""Recording command runner for in-process bump preflight tests.

The runner journals every invocation, serves ``cargo metadata`` and
``cargo update`` from a fixture, and reports Git discovery as an empty tracked
lockfile set. Tests can inspect the journal to prove that a rejected plan made
no metadata call, no update call, or no subprocess call at all.
"""

import collections.abc as cabc
import dataclasses as dc
import json
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    import pytest


@dc.dataclass(frozen=True, slots=True)
class RecordedInvocation:
    """One command invocation observed by :class:`RecordingCommandRunner`.

    Attributes
    ----------
    command : tuple[str, ...]
        Complete argv.
    cwd : Path | None
        Working directory, when one was supplied.
    env : cabc.Mapping[str, str] | None
        Environment override, when one was supplied.
    echo_stdout : bool
        Whether the caller asked for stdout mirroring.
    """

    command: tuple[str, ...]
    cwd: Path | None
    env: cabc.Mapping[str, str] | None
    echo_stdout: bool


@dc.dataclass(slots=True)
class RecordingCommandRunner:
    """Serve fixture responses while recording every invocation.

    Attributes
    ----------
    metadata : dict[str, typ.Any] | None
        Payload returned for a plain ``cargo metadata`` invocation.
    metadata_by_manifest : cabc.Callable[[Path], dict[str, typ.Any]] | None
        Payload builder for ``cargo metadata --manifest-path`` queries. It
        receives the queried manifest.
    update_result : tuple[int, str, str]
        Exit code, stdout, and stderr for ``cargo update``.
    git_result : tuple[int, str, str]
        Exit code, stdout, and stderr for ``git ls-files``.
    calls : list[RecordedInvocation]
        Journal of every invocation.
    """

    metadata: dict[str, typ.Any] | None = None
    metadata_by_manifest: cabc.Callable[[Path], dict[str, typ.Any]] | None = None
    update_result: tuple[int, str, str] = (0, "", "")
    git_result: tuple[int, str, str] = (0, "", "")
    calls: list[RecordedInvocation] = dc.field(default_factory=list)

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        """Record ``command`` and return the configured fixture response."""
        argv = tuple(command)
        self.calls.append(
            RecordedInvocation(
                command=argv,
                cwd=cwd,
                env=env,
                echo_stdout=echo_stdout,
            )
        )
        if argv[:2] == ("git", "ls-files"):
            return self.git_result
        if argv[:2] == ("cargo", "metadata"):
            return self._metadata_response(argv)
        if argv[:2] == ("cargo", "update"):
            return self.update_result
        return 1, "", f"unexpected command: {argv}"

    def _metadata_response(
        self,
        argv: tuple[str, ...],
    ) -> tuple[int, str, str]:
        """Return the metadata payload for ``argv``."""
        if "--manifest-path" in argv and self.metadata_by_manifest is not None:
            manifest = Path(argv[argv.index("--manifest-path") + 1])
            return 0, json.dumps(self.metadata_by_manifest(manifest)), ""
        if self.metadata is None:
            return 1, "", "unexpected cargo metadata invocation"
        return 0, json.dumps(self.metadata), ""

    def cargo_calls(self) -> list[tuple[str, ...]]:
        """Return the recorded Cargo argument tuples."""
        return [
            invocation.command
            for invocation in self.calls
            if invocation.command[:1] == ("cargo",)
        ]

    def metadata_calls(self) -> list[tuple[str, ...]]:
        """Return the recorded ``cargo metadata`` argument tuples."""
        return [
            command
            for command in self.cargo_calls()
            if command[:2]
            == (
                "cargo",
                "metadata",
            )
        ]

    def update_calls(self) -> list[tuple[str, ...]]:
        """Return the recorded ``cargo update`` argument tuples."""
        return [
            command
            for command in self.cargo_calls()
            if command[:2] == ("cargo", "update")
        ]

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Route the CLI's runner selection through this instance.

        Parameters
        ----------
        monkeypatch : pytest.MonkeyPatch
            Fixture used to replace the CLI seam for the current test.
        """
        from lading import cli

        monkeypatch.setattr(cli, "_select_runner", lambda: self)
