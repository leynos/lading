"""Unit tests for the ``lading publish --skip-preflight`` flag.

The flag, its negative form, and the ``LADING_SKIP_PREFLIGHT`` default are
covered here rather than in ``test_cli.py`` because the decision they produce
carries a provenance label that the publish log prints verbatim.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import typing as typ
from pathlib import Path

import pytest

from lading import cli
from lading.commands import publish as publish_command

if typ.TYPE_CHECKING:
    from lading.workspace import WorkspaceCrate, WorkspaceGraph


@dc.dataclass(frozen=True)
class _SkipPreflightCase:
    """One CLI invocation and the skip decision it must produce."""

    extra_args: tuple[str, ...]
    env: dict[str, str]
    expected_skip: bool | None
    expected_source: str | None = None


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _SkipPreflightCase((), {}, expected_skip=None),
            id="unset-leaves-the-decision-to-configuration",
        ),
        pytest.param(
            _SkipPreflightCase(
                ("--skip-preflight",),
                {},
                expected_skip=True,
                expected_source=cli.SKIP_PREFLIGHT_FLAG_SOURCE,
            ),
            id="flag",
        ),
        pytest.param(
            _SkipPreflightCase(
                ("--no-skip-preflight",),
                {},
                expected_skip=False,
                expected_source=cli.SKIP_PREFLIGHT_FLAG_SOURCE,
            ),
            id="negative-flag",
        ),
        pytest.param(
            _SkipPreflightCase(
                (),
                {"LADING_SKIP_PREFLIGHT": "1"},
                expected_skip=True,
                expected_source=cli.SKIP_PREFLIGHT_ENVIRONMENT_SOURCE,
            ),
            id="environment",
        ),
        pytest.param(
            _SkipPreflightCase(
                ("--no-skip-preflight",),
                {"LADING_SKIP_PREFLIGHT": "true"},
                expected_skip=False,
                expected_source=cli.SKIP_PREFLIGHT_FLAG_SOURCE,
            ),
            id="flag-overrides-environment",
        ),
    ],
)
@pytest.mark.usefixtures("restore_root_logger")
def test_publish_cli_labels_the_skip_preflight_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: _SkipPreflightCase,
    make_workspace: cabc.Callable[..., WorkspaceGraph],
    make_crate: cabc.Callable[..., WorkspaceCrate],
) -> None:
    """The flag, its negative form, and its environment default reach publish.

    Each decision carries the input that supplied it, because the publish log
    names that source; a wrong label would tell an operator the checks were
    skipped by something they did not set.
    """
    monkeypatch.delenv("LADING_SKIP_PREFLIGHT", raising=False)
    for name, value in case.env.items():
        monkeypatch.setenv(name, value)
    root = tmp_path.resolve()
    workspace_graph = make_workspace(root, make_crate(root, "crate"))
    captured_options: dict[str, publish_command.PublishOptions] = {}

    def fake_run(
        workspace_root: Path,
        configuration: object,
        workspace_model: object,
        *,
        options: publish_command.PublishOptions | None = None,
    ) -> str:
        del workspace_root, configuration, workspace_model
        assert options is not None
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
    decision = captured_options["options"].skip_preflight
    if case.expected_skip is None:
        assert decision is None, f"expected no override, got {decision!r}"
        return
    assert decision is not None, "expected an explicit skip decision"
    assert decision.skip is case.expected_skip
    assert decision.source == case.expected_source


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("0", False),
        ("false", False),
        (" no ", False),
        ("maybe", None),
        (None, None),
    ],
)
def test_environment_boolean_mirrors_cyclopts_coercion(
    raw: str | None, *, expected: bool | None
) -> None:
    """Only the literals cyclopts coerces count as an environment decision.

    Attribution depends on this: a value cyclopts would have rejected cannot
    be the source of the resolved flag.
    """
    assert cli._environment_boolean(raw) is expected
