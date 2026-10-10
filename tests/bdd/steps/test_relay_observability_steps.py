"""BDD steps that pin relay observability at the CLI subprocess boundary."""

import sys
from pathlib import Path

import pytest
from pytest_bdd import scenarios, then, when

from .cli_run_types import CliRunResult
from .test_publish_infrastructure import (
    PreflightTestContext,
    _CommandResponse,
    _invoke_publish_with_options,
)

_FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"
_PAYLOAD = "private child output: ś ń"
_EVENT = (
    "RelayEvent(operation='relay_mirror', stream='stdout', "
    "transition='text_to_binary', error_category='unicode_encode')"
)

scenarios(str(_FEATURES_DIR / "relay_observability.feature"))


@when(
    "the CLI relays UTF-8 cargo output through a cp1252 text stream",
    target_fixture="cli_run",
)
def when_cli_relays_utf8_cargo_output(
    request: pytest.FixtureRequest,
) -> CliRunResult:
    """Run publish with one fixed UTF-8 result through cmd-mox passthrough."""
    workspace_directory: Path = request.getfixturevalue("workspace_directory")
    repo_root: Path = request.getfixturevalue("repo_root")
    preflight_test_context: PreflightTestContext = request.getfixturevalue(
        "preflight_test_context"
    )
    monkeypatch: pytest.MonkeyPatch = request.getfixturevalue("monkeypatch")
    tmp_path: Path = request.getfixturevalue("tmp_path")
    producer = tmp_path / "cargo-output"
    marker = tmp_path / "payload-emitted"
    payload_bytes = _PAYLOAD.encode("utf-8")
    producer.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "from pathlib import Path\n"
        f"marker = Path({str(marker)!r})\n"
        "if not marker.exists():\n"
        "    marker.touch()\n"
        f"    os.write(1, {payload_bytes!r})\n",
        encoding="utf-8",
    )
    producer.chmod(0o755)

    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("CMOX_REAL_COMMAND_cargo::package", str(producer))
    preflight_test_context.cmd_mox.spy("cargo::package").passthrough()
    preflight_test_context.overrides["cargo", "publish"] = _CommandResponse(exit_code=0)
    stub_config = preflight_test_context.create_stub_config()
    return _invoke_publish_with_options(
        repo_root,
        workspace_directory,
        stub_config,
        "--live",
    )


@then("the CLI emits one Unicode fallback relay event for stdout")
def then_cli_emits_one_stdout_fallback_event(
    cli_run: CliRunResult,
    preflight_test_context: PreflightTestContext,
) -> None:
    """Assert the real CLI emits the exact bounded fallback event once."""
    assert cli_run["returncode"] == 0, (
        f"publish CLI should succeed, stderr was:\n{cli_run['stderr']}"
    )
    event_message = f"relay observability event: {_EVENT}"
    assert cli_run["stderr"].count("relay observability event:") == 1, (
        "CLI stderr should contain exactly one relay observability event"
    )
    assert event_message in cli_run["stderr"], (
        "CLI stderr should report the stable stdout Unicode fallback fields"
    )
    preflight_test_context.cmd_mox.spy("cargo::package").assert_called()


@then("the CLI preserves the exact child output")
def then_cli_preserves_exact_child_output(cli_run: CliRunResult) -> None:
    """Assert the child's exact UTF-8 bytes lead the captured CLI output."""
    assert cli_run["stdout"].encode("utf-8").startswith(_PAYLOAD.encode("utf-8")), (
        "CLI stdout should begin with the child's exact UTF-8 bytes"
    )
    assert cli_run["stdout"].count(_PAYLOAD) == 1, (
        "the child payload should be captured once across publish invocations"
    )


@then("the relay event excludes the child output")
def then_relay_event_excludes_child_output(cli_run: CliRunResult) -> None:
    """Assert the child payload is absent from the CLI's event log."""
    assert _PAYLOAD not in cli_run["stderr"], (
        "CLI event logs must not expose child output"
    )
