"""Unit tests for ``lading.cli`` publish flag forwarding.

Covers the nullable publish flags the CLI resolves:
``--allow-unpublished-workspace-deps`` and
``--sccache-stats``/``--sccache-stats-json``. The ``bump`` flags are covered by
``test_cli_bump_flags``.
"""

import dataclasses as dc
import logging
import typing as typ
from pathlib import Path

import pytest

from lading import cli
from lading.commands import publish as publish_command
from tests.helpers.cli_workspace import make_workspace

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


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


@dc.dataclass(frozen=True, slots=True)
class _UnpublishedDepsCase:
    """One ``--allow-unpublished-workspace-deps`` input and its resolution.

    ``flag`` is the nullable CLI value, ``live`` selects the publish mode, and
    ``expected`` is the boolean the resolution must produce.
    """

    flag: bool | None
    live: bool
    expected: bool


_UNPUBLISHED_DEPS_CASES = (
    pytest.param(
        _UnpublishedDepsCase(flag=None, live=True, expected=False), id="none-live"
    ),
    pytest.param(
        _UnpublishedDepsCase(flag=None, live=False, expected=True), id="none-dry-run"
    ),
    pytest.param(
        _UnpublishedDepsCase(flag=True, live=True, expected=True), id="true-live"
    ),
    pytest.param(
        _UnpublishedDepsCase(flag=True, live=False, expected=True), id="true-dry-run"
    ),
    pytest.param(
        _UnpublishedDepsCase(flag=False, live=True, expected=False), id="false-live"
    ),
    pytest.param(
        _UnpublishedDepsCase(flag=False, live=False, expected=False), id="false-dry-run"
    ),
)


@pytest.mark.parametrize("case", _UNPUBLISHED_DEPS_CASES)
def test_resolve_allow_unpublished_workspace_deps_matrix(
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
    case: _UnpublishedDepsCase,
) -> None:
    """Each input combination resolves correctly and logs exactly once at DEBUG.

    The resolution reason is verified through the snapshotted DEBUG message.
    """
    caplog.set_level(logging.DEBUG, logger="lading.cli")

    resolved = cli._resolve_allow_unpublished_workspace_deps(
        live=case.live,
        allow_unpublished_workspace_deps=case.flag,
    )

    assert resolved is case.expected, (
        f"flag={case.flag!r} live={case.live!r}: expected {case.expected!r}, "
        f"got {resolved!r}"
    )
    debug_records = [
        record
        for record in caplog.records
        if record.levelno == logging.DEBUG and record.name == "lading.cli"
    ]
    assert len(debug_records) == 1, (
        f"flag={case.flag!r} live={case.live!r}: expected one DEBUG record, "
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
        """Record the publish options, asserting they were supplied."""
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
        """Record the publish options the CLI forwards, asserting presence."""
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
