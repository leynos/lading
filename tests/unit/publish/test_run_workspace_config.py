"""Publish run workspace-root and configuration test coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from lading import config as config_module
from lading.commands import publish
from lading.workspace import WorkspaceGraph, WorkspaceModelError

from .conftest import make_config, make_crate, make_workspace


def test_run_normalises_workspace_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The run helper resolves the workspace root before planning."""
    workspace = Path("workspace")
    monkeypatch.chdir(tmp_path)
    resolved = tmp_path / "workspace"
    plan_workspace = make_workspace(resolved)
    configuration = make_config()

    def fake_load(root: Path) -> WorkspaceGraph:
        assert root == resolved
        return plan_workspace

    monkeypatch.setattr("lading.workspace.load_workspace", fake_load)
    monkeypatch.setattr(
        publish,
        "prepare_workspace",
        lambda *_args, **_kwargs: publish.PublishPreparation(
            staging_root=resolved, copied_readmes=()
        ),
    )
    output = publish.run(workspace, configuration)

    assert output.splitlines()[0] == f"Publish plan for {resolved}"


def test_run_uses_active_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``run`` falls back to :func:`current_configuration` when needed."""
    configuration = make_config(exclude=("skip-me",))
    monkeypatch.setattr(config_module, "current_configuration", lambda: configuration)
    root = tmp_path.resolve()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    monkeypatch.setattr("lading.workspace.load_workspace", lambda _: workspace)

    output = publish.run(tmp_path)

    assert "skip-me" in output


def test_run_loads_configuration_when_inactive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``run`` loads configuration from disk if no active configuration exists."""
    root = tmp_path.resolve()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    monkeypatch.setattr("lading.workspace.load_workspace", lambda _: workspace)
    loaded_configuration = make_config()
    load_calls: list[Path] = []

    def raise_not_loaded() -> config_module.LadingConfig:
        message = "Configuration unavailable"
        raise config_module.ConfigurationNotLoadedError(message)

    def capture_load(path: Path) -> config_module.LadingConfig:
        load_calls.append(path)
        return loaded_configuration

    monkeypatch.setattr(config_module, "current_configuration", raise_not_loaded)
    monkeypatch.setattr(config_module, "load_configuration", capture_load)

    output = publish.run(root)

    assert "Crates to publish" in output
    assert load_calls == [root]


def test_run_formats_plan_summary(tmp_path: Path) -> None:
    """``run`` returns a structured summary of the publish plan."""
    root = tmp_path.resolve()
    publishable = make_crate(root, "alpha")
    manifest_skipped = make_crate(root, "beta", publish_flag=False)
    config_skipped = make_crate(root, "gamma")
    workspace = make_workspace(root, publishable, manifest_skipped, config_skipped)
    configuration = make_config(exclude=("gamma", "missing"))

    message = publish.run(root, configuration, workspace)

    lines = message.splitlines()
    assert lines[0] == f"Publish plan for {root}"
    assert "Strip patch strategy: all" in lines[1]
    assert "- alpha @ 0.1.0" in lines
    assert "Skipped (publish = false):" in lines
    assert "- beta" in lines
    assert "Skipped via publish.exclude:" in lines
    assert "- gamma" in lines
    assert "Configured exclusions not found in workspace:" in lines
    assert "- missing" in lines


def test_run_reports_no_publishable_crates(tmp_path: Path) -> None:
    """``run`` highlights when no crates are eligible for publication."""
    root = tmp_path.resolve()
    manifest_skipped = make_crate(root, "alpha", publish_flag=False)
    config_skipped_first = make_crate(root, "beta")
    config_skipped_second = make_crate(root, "gamma")
    workspace = make_workspace(
        root, manifest_skipped, config_skipped_first, config_skipped_second
    )
    configuration = make_config(exclude=("beta", "gamma"))

    message = publish.run(root, configuration, workspace)

    lines = message.splitlines()
    assert "Crates to publish: none" in lines
    assert "Skipped (publish = false):" in lines
    assert "- alpha" in lines
    assert "Skipped via publish.exclude:" in lines
    assert "- beta" in lines
    assert "- gamma" in lines


def test_run_surfaces_missing_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``run`` converts missing workspace roots into workspace model errors."""
    configuration = make_config()

    def raise_missing(_: Path) -> WorkspaceGraph:
        message = "workspace missing"
        raise FileNotFoundError(message)

    monkeypatch.setattr("lading.workspace.load_workspace", raise_missing)

    with pytest.raises(WorkspaceModelError) as excinfo:
        publish.run(tmp_path, configuration)

    message = str(excinfo.value)
    assert "Workspace root not found" in message
    assert str(tmp_path.resolve()) in message


def test_run_surfaces_configuration_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``run`` propagates configuration errors encountered while loading."""

    def raise_not_loaded() -> config_module.LadingConfig:
        message = "Configuration inactive"
        raise config_module.ConfigurationNotLoadedError(message)

    def raise_config_error(_: Path) -> config_module.LadingConfig:
        message = "invalid configuration"
        raise config_module.ConfigurationError(message)

    monkeypatch.setattr(config_module, "current_configuration", raise_not_loaded)
    monkeypatch.setattr(config_module, "load_configuration", raise_config_error)

    with pytest.raises(config_module.ConfigurationError) as excinfo:
        publish.run(tmp_path)

    assert str(excinfo.value) == "invalid configuration"
