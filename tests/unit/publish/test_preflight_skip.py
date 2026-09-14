"""Behaviour of the publish pre-flight when the build checks are skipped."""

from __future__ import annotations

import collections.abc as cabc
import logging
import typing as typ
from pathlib import Path

from lading.commands import publish, publish_preflight
from lading.commands.publish_skip import SkipPreflightDecision

from .conftest import (
    ORIGINAL_PREFLIGHT,
    make_config,
    make_crate,
    make_preflight_config,
    make_workspace,
)

if typ.TYPE_CHECKING:
    import pytest

    from lading.runtime import CommandRunner

AUX_BUILD_COMMAND = ("cargo", "build", "--package", "lint")


def _recording_runner(
    calls: list[tuple[str, ...]],
) -> CommandRunner:
    """Return a runner that records each command and reports success."""

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del cwd, env
        calls.append(tuple(command))
        return 0, "", ""

    return runner


def _cargo_subcommands(calls: cabc.Iterable[tuple[str, ...]]) -> set[str]:
    """Return the cargo subcommands present in ``calls``."""
    return {
        command[1] for command in calls if command[0] == "cargo" and len(command) > 1
    }


def _run_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    configured_skip: bool,
    override: SkipPreflightDecision | None,
) -> list[tuple[str, ...]]:
    """Run the real pre-flight over a recording runner and return its commands."""
    monkeypatch.setattr(publish_preflight, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    root = tmp_path / "workspace"
    root.mkdir()
    calls: list[tuple[str, ...]] = []
    configuration = make_config(
        preflight=make_preflight_config(
            skip=configured_skip, aux_build=(AUX_BUILD_COMMAND,)
        )
    )

    publish_preflight._run_preflight_checks(
        root,
        publish_preflight.PreflightRequest(
            allow_dirty=False,
            configuration=configuration,
            runner=_recording_runner(calls),
            skip=override,
        ),
    )
    return calls


def test_configured_skip_suppresses_the_build_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``[preflight] skip`` stops the auxiliary build, cargo check and cargo test.

    This is the defect the setting exists for: a caller whose job already ran
    the suite must not pay for it a second time.
    """
    calls = _run_preflight(tmp_path, monkeypatch, configured_skip=True, override=None)

    assert _cargo_subcommands(calls) == set(), (
        f"no cargo command should run when the pre-flight is skipped: {calls}"
    )
    assert AUX_BUILD_COMMAND not in calls, (
        "auxiliary builds exist to support the skipped cargo checks"
    )


def test_skip_still_verifies_the_tree_and_the_lockfiles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The two cheap publication guards survive the skip.

    Skipping is about not repeating the caller's build; a dirty tree or a
    stale ``Cargo.lock`` would still produce a wrong publication.
    """
    calls = _run_preflight(tmp_path, monkeypatch, configured_skip=True, override=None)

    assert ("git", "status", "--porcelain") in calls, (
        "the working-tree guard must still run"
    )
    assert any(command[:2] == ("git", "ls-files") for command in calls), (
        "the lockfile freshness guard must still run"
    )


def test_skip_logs_the_source_of_the_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The skip is announced with the input that requested it.

    A publish log that omitted this would read as though the checks passed.
    """
    override = SkipPreflightDecision(
        skip=True, source="the --skip-preflight command-line flag"
    )

    with caplog.at_level(logging.INFO, logger=publish_preflight.LOGGER.name):
        _run_preflight(tmp_path, monkeypatch, configured_skip=False, override=override)

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "Skipping" in message
        and "the --skip-preflight command-line flag" in message
        and "cargo test" in message
        for message in messages
    ), f"expected a skip line naming the flag: {messages}"


def test_default_configuration_runs_the_build_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without a skip request the pre-flight behaves exactly as before."""
    calls = _run_preflight(tmp_path, monkeypatch, configured_skip=False, override=None)

    assert _cargo_subcommands(calls) >= {"check", "test"}
    assert AUX_BUILD_COMMAND in calls


def test_override_reinstates_the_build_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--no-skip-preflight`` beats a workspace that configures the skip."""
    override = SkipPreflightDecision(
        skip=False, source="the --skip-preflight command-line flag"
    )

    calls = _run_preflight(
        tmp_path, monkeypatch, configured_skip=True, override=override
    )

    assert _cargo_subcommands(calls) >= {"check", "test"}
    assert AUX_BUILD_COMMAND in calls


def test_publish_run_forwards_the_skip_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``PublishOptions.skip_preflight`` reaches the pre-flight from ``run``.

    The packaging commands still run, so the skip removes only the duplicated
    verification and not the work the publish step exists to do.
    """
    monkeypatch.setattr(publish_preflight, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(publish, "_invoke", _recording_runner(calls))

    publish.run(
        root,
        make_config(),
        workspace,
        options=publish.PublishOptions(
            skip_preflight=SkipPreflightDecision(skip=True, source="the caller")
        ),
    )

    assert _cargo_subcommands(calls) == {"package", "publish"}, (
        f"only the packaging commands should run: {calls}"
    )
