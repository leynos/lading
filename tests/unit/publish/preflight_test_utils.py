"""Shared helpers for exercising publish preflight behaviour in tests."""

from __future__ import annotations

import typing as typ
from pathlib import Path

from lading.commands import publish

from .conftest import ORIGINAL_PREFLIGHT, make_crate, make_workspace

if typ.TYPE_CHECKING:
    import pytest

    from lading import config as config_module
    from lading.workspace import WorkspaceGraph

CallRecord = tuple[tuple[str, ...], Path | None, typ.Mapping[str, str] | None]
RecordedCommands = list[CallRecord]


def _setup_preflight_test(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    configuration: config_module.LadingConfig,
    crate_names: typ.Sequence[str] | None = None,
) -> tuple[Path, WorkspaceGraph, RecordedCommands]:
    """Execute ``publish.run`` with optional workspace crates and capture calls."""
    monkeypatch.setattr(publish, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    root = tmp_path / "workspace"
    root.mkdir()
    selected_crates = ("alpha",) if crate_names is None else tuple(crate_names)
    workspace = make_workspace(
        root, *(make_crate(root, name) for name in selected_crates)
    )
    calls: RecordedCommands = []

    def recording_invoke(
        command: typ.Sequence[str],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        calls.append((tuple(command), cwd, env))
        return 0, "", ""

    monkeypatch.setattr(publish, "_invoke", recording_invoke)
    publish.run(root, configuration, workspace)
    return root, workspace, calls


def _extract_cargo_test_call(
    calls: RecordedCommands,
) -> tuple[tuple[str, ...], Path | None]:
    """Return the captured cargo test invocation from ``calls``."""
    command, cwd, _env = next(
        entry
        for entry in calls
        if entry[0][0] == "cargo" and len(entry[0]) > 1 and entry[0][1] == "test"
    )
    return command, cwd
