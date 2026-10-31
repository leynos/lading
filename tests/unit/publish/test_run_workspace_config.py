"""Publish run workspace-root and configuration test coverage."""

import collections.abc as cabc
import contextlib
import dataclasses as dc
import re
import typing as typ
from pathlib import Path

import pytest

from lading import config as config_module
from lading.commands import publish, publish_staging
from lading.workspace import WorkspaceGraph, WorkspaceModelError
from tests.helpers.cwd import chdir_for_test

from .conftest import make_config, make_crate, make_workspace

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def test_run_normalizes_workspace_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The run helper resolves the workspace root before planning."""
    workspace = Path("workspace")
    chdir_for_test(monkeypatch, tmp_path)
    resolved = tmp_path / "workspace"
    plan_workspace = make_workspace(resolved)
    configuration = make_config()

    def fake_load(root: Path) -> WorkspaceGraph:
        """Stub workspace loader that asserts the resolved root."""
        assert root == resolved, "workspace should be loaded from the resolved root"
        return plan_workspace

    monkeypatch.setattr("lading.workspace.load_workspace", fake_load)
    monkeypatch.setattr(
        publish_staging,
        "prepare_workspace",
        lambda *_args, **_kwargs: publish_staging.PublishPreparation(
            staging_root=resolved
        ),
    )
    output = publish.run(workspace, configuration)

    assert output.splitlines()[0] == f"Publish plan for {resolved}", (
        "summary header should report the resolved workspace root"
    )


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

    assert "skip-me" in output, "active configuration exclusions should appear"


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
        """Raise ConfigurationNotLoadedError unconditionally."""
        message = "Configuration unavailable"
        raise config_module.ConfigurationNotLoadedError(message)

    def capture_load(path: Path) -> config_module.LadingConfig:
        """Record the load call and return the loaded configuration."""
        load_calls.append(path)
        return loaded_configuration

    monkeypatch.setattr(config_module, "current_configuration", raise_not_loaded)
    monkeypatch.setattr(config_module, "load_configuration", capture_load)

    output = publish.run(root)

    assert "Crates to publish" in output, "summary should list crates to publish"
    assert load_calls == [root], (
        "configuration should be loaded once from the workspace root"
    )


def _normalize_summary(message: str, root: Path) -> str:
    """Redact non-deterministic paths so snapshots are stable across runs."""
    normalized = message.replace(str(root), "<workspace-root>")
    return re.sub(
        r"^Staged workspace at: .*$",
        "Staged workspace at: <staging-root>",
        normalized,
        flags=re.MULTILINE,
    )


def test_run_formats_plan_summary(tmp_path: Path, snapshot: SnapshotAssertion) -> None:
    """``run`` returns a structured summary of the publish plan."""
    root = tmp_path.resolve()
    publishable = make_crate(root, "alpha")
    manifest_skipped = make_crate(root, "beta", publish_flag=False)
    config_skipped = make_crate(root, "gamma")
    workspace = make_workspace(root, publishable, manifest_skipped, config_skipped)
    configuration = make_config(exclude=("gamma", "missing"))

    message = publish.run(root, configuration, workspace)

    assert _normalize_summary(message, root) == snapshot, (
        "publish summary should match snapshot"
    )


def test_run_reports_no_publishable_crates(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
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

    assert _normalize_summary(message, root) == snapshot, (
        "no-publishable-crates summary should match snapshot"
    )


def _capture_stage_exclude(
    monkeypatch: pytest.MonkeyPatch, staging_root: Path
) -> dict[str, object]:
    """Replace staging with a fake that records the exclusion it receives."""
    captured: dict[str, object] = {}

    @contextlib.contextmanager
    def fake_staged_workspace(
        _plan: object,
        *,
        options: object | None = None,
        stage_exclude: cabc.Sequence[str] = (),
    ) -> cabc.Generator[publish_staging.StagedWorkspace]:
        del options
        captured["stage_exclude"] = tuple(stage_exclude)
        yield publish_staging.StagedWorkspace(
            preparation=publish_staging.PublishPreparation(staging_root=staging_root)
        )

    monkeypatch.setattr(publish_staging, "staged_workspace", fake_staged_workspace)
    return captured


def test_run_forwards_stage_exclude_from_explicit_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit configuration's exclusions take precedence over the others."""
    root = tmp_path.resolve()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    monkeypatch.setattr("lading.workspace.load_workspace", lambda _: workspace)
    explicit = dc.replace(
        make_config(),
        publish=dc.replace(make_config().publish, stage_exclude=("explicit",)),
    )
    from_options = dc.replace(
        make_config(),
        publish=dc.replace(make_config().publish, stage_exclude=("from-options",)),
    )
    active = dc.replace(
        make_config(),
        publish=dc.replace(make_config().publish, stage_exclude=("active",)),
    )
    monkeypatch.setattr(config_module, "current_configuration", lambda: active)
    options = publish.PublishOptions(configuration=from_options)
    captured = _capture_stage_exclude(monkeypatch, root)

    publish.run(root, explicit, workspace, options=options)

    assert captured["stage_exclude"] == ("explicit",), (
        "the explicit configuration must win over options.configuration and the "
        "active configuration"
    )


def test_run_forwards_stage_exclude_from_active_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``run`` forwards the active configuration's exclusions to staging."""
    root = tmp_path.resolve()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    monkeypatch.setattr("lading.workspace.load_workspace", lambda _: workspace)
    active = dc.replace(
        make_config(),
        publish=dc.replace(make_config().publish, stage_exclude=("venv", "target")),
    )
    monkeypatch.setattr(config_module, "current_configuration", lambda: active)
    captured = _capture_stage_exclude(monkeypatch, root)

    publish.run(root)

    assert captured["stage_exclude"] == ("venv", "target"), (
        "the active configuration's stage_exclude must reach staging"
    )


def test_run_surfaces_missing_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``run`` converts missing workspace roots into workspace model errors."""
    configuration = make_config()

    def raise_missing(_: Path) -> WorkspaceGraph:
        """Raise FileNotFoundError unconditionally."""
        message = "workspace missing"
        raise FileNotFoundError(message)

    monkeypatch.setattr("lading.workspace.load_workspace", raise_missing)

    with pytest.raises(WorkspaceModelError) as excinfo:
        publish.run(tmp_path, configuration)

    message = str(excinfo.value)
    assert "Workspace root not found" in message, (
        "missing workspace error should be explicit"
    )
    assert str(tmp_path.resolve()) in message, (
        "error should name the resolved workspace root"
    )


def test_run_surfaces_configuration_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``run`` propagates configuration errors encountered while loading."""

    def raise_not_loaded() -> config_module.LadingConfig:
        """Raise ConfigurationNotLoadedError unconditionally."""
        message = "Configuration inactive"
        raise config_module.ConfigurationNotLoadedError(message)

    def raise_config_error(_: Path) -> config_module.LadingConfig:
        """Raise ConfigurationError unconditionally."""
        message = "invalid configuration"
        raise config_module.ConfigurationError(message)

    monkeypatch.setattr(config_module, "current_configuration", raise_not_loaded)
    monkeypatch.setattr(config_module, "load_configuration", raise_config_error)

    with pytest.raises(config_module.ConfigurationError) as excinfo:
        publish.run(tmp_path)

    assert str(excinfo.value) == "invalid configuration", (
        "configuration error should propagate unchanged"
    )
