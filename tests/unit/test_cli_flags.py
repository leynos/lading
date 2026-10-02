"""Unit tests for ``lading.cli`` tri-state flag forwarding.

Covers the nullable flags the CLI resolves and forwards to ``bump`` and
``publish``: ``--dry-run``, ``--allow-unpublished-workspace-deps``,
``--sccache-stats``/``--sccache-stats-json``, and ``--rebuild-lockfiles``.
"""

import collections.abc as cabc
import dataclasses as dc
import logging
import typing as typ
from pathlib import Path

import pytest

from lading import cli
from lading.commands import bump as bump_command
from lading.commands import bump_lockfiles
from lading.commands import publish as publish_command
from tests.helpers.cli_workspace import make_workspace

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


@pytest.mark.usefixtures("minimal_config")
def test_bump_cli_accepts_dry_run_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The CLI passes ``dry_run=True`` when the flag is provided."""
    workspace_graph = make_workspace(tmp_path.resolve())
    captured_kwargs: dict[str, typ.Any] = {}

    def fake_run(*args: object, **kwargs: object) -> str:
        captured_kwargs.update(kwargs)
        return "preview"

    monkeypatch.setattr(bump_command, "run", fake_run)
    monkeypatch.setattr(cli, "load_workspace", lambda _: workspace_graph)

    exit_code = cli.main([
        "--workspace-root",
        str(tmp_path),
        "bump",
        "1.2.3",
        "--dry-run",
    ])

    assert exit_code == 0, f"a dry-run bump must exit 0, got {exit_code}"
    options = captured_kwargs["options"]
    assert isinstance(options, bump_command.BumpOptions), (
        "bump.run must receive a BumpOptions instance"
    )
    assert options.dry_run is True, "the --dry-run flag must be forwarded as True"
    repository = options.lockfile_repository
    assert isinstance(repository, bump_lockfiles.CargoLockfileRepository), (
        "the options must carry the cargo lockfile repository"
    )
    assert repository.runner is cli.subprocess_runner, (
        "the repository must use the CLI subprocess runner"
    )


def test_publish_cli_logs_dry_run_default_flag_resolution(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Default dry-run resolution emits an operator-facing INFO log."""
    caplog.set_level(logging.INFO, logger="lading.cli")

    resolved = cli._resolve_allow_unpublished_workspace_deps(
        live=False,
        allow_unpublished_workspace_deps=None,
    )

    assert resolved is True, "an omitted flag should default to allowed in dry runs"
    assert (
        "Defaulting to allow unpublished workspace dependencies during dry-run publish"
    ) in caplog.messages, "the default resolution should be logged for the operator"


@pytest.mark.parametrize(
    ("flag", "live", "expected"),
    [
        pytest.param(None, True, False, id="none-live"),
        pytest.param(None, False, True, id="none-dry-run"),
        pytest.param(True, True, True, id="true-live"),
        pytest.param(True, False, True, id="true-dry-run"),
        pytest.param(False, True, False, id="false-live"),
        pytest.param(False, False, False, id="false-dry-run"),
    ],
)
def test_resolve_allow_unpublished_workspace_deps_matrix(
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
    *,
    flag: bool | None,
    live: bool,
    expected: bool,
) -> None:
    """Each input combination resolves correctly and logs exactly once at DEBUG.

    The resolution reason is verified through the snapshotted DEBUG message.
    """
    caplog.set_level(logging.DEBUG, logger="lading.cli")

    resolved = cli._resolve_allow_unpublished_workspace_deps(
        live=live,
        allow_unpublished_workspace_deps=flag,
    )

    assert resolved is expected, (
        f"flag={flag!r} live={live!r}: expected {expected!r}, got {resolved!r}"
    )
    debug_records = [
        record
        for record in caplog.records
        if record.levelno == logging.DEBUG and record.name == "lading.cli"
    ]
    assert len(debug_records) == 1, (
        f"flag={flag!r} live={live!r}: expected one DEBUG record, "
        f"got {len(debug_records)}"
    )
    assert debug_records[0].getMessage() == snapshot, (
        "the resolution reason should match the snapshot"
    )


@pytest.mark.usefixtures("minimal_config")
@pytest.mark.parametrize(
    ("extra_args", "expected"),
    [
        pytest.param((), True, id="default"),
        pytest.param(("--allow-unpublished-workspace-deps",), True, id="enabled"),
        pytest.param(("--no-allow-unpublished-workspace-deps",), False, id="disabled"),
        pytest.param(("--live",), False, id="live-default"),
        pytest.param(
            ("--live", "--allow-unpublished-workspace-deps"), True, id="live-enabled"
        ),
        pytest.param(
            ("--live", "--no-allow-unpublished-workspace-deps"),
            False,
            id="live-disabled",
        ),
    ],
)
def test_publish_cli_passes_unpublished_workspace_deps_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    extra_args: tuple[str, ...],
    expected: object,
) -> None:
    """The CLI resolves omitted, enabled, and disabled flag states."""
    workspace_graph = make_workspace(tmp_path.resolve())
    captured_options: dict[str, publish_command.PublishOptions] = {}

    def fake_run(
        workspace_root: Path,
        configuration: object,
        workspace_model: object,
        *,
        options: publish_command.PublishOptions | None = None,
    ) -> str:
        del workspace_root, configuration, workspace_model
        assert options is not None, "publish.run must receive options"
        captured_options["options"] = options
        return "publish"

    monkeypatch.setattr(publish_command, "run", fake_run)
    monkeypatch.setattr(cli, "load_workspace", lambda _: workspace_graph)

    exit_code = cli.main([
        "--workspace-root",
        str(tmp_path),
        "publish",
        *extra_args,
    ])

    assert exit_code == 0, f"publish should succeed, got exit code {exit_code}"
    assert captured_options["options"].allow_unpublished_workspace_deps is expected, (
        f"the flag should resolve to {expected!r}"
    )


@dc.dataclass(frozen=True, slots=True)
class _SccacheFlagCase:
    """One CLI invocation and the options it must produce."""

    extra_args: tuple[str, ...]
    env: dict[str, str]
    expected_stats: bool
    expected_json: Path | None


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _SccacheFlagCase((), {}, expected_stats=False, expected_json=None),
            id="default-off",
        ),
        pytest.param(
            _SccacheFlagCase(
                ("--sccache-stats",), {}, expected_stats=True, expected_json=None
            ),
            id="flag",
        ),
        pytest.param(
            _SccacheFlagCase(
                ("--sccache-stats-json", "out/stats.json"),
                {},
                expected_stats=False,
                expected_json=Path("out/stats.json"),
            ),
            id="json-flag-forwarded-raw",
        ),
        pytest.param(
            _SccacheFlagCase(
                (),
                {"LADING_SCCACHE_STATS": "1"},
                expected_stats=True,
                expected_json=None,
            ),
            id="env-flag",
        ),
        pytest.param(
            _SccacheFlagCase(
                (),
                {"LADING_SCCACHE_STATS_JSON": "out/stats.json"},
                expected_stats=False,
                expected_json=Path("out/stats.json"),
            ),
            id="env-json",
        ),
        pytest.param(
            _SccacheFlagCase(
                ("--no-sccache-stats",),
                {"LADING_SCCACHE_STATS": "1"},
                expected_stats=False,
                expected_json=None,
            ),
            id="flag-overrides-env",
        ),
    ],
)
def test_publish_cli_passes_sccache_flags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: _SccacheFlagCase,
) -> None:
    """The sccache flags and their environment defaults reach PublishOptions.

    The flags are forwarded unresolved: a report path implying the
    measurement is the publish command's decision (``create_session``), so
    library callers and the CLI behave alike.
    """
    for name in ("LADING_SCCACHE_STATS", "LADING_SCCACHE_STATS_JSON"):
        monkeypatch.delenv(name, raising=False)
    for name, value in case.env.items():
        monkeypatch.setenv(name, value)
    workspace_graph = make_workspace(tmp_path.resolve())
    captured_options: dict[str, publish_command.PublishOptions] = {}

    def fake_run(
        workspace_root: Path,
        configuration: object,
        workspace_model: object,
        *,
        options: publish_command.PublishOptions | None = None,
    ) -> str:
        del workspace_root, configuration, workspace_model
        assert options is not None, "publish.run must receive options"
        captured_options["options"] = options
        return "publish"

    monkeypatch.setattr(publish_command, "run", fake_run)
    monkeypatch.setattr(cli, "load_workspace", lambda _: workspace_graph)

    exit_code = cli.main([
        "--workspace-root",
        str(tmp_path),
        "publish",
        *case.extra_args,
    ])

    assert exit_code == 0, "publish should succeed with the stubbed run"
    options = captured_options["options"]
    assert options.sccache_stats is case.expected_stats, (
        f"--sccache-stats forwarded as {options.sccache_stats!r}"
    )
    assert options.sccache_stats_json == case.expected_json, (
        f"--sccache-stats-json forwarded as {options.sccache_stats_json!r}"
    )


@dc.dataclass(frozen=True, slots=True)
class _RebuildLockfilesCase:
    """One ``[bump]`` configuration, CLI flags, and the value they must yield.

    ``config_body`` is empty where the case tests a flag on its own, so the
    absence of configuration is stated explicitly rather than inferred.
    """

    config_body: str
    extra_args: tuple[str, ...]
    expected: bool | None


_REBUILD_LOCKFILES_CASES = (
    pytest.param(
        _RebuildLockfilesCase(config_body="", extra_args=(), expected=None),
        id="default",
    ),
    # The False here is hydrated by the Cyclopts TOML loader
    # (use_commands_as_keys=True), not by resolution logic in cli.bump.
    pytest.param(
        _RebuildLockfilesCase(
            config_body="[bump]\nrebuild_lockfiles = false\n",
            extra_args=(),
            expected=False,
        ),
        id="configuration-hydrated-by-cyclopts",
    ),
    pytest.param(
        _RebuildLockfilesCase(
            config_body="[bump]\nrebuild_lockfiles = true\n",
            extra_args=(),
            expected=True,
        ),
        id="configuration-hydrated-by-cyclopts-true",
    ),
    pytest.param(
        _RebuildLockfilesCase(
            config_body="[bump]\nrebuild_lockfiles = false\n",
            extra_args=("--rebuild-lockfiles",),
            expected=True,
        ),
        id="explicit-enable",
    ),
    pytest.param(
        _RebuildLockfilesCase(
            config_body="[bump]\nrebuild_lockfiles = false\n",
            extra_args=("--no-rebuild-lockfiles",),
            expected=False,
        ),
        id="explicit-disable",
    ),
)


@pytest.mark.parametrize("case", _REBUILD_LOCKFILES_CASES)
def test_bump_cli_forwards_raw_rebuild_lockfiles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    write_config: cabc.Callable[[str], Path],
    case: _RebuildLockfilesCase,
) -> None:
    """The CLI forwards the nullable flag; resolution belongs to the command."""
    write_config(case.config_body)
    workspace_graph = make_workspace(tmp_path.resolve())
    captured_kwargs: dict[str, typ.Any] = {}

    def fake_run(*args: object, **kwargs: object) -> str:
        captured_kwargs.update(kwargs)
        return "bumped"

    monkeypatch.setattr(bump_command, "run", fake_run)
    monkeypatch.setattr(cli, "load_workspace", lambda _: workspace_graph)

    exit_code = cli.main([
        "--workspace-root",
        str(tmp_path),
        "bump",
        "1.2.3",
        *case.extra_args,
    ])

    assert exit_code == 0, f"bump should succeed, got exit code {exit_code}"
    options = captured_kwargs["options"]
    assert isinstance(options, bump_command.BumpOptions), (
        "bump.run must receive a BumpOptions instance"
    )
    assert options.rebuild_lockfiles is case.expected, (
        f"the flag should be forwarded as {case.expected!r}"
    )
