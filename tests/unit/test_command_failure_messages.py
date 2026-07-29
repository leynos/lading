"""Snapshot tests for operator-facing command-failure messages.

Issue #102 collapsed the ``(stderr or stdout).strip()`` idiom into the shared
``lading.utils.process`` helpers. These snapshots pin the rendered messages at
each consuming boundary so the extraction cannot silently change operator-facing
text.

Every test drives a *public* command entry point with an injected failing
runner, so the message flows through the real failure path rather than being
read back from a formatting helper in isolation. The remaining two consumers of
the shared helpers are snapshotted at their own functional boundaries:

* the cargo pre-flight message in
  ``tests/unit/publish/test_preflight_cargo_runner.py`` (driving
  ``_run_cargo_preflight``), and
* the package/publish message in ``tests/unit/publish/test_packaging.py``
  (driving ``_package_crate``/``_publish_crate``).
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from lading.commands import bump_lockfiles
from lading.workspace import metadata

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from syrupy.assertion import SnapshotAssertion


def _failing_runner(
    stdout: str, stderr: str, exit_code: int = 101
) -> cabc.Callable[..., tuple[int, str, str]]:
    """Return a stub runner that always fails with the supplied output."""

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del command, cwd, env, echo_stdout
        return exit_code, stdout, stderr

    return runner


def test_lockfile_regeneration_failure_message(snapshot: SnapshotAssertion) -> None:
    """``regenerate_lockfiles`` renders the canonical detail suffix."""
    with pytest.raises(bump_lockfiles.LockfileRegenerationError) as excinfo:
        bump_lockfiles.regenerate_lockfiles(
            Path("/ws"),
            (),
            runner=_failing_runner("", "error: dependency conflict\n"),
        )

    assert snapshot == str(excinfo.value)


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        pytest.param("out", "err", id="stderr_wins"),
        pytest.param("out", "   ", id="stdout_fallback"),
        pytest.param("", "", id="status_fallback"),
    ],
)
def test_cargo_metadata_invocation_message(
    tmp_path: Path, snapshot: SnapshotAssertion, stdout: str, stderr: str
) -> None:
    """``load_cargo_metadata`` prefers stderr, then stdout, then the status."""
    with pytest.raises(metadata.CargoMetadataInvocationError) as excinfo:
        metadata.load_cargo_metadata(
            tmp_path, runner=_failing_runner(stdout, stderr, exit_code=2)
        )

    assert snapshot == str(excinfo.value)
