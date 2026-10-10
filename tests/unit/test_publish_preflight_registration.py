"""Tests for publish preflight command-double registration."""

import typing as typ

from tests.bdd.steps.test_publish_infrastructure import (
    _PreflightStubConfig,
    _register_preflight_commands,
)

if typ.TYPE_CHECKING:
    from cmd_mox import CmdMox


def test_register_preflight_commands_preserves_git_passthrough_spy(
    cmd_mox: CmdMox,
) -> None:
    """Keep a real-Git passthrough spy registered during preflight setup."""
    git_spy = cmd_mox.spy("git")
    git_spy.passthrough()
    config = _PreflightStubConfig(cmd_mox, {})

    _register_preflight_commands(config)

    assert cmd_mox._doubles["git"] is git_spy, (
        "preflight setup must preserve the existing git passthrough spy"
    )
