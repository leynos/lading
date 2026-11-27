"""Shared fixtures for the lockfile planning unit tests.

The runner and workspace writers are used by both the planning and validation
suites, so they live here rather than being copied between them.
"""

import collections.abc as cabc
import dataclasses as dc
import json
import typing as typ
from pathlib import Path

__all__ = ["RecordingRunner", "write_package", "write_workspace"]


@dc.dataclass(slots=True)
class RecordingRunner:
    """Record Git and Cargo invocations and answer from fixed payloads."""

    metadata_root: Path
    roots_by_manifest: dict[Path, Path] = dc.field(default_factory=dict)
    metadata_payload: dict[str, typ.Any] | None = None
    metadata_failure: tuple[int, str, str] | None = None
    metadata_failure_manifest: Path | None = None
    calls: list[tuple[tuple[str, ...], Path | None]] = dc.field(default_factory=list)

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        """Record the command and return a canned response."""
        del env, echo_stdout
        self.calls.append((tuple(command), cwd))
        if command[0] == "git":
            return 0, "", ""
        if command[0] == "cargo" and command[1] == "metadata":
            return self._metadata_response(command)
        if command[0] == "cargo" and command[1] == "update":
            return 0, "", ""
        return 1, "", f"unexpected command: {command}"

    def _metadata_response(
        self,
        command: cabc.Sequence[str],
    ) -> tuple[int, str, str]:
        """Return a failure, an override payload, or a computed payload."""
        if self.metadata_failure is not None and self._failure_applies(command):
            return self.metadata_failure
        if self.metadata_payload is not None:
            return 0, json.dumps(self.metadata_payload), ""
        payload = {"workspace_root": str(self._metadata_root_for(command))}
        return 0, json.dumps(payload), ""

    def _failure_applies(self, command: cabc.Sequence[str]) -> bool:
        """Return whether the configured failure targets this invocation."""
        if self.metadata_failure_manifest is None:
            return True
        if "--manifest-path" not in command:
            return False
        manifest = Path(command[list(command).index("--manifest-path") + 1])
        return manifest == self.metadata_failure_manifest

    def _metadata_root_for(self, command: cabc.Sequence[str]) -> Path:
        """Return the workspace root this invocation should report."""
        if "--manifest-path" in command:
            manifest = Path(command[list(command).index("--manifest-path") + 1])
            return self.roots_by_manifest.get(manifest, manifest.parent)
        return self.metadata_root

    def cargo_metadata_calls(self) -> list[tuple[str, ...]]:
        """Return the recorded ``cargo metadata`` argument tuples."""
        return [
            command
            for command, _cwd in self.calls
            if command[:2] == ("cargo", "metadata")
        ]

    def cargo_update_calls(self) -> list[tuple[str, ...]]:
        """Return the recorded ``cargo update`` argument tuples."""
        return [
            command
            for command, _cwd in self.calls
            if command[:2] == ("cargo", "update")
        ]


def write_workspace(root: Path, *, members: cabc.Sequence[str] = ()) -> Path:
    """Write a root manifest and return it."""
    members_literal = ", ".join(f'"{member}"' for member in members)
    manifest = root / "Cargo.toml"
    manifest.write_text(
        f"[workspace]\nmembers = [{members_literal}]\n\n"
        '[workspace.package]\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    return manifest


def write_package(root: Path, relative: str, *, standalone: bool) -> Path:
    """Write a package manifest and return it."""
    manifest = root / relative
    manifest.parent.mkdir(parents=True, exist_ok=True)
    suffix = "\n[workspace]\n" if standalone else ""
    manifest.write_text(
        f'[package]\nname = "nested"\nversion = "0.1.0"\n{suffix}',
        encoding="utf-8",
    )
    return manifest
